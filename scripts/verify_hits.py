#!/usr/bin/env python
"""策略命中可信度校验 —— 抽查命中股票是否真的满足策略条件。

用途：analyze.py 跑出结果后，用它证明「这些命中是真实形态，不是数据问题造成的假阳性」。
这是本 skill 最容易被忽略的一步：策略返回 0 时，必须区分「真没信号」还是
「数据缺字段（如腾讯无成交额 → 海龟恒 0）」。

用法：
    cd ~/.hermes/skills/research/sequoia-x
    .venv/bin/python scripts/verify_hits.py turtle          # 抽查海龟命中
    .venv/bin/python scripts/verify_hits.py turtle 000921   # 指定股票

输出：
  1. 全库除权跳空比例（评估「不复权」数据的失真面）
  2. 指定股票的逐日明细 + 各条件是否成立（✅/❌）
  3. 命中股票中近期是否有除权跳空（逐只标注）
"""

import logging
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "sequoia_v2.db"

logging.disable(logging.CRITICAL)

from sequoia_x.core.config import Settings  # noqa: E402
from sequoia_x.data.engine import DataEngine  # noqa: E402

# 策略短名 → (模块路径, 类名)
STRATEGIES = {
    "turtle": ("sequoia_x.strategy.turtle_trade", "TurtleTradeStrategy"),
    "ma_volume": ("sequoia_x.strategy.ma_volume", "MaVolumeStrategy"),
    "flag": ("sequoia_x.strategy.high_tight_flag", "HighTightFlagStrategy"),
    "shakeout": ("sequoia_x.strategy.limit_up_shakeout", "LimitUpShakeoutStrategy"),
    "limit_down": ("sequoia_x.strategy.uptrend_limit_down", "UptrendLimitDownStrategy"),
    "rps": ("sequoia_x.strategy.rps_breakout", "RpsBreakoutStrategy"),
}


def load_strategy(key, engine, settings):
    mod_name, cls_name = STRATEGIES[key]
    mod = __import__(mod_name, fromlist=[cls_name])
    return getattr(mod, cls_name)(engine=engine, settings=settings)


def ex_rights_ratio(conn) -> float:
    """全库疑似除权跳空比例（单日 |涨跌| > 21%，超过 A 股涨跌停上限）。"""
    df = pd.read_sql("SELECT symbol, date, close FROM stock_daily ORDER BY symbol, date", conn)
    if df.empty:
        return 0.0
    df["ret"] = df.groupby("symbol")["close"].pct_change()
    jumps = df[df["ret"].abs() > 0.21]
    print(f"全库疑似除权跳空: {len(jumps)} / {len(df)} = {len(jumps) / len(df) * 100:.3f}%")
    if len(jumps):
        print(f"  涉及 {jumps['symbol'].nunique()} 只股票的孤立日期")
    return len(jumps) / len(df)


def check_turtle(conn, code: str) -> bool:
    """海龟三条件的逐条核验：20日新高 / 成交额>1亿 / 真阳线。"""
    d = pd.read_sql(
        "SELECT date,open,high,low,close,volume,turnover FROM stock_daily "
        "WHERE symbol=? ORDER BY date",
        conn,
        params=(code,),
    )
    if len(d) < 21:
        print(f"\n[{code}] 数据不足 21 根 K 线（{len(d)} 根），跳过")
        return False

    d["high_20_prev"] = d["high"].shift(1).rolling(20).max()
    last, prev = d.iloc[-1], d.iloc[-2]

    cond_breakout = last["close"] > last["high_20_prev"]
    cond_liquid = last["turnover"] > 100_000_000
    cond_yang = last["close"] > last["open"]
    cond_up = last["close"] > prev["close"]

    print(f"\n[{code}] {last['date']}")
    print(f"  开 {last['open']:.2f}  高 {last['high']:.2f}  低 {last['low']:.2f}  收 {last['close']:.2f}")
    print(f"  前20日最高 {last['high_20_prev']:.2f}")
    print(f"  {'✅' if cond_breakout else '❌'} 突破20日新高  (收 {last['close']:.2f} vs {last['high_20_prev']:.2f})")
    print(f"  {'✅' if cond_liquid else '❌'} 成交额>1亿    ({last['turnover'] / 1e8:.2f} 亿)")
    print(f"  {'✅' if cond_yang else '❌'} 真阳线        (收 vs 开)")
    print(f"  {'✅' if cond_up else '❌'} 真上涨        (收 vs 昨收)")

    if not cond_liquid and last["turnover"] == 0:
        print("  ⚠️ turnover 为 0 —— 数据源缺成交额字段（如腾讯），该策略会恒为 0")
    return all([cond_breakout, cond_liquid, cond_yang, cond_up])


def recent_ex_rights(conn, codes: list[str], window: int = 60) -> list[str]:
    """命中股票中近期有除权跳空的（其长周期形态判断可能失真）。"""
    suspect = []
    for code in codes:
        d = pd.read_sql(
            "SELECT date, close FROM stock_daily WHERE symbol=? ORDER BY date DESC LIMIT ?",
            conn,
            params=(code, window + 10),
        ).sort_values("date")
        d["ret"] = d["close"].pct_change()
        if (d.tail(window)["ret"] < -0.21).any():
            suspect.append(code)
    return suspect


def main() -> int:
    if not DB.exists():
        print(f"!! 数据库不存在: {DB}\n   先跑 backfill_tushare.py")
        return 1

    key = sys.argv[1] if len(sys.argv) > 1 else "turtle"
    if key not in STRATEGIES:
        print(f"未知策略 {key}，可选: {list(STRATEGIES)}")
        return 2
    target = sys.argv[2] if len(sys.argv) > 2 else None

    conn = sqlite3.connect(DB)

    # 1. 不复权失真面评估
    ex_rights_ratio(conn)
    print("  → 低于 1% 时，中短期形态策略结论可信；长周期策略需逐只复核\n")

    settings = Settings(feishu_webhook_url="https://open.feishu.cn/placeholder")
    engine = DataEngine(settings)

    # 2. 指定股票逐条核验
    if target:
        check_turtle(conn, target)
        conn.close()
        return 0

    # 3. 跑策略 + 抽查命中
    hits = load_strategy(key, engine, settings).run()
    print(f"{key} 命中 {len(hits)} 只")

    if not hits:
        print("命中为 0 —— 先确认是「真没信号」还是「数据缺字段」：")
        print("  检查 turnover 是否全为 NULL/0，以及回填是否覆盖了最后一个完整交易日。")
        conn.close()
        return 0

    # 抽查前 3 只
    for code in hits[:3]:
        check_turtle(conn, code)

    # 4. 除权影响面
    suspect = recent_ex_rights(conn, hits)
    print(f"\n近期(60日)有除权跳空的命中股票: {len(suspect)} / {len(hits)}")
    if suspect:
        print("  代码:", " ".join(suspect), "→ 这些票的20日新高判断可能失真，需人工复核")
    else:
        print("  → 全部命中未受除权影响，结果可信")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
