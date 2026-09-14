#!/usr/bin/env python
"""建立股票元数据表（名称 + 行业），供按行业/名称/代码筛选使用。

数据源：Tushare stock_basic（本 token 可用）。
写入 stock_meta 表：symbol, name, industry, area。

用法：
    .venv/bin/python build_meta.py
"""

import os
import sqlite3
from pathlib import Path

import tushare as ts

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "sequoia_v2.db"
ENV = Path.home() / "ai-invest-agent" / ".env"
KEY = "TUSHARE" + "_TOKEN"


def load_token() -> str:
    for line in open(ENV, encoding="utf-8"):
        line = line.strip()
        if line.startswith(KEY + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("token not found")


def main() -> int:
    ts.set_token(load_token())
    pro = ts.pro_api()

    conn = sqlite3.connect(DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stock_meta (
            symbol    TEXT PRIMARY KEY,
            name      TEXT,
            industry  TEXT,
            area      TEXT,
            market    TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_industry ON stock_meta(industry)")

    total = 0
    for status, label in [("L", "上市"), ("D", "退市"), ("P", "暂停")]:
        try:
            df = pro.stock_basic(
                exchange="",
                list_status=status,
                fields="ts_code,symbol,name,industry,area,market",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {label} 拉取失败: {exc}")
            continue
        if df is None or df.empty:
            continue
        recs = [
            (
                str(r.symbol),
                str(r.name),
                str(r.industry) if r.industry else "",
                str(r.area) if r.area else "",
                str(r.market) if r.market else "",
            )
            for r in df.itertuples(index=False)
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO stock_meta (symbol,name,industry,area,market) "
            "VALUES (?,?,?,?,?)",
            recs,
        )
        total += len(recs)
        print(f"  {label}: {len(recs)} 只")
    conn.commit()

    n = conn.execute("SELECT COUNT(*) FROM stock_meta").fetchone()[0]
    n_ind = conn.execute(
        "SELECT COUNT(DISTINCT industry) FROM stock_meta WHERE industry != ''"
    ).fetchone()[0]
    print(f"\nstock_meta 完成: {n} 只 / {n_ind} 个行业")

    # 与日线库的交集
    both = conn.execute(
        "SELECT COUNT(*) FROM stock_meta m "
        "WHERE EXISTS (SELECT 1 FROM stock_daily d WHERE d.symbol = m.symbol)"
    ).fetchone()[0]
    print(f"与 stock_daily 交集: {both} 只")

    print("\n行业分布 Top 20:")
    for ind, cnt in conn.execute(
        "SELECT industry, COUNT(*) c FROM stock_meta WHERE industry != '' "
        "GROUP BY industry ORDER BY c DESC LIMIT 20"
    ):
        print(f"  {ind:<12} {cnt:>4}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
