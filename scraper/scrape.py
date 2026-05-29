# -*- coding: utf-8 -*-
"""任天堂台灣 Switch / Switch 2 遊戲監看 — MVP 爬蟲

流程：
1. Playwright 並行渲染兩個目錄頁，萃取每卡片 NSUID + 名稱 + 封面 URL
2. 缺名稱的 NSUID 用 aiohttp 從 store.nintendo.com.hk / ec.nintendo.com 補抓
3. requests 打 Nintendo Price API（country=TW），拿即時原價、特價、特價區間
4. 套用中英文系列字典補搜尋別名
5. 輸出 web/games.json
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import requests
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from bilingual_dict import enrich_aliases  # noqa: E402


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"}

CATALOG_URLS = {
    "Switch": "https://www.nintendo.com/tw/software/switch",
    "Switch 2": "https://www.nintendo.com/tw/games/switch2/",
}
PRICE_API = "https://api.ec.nintendo.com/v1/price"

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "web" / "games.json"

_PLATFORM_SPLITTERS = ["Nintendo Switch 2", "Nintendo Switch", "盒裝版", "下載版"]
_INVALID_NAMES = {"任天堂", "Nintendo Store", "404 Not Found", ""}


def clean_name(s: str) -> str:
    if not s:
        return ""
    s = html_lib.unescape(html_lib.unescape(s))  # 站台 double-encoded
    s = re.split(r"[｜|]", s)[0].strip()  # 砍「｜下載版軟體｜任天堂」
    return s


def parse_card_name(text: str) -> str:
    if not text:
        return ""
    earliest = len(text)
    for sp in _PLATFORM_SPLITTERS:
        i = text.find(sp)
        if i > 0:
            earliest = min(earliest, i)
    return text[:earliest].strip()


# ---------- 目錄頁渲染 ----------

async def render_catalog(url: str, browser) -> list[dict]:
    ctx = await browser.new_context(user_agent=UA, locale="zh-TW")
    page = await ctx.new_page()
    await page.goto(url, wait_until="networkidle", timeout=60000)
    for y in [2000, 6000, 12000, 20000]:
        await page.evaluate(f"window.scrollTo(0, {y})")
        await page.wait_for_timeout(500)
    cards = await page.evaluate(r"""
() => {
  const out = [];
  document.querySelectorAll('.ncmn-thumb').forEach(t => {
    const bg = window.getComputedStyle(t).backgroundImage || '';
    const m = bg.match(/\/(\d{14})[a-f0-9]*\/[^"')\s]+/);
    if (!m) return;
    const urlMatch = bg.match(/url\(["']?(https?:\/\/[^"')]+)["']?\)/);
    const coverUrl = urlMatch ? urlMatch[1].split('?')[0] : '';
    const card = t.closest('a, article, li, [class*=card]');
    const text = card ? (card.innerText || '').trim().replace(/\s+/g, ' ') : '';
    const href = (card && card.tagName === 'A') ? card.getAttribute('href') : '';
    out.push({ nsuid: m[1], coverUrl, text, alt: '', href });
  });
  document.querySelectorAll('img').forEach(img => {
    const m = (img.src || '').match(/\/(\d{14})[a-f0-9]*\/[^"')\s]+/);
    if (!m) return;
    const coverUrl = (img.src || '').split('?')[0];
    const card = img.closest('a, article, li, [class*=card], [class*=item]');
    const href = (card && card.tagName === 'A') ? card.getAttribute('href') : '';
    out.push({ nsuid: m[1], coverUrl, text: '', alt: img.alt || '', href });
  });
  return out;
}
    """)
    await ctx.close()
    return cards


# ---------- 從 HK 補抓缺失的名稱 ----------

async def _fetch_title_one(session, url, sem):
    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                t = await r.text()
                m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', t)
                if m:
                    return clean_name(m.group(1))
                m = re.search(r"<title>([^<]+)", t)
                if m:
                    return clean_name(m.group(1))
        except Exception:
            pass
        return ""


async def _resolve_name(session, nsuid, sem):
    for url in [
        f"https://store.nintendo.com.hk/{nsuid}",
        f"https://ec.nintendo.com/HK/zh/titles/{nsuid}",
    ]:
        name = await _fetch_title_one(session, url, sem)
        if name and name not in _INVALID_NAMES:
            return nsuid, name
    return nsuid, ""


async def fill_missing_names(cards: dict[str, dict]):
    missing = [n for n, c in cards.items() if not c["name"]]
    if not missing:
        return
    print(f"  補抓 {len(missing)} 個缺失名稱（從 HK）...")
    sem = asyncio.Semaphore(10)
    async with aiohttp.ClientSession(headers={"User-Agent": UA, "Accept-Language": "zh-TW"}) as s:
        results = await asyncio.gather(*[_resolve_name(s, n, sem) for n in missing])
    got = 0
    for n, name in results:
        if name:
            cards[n]["name"] = name
            got += 1
    print(f"    補到 {got}/{len(missing)}")


# ---------- Price API ----------

def get_prices(nsuids: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(nsuids), 50):
        batch = nsuids[i:i + 50]
        r = requests.get(
            PRICE_API,
            params={"country": "TW", "lang": "zh", "ids": ",".join(batch)},
            headers=HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        for p in r.json().get("prices", []):
            out[str(p["title_id"])] = p
        time.sleep(0.3)
    return out


def calc_discount_percent(regular, discount):
    if not regular or not discount:
        return 0
    return round((1 - int(discount) / int(regular)) * 100)


def parse_price(p: dict) -> dict:
    rp = (p.get("regular_price") or {}).get("raw_value")
    dp_obj = p.get("discount_price") or {}
    dp = dp_obj.get("raw_value")
    return {
        "sales_status": p.get("sales_status"),
        "regular_price": int(rp) if rp else None,
        "discount_price": int(dp) if dp else None,
        "discount_percent": calc_discount_percent(rp, dp),
        "discount_start": dp_obj.get("start_datetime"),
        "discount_end": dp_obj.get("end_datetime"),
        "on_sale": bool(dp),
    }


# ---------- Main ----------

async def main():
    print(f"[{datetime.now().isoformat(timespec='seconds')}] === 開始爬蟲 ===")
    all_cards: dict[str, dict] = {}

    print("[1/4] 並行渲染兩個目錄頁...")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        results = await asyncio.gather(*[render_catalog(url, browser) for url in CATALOG_URLS.values()])
        await browser.close()
    for (platform, _), cards in zip(CATALOG_URLS.items(), results):
        print(f"  {platform}: {len(cards)} cards")
        for c in cards:
            n = c["nsuid"]
            name = clean_name((c.get("alt") or "").strip() or parse_card_name(c.get("text", "")))
            href = c.get("href") or ""
            detail = ""
            if href.startswith("/"):
                detail = "https://www.nintendo.com" + href
            elif href.startswith("http"):
                detail = href
            existing = all_cards.get(n)
            if existing is None:
                all_cards[n] = {"platform": platform, "cover": c["coverUrl"], "name": name, "detail_url": detail}
            else:
                if platform == "Switch 2" and existing["platform"] != "Switch 2":
                    existing["platform"] = platform
                if len(name) > len(existing["name"]):
                    existing["name"] = name
                if detail and not existing["detail_url"]:
                    existing["detail_url"] = detail
    print(f"  合計去重 NSUIDs: {len(all_cards)}")

    print("[2/4] 補抓缺失名稱（從 HK）...")
    await fill_missing_names(all_cards)

    print("[3/4] 打 Price API...")
    prices = get_prices(sorted(all_cards.keys()))
    valid = sum(1 for p in prices.values() if p.get("sales_status") != "not_found")
    print(f"  {valid}/{len(prices)} 有效")

    print("[4/4] 合併與輸出...")
    games = []
    for n, info in all_cards.items():
        pi = parse_price(prices.get(n, {}))
        name = info["name"] or ""
        if not name or pi["sales_status"] in (None, "not_found"):
            continue
        games.append({
            "nsuid": n,
            "platform": info["platform"],
            "name": name,
            "name_zh": name,
            "aliases": enrich_aliases(name),
            "cover_url": info["cover"],
            "detail_url": info["detail_url"] or None,
            "store_url": f"https://store.nintendo.com.hk/{n}",
            **pi,
        })
    on_sale = [g for g in games if g["on_sale"]]
    print(f"  輸出 {len(games)} 款（特價 {len(on_sale)} 款）")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "nintendo.com/tw + api.ec.nintendo.com (TW) + store.nintendo.com.hk",
        "total": len(games),
        "on_sale_count": len(on_sale),
        "games": sorted(games, key=lambda g: (-(g.get("discount_percent") or 0), g["name"])),
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] 寫入 {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
