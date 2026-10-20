#!/usr/bin/env python3
"""
在地端（您自己的電腦）執行：掃描瀏覽器今日的瀏覽紀錄，過濾掉廣告/購物/社群/搜尋
等非文章連結，將剩下的連結整理並附加寫入 links.md。

支援：Chrome / Edge / Brave（Chromium 系列）與 Firefox，macOS / Windows / Linux。
本腳本只讀取「瀏覽器歷史紀錄資料庫」，不會上傳任何資料，所有處理都在本機執行。

用法：
    python3 tools/fetch_reading_links.py            # 掃描「今天」
    python3 tools/fetch_reading_links.py --days 3    # 掃描最近 3 天
    python3 tools/fetch_reading_links.py --dry-run   # 只印出結果，不寫入 links.md
"""
import argparse
import datetime
import os
import platform
import re
import shutil
import sqlite3
import sys
import tempfile
from urllib.parse import urlparse

# ── 過濾規則：網域黑名單（廣告、購物、社群、搜尋引擎、郵件/雲端服務、串流影音等） ──
BLOCKED_DOMAIN_PATTERNS = [
    r"(^|\.)doubleclick\.net$", r"(^|\.)googlesyndication\.com$",
    r"(^|\.)googleadservices\.com$", r"(^|\.)adservice\.google\.",
    r"(^|\.)outbrain\.com$", r"(^|\.)taboola\.com$", r"(^|\.)criteo\.com$",
    r"(^|\.)amazon\.", r"(^|\.)ebay\.", r"(^|\.)shopee\.",
    r"(^|\.)momoshop\.com\.tw$", r"(^|\.)pchome\.com\.tw$",
    r"(^|\.)taobao\.com$", r"(^|\.)tmall\.com$", r"(^|\.)rakuten\.",
    r"(^|\.)etsy\.com$", r"(^|\.)ruten\.com\.tw$",
    r"(^|\.)facebook\.com$", r"(^|\.)instagram\.com$", r"(^|\.)threads\.net$",
    r"(^|\.)tiktok\.com$", r"(^|\.)twitter\.com$", r"(^|\.)x\.com$",
    r"(^|\.)linkedin\.com$",
    r"(^|\.)google\.[a-z.]+$",  # Google 搜尋/首頁（不含 docs/drive 等已另列但保守一起擋）
    r"(^|\.)bing\.com$", r"(^|\.)yahoo\.com$", r"(^|\.)duckduckgo\.com$",
    r"(^|\.)mail\.google\.com$", r"(^|\.)outlook\.live\.com$",
    r"(^|\.)drive\.google\.com$", r"(^|\.)calendar\.google\.com$",
    r"(^|\.)youtube\.com$", r"(^|\.)netflix\.com$", r"(^|\.)twitch\.tv$",
    r"(^|\.)localhost$",
]
BLOCKED_RE = re.compile("|".join(BLOCKED_DOMAIN_PATTERNS), re.IGNORECASE)

CHROME_EPOCH = datetime.datetime(1601, 1, 1)


def is_blocked(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return True
    if not host:
        return True
    return bool(BLOCKED_RE.search(host))


def find_chromium_profiles():
    """回傳 (browser_name, History 檔案路徑) 的清單。"""
    home = os.path.expanduser("~")
    system = platform.system()
    candidates = []
    if system == "Darwin":
        bases = {
            "Chrome": f"{home}/Library/Application Support/Google/Chrome",
            "Edge": f"{home}/Library/Application Support/Microsoft Edge",
            "Brave": f"{home}/Library/Application Support/BraveSoftware/Brave-Browser",
        }
    elif system == "Windows":
        local = os.environ.get("LOCALAPPDATA", f"{home}\\AppData\\Local")
        bases = {
            "Chrome": f"{local}\\Google\\Chrome\\User Data",
            "Edge": f"{local}\\Microsoft\\Edge\\User Data",
            "Brave": f"{local}\\BraveSoftware\\Brave-Browser\\User Data",
        }
    else:  # Linux
        bases = {
            "Chrome": f"{home}/.config/google-chrome",
            "Chromium": f"{home}/.config/chromium",
            "Brave": f"{home}/.config/BraveSoftware/Brave-Browser",
        }
    for name, base in bases.items():
        if not os.path.isdir(base):
            continue
        for profile in os.listdir(base):
            hist = os.path.join(base, profile, "History")
            if os.path.isfile(hist):
                candidates.append((f"{name}:{profile}", hist))
    return candidates


def find_firefox_profiles():
    home = os.path.expanduser("~")
    system = platform.system()
    if system == "Darwin":
        base = f"{home}/Library/Application Support/Firefox/Profiles"
    elif system == "Windows":
        base = f"{os.environ.get('APPDATA', home)}\\Mozilla\\Firefox\\Profiles"
    else:
        base = f"{home}/.mozilla/firefox"
    candidates = []
    if os.path.isdir(base):
        for profile in os.listdir(base):
            db = os.path.join(base, profile, "places.sqlite")
            if os.path.isfile(db):
                candidates.append((f"Firefox:{profile}", db))
    return candidates


def read_sqlite_copy(path):
    """複製一份唯讀快照，避免瀏覽器開著時的檔案鎖定問題。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    shutil.copy2(path, tmp.name)
    for ext in ("-wal", "-journal"):
        side = path + ext
        if os.path.isfile(side):
            try:
                shutil.copy2(side, tmp.name + ext)
            except OSError:
                pass
    return tmp.name


def fetch_chromium_entries(db_path, since_dt):
    tmp_path = read_sqlite_copy(db_path)
    try:
        conn = sqlite3.connect(tmp_path)
        since_chrome = int((since_dt - CHROME_EPOCH).total_seconds() * 1_000_000)
        cur = conn.execute(
            "SELECT url, title, last_visit_time FROM urls WHERE last_visit_time >= ?",
            (since_chrome,),
        )
        rows = cur.fetchall()
        conn.close()
        return [(url, title) for url, title, _ in rows]
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def fetch_firefox_entries(db_path, since_dt):
    tmp_path = read_sqlite_copy(db_path)
    try:
        conn = sqlite3.connect(tmp_path)
        since_unix_us = int(since_dt.timestamp() * 1_000_000)
        cur = conn.execute(
            "SELECT url, title FROM moz_places WHERE last_visit_date >= ?",
            (since_unix_us,),
        )
        rows = cur.fetchall()
        conn.close()
        return rows
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def collect_entries(days):
    since_dt = datetime.datetime.now() - datetime.timedelta(days=days)
    results = []
    for name, path in find_chromium_profiles():
        try:
            for url, title in fetch_chromium_entries(path, since_dt):
                results.append((name, url, title))
        except sqlite3.Error as exc:
            print(f"[警告] 無法讀取 {name}: {exc}", file=sys.stderr)
    for name, path in find_firefox_profiles():
        try:
            for url, title in fetch_firefox_entries(path, since_dt):
                results.append((name, url, title))
        except sqlite3.Error as exc:
            print(f"[警告] 無法讀取 {name}: {exc}", file=sys.stderr)
    return results


def load_existing_urls(links_md_path):
    if not os.path.isfile(links_md_path):
        return set()
    urls = set()
    with open(links_md_path, encoding="utf-8") as f:
        for line in f:
            m = re.search(r"\((https?://[^)]+)\)", line)
            if m:
                urls.add(m.group(1))
    return urls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1, help="掃描最近幾天（預設 1 = 今天）")
    parser.add_argument("--dry-run", action="store_true", help="只印出結果，不寫入 links.md")
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    links_md = os.path.join(repo_root, "links.md")

    raw = collect_entries(args.days)
    existing = load_existing_urls(links_md)

    kept, blocked = [], []
    seen = set()
    for browser, url, title in raw:
        if not url.startswith(("http://", "https://")):
            continue
        if url in existing or url in seen:
            continue
        seen.add(url)
        if is_blocked(url):
            blocked.append((browser, url, title))
        else:
            kept.append((browser, url, title))

    print(f"掃描到 {len(raw)} 筆紀錄，過濾後保留 {len(kept)} 筆，排除 {len(blocked)} 筆。")

    if not kept:
        print("沒有新的候選連結。")
        return

    today_str = datetime.date.today().isoformat()
    lines = [f"\n## {today_str}（自動掃描，請自行覆核）\n"]
    for browser, url, title in kept:
        label = title.strip() if title and title.strip() else url
        label = label.replace("\n", " ").replace("]", ")").replace("[", "(")
        lines.append(f"- [{label}]({url}) <!-- {browser} -->\n")

    if args.dry_run:
        print("".join(lines))
        if blocked:
            print(f"\n（已排除 {len(blocked)} 筆，使用 --dry-run 未實際寫入）")
        return

    with open(links_md, "a", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"已附加 {len(kept)} 筆連結到 {links_md}")
    print("請自行檢查內容是否恰當，再 git add / commit / push。")


if __name__ == "__main__":
    main()
