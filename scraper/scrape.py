# -*- coding: utf-8 -*-
"""任天堂台灣 Switch / Switch 2 遊戲監看 — 爬蟲 v2（廣域版）

四階段，可獨立執行（避開 bash 45s 限制）：

  scrape.py --phase=catalog   # Playwright 渲染兩個目錄頁
  scrape.py --phase=scan      # Price API 範圍掃描，找出所有有效 TW NSUID
  scrape.py --phase=names     # 對 cache 缺名稱的 NSUID 補抓 HK title + 封面
  scrape.py --phase=build     # Price API 重新打一輪、合併、輸出 web/games.json
  scrape.py                   # 四階段依序全跑（CI 用）

持久化 cache：scraper/_cache/games_meta.json
  - 已知 NSUID 的 platform / name / cover 快取
  - 每小時只更新 prices，metadata 累積
"""
from __future__ import annotations

import argparse
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


# ---------- CONFIG ----------

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"}

CATALOG_URLS = {
    "Switch": "https://www.nintendo.com/tw/software/switch",
    "Switch 2": "https://www.nintendo.com/tw/games/switch2/",
}
PRICE_API = "https://api.ec.nintendo.com/v1/price"

# NSUID 掃描範圍（依現有資料 67524–127209 估算，邊界縮一點避免 Akamai 退避燒時間）
SCAN_LO = 50000
SCAN_HI = 135000
BATCH_SIZE = 50          # Price API 上限
SCAN_CONCURRENCY = 4     # 並行 Price API 請求數（Akamai 對高並行很敏感）
SCAN_DELAY = 0.25        # 每個 request 之間的 sleep（per worker）
SCAN_MAX_RETRY = 2       # 403/429 退避次數（多了反而把時間吃光）
HK_CONCURRENCY = 15      # 並行 HK store 抓取
HK_TIMEOUT = 12
HK_RETRY_202 = 2         # HK store 回 202（async 生成中）時的重試次數
HK_RETRY_SLEEP = 1.5     # 202 retry 前 sleep 秒數

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "scraper" / "_cache"
META_PATH = CACHE_DIR / "games_meta.json"
SCAN_PATH = CACHE_DIR / "scan_nsuids.json"
CATALOG_PATH = CACHE_DIR / "catalog.json"
OUT_PATH = ROOT / "web" / "games.json"

_PLATFORM_SPLITTERS = ["Nintendo Switch 2", "Nintendo Switch", "盒裝版", "下載版"]
_INVALID_NAMES = {"任天堂", "Nintendo Store", "404 Not Found", "", "Nintendo HK"}


# ---------- 共用工具 ----------

def clean_name(s: str) -> str:
    if not s:
        return ""
    s = html_lib.unescape(html_lib.unescape(s))
    s = re.split(r"[｜|]", s)[0].strip()
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


def load_meta() -> dict:
    if META_PATH.exists():
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    return {}


def save_meta(meta: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- Phase: catalog（目錄頁渲染）----------

async def _render_catalog(url: str, browser) -> list[dict]:
    ctx = await browser.new_context(user_agent=UA, locale="zh-TW")
    page = await ctx.new_page()
    await page.goto(url, wait_until="networkidle", timeout=60000)
    for y in [2000, 6000, 12000, 20000, 30000]:
        await page.evaluate(f"window.scrollTo(0, {y})")
        await page.wait_for_timeout(600)
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


async def phase_catalog():
    """Playwright 渲染兩個目錄頁，更新 meta 的 platform/cover/name"""
    print("[catalog] 並行渲染兩個目錄頁...")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        results = await asyncio.gather(*[_render_catalog(url, browser) for url in CATALOG_URLS.values()])
        await browser.close()

    meta = load_meta()
    catalog_summary = {}
    for (platform, _), cards in zip(CATALOG_URLS.items(), results):
        print(f"  {platform}: {len(cards)} cards")
        catalog_summary[platform] = len(cards)
        for c in cards:
            n = c["nsuid"]
            name = clean_name((c.get("alt") or "").strip() or parse_card_name(c.get("text", "")))
            href = c.get("href") or ""
            detail = ""
            if href.startswith("/"):
                detail = "https://www.nintendo.com" + href
            elif href.startswith("http"):
                detail = href

            entry = meta.get(n, {})
            entry["nsuid"] = n
            # 平台：Switch 2 蓋過 Switch
            if platform == "Switch 2" or not entry.get("platform"):
                entry["platform"] = platform
            # 名稱：取長者勝（catalog 名稱通常較完整）
            if name and len(name) > len(entry.get("name", "")):
                entry["name"] = name
            # 封面：catalog 來的優先
            if c.get("coverUrl"):
                entry["cover_url"] = c["coverUrl"]
                entry["cover_source"] = "catalog"
            if detail:
                entry["detail_url"] = detail
            entry["from_catalog"] = True
            meta[n] = entry

    save_meta(meta)
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(catalog_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[catalog] meta 中 NSUID 總數: {len(meta)}")


# ---------- Phase: scan（Price API 範圍掃描）----------

async def _scan_batch(session, ids: list[str], sem) -> list[dict]:
    """單批 Price API 請求，含 403/429/503 backoff 與 per-worker delay。"""
    async with sem:
        params = {"country": "TW", "lang": "zh", "ids": ",".join(ids)}
        for attempt in range(SCAN_MAX_RETRY):
            try:
                async with session.get(PRICE_API, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if r.status == 200:
                        data = await r.json()
                        await asyncio.sleep(SCAN_DELAY)
                        return data.get("prices", [])
                    if r.status in (403, 429, 503):
                        # Akamai 軟性封鎖：指數退避
                        await asyncio.sleep(2 ** attempt + 1)
                        continue
                    await asyncio.sleep(SCAN_DELAY)
                    return []
            except Exception:
                await asyncio.sleep(1.0 * (attempt + 1))
        return []


async def phase_scan(lo: int = SCAN_LO, hi: int = SCAN_HI):
    """並行掃描 NSUID 範圍，找出所有有效 ID。邊掃邊存 cache，避免中斷掉資料。"""
    print(f"[scan] 範圍 {lo}–{hi}，批次 {BATCH_SIZE}，並行 {SCAN_CONCURRENCY}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # 載入既有 scan cache（中斷續跑用）
    all_prices: dict = {}
    if SCAN_PATH.exists():
        try:
            all_prices = json.loads(SCAN_PATH.read_text(encoding="utf-8"))
            print(f"  載入既有 scan cache：{len(all_prices)} 個 NSUID")
        except Exception:
            pass

    sem = asyncio.Semaphore(SCAN_CONCURRENCY)
    start = time.time()

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        tasks = []
        for base in range(lo, hi, BATCH_SIZE):
            ids = [f"70010000{base + i:06d}" for i in range(BATCH_SIZE)]
            tasks.append(_scan_batch(session, ids, sem))

        done = 0
        last_flush = 0
        for coro in asyncio.as_completed(tasks):
            prices = await coro
            done += 1
            for p in prices:
                if p.get("sales_status") and p["sales_status"] != "not_found":
                    all_prices[str(p["title_id"])] = p
            if done % 50 == 0:
                print(f"  進度 {done}/{len(tasks)} 批，命中 {len(all_prices)}", flush=True)
            # 每 100 批 flush 一次
            if done - last_flush >= 100:
                SCAN_PATH.write_text(json.dumps(all_prices, ensure_ascii=False), encoding="utf-8")
                last_flush = done

    elapsed = time.time() - start
    print(f"[scan] 完成。{len(tasks)} 批 / 命中 {len(all_prices)} 款 / {elapsed:.1f}s")
    SCAN_PATH.write_text(json.dumps(all_prices, ensure_ascii=False, indent=2), encoding="utf-8")

    # 把新發現的 NSUID 寫入 meta（保留既有資料）
    meta = load_meta()
    new_count = 0
    for nsuid in all_prices.keys():
        if nsuid not in meta:
            meta[nsuid] = {"nsuid": nsuid, "platform": "Switch", "name": "", "cover_url": ""}
            new_count += 1
    save_meta(meta)
    print(f"[scan] 新增 {new_count} 個未知 NSUID 進 meta")


# ---------- Phase: names（補抓 HK 名稱 + 封面 + 平台）----------

_HK_TITLE_RE = re.compile(r"<title>([^<]+)</title>")
_HK_PROD_IMG_RE = re.compile(r'src=["\'](https?://store\.nintendo\.com\.hk/media/catalog/product/cache/[^"\']+\.(?:jpg|jpeg|png|webp))["\']')
_HK_PLATFORM_RE = re.compile(r"(Switch ?2|Switch)")


_hk_status_log = {"counts": {}, "samples": []}


async def _fetch_hk_one(session, nsuid: str, sem) -> dict:
    """從 HK store 抓 title + 第一張產品圖 + 平台。會記錄 HTTP 狀態碼分佈以便診斷。"""
    out = {"nsuid": nsuid, "name": "", "cover": "", "platform_hint": ""}
    async with sem:
        for url in [f"https://store.nintendo.com.hk/{nsuid}", f"https://ec.nintendo.com/HK/zh/titles/{nsuid}"]:
            text = None
            try:
                # 對 202（async 生成中）做 retry
                for attempt in range(1 + HK_RETRY_202):
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=HK_TIMEOUT)) as r:
                        key = f"{r.status}"
                        _hk_status_log["counts"][key] = _hk_status_log["counts"].get(key, 0) + 1
                        if r.status == 200:
                            text = await r.text()
                            if attempt > 0:
                                _hk_status_log["counts"]["202_retry_hit"] = _hk_status_log["counts"].get("202_retry_hit", 0) + 1
                            break
                        if r.status == 202 and attempt < HK_RETRY_202:
                            await asyncio.sleep(HK_RETRY_SLEEP)
                            continue
                        if len(_hk_status_log["samples"]) < 3:
                            _hk_status_log["samples"].append(f"{nsuid} {url[:40]} -> {r.status}")
                        if r.status == 202:
                            _hk_status_log["counts"]["202_giveup"] = _hk_status_log["counts"].get("202_giveup", 0) + 1
                        break
                if text is None:
                    continue
                m = _HK_TITLE_RE.search(text)
                if m:
                    name = clean_name(m.group(1))
                    if name and name not in _INVALID_NAMES:
                        out["name"] = name
                if "store.nintendo.com.hk" in url:
                    m = _HK_PROD_IMG_RE.search(text)
                    if m:
                        out["cover"] = m.group(1)
                    if re.search(r"Switch ?2", text):
                        out["platform_hint"] = "Switch 2"
                    elif "Switch" in text:
                        out["platform_hint"] = "Switch"
                if out["name"]:
                    break
            except Exception as e:
                key = f"err:{type(e).__name__}"
                _hk_status_log["counts"][key] = _hk_status_log["counts"].get(key, 0) + 1
                continue
            await asyncio.sleep(0.2)  # per-worker pacing
    return out


async def phase_names(force_all: bool = False, limit: int = 0):
    """對 meta 中缺名稱（或缺封面）的 NSUID 補抓。limit > 0 時只處理前 N 個。"""
    meta = load_meta()
    if force_all:
        targets = list(meta.keys())
    else:
        targets = [n for n, m in meta.items() if not m.get("name") or not m.get("cover_url")]
    # 之前已 check 過但沒結果的放後面，優先處理沒查過的
    targets.sort(key=lambda n: meta[n].get("hk_checked", False))
    if limit and limit > 0:
        targets = targets[:limit]
    if not targets:
        print("[names] 沒有需要補抓的 NSUID")
        return
    # 降並行 + UA 補強，避免 Akamai 立刻封
    conc = min(HK_CONCURRENCY, 6)
    print(f"[names] 補抓 {len(targets)} 個 NSUID（並行 {conc}）...")
    sem = asyncio.Semaphore(conc)
    start = time.time()
    _hk_status_log["counts"].clear()
    _hk_status_log["samples"].clear()
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        results = await asyncio.gather(*[_fetch_hk_one(session, n, sem) for n in targets])

    got_name = got_cover = 0
    for r in results:
        n = r["nsuid"]
        entry = meta[n]
        if r["name"] and not entry.get("name"):
            entry["name"] = r["name"]
            got_name += 1
        if r["cover"] and not entry.get("cover_url"):
            entry["cover_url"] = r["cover"]
            entry["cover_source"] = "hk_store"
            got_cover += 1
        if r["platform_hint"] == "Switch 2" and entry.get("platform") != "Switch 2":
            if not entry.get("from_catalog"):
                entry["platform"] = "Switch 2"
        entry["hk_checked"] = True
    save_meta(meta)
    print(f"[names] 補到 name {got_name}、cover {got_cover}，耗時 {time.time()-start:.1f}s")
    print(f"[names] HTTP 狀態分佈: {dict(sorted(_hk_status_log['counts'].items(), key=lambda x: -x[1]))}")
    if _hk_status_log["samples"]:
        print(f"[names] 非 200 樣本: {_hk_status_log['samples']}")


# ---------- Phase: build（重打 Price API、合併、輸出 games.json）----------

def _calc_discount_percent(regular, discount):
    if not regular or not discount:
        return 0
    return round((1 - int(discount) / int(regular)) * 100)


def _parse_price(p: dict) -> dict:
    if not p:
        return {
            "sales_status": None, "regular_price": None, "discount_price": None,
            "discount_percent": 0, "discount_start": None, "discount_end": None, "on_sale": False,
        }
    rp = (p.get("regular_price") or {}).get("raw_value")
    dp_obj = p.get("discount_price") or {}
    dp = dp_obj.get("raw_value")
    return {
        "sales_status": p.get("sales_status"),
        "regular_price": int(rp) if rp else None,
        "discount_price": int(dp) if dp else None,
        "discount_percent": _calc_discount_percent(rp, dp),
        "discount_start": dp_obj.get("start_datetime"),
        "discount_end": dp_obj.get("end_datetime"),
        "on_sale": bool(dp),
    }


async def _refresh_prices(nsuids: list[str]) -> dict:
    """重打 Price API 取最新價格"""
    sem = asyncio.Semaphore(SCAN_CONCURRENCY)
    out = {}
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        tasks = []
        for i in range(0, len(nsuids), BATCH_SIZE):
            batch = nsuids[i:i + BATCH_SIZE]
            tasks.append(_scan_batch(session, batch, sem))
        for coro in asyncio.as_completed(tasks):
            prices = await coro
            for p in prices:
                out[str(p["title_id"])] = p
    return out


async def phase_build():
    meta = load_meta()
    if not meta:
        print("[build] meta 是空的，請先跑 catalog/scan/names")
        return
    nsuids = sorted(meta.keys())

    # 讀 scan cache（fallback 用）
    scan_cache = {}
    if SCAN_PATH.exists():
        try:
            scan_cache = json.loads(SCAN_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    print(f"[build] 重打 Price API（{len(nsuids)} 個 NSUID）...")
    start = time.time()
    prices = await _refresh_prices(nsuids)
    print(f"[build]   refresh {len(prices)} 筆有回，耗時 {time.time()-start:.1f}s")

    # Refresh 失敗的 NSUID 用 scan cache 補：保證即使 Akamai 擋住 build 也有產出
    fallback_used = 0
    for n in nsuids:
        if n not in prices and n in scan_cache:
            prices[n] = scan_cache[n]
            fallback_used += 1
    if fallback_used:
        print(f"[build]   scan cache 補價格: {fallback_used} 筆")

    games = []
    for n in nsuids:
        info = meta[n]
        p = prices.get(n)
        pi = _parse_price(p)
        # 過濾無名稱、無價或 not_found
        if not info.get("name") or pi["sales_status"] in (None, "not_found"):
            continue
        name = info["name"]
        games.append({
            "nsuid": n,
            "platform": info.get("platform") or "Switch",
            "name": name,
            "name_zh": name,
            "aliases": enrich_aliases(name),
            "cover_url": info.get("cover_url") or "",
            "detail_url": info.get("detail_url"),
            "store_url": f"https://store.nintendo.com.hk/{n}",
            **pi,
        })
    on_sale = [g for g in games if g["on_sale"]]
    print(f"[build] 輸出 {len(games)} 款（特價 {len(on_sale)}）")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "nintendo.com/tw (catalog) + api.ec.nintendo.com (TW, NSUID range scan) + store.nintendo.com.hk (names + covers)",
        "total": len(games),
        "on_sale_count": len(on_sale),
        "games": sorted(games, key=lambda g: (-(g.get("discount_percent") or 0), g["name"])),
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[build] 寫入 {OUT_PATH}")


# ---------- main ----------

async def run_bootstrap(lo: int = SCAN_LO, hi: int = SCAN_HI, names_limit: int = 500):
    """完整流程：catalog + 全範圍 scan + names(限量) + build。
    names 每次只補 500（避免 Akamai 暴怒），其他靠 hourly refresh 慢慢累積。"""
    await phase_catalog()
    await phase_scan(lo, hi)
    # scan 後 sleep 一下讓 Akamai 退火
    print("[bootstrap] scan 完，sleep 30s 退火...")
    await asyncio.sleep(30)
    await phase_names(limit=names_limit)
    await phase_build()


async def run_refresh(names_limit: int = 300):
    """快速流程：catalog + names(限量) + build。每小時跑。
    names_limit 控制每次最多補幾個，避免 Akamai 觸發。"""
    await phase_catalog()
    await phase_names(limit=names_limit)
    await phase_build()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--phase",
        choices=["catalog", "scan", "names", "build", "refresh", "bootstrap", "all"],
        default="refresh",
        help="refresh=每小時用（不掃描），bootstrap=完整掃描，all=同 bootstrap",
    )
    ap.add_argument("--scan-lo", type=int, default=SCAN_LO)
    ap.add_argument("--scan-hi", type=int, default=SCAN_HI)
    ap.add_argument("--names-force-all", action="store_true")
    ap.add_argument("--names-limit", type=int, default=0, help="names phase 單次處理上限（0=不限）")
    args = ap.parse_args()

    print(f"[{datetime.now().isoformat(timespec='seconds')}] === scrape v2 / phase={args.phase} ===")
    if args.phase == "catalog":
        asyncio.run(phase_catalog())
    elif args.phase == "scan":
        asyncio.run(phase_scan(args.scan_lo, args.scan_hi))
    elif args.phase == "names":
        asyncio.run(phase_names(args.names_force_all, args.names_limit))
    elif args.phase == "build":
        asyncio.run(phase_build())
    elif args.phase == "refresh":
        asyncio.run(run_refresh(args.names_limit or 300))
    else:  # bootstrap / all
        asyncio.run(run_bootstrap(args.scan_lo, args.scan_hi, args.names_limit or 500))


if __name__ == "__main__":
    main()
