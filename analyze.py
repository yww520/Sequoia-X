#!/usr/bin/env python
"""Sequoia-X 纯分析模式：跑全部策略，结果直接输出到 stdout（不推送飞书）。

用法：
    cd ~/.hermes/skills/research/sequoia-x
    .venv/bin/python analyze.py            # 跑全部策略
    .venv/bin/python analyze.py turtle rps # 只跑指定策略
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from sequoia_x.core.logger import get_logger  # noqa: E402
from sequoia_x.core.config import Settings  # noqa: E402
from sequoia_x.data.engine import DataEngine  # noqa: E402

from sequoia_x.strategy.ma_volume import MaVolumeStrategy  # noqa: E402
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy  # noqa: E402
from sequoia_x.strategy.high_tight_flag import HighTightFlagStrategy  # noqa: E402
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy  # noqa: E402
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy  # noqa: E402
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy  # noqa: E402
from sequoia_x.strategy.private_placement import PrivatePlacementStrategy  # noqa: E402

# 策略注册表：短名 -> (策略类, 中文名)
REGISTRY = {
    "turtle": (TurtleTradeStrategy, "海龟突破"),
    "ma_volume": (MaVolumeStrategy, "均线放量"),
    "flag": (HighTightFlagStrategy, "高窄旗形"),
    "shakeout": (LimitUpShakeoutStrategy, "涨停洗盘"),
    "limit_down": (UptrendLimitDownStrategy, "上升跌停反包"),
    "rps": (RpsBreakoutStrategy, "RPS强度突破"),
    "private_placement": (PrivatePlacementStrategy, "定增监控"),
}

# 静音日志（策略内部会打 INFO，分析模式只想要结果）
import logging  # noqa: E402

logging.disable(logging.CRITICAL)


def get_names(symbols: list[str]) -> dict[str, str]:
    """批量查股票名（baostock）。失败时静默降级为纯代码。"""
    if not symbols:
        return {}
    try:
        import baostock as bs

        bs.login()
        mapping = {}
        for code in symbols:
            prefix = "sh" if code.startswith(("6", "9")) else "sz"
            rs = bs.query_stock_basic(code=f"{prefix}.{code}")
            while rs.next():
                row = rs.get_row_data()
                mapping[code] = row[1]
        bs.logout()
        return mapping
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 查股票名失败: {exc}", file=sys.stderr)
        return {}


def to_xueqiu(code: str) -> str:
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SZ{code}"


def main() -> int:
    wanted = sys.argv[1:] or list(REGISTRY)
    unknown = [k for k in wanted if k not in REGISTRY]
    if unknown:
        print(f"未知策略: {unknown}\n可选: {list(REGISTRY)}", file=sys.stderr)
        return 2

    settings = Settings(feishu_webhook_url="https://open.feishu.cn/placeholder")
    engine = DataEngine(settings)
    logger = get_logger(__name__)

    total_symbols = len(engine.get_local_symbols())
    if total_symbols == 0:
        print("!! 本地数据库为空 —— 请先跑 `main.py --backfill` 回填历史 K 线。")
        return 1
    print(f"本地库股票数: {total_symbols}\n")

    all_hits: dict[str, list[str]] = {}
    for key in wanted:
        cls, cn_name = REGISTRY[key]
        try:
            selected = cls(engine=engine, settings=settings).run()
        except Exception as exc:  # noqa: BLE001
            print(f"[{cn_name}] 策略执行异常: {exc}", file=sys.stderr)
            selected = []
        all_hits[key] = selected
        print(f"===== {cn_name} ({key}) — 命中 {len(selected)} 只 =====")
        if selected:
            names = get_names(selected)
            for code in selected:
                nm = names.get(code, "?")
                print(f"  {code}  {nm}  https://xueqiu.com/S/{to_xueqiu(code)}")
        print()
        logger.debug("done %s", key)

    print("===== 汇总 =====")
    for key, hits in all_hits.items():
        print(f"{REGISTRY[key][1]:<10} {len(hits):>3} 只  {' '.join(hits) if hits else '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
