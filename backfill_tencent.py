#!/usr/bin/env python
"""腾讯数据源 backfill —— baostock 挂掉时的替代方案。

拉全 A 股后复权(hfq)日 K，写入与 Sequoia-X 相同 schema 的 stock_daily 表。
数据源：腾讯财经公开接口（无需注册，无 baostock 那样的服务端限流）。

用法：
    cd ~/.hermes/skills/research/sequoia-x
    .venv/bin/python backfill_tencent.py                 # 增量（默认）
    .venv/bin/python backfill_tencent.py --full          # 全量重拉
    .venv/bin/python backfill_tencent.py --start 2024-01-01
"""

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "sequoia_v2.db"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def get_all_symbols() -> list[tuple[str, str]]:
    """腾讯行情榜拉全市场 A 股列表 → [(code6, name), ...]"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    offset = 0
    while True:
        url = (
            "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
            f"?board_code=aStock&sort_type=price&direct=down&offset={offset}&count=200"
        )
        try:
            r = SESSION.get(url, timeout=20)
            data = r.json().get("data", {})
            lst = data.get("rank_list") or []
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[warn] 列表分页 offset={offset} 失败: {exc}\n")
            break
        if not lst:
            break
        for item in lst:
            full = item.get("code", "")  # e.g. sh600519
            name = item.get("name", "")
            if len(full) < 8:
                continue
            code = full[2:]
            if code.startswith(("6", "0", "3", "4", "8", "9")) and code not in seen:
                seen.add(code)
                out.append((code, name))
        offset += len(lst)
        if len(lst) < 200:
            break
        time.sleep(0.15)
    return out


def to_tencent(code: str) -> str:
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def fetch_kline(code: str, start: str, end: str, retries: int = 3) -> list[list]:
    """拉单只股票后复权日 K。返回腾讯原始行 [date, open, close, high, low, volume]。"""
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={to_tencent(code)},day,{start},{end},640,hfq"
    )
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=25)
            payload = r.json()
            node = payload.get("data", {}).get(to_tencent(code), {})
            rows = node.get("hfqday") or node.get("day") or []
            return rows
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                sys.stderr.write(f"[warn] {code} 拉取失败: {exc}\n")
                return []
            time.sleep(1.5 * (attempt + 1))
    return []


def write_rows(conn: sqlite3.Connection, code: str, rows: list[list]) -> int:
    recs = []
    for r in rows:
        if len(r) < 6:
            continue
        d, o, c, h, l, v = r[0], r[1], r[2], r[3], r[4], r[5]
        try:
            o, h, l, c, v = float(o), float(h), float(l), float(c), float(v)
        except (TypeError, ValueError):
            continue
        # 腾讯 K 线不提供成交额，用 (O+H+L+C)/4 作 VWAP 近似 * 成交量（手→股 *100）
        vwap = (o + h + l + c) / 4
        turnover = vwap * v * 100
        recs.append((code, d, o, h, l, c, v, turnover))
    if not recs:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO stock_daily "
        "(symbol,date,open,high,low,close,volume,turnover) VALUES (?,?,?,?,?,?,?,?)",
        recs,
    )
    return len(recs)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stock_daily (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol   TEXT    NOT NULL,
            date     TEXT    NOT NULL,
            open     REAL, high REAL, low REAL, close REAL, volume REAL, turnover REAL,
            UNIQUE (symbol, date))"""
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--full", action="store_true", help="忽略已有数据，全量重拉")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="只拉前 N 只（调试用）")
    args = ap.parse_args()

    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    ensure_schema(conn)

    print("拉取全市场股票列表...", flush=True)
    syms = get_all_symbols()
    if args.limit:
        syms = syms[: args.limit]
    print(f"股票数: {len(syms)}", flush=True)
    if not syms:
        print("!! 列表为 0，数据源可能不可用")
        return 1

    # 已拉到的最后日期（增量）
    last: dict[str, str] = {}
    if not args.full:
        for sym, d in conn.execute("SELECT symbol, MAX(date) FROM stock_daily GROUP BY symbol"):
            last[sym] = d

    t0 = time.time()
    ok = fail = 0
    total_rows = 0

    def work(item: tuple[str, str]) -> tuple[str, list[list], bool]:
        """只负责网络拉取（线程安全）；写库回主线程做。"""
        code, _ = item
        start = args.start
        if code in last:
            try:
                nxt = datetime.strptime(last[code], "%Y-%m-%d").date()
                start = date.fromordinal(nxt.toordinal() + 1).isoformat()
            except ValueError:
                pass
        if start > args.end:
            return code, [], True
        rows = fetch_kline(code, start, args.end)
        if not rows:
            # 已经是最新（增量无新数据）不算失败
            return code, [], code in last
        return code, rows, True

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(work, it): it[0] for it in syms}
        done = 0
        for fut in as_completed(futures):
            code = futures[fut]
            done += 1
            try:
                _code, rows, success = fut.result()
                if success:
                    ok += 1
                else:
                    fail += 1
                if rows:
                    total_rows += write_rows(conn, code, rows)
            except Exception as exc:  # noqa: BLE001
                fail += 1
                sys.stderr.write(f"[warn] {code}: {exc}\n")
            if done % 200 == 0:
                conn.commit()
                elapsed = time.time() - t0
                print(
                    f"  进度 {done}/{len(syms)}  写入 {total_rows} 行  "
                    f"耗时 {elapsed:.0f}s",
                    flush=True,
                )
    conn.commit()

    n_total = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
    n_syms = conn.execute("SELECT COUNT(DISTINCT symbol) FROM stock_daily").fetchone()[0]
    dmin, dmax = conn.execute("SELECT MIN(date), MAX(date) FROM stock_daily").fetchone()
    conn.close()

    print(
        f"\n回填完成 — 成功 {ok} | 失败 {fail} | 写入 {total_rows} 行 | "
        f"耗时 {time.time() - t0:.0f}s"
    )
    print(f"库内容: {n_total} 行 / {n_syms} 只股票 / 日期 {dmin} ~ {dmax}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
