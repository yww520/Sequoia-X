# Sequoia-X: 王者回归 | The King Returns

> A 股量化选股系统 V2 | A-Share Quantitative Stock Selection System V2

---

## 简介 | Introduction

Sequoia-X V2 是面向 A 股市场的量化选股系统，基于现代 Python 工程化标准从零重构。
系统以 OOP 架构、向量化计算和增量数据更新为核心设计原则，每日收盘后自动选股并推送至飞书群。

数据层使用 [baostock](http://baostock.com)（免费、无需注册、无限流）拉取历史及增量日 K 数据（后复权），
存储于本地 SQLite，彻底规避东方财富反爬问题。

---

> ## ⚠️ 本分支改造说明（yww520 fork）
>
> 原项目的数据层设计依赖 baostock + 后复权。在部分网络环境下 **baostock 完全不可用**
> （登录能过但任何查询都卡死超时，`baostock.com:10030` 不可达），腾讯行情接口也已被反爬拦截。
>
> 本分支做了以下工程改造，**并额外增加了「形态选股 → 基本面估值+排雷」的闭环验证能力**：
>
> ### 1. 数据源切换：baostock → Tushare
> - `backfill_tushare.py`：基于 `pro.daily(trade_date=...)` 的按交易日批量回填
>   - 全市场 5600+ 只 / 353 万行 约 44 分钟灌完，**含真实 `amount`（成交额）**
>   - 无 `trade_cal` / `adj_factor` 调用，规避 Tushare 免费档的 1 次/小时限流
>   - 交易日从数据本身推导，不依赖日历接口
> - `backfill_tencent.py`：腾讯源回填（**已弃用**，批量抓取会触发 501 JS 挑战反爬，留作参考）
> - 取舍：Tushare 免费档 `adj_factor` 限流 → 使用**不复权**数据。
>   实测全库仅 **0.074%** 样本存在除权跳空，海龟策略 92 只命中中 **0 只**受影响，结果可信。
>
> ### 2. 行业与元数据
> - `sync_valuation.py`：从 [stock-valuation-lite](https://github.com/yww520) 同步**申万三级行业**+市值进 `stock_meta`
>   - 同步得 5,896 只 / 31 个申万一级行业
> - `build_meta.py`：本地构建 `stock_meta` 表
> - `load_industry_sina.py`：新浪行业源（**已弃用**，仅 2,978 只粗行业，被申万三级取代）
>
> ### 3. 查询能力
> - `query.py`：单票 / 行业 / 关键词 / 申万一二三级查询 + 实时行情 + 形态筛选
>
> ### 4. 闭环验证（核心新增）
> - `verify.py`：**把形态选股结果送进投研系统做基本面体检**
>   - 排雷：48 条唐门规则、三大前提、现金流画像、一票否决
>   - 估值：EPV_T0 / TC估值 / EPV当期 的投资收益率（隐含回报率）
>   - 输出分层结论：**「形态+基本面双通过」** vs **「形态好看但有雷」**
> - `analyze.py`：纯分析脚本，跑全策略但不推送飞书
>
> ### 5. Hermes Skill 包装
> - `SKILL.md`：可作为 [Hermes Agent](https://hermes-agent.nousresearch.com/docs) skill 加载，
>   含数据源现状、完整命令、Pitfalls 与排错手册
> - `references/`：A 股数据源对比、回填工程模式、财务比率排错、投研系统集成文档
>
> ---
>
> ### 快速开始（本分支）
>
> ```bash
> uv sync
>
> # 1. 全量回填（约 44 分钟，353 万行 / 5662 只）
> .venv/bin/python backfill_tushare.py --start 2024-01-01
>
> # 2. 每日增量（只补最新交易日，几秒）
> .venv/bin/python backfill_tushare.py
>
> # 3. 跑全策略并输出分析（不推送）
> .venv/bin/python analyze.py
>
> # 4. 形态 → 基本面闭环验证
> .venv/bin/python verify.py --from-strategy turtle,rps
> .venv/bin/python verify.py 002815 002913 600183
> .venv/bin/python verify.py --l3 印制电路板
>
> # 5. 单票 / 行业查询
> .venv/bin/python query.py 600519
> .venv/bin/python query.py 茅台
> .venv/bin/python query.py --industry 有色金属
> .venv/bin/python query.py --l1 电子 --screen --min-turnover 5
> ```
>
> **注意**：本分支下不要直接用 `main.py --backfill`（走 baostock，本机不可用）。
> `main.py` 仅在配置好飞书 webhook 且数据层换源后才可用于「跑策略+推送」。

---

## 原项目文档（上游原文）

## 两种运行模式

```bash
python main.py               # 日常模式：8进程增量补数据 + 跑策略 + 飞书推送（2~3分钟）

python main.py --backfill    # 回填模式：拉取全量历史数据（首次使用或长期间隔后使用）
```

### 日常模式（默认）

每个交易日收盘后运行，自动完成：

1. **增量补数据** — 8 进程并发，从上次日期补到最新交易日
2. **执行 7 个策略** — 扫描全市场，筛选符合条件的股票
3. **飞书推送** — 按策略分组，推送到对应的飞书群

耗时约 2~3 分钟。

### 回填模式

首次使用或长时间未更新时，拉取全量历史数据：

```bash
python main.py --backfill
```

耗时取决于数据量大小，通常 10~30 分钟。

---

## 策略列表

| 策略 | 标识 | 说明 |
|------|------|------|
| 海龟交易法则 | `turtle` | 突破 20 日高点 |
| RPS 相对强度 | `rps` | 相对强度排名前 10% |
| 均线放量 | `ma_volume` | 均线多头排列 + 放量 |
| 高窄旗形 | `flag` | 强势股高位窄幅整理 |
| 涨停洗盘 | `shakeout` | 涨停后缩量回调洗盘 |
| 上升趋势跌停反包 | `limit_down` | 上涨趋势中的跌停后反包 |
| 定增监控 | `private_placement` | 定增事件监控 |

---

## 架构

```
sequoia_x/
├── core/           # 配置、日志
├── data/           # 数据引擎（baostock / SQLite）
├── strategy/       # 7 个策略实现
└── notify/         # 飞书推送
```

---

## 配置

复制 `.env.example` 为 `.env`，按需修改：

```bash
cp .env.example .env
```

| 变量 | 说明 | 必填 |
|------|------|------|
| `DB_PATH` | SQLite 数据库路径 | 否 |
| `START_DATE` | 数据起始日期 | 否 |
| `FEISHU_WEBHOOK_URL` | 默认飞书 Webhook | 是 |
| `STRATEGY_WEBHOOK_<名称>` | 策略专属 Webhook | 否 |

---

## 环境要求

- Python >= 3.13
- [uv](https://github.com/astral-sh/uv) 包管理器

```bash
uv sync
```

---

## 许可证 | License

MIT

---

## 致谢 | Credits

原项目：[sngyai/Sequoia-X](https://github.com/sngyai/Sequoia-X)
