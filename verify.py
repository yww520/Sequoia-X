#!/usr/bin/env python
"""形态选股 → 投研系统估值+排雷 闭环验证。

把 Sequoia-X 的形态选股结果，送给 stock-valuation-lite 做基本面体检：
  - 排雷：48 条唐门规则、三大前提、现金流画像、一票否决
  - 估值：EPV_T0 / TC估值 / EPV当期 的投资收益率（隐含回报率）

核心用法（闭环）：
    .venv/bin/python verify.py --from-strategy turtle
    .venv/bin/python verify.py --from-strategy turtle,rps --limit 30
    .venv/bin/python verify.py 002815 002913 600183     # 指定代码
    .venv/bin/python verify.py --l3 印制电路板

数据来源（无需鉴权，直读本地文件 + 本机 API）：
  - 估值/公司资料：~/stock-valuation-lite/backend/data/<行业>-<代码>-<名>-<日>.json
  - 排雷报告：http://127.0.0.1:8888/api/caibao/audit?code=<代码>
  - 形态指标：本地 SQLite stock_daily
"""

import argparse
import glob
import json
import os
import random
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "sequoia_v2.db"
SV_DIR = Path.home() / "stock-valuation-lite" / "backend" / "data"
SV_API = "http://127.0.0.1:8888"


# ─────────────── 投研系统数据 ───────────────

def build_sv_index() -> dict[str, Path]:
    """扫描投研系统数据目录，返回 {6位代码: json路径}。
    文件名形如 `电子--元件--被动元件-300408.SZ-三环集团-20260328.json`。
    """
    idx: dict[str, Path] = {}
    if not SV_DIR.exists():
        return idx
    for p in SV_DIR.glob("*.json"):
        parts = p.stem.rsplit("-", 3)
        if len(parts) != 4:
            continue
        code_full = parts[1]        # 300408.SZ
        code = code_full.split(".")[0]
        if not code.isdigit():
            continue
        # 同代码多文件时取日期最新的
        prev = idx.get(code)
        if prev is None or parts[3] > prev.stem.rsplit("-", 3)[3]:
            idx[code] = p
    return idx


def load_sv_meta(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:  # noqa: BLE001
        return {}
    m = dict(d.get("meta", {}))
    m["_companyBrief"] = d.get("companyBrief", "") or ""
    pb = d.get("pbBand") or {}
    if pb:
        m["_pb_last"] = pb.get("lastPb")
        m["_pb_quantiles"] = pb.get("quantiles")
        m["_pb_first"] = pb.get("firstDate")
        m["_pb_lastdate"] = pb.get("lastDate")
    return m


def fetch_audit(code_full: str) -> dict:
    """调投研系统排雷接口。code_full 形如 300408.SZ。"""
    url = f"{SV_API}/api/caibao/audit?code={urllib.parse.quote(code_full)}"
    try:
        with urllib.request.urlopen(url, timeout=45) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        return d.get("report", {}) or {}
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[warn] 排雷接口失败 {code_full}: {str(exc)[:70]}\n")
        return {}


def fnum(v, default=None):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError, AttributeError):
        return default


# ─────────────── 形态指标 ───────────────

def form_metrics(conn, code: str) -> dict:
    df = pd.read_sql(
        "SELECT date,open,high,low,close,volume,turnover FROM stock_daily "
        "WHERE symbol=? ORDER BY date",
        conn, params=(code,),
    )
    if len(df) < 25:
        return {}
    last, prev = df.iloc[-1], df.iloc[-2]
    high_20 = df["high"].shift(1).rolling(20).max().iloc[-1]
    ma5 = df["close"].rolling(5).mean().iloc[-1]
    ma20 = df["close"].rolling(20).mean().iloc[-1]
    ma60 = df["close"].rolling(60).mean().iloc[-1] if len(df) >= 60 else float("nan")

    def chg(n):
        if len(df) <= n:
            return float("nan")
        return (last["close"] - df["close"].iloc[-1 - n]) / df["close"].iloc[-1 - n] * 100

    return {
        "date": last["date"], "close": last["close"],
        "turnover_yi": last["turnover"] / 1e8,
        "breakout": bool(last["close"] > high_20) if pd.notna(high_20) else False,
        "ma_bull": ma5 > ma20, "above_ma60": (last["close"] > ma60) if pd.notna(ma60) else None,
        "r20": chg(20), "r60": chg(60), "r120": chg(120),
    }


def verdict_score(report: dict) -> tuple[int, str]:
    """把排雷结论压成排序分（越高越好）。"""
    v = (report.get("overall_verdict") or "").strip()
    rs = report.get("rule_summary") or {}
    high = rs.get("triggered_high", 0)
    med = rs.get("triggered_medium", 0)
    pre = report.get("prerequisites") or {}
    # 一票否决
    if "一票否决" in v or "高危" in v:
        return (0, v)
    if "存疑" in v or "谨慎" in v:
        return (1, v)
    # 前提不通过也降级
    if pre.get("profit_real") == "不通过":
        return (0, v or "利润真实性不通过")
    score = 3
    if high:
        score = 1
    elif med:
        score = 2
    return (score, v or "通过")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*", help="股票代码（6位）")
    ap.add_argument("--from-strategy", help="从策略结果取标的，如 turtle / turtle,rps")
    ap.add_argument("--l3", help="申万三级行业")
    ap.add_argument("--l1", help="申万一级行业")
    ap.add_argument("--limit", type=int, default=15, help="最多验证多少只")
    ap.add_argument("--no-audit", action="store_true", help="跳过排雷接口（只用本地估值）")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)

    # ── 收集待验证代码 ──
    codes: list[str] = []
    label = ""

    if args.codes:
        codes = [c.strip() for c in args.codes]
        label = "指定标的"
    elif args.from_strategy:
        sys.path.insert(0, str(ROOT))
        import logging

        logging.disable(logging.CRITICAL)
        from sequoia_x.core.config import Settings
        from sequoia_x.data.engine import DataEngine

        REG = {
            "turtle": ("sequoia_x.strategy.turtle_trade", "TurtleTradeStrategy"),
            "rps": ("sequoia_x.strategy.rps_breakout", "RpsBreakoutStrategy"),
            "ma_volume": ("sequoia_x.strategy.ma_volume", "MaVolumeStrategy"),
            "flag": ("sequoia_x.strategy.high_tight_flag", "HighTightFlagStrategy"),
            "shakeout": ("sequoia_x.strategy.limit_up_shakeout", "LimitUpShakeoutStrategy"),
            "limit_down": ("sequoia_x.strategy.uptrend_limit_down", "UptrendLimitDownStrategy"),
        }
        s = Settings(feishu_webhook_url="https://x")
        e = DataEngine(s)
        names = [x.strip() for x in args.from_strategy.split(",") if x.strip()]
        for n in names:
            if n not in REG:
                print(f"未知策略 {n}，可选 {list(REG)}")
                return 2
            mod, cls = REG[n]
            import importlib

            strategy = getattr(importlib.import_module(mod), cls)(engine=e, settings=s)
            hits = strategy.run()
            print(f"  {n}: {len(hits)} 只")
            codes.extend(hits)
        # 去重保序
        seen = set()
        codes = [c for c in codes if not (c in seen or seen.add(c))]
        label = f"策略({'/'.join(names)})"
    elif args.l3 or args.l1:
        if not SV_DIR.exists():
            print("!! 找不到投研系统数据目录")
            return 1
        key, val = ("l3", args.l3) if args.l3 else ("l1", args.l1)
        for p in SV_DIR.glob("*.json"):
            parts = p.stem.rsplit("-", 3)
            if len(parts) != 4:
                continue
            l1, l2, l3 = parts[0].split("--") + ["", ""][: max(0, 3 - len(parts[0].split("--")))]
            if (key == "l3" and l3 == val) or (key == "l1" and l1 == val):
                codes.append(parts[1].split(".")[0])
        label = f"申万{key}={val}"
    else:
        ap.print_help()
        return 0

    if not codes:
        print("没有待验证标的")
        return 0

    print(f"\n待验证: {len(codes)} 只（{label}），最多体检 {args.limit} 只\n")

    sv_index = build_sv_index()
    if not sv_index:
        print(f"!! 投研系统数据目录为空: {SV_DIR}")
        return 1

    rows = []
    for i, code in enumerate(codes[: args.limit], 1):
        fm = form_metrics(conn, code)
        svp = sv_index.get(code)
        if not svp:
            continue
        meta = load_sv_meta(svp)
        report = {} if args.no_audit else fetch_audit(f"{code}.{_suffix(code)}")
        score, verdict = verdict_score(report)
        rs = report.get("rule_summary") or {}
        pre = report.get("prerequisites") or {}
        rows.append({
            "code": code,
            "name": meta.get("公司简称", "?"),
            "ind": meta.get("申万行业", "?"),
            "score": score,
            "verdict": verdict,
            "high": rs.get("triggered_high", 0),
            "med": rs.get("triggered_medium", 0),
            "pre_real": pre.get("profit_real", "-"),
            "pre_sust": pre.get("profit_sustainable", "-"),
            "cash": (report.get("cash_portrait") or {}).get("type", "-"),
            "epv_ret": fnum(meta.get("EPV_T0_投资收益率")),
            "tc_ret": fnum(meta.get("TC估值_投资收益率")),
            "cur_ret": fnum(meta.get("EPV_当期_投资收益率")),
            "roe": fnum(meta.get("ROE_PB")),
            "pe": fnum(meta.get("PE_TTM")),
            "pb": fnum(meta.get("PB_LF")),
            "mcap": fnum(meta.get("市值")),
            "fm": fm,
        })
        if i % 5 == 0:
            print(f"  ...已体检 {i} 只", flush=True)

    if not rows:
        print("无结果（可能投研系统里没有这些标的）")
        return 0

    # ── 排序：先按排雷分，再按估值隐含回报率 ──
    rows.sort(key=lambda r: (-r["score"], -(r["epv_ret"] if r["epv_ret"] is not None else -9)))

    print(f"\n{'='*100}")
    print("形态选股 → 基本面体检结果（排雷分优先，其次 EPV隐含回报）")
    print(f"{'='*100}\n")
    hdr = (f"{'代码':<7}{'名称':<9}{'排雷':<5}{'高危':<5}{'中危':<5}"
           f"{'利润真':<7}{'可持续':<7}{'EPV回报':<9}{'TC回报':<9}{'PE':<8}{'PB':<7}{'市值亿':<9}")
    print(hdr)
    print("-" * 100)

    def pct(v):
        return f"{v * 100:>6.1f}%" if v is not None else "     -"

    for r in rows:
        lvl = {3: "✅", 2: "⚠️", 1: "🟡", 0: "🔴"}[r["score"]]
        pe_s = f"{r['pe']:.1f}" if r["pe"] else "-"
        pb_s = f"{r['pb']:.2f}" if r["pb"] else "-"
        mc_s = f"{r['mcap']:.0f}" if r["mcap"] else "-"
        print(
            f"{r['code']:<7}{r['name'][:8]:<9}{lvl:<5}{r['high']:<5}{r['med']:<5}"
            f"{r['pre_real'][:6]:<7}{r['pre_sust'][:6]:<7}"
            f"{pct(r['epv_ret']):<9}{pct(r['tc_ret']):<9}{pe_s:<8}{pb_s:<7}{mc_s:<9}"
        )

    # ── 分层结论 ──
    good = [r for r in rows if r["score"] == 3]
    warn = [r for r in rows if r["score"] == 2]
    bad = [r for r in rows if r["score"] <= 1]

    print(f"\n{'='*100}")
    print(f"✅ 排雷通过 {len(good)} 只 | ⚠️ 存疑 {len(warn)} 只 | 🔴 高危/否决 {len(bad)} 只")
    print(f"{'='*100}")

    if good:
        print("\n【✅ 形态+基本面双通过 —— 值得深入研究】")
        for r in good[:12]:
            ek = f"EPV隐含回报 {r['epv_ret']*100:.1f}%" if r["epv_ret"] is not None else ""
            print(f"  {r['code']} {r['name']}  {r['ind']}")
            print(f"      形态: 20日{r['fm'].get('r20',0):+.1f}%  60日{r['fm'].get('r60',0):+.1f}%"
                  f"  成交额{r['fm'].get('turnover_yi',0):.1f}亿"
                  f"{'  突破20日高' if r['fm'].get('breakout') else ''}")
            print(f"      基本面: ROE {r['roe']:.1f}%  PE {r['pe']:.1f}  PB {r['pb']:.2f}"
                  f"  市值{r['mcap']:.0f}亿  {ek}")
            print(f"      现金流: {r['cash']}")

    if bad:
        print("\n【🔴 形态好看但有雷 —— 建议排除】")
        for r in bad[:10]:
            print(f"  {r['code']} {r['name']:<8} {r['verdict'][:52]}")
            if r["high"]:
                print(f"      高危规则 {r['high']} 条")

    # 落盘
    out = ROOT / "data" / "verify_last.json"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n明细已存: {out}")
    return 0


def _suffix(code: str) -> str:
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("4", "8", "92")):
        return "BJ"
    return "SZ"


if __name__ == "__main__":
    raise SystemExit(main())
