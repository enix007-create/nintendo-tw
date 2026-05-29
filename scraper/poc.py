# -*- coding: utf-8 -*-
"""PoC v2: use Playwright for JS rendering + Price API for live prices"""
import asyncio
import json
import re
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"}

CATALOG_URLS = {
    "Switch": "https://www.nintendo.com/tw/software/switch",
    "Switch 2": "https://www.nintendo.com/tw/games/switch2/",
}
PRICE_API = "https://api.ec.nintendo.com/v1/price"
OUT = Path(__file__).parent / "sample_games.json"

NSUID_PATTERN = re.compile(r'images\.ctfassets\.net/[^/"\s]+/(\d{14})[a-f0-9]*/[^"\s)]+')


def extract_nsuids_and_covers(html):
    out = {}
    for m in NSUID_PATTERN.finditer(html):
        nsuid = m.group(1)
        url = m.group(0).split('"')[0].split('?')[0]
        if not url.startswith("http"):
            url = "https://" + url
        out.setdefault(nsuid, url)
    return out


def extract_game_links(html):
    pat = re.compile(r'/tw/games/(switch2?)/([a-z0-9]+)/')
    seen = set()
    out = []
    for m in pat.finditer(html):
        platform, code = m.group(1), m.group(2)
        key = f"{platform}/{code}"
        if key not in seen:
            seen.add(key)
            out.append((platform, code, f"https://www.nintendo.com/tw/games/{platform}/{code}/"))
    return out


async def render_catalog(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(user_agent=UA, locale="zh-TW")
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        for y in [800, 2000, 4000, 6000, 8000, 12000]:
            await page.evaluate(f"window.scrollTo(0, {y})")
            await page.wait_for_timeout(700)
        html = await page.content()
        await browser.close()
        return html


def fetch_detail(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.encoding = "utf-8"
    html = r.text
    m = re.search(r"<title>([^<|]+)", html)
    title = m.group(1).strip() if m else ""
    box = re.search(r"盒裝版\s*TWD\s*([\d,]+)", html)
    dl = re.search(r"下載版\s*TWD\s*([\d,]+)", html)
    nsuid = re.search(r"images\.ctfassets\.net/[^/]+/(\d{14})", html)
    return {
        "name_zh": title,
        "box_price_twd": int(box.group(1).replace(",", "")) if box else None,
        "download_price_twd": int(dl.group(1).replace(",", "")) if dl else None,
        "nsuid": nsuid.group(1) if nsuid else None,
    }


def get_prices(nsuids):
    out = []
    for i in range(0, len(nsuids), 50):
        batch = nsuids[i:i + 50]
        r = requests.get(
            PRICE_API,
            params={"country": "TW", "lang": "zh", "ids": ",".join(batch)},
            headers=HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        out.extend(r.json().get("prices", []))
        time.sleep(0.3)
    return out


async def main():
    print("=== Phase 1: Render catalogs with Playwright ===")
    catalogs = {}
    all_games = {}
    catalog_htmls = {}
    for platform, url in CATALOG_URLS.items():
        print(f"  rendering {platform}: {url}")
        html = await render_catalog(url)
        catalog_htmls[platform] = html
        ns_cover = extract_nsuids_and_covers(html)
        links = extract_game_links(html)
        catalogs[platform] = {"nsuids": len(ns_cover), "links": len(links), "html_len": len(html)}
        print(f"    HTML {len(html)} bytes | NSUIDs {len(ns_cover)} | detail links {len(links)}")
        for n, c in ns_cover.items():
            all_games.setdefault(n, {"platform": platform, "cover": c})

    print(f"\nTotal unique NSUIDs: {len(all_games)}")
    sample_nsuids = list(all_games.keys())[:10]

    print(f"\n=== Phase 2: Query Price API for {len(sample_nsuids)} NSUIDs ===")
    prices = get_prices(sample_nsuids)
    price_by_id = {str(p["title_id"]): p for p in prices}
    for p in prices:
        st = p.get("sales_status")
        rp = (p.get("regular_price") or {}).get("amount", "-")
        dp = (p.get("discount_price") or {}).get("amount", "-")
        print(f"  {p['title_id']}: {st} | regular {rp} | discount {dp}")

    print("\n=== Phase 3: Fetch 5 detail pages ===")
    links = extract_game_links(catalog_htmls.get("Switch", ""))[:5]
    sample_details = []
    for platform, code, url in links:
        try:
            d = fetch_detail(url)
            d.update({"url": url, "platform_catalog": platform, "code": code})
            sample_details.append(d)
            print(f"  {platform}/{code}: {d['name_zh'][:30]} | box {d['box_price_twd']} | dl {d['download_price_twd']} | NSUID {d['nsuid']}")
            time.sleep(0.3)
        except Exception as e:
            print(f"  {platform}/{code} failed: {e}")

    merged = []
    for n in sample_nsuids:
        info = all_games[n]
        ap = price_by_id.get(n, {})
        rp = ap.get("regular_price") or {}
        dp = ap.get("discount_price") or {}
        merged.append({
            "nsuid": n,
            "platform": info["platform"],
            "cover_url": info["cover"],
            "sales_status": ap.get("sales_status"),
            "regular_price_twd": rp.get("raw_value"),
            "discount_price_twd": dp.get("raw_value"),
            "discount_start": dp.get("start_datetime"),
            "discount_end": dp.get("end_datetime"),
        })

    OUT.write_text(
        json.dumps(
            {
                "summary": {"catalogs": catalogs, "total_unique_nsuids": len(all_games)},
                "price_api_sample": merged,
                "detail_pages_sample": sample_details,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[OK] wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
