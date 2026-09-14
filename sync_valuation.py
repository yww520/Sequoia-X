#!/usr/bin/env python
"""从 stock-valuation-lite（云端 A 股价值投研系统）同步元数据到 Sequoia-X。

收益：
  - 申万三级行业分类（L1/L2/L3），覆盖 5400+ 只 —— 远优于新浪版（2978只/48行业）
  - 市值（mcap）
  - 实时行情（PE/PB/换手率/成交额）可另取

数据源：本机 http://127.0.0.1:8888/api/stocks（该服务已在运行）

用法：
    cd ~/.hermes/skills/research/sequoia-x
    .venv/bin/python sync_valuation.py
"""

import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "sequoia_v2.db"
API = "http://127.0.0.1:8888/api/stocks"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stock_meta (
            symbol    TEXT PRIMARY KEY,
            name      TEXT,
            industry  TEXT,
            area      TEXT,
            market    TEXT
        )"""
    )
    # 扩展字段：申万三级 + 市值
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_meta)")}
    for col, decl in [
        ("sw_l1", "TEXT"),
        ("sw_l2", "TEXT"),
        ("sw_l3", "TEXT"),
        ("mcap_yi", "REAL"),
        ("meta_src", "TEXT"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE stock_meta ADD COLUMN {col} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_l1 ON stock_meta(sw_l1)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_industry ON stock_meta(industry)")


def main() -> int:
    print(f"拉取 {API} ...")
    try:
        with urllib.request.urlopen(API, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"!! 无法连接投研系统: {exc}")
        print("   确认服务在跑: ps aux | grep stock-valuation-lite")
        return 1

    stocks = data.get("stocks", [])
    if not stocks:
        print("!! 返回为空")
        return 1
    print(f"拿到 {len(stocks)} 只股票\n")

    conn = sqlite3.connect(DB)
    ensure_schema(conn)

    recs = []
    for s in stocks:
        raw = (s.get("code") or "").strip()  # e.g. 300408.SZ
        if "." in raw:
            sym = raw.split(".")[0]
        else:
            sym = raw
        if not sym or not sym.isdigit():
            continue
        l1, l2, l3 = s.get("l1", ""), s.get("l2", ""), s.get("l3", "")
        recs.append(
            (
                sym,
                s.get("name", ""),
                l3 or l2 or l1,   # industry 字段存最细一级，兼容旧查询
                "",               # area
                "",               # market
                l1, l2, l3,
                float(s.get("mcap") or 0),
                "stock-valuation-lite",
            )
        )

    conn.executemany(
        """INSERT INTO stock_meta
           (symbol,name,industry,area,market,sw_l1,sw_l2,sw_l3,mcap_yi,meta_src)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(symbol) DO UPDATE SET
             name      = COALESCE(NULLIF(excluded.name,''), stock_meta.name),
             industry  = excluded.industry,
             sw_l1     = excluded.sw_l1,
             sw_l2     = excluded.sw_l2,
             sw_l3     = excluded.sw_l3,
             mcap_yi   = excluded.mcap_yi,
             meta_src  = excluded.meta_src""",
        recs,
    )
    conn.commit()

    n = conn.execute("SELECT COUNT(*) FROM stock_meta").fetchone()[0]
    n_sv = conn.execute(
        "SELECT COUNT(*) FROM stock_meta WHERE meta_src='stock-valuation-lite'"
    ).fetchone()[0]
    n_l1 = conn.execute("SELECT COUNT(DISTINCT sw_l1) FROM stock_meta WHERE sw_l1!=''").fetchone()[0]
    both = conn.execute(
        "SELECT COUNT(*) FROM stock_meta m WHERE EXISTS "
        "(SELECT 1 FROM stock_daily d WHERE d.symbol=m.symbol)"
    ).fetchone()[0]

    print(f"stock_meta 总计: {n} 只（其中来自投研系统 {n_sv} 只）")
    print(f"申万一级行业: {n_l1} 个")
    print(f"与日线库(stock_daily)交集: {both} 只 ← 可做形态分析的股票")

    print("\n申万一级行业分布 Top 15:")
    for k, c in conn.execute(
        "SELECT sw_l1, COUNT(*) FROM stock_meta WHERE sw_l1!='' "
        "GROUP BY sw_l1 ORDER BY COUNT(*) DESC LIMIT 15"
    ):
        print(f"  {k:<10} {c:>5}")

    print("\n三级行业样例（电子）:")
    for r in conn.execute(
        "SELECT sw_l2, sw_l3, COUNT(*) FROM stock_meta WHERE sw_l1='电子' "
        "GROUP BY sw_l2, sw_l3 ORDER BY COUNT(*) DESC LIMIT 10"
    ):
        print(f"  {r[0]:<12} {r[1]:<16} {r[2]:>3}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
