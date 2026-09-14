"""量化不复权对海龟策略的影响：检查命中股票在近 60 日内是否发生过除权跳空。"""
import sqlite3

import pandas as pd

conn = sqlite3.connect("data/sequoia_v2.db")

# 海龟命中列表（从 analyze.py 的输出解析或直接重跑）
import sys

sys.path.insert(0, ".")
import logging

logging.disable(logging.CRITICAL)
from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy

s = Settings(feishu_webhook_url="https://x")
e = DataEngine(s)
hits = TurtleTradeStrategy(engine=e, settings=s).run()
print(f"海龟命中: {len(hits)} 只\n")

# 统计命中股票近期是否有除权跳空（>21% 单日跌幅，排除真实跌停）
suspect = []
for code in hits:
    d = pd.read_sql(
        "SELECT date, close FROM stock_daily WHERE symbol=? ORDER BY date DESC LIMIT 70",
        conn,
        params=(code,),
    )
    d = d.sort_values("date")
    d["ret"] = d["close"].pct_change()
    # 只看最近 60 个交易日
    tail = d.tail(60)
    if (tail["ret"] < -0.21).any():
        suspect.append(code)

print(f"近期(60日)有疑似除权跳空的命中股票: {len(suspect)} / {len(hits)}")
if suspect:
    print("  代码:", " ".join(suspect))
    print("  → 这些票的 20 日新高判断可能因未复权而失真，需人工复核")
else:
    print("  → 全部命中未受除权影响，海龟结果可信")
