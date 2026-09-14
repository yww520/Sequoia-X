#!/usr/bin/env python
"""按单只股票 / 申万行业 / 关键词查询 Sequoia-X 形态数据。

数据来源：
  - 形态指标：本地 SQLite stock_daily（Tushare 日 K）
  - 行业分类：stock_meta（来自 stock-valuation-lite 投研系统，申万三级）
  - 实时行情：stock-valuation-lite 的 /api/realtime/quotes（PE/PB/换手率/成交额）

用法：
    .venv/bin/python query.py 600519              # 按代码
    .venv/bin/python query.py 茅台                # 按名称模糊匹配
    .venv/bin/python query.py --l1 电子            # 申万一级
    .venv/bin/python query.py --l2 半导体          # 申万二级
    .venv/bin/python query.py --l3 印制电路板      # 申万三级
    .venv/bin/python query.py --grep 光电          # 名称/行业关键词
    .venv/bin/python query.py --l1 电子 --screen   # 该行业内跑形态筛选
    .venv/bin/python query.py --list-l1            # 列出申万一级
    .venv/bin/python query.py --realtime 600519    # 只取实时行情
"""

import argparse
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "sequoia_v2.db"
SV_API = "http://127.0.0.1:8888/api/realtime/quotes"


# ───────────────────────── 元数据 ─────────────────────────

def load_meta(conn) -> dict[str, dict]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_meta)")}
    has_sw = "sw_l1" in cols
    sel = "symbol,name,industry"
    if has_sw:
        sel += ",sw_l1,sw_l2,sw_l3,mcap_yi"
    try:
        rows = conn.execute(f"SELECT {sel} FROM stock_meta").fetchall()
    except sqlite3.OperationalError:
        return {}
    out = {}
    for r in rows:
        d = {"name": r[1], "industry": r[2]}
        if has_sw:
            d.update({"l1": r[3], "l2": r[4], "l3": r[5], "mcap": r[6]})
        out[r[0]] = d
    return out


# ───────────────────────── 实时行情 ─────────────────────────

def fetch_realtime(codes: list[str]) -> dict:
    if not codes:
        return {}
    try:
        url = f"{SV_API}?codes={','.join(codes[:200])}"
        with urllib.request.urlopen(url, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("quotes", {}) or {}
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[warn] 实时行情不可用: {str(exc)[:80]}\n")
        return {}


# ───────────────────────── 形态指标 ─────────────────────────

def compute_metrics(df: pd.DataFrame) -> dict:
    if len(df) < 25:
        return {}
    df = df.copy()
    last, prev = df.iloc[-1], df.iloc[-2]
    high_20 = df["high"].shift(1).rolling(20).max().iloc[-1]
    ma5 = df["close"].rolling(5).mean().iloc[-1]
    ma10 = df["close"].rolling(10).mean().iloc[-1]
    ma20 = df["close"].rolling(20).mean().iloc[-1]
    ma60 = df["close"].rolling(60).mean().iloc[-1] if len(df) >= 60 else float("nan")
    vol_ma5_prev = df["volume"].rolling(5).mean().iloc[-2]

    def chg(n):
        if len(df) <= n:
            return float("nan")
        return (last["close"] - df["close"].iloc[-1 - n]) / df["close"].iloc[-1 - n] * 100

    return {
        "date": last["date"], "close": last["close"],
        "pct": (last["close"] - prev["close"]) / prev["close"] * 100,
        "turnover_yi": last["turnover"] / 1e8,
        "high_20": high_20,
        "breakout": bool(last["close"] > high_20) if pd.notna(high_20) else False,
        "new_high_dist": (last["close"] / high_20 - 1) * 100 if pd.notna(high_20) else float("nan"),
        "is_yang": last["close"] > last["open"],
        "vol_ratio": last["volume"] / vol_ma5_prev if vol_ma5_prev else float("nan"),
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "above_ma20": last["close"] > ma20,
        "above_ma60": (last["close"] > ma60) if pd.notna(ma60) else None,
        "ma_bull": ma5 > ma10 > ma20,
        "r5": chg(5), "r20": chg(20), "r60": chg(60), "r120": chg(120),
        "high_60": df["high"].tail(60).max(), "low_60": df["low"].tail(60).min(),
        "n_bars": len(df),
    }


def scan_universe(conn, codes: list[str], meta: dict, min_turnover_yi: float = 0.0) -> list[dict]:
    """对一组股票批量算指标，返回 [{'code','name','ind','m':metrics}, ...]"""
    out = []
    for code in codes:
        df = pd.read_sql(
            "SELECT date,open,high,low,close,volume,turnover FROM stock_daily "
            "WHERE symbol=? ORDER BY date",
            conn, params=(code,),
        )
        m = compute_metrics(df)
        if not m:
            continue
        if m["turnover_yi"] < min_turnover_yi:
            continue
        info = meta.get(code, {})
        ind = " / ".join(x for x in (info.get("l1"), info.get("l2"), info.get("l3")) if x) \
            or info.get("industry", "?")
        out.append({"code": code, "name": info.get("name", "?"), "ind": ind, "m": m})
    return out


def fmt_stock(conn, code: str, meta: dict, rt: dict | None = None) -> str:
    df = pd.read_sql(
        "SELECT date,open,high,low,close,volume,turnover FROM stock_daily "
        "WHERE symbol=? ORDER BY date",
        conn, params=(code,),
    )
    if df.empty:
        return f"{code}: 库中无数据"
    m = compute_metrics(df)
    if not m:
        return f"{code}: 数据不足（{len(df)} 根K线，需 ≥25）"
    info = meta.get(code, {})
    ind = " / ".join(x for x in (info.get("l1"), info.get("l2"), info.get("l3")) if x) \
        or info.get("industry", "?")
    yn = lambda v: "✅" if v else "❌"  # noqa: E731

    L = [
        f"【{code} {info.get('name','?')}】{ind}",
        f"  市值 {info.get('mcap', 0):.0f} 亿" if info.get("mcap") else "",
        f"  基准日 {m['date']}   收盘 {m['close']:.2f}  当日 {m['pct']:+.2f}%",
        f"  成交额 {m['turnover_yi']:.2f} 亿   {'✅>1亿' if m['turnover_yi'] > 1 else '⚠️<1亿'}"
        f"   量比(vs5日均) {m['vol_ratio']:.2f}x",
        "",
        "  ── 形态 ──",
        f"  20日新高突破 {yn(m['breakout'])}  (距20日高 {m['new_high_dist']:+.2f}%)",
        f"  阳线 {yn(m['is_yang'])}   均线多头(5>10>20) {yn(m['ma_bull'])}",
        f"  站上MA20 {yn(m['above_ma20'])}   站上MA60 "
        + (yn(m["above_ma60"]) if m["above_ma60"] is not None else "n/a"),
        f"  MA5 {m['ma5']:.2f}  MA10 {m['ma10']:.2f}  MA20 {m['ma20']:.2f}"
        + (f"  MA60 {m['ma60']:.2f}" if pd.notna(m["ma60"]) else ""),
        "",
        "  ── 涨幅 ──",
        f"  5日 {m['r5']:+.2f}%   20日 {m['r20']:+.2f}%   60日 {m['r60']:+.2f}%"
        f"   120日 {m['r120']:+.2f}%",
        f"  60日区间 {m['low_60']:.2f} ~ {m['high_60']:.2f}   （{m['n_bars']} 根K线）",
    ]
    if rt and code in rt:
        q = rt[code]
        L += [
            "",
            "  ── 实时（投研系统）──",
            f"  现价 {q.get('price')}  {q.get('change_pct'):+.2f}%   "
            f"换手 {q.get('turnover_pct')}%   成交额 {q.get('amount_wan', 0)/10000:.2f} 亿",
            f"  PE_TTM {q.get('pe_ttm')}   PB {q.get('pb')}   总市值 {q.get('mcap_yi')} 亿",
        ]
    return "\n".join(x for x in L if x != "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?", help="股票代码或名称")
    ap.add_argument("--l1", help="申万一级行业")
    ap.add_argument("--l2", help="申万二级行业")
    ap.add_argument("--l3", help="申万三级行业")
    ap.add_argument("--grep", help="在名称/行业中关键词搜索")
    ap.add_argument("--screen", action="store_true", help="对选中的股票跑形态筛选")
    ap.add_argument("--min-turnover", type=float, default=1.0, help="筛选：最低成交额(亿)，默认1")
    ap.add_argument("--realtime", help="只取实时行情")
    ap.add_argument("--list-l1", action="store_true", help="列出申万一级行业")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)

    if args.realtime:
        rt = fetch_realtime([args.realtime])
        print(json.dumps(rt.get(args.realtime, {}), ensure_ascii=False, indent=2))
        return 0

    meta = load_meta(conn)
    if not meta:
        print("!! stock_meta 为空，先跑 sync_valuation.py")
        return 1

    if args.list_l1:
        print("申万一级行业（按股票数）:")
        for ind, c in conn.execute(
            "SELECT sw_l1, COUNT(*) FROM stock_meta WHERE sw_l1!='' "
            "GROUP BY sw_l1 ORDER BY COUNT(*) DESC"
        ):
            print(f"  {ind:<12} {c:>5}")
        return 0

    # ── 模式 1：单只股票 ──
    if args.code:
        q = args.code.strip()
        if q.isdigit() and len(q) == 6:
            codes = [q]
        else:
            codes = [s for s, i in meta.items() if q in (i.get("name") or "")]
            if not codes:
                print(f"未找到匹配「{q}」的股票")
                return 1
            if len(codes) > 20:
                print(f"「{q}」匹配 {len(codes)} 只，请更精确：")
                for c in codes[:20]:
                    print(f"  {c} {meta[c].get('name')}")
                return 0
        rt = fetch_realtime(codes)
        for c in codes:
            print(fmt_stock(conn, c, meta, rt))
            print()
        return 0

    # ── 模式 2/3：行业 / 关键词 ──
    conds, labels = [], []
    if args.l1:
        conds.append(("l1", args.l1)); labels.append(f"申万一级={args.l1}")
    if args.l2:
        conds.append(("l2", args.l2)); labels.append(f"申万二级={args.l2}")
    if args.l3:
        conds.append(("l3", args.l3)); labels.append(f"申万三级={args.l3}")

    if conds:
        picks = [
            (s, i) for s, i in meta.items()
            if all(i.get(k) == v for k, v in conds)
        ]
    elif args.grep:
        g = args.grep
        picks = [
            (s, i) for s, i in meta.items()
            if g in (i.get("name") or "") or g in (i.get("industry") or "")
            or g in (i.get("l1") or "") or g in (i.get("l2") or "") or g in (i.get("l3") or "")
        ]
        labels.append(f"关键词={g}")
    else:
        ap.print_help()
        return 0

    label = " ".join(labels)
    if not picks:
        print(f"{label} 无匹配")
        return 0

    if args.screen:
        codes = [s for s, _ in picks]
        print(f"{label} —— 共 {len(codes)} 只，跑形态筛选（成交额 ≥{args.min_turnover}亿）...\n")
        rows = scan_universe(conn, codes, meta, args.min_turnover)
        rows.sort(key=lambda x: -x["m"]["r20"])
        print(f"符合条件 {len(rows)} 只（按20日涨幅排序）\n")
        for r in rows[:40]:
            m = r["m"]
            flags = []
            if m["breakout"]:
                flags.append("突破20日高")
            if m["ma_bull"]:
                flags.append("均线多头")
            if m["turnover_yi"] > 3:
                flags.append(f"额{m['turnover_yi']:.1f}亿")
            print(
                f"  {r['code']}  {r['name']:<8} 收{m['close']:>8.2f}  "
                f"20日{m['r20']:>+7.2f}%  60日{m['r60']:>+7.2f}%  "
                f"{'  '.join(flags)}"
            )
        print(f"\n  行业: {label}")
        return 0

    print(f"{label} 命中 {len(picks)} 只\n")
    for s, i in picks:
        lv = " / ".join(x for x in (i.get("l1"), i.get("l2"), i.get("l3")) if x)
        print(f"  {s}  {(i.get('name') or '?'):<10} {lv}")
    print("\n提示: 加 --screen 可对该集合跑形态筛选")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
