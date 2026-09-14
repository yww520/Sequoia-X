#!/usr/bin/env python
"""从新浪财经抓行业分类，填充 stock_meta 表（绕开 Tushare 配额）。

新浪接口（无需 token，实测可用）：
  https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php
    → 返回 {行业代码: "代码,行业名,股票数,...,领涨股,..."}
  https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/
      Market_Center.getHQNodeData?page=1&num=100&sort=symbol&asc=1&node=<行业代码>
    → 返回该行业成分股 [{symbol, name, ...}]

用法：
    .venv/bin/python load_industry_sina.py
"""

import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "sequoia_v2.db"
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn/",
}


def get_sectors(sess: requests.Session) -> dict[str, str]:
    """返回 {行业代码: 行业名}"""
    url = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
    r = sess.get(url, headers=HDR, timeout=20)
    r.encoding = "gbk"
    m = re.search(r"=\s*(\{.*\})", r.text, re.S)
    if not m:
        raise RuntimeError("新浪行业列表解析失败")
    raw = json.loads(m.group(1))
    out = {}
    for key, val in raw.items():
        parts = val.split(",")
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def get_members(sess: requests.Session, node: str, pages: int = 20) -> list[tuple[str, str]]:
    """返回该行业的 [(6位代码, 名称), ...]"""
    out: list[tuple[str, str]] = []
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData"
    )
    for page in range(1, pages + 1):
        params = {
            "page": page, "num": 100, "sort": "symbol", "asc": 1,
            "node": node, "_s_r_a": "page",
        }
        try:
            r = sess.get(url, params=params, headers=HDR, timeout=20)
            r.encoding = "gbk"
            txt = r.text.strip()
            if not txt or txt == "null" or txt == "[]":
                break
            data = json.loads(txt)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"  [warn] {node} p{page}: {str(exc)[:70]}\n")
            break
        if not data:
            break
        for it in data:
            sym = it.get("symbol", "")  # e.g. sh600519
            nm = it.get("name", "")
            if len(sym) >= 8:
                out.append((sym[2:], nm))
        if len(data) < 100:
            break
        time.sleep(0.2)
    return out


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stock_meta (
            symbol TEXT PRIMARY KEY, name TEXT, industry TEXT, area TEXT, market TEXT)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_industry ON stock_meta(industry)")

    sess = requests.Session()
    print("拉取新浪行业列表...")
    sectors = get_sectors(sess)
    print(f"共 {len(sectors)} 个行业\n")

    total = 0
    for i, (code, name) in enumerate(sectors.items(), 1):
        members = get_members(sess, code)
        if not members:
            continue
        conn.executemany(
            "INSERT INTO stock_meta (symbol,name,industry) VALUES (?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET "
            "name=COALESCE(NULLIF(excluded.name,''), stock_meta.name), "
            "industry=excluded.industry",
            [(s, n, name) for s, n in members],
        )
        conn.commit()
        total += len(members)
        print(f"  [{i}/{len(sectors)}] {name:<12} {len(members):>4} 只", flush=True)
        time.sleep(0.15)

    n = conn.execute("SELECT COUNT(*) FROM stock_meta").fetchone()[0]
    ni = conn.execute("SELECT COUNT(DISTINCT industry) FROM stock_meta WHERE industry!=''").fetchone()[0]
    both = conn.execute(
        "SELECT COUNT(*) FROM stock_meta m WHERE EXISTS "
        "(SELECT 1 FROM stock_daily d WHERE d.symbol=m.symbol)"
    ).fetchone()[0]
    print(f"\n完成: stock_meta {n} 只 / {ni} 个行业")
    print(f"与日线库交集: {both} 只（可做形态分析的股票）")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
