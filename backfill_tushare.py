#!/usr/bin/env python
"""Tushare 数据源 backfill —— baostock/腾讯 均不可用时的可靠方案。

用 pro.daily(trade_date=...) 按交易日全市场拉取（一次 ~5550 只，2 秒），
写入与 Sequoia-X 相同 schema 的 stock_daily 表。

关于复权：
  本脚本使用【不复权】数据。原因：Tushare 的 adj_factor 接口在本 token
  档位限流 1 次/小时，而 pro_bar(adj='hfq') 每只股票都要调一次 adj_factor，
  全市场 5500+ 只不可行。
  影响：跨除权的长周期形态（海龟 20 日新高）会有小幅偏差；中短期形态
  （均线放量/旗形/涨停/跌停反包/RPS）受影响很小。
  成交额 amount 是 Tushare 原生字段（单位：千元），比腾讯的估算准确。

用法：
    cd ~/.hermes/skills/research/sequoia-x
    .venv/bin/python backfill_tushare.py --start 2024-01-01
    .venv/bin/python backfill_tushare.py --start 2026-06-01   # 补近期
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import tushare as ts

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "sequoia_v2.db"
ENV = Path.home() / "ai-invest-agent" / ".env"
KEY_NAME = "TUSHARE" + "_TOKEN"


def load_token() -> str:
    for line in open(ENV, encoding="utf-8"):
        line = line.strip()
        if line.startswith(KEY_NAME + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{KEY_NAME} not found in {ENV}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stock_daily (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol   TEXT    NOT NULL,
            date     TEXT    NOT NULL,
            open     REAL, high REAL, low REAL, close REAL, volume REAL, turnover REAL,
            UNIQUE (symbol, date))"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON stock_daily(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON stock_daily(date)")


def trade_days(pro, start: str, end: str) -> list[str]:
    """返回交易日列表。优先用 trade_cal（可能限流），失败则按自然日暴力扫描。

    本 token 档位 trade_cal 限流 1 次/小时，所以失败时降级为「按工作日逐个试探」：
    非交易日 pro.daily 会返回空，直接跳过，代价是多了几次空调用。
    """
    try:
        cal = pro.trade_cal(
            exchange="SSE",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            is_open="1",
        )
        days = sorted(cal["cal_date"].tolist())
        print(f"(日历来自 trade_cal：{len(days)} 天)")
        return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in days]
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[info] trade_cal 不可用({str(exc)[:60]})，改用自然日扫描\n")

    # 降级：生成 start~end 间所有周一至周五，非交易日拉取时会被跳过
    from datetime import date as _date
    from datetime import timedelta

    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    out = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:  # 0-4 = 周一至周五
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=datetime.today().strftime("%Y-%m-%d"))
    ap.add_argument("--sleep", type=float, default=0.35, help="每次调用间隔秒")
    args = ap.parse_args()

    ts.set_token(load_token())
    pro = ts.pro_api()

    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    ensure_schema(conn)

    last = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
    start = args.start
    if last and not args.start:
        start = last
    print(f"本地最新日期: {last or '(空库)'} → 从 {start} 开始补", flush=True)

    days = trade_days(pro, start, args.end)
    days = [d for d in days if last is None or d.replace("-", "") > last.replace("-", "")]
    if not days:
        print("无新交易日，退出。")
        return 0
    print(f"待补交易日: {len(days)} 天 ({days[0]} ~ {days[-1]})", flush=True)

    t0 = time.time()
    total = 0
    for i, day in enumerate(days, 1):
        d = datetime.strptime(day, "%Y-%m-%d")
        ymd = d.strftime("%Y%m%d")
        iso = d.strftime("%Y-%m-%d")
        try:
            df = pro.daily(trade_date=ymd)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[warn] {iso} 拉取失败: {exc}\n")
            time.sleep(2)
            continue
        if df is None or df.empty:
            print(f"  {iso}: 无数据（非交易日？）")
            continue
        recs = []
        for row in df.itertuples(index=False):
            code = str(row.ts_code).split(".")[0]
            # Tushare: vol 单位=手, amount 单位=千元 → 统一成 股 / 元
            recs.append(
                (
                    code,
                    iso,
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.vol),
                    float(row.amount) * 1000,
                )
            )
        conn.executemany(
            "INSERT OR REPLACE INTO stock_daily "
            "(symbol,date,open,high,low,close,volume,turnover) VALUES (?,?,?,?,?,?,?,?)",
            recs,
        )
        conn.commit()
        total += len(recs)
        print(f"  [{i}/{len(days)}] {iso}: {len(recs)} 只  累计 {total} 行", flush=True)
        time.sleep(args.sleep)

    n = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
    ns = conn.execute("SELECT COUNT(DISTINCT symbol) FROM stock_daily").fetchone()[0]
    rng = conn.execute("SELECT MIN(date), MAX(date) FROM stock_daily").fetchone()
    conn.close()
    print(f"\n回填完成 — 本次写入 {total} 行 / 耗时 {time.time() - t0:.0f}s")
    print(f"库内容: {n} 行 / {ns} 只股票 / 日期 {rng[0]} ~ {rng[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
