---
name: sequoia-x
description: "A股量化选股系统 Sequoia-X — 收盘后自动扫描 7 种技术形态（海龟突破/均线放量/高窄旗形/涨停洗盘/上升跌停反包/RPS强度/定增监控），支持全市场扫描、单票/申万行业查询、以及「形态选股 → 估值+排雷」基本面闭环验证。当用户要跑 A 股技术选股、查某只股票或某个行业的形态、回填历史K线、给选股结果做基本面体检、查看/新增选股策略，或需要 A 股日K/行业元数据源（baostock/Tushare/腾讯/新浪）时使用。"
version: 2.3.0
author: sngyai / yww520 (Hermes skill wrapper)
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [a-share, quant, stock-screening, technical-analysis, feishu, baostock, tushare, cron]
    homepage: https://github.com/sngyai/Sequoia-X
    upstream: https://github.com/yww520/Sequoia-X
---

# Sequoia-X · A股量化选股系统

面向 A 股市场的量化选股系统（V2），拉日K 存本地 SQLite，收盘后跑 7 个技术形态策略，
选出股票通过飞书 Webhook 推送到群（也可只输出分析、不推送）。

> 📌 **原项目设计用 baostock + 后复权，但本机 baostock 已不可用，实际跑在 Tushare 不复权数据上。**
> 先读下面的「数据源现状」，不要照 README 直接跑 `main.py --backfill`。

**项目本体就在本 skill 目录下**（`~/.hermes/skills/research/sequoia-x/`），已 `uv sync` 装好依赖。
`main.py` 是入口，`sequoia_x/` 是包，`.venv/` 是虚拟环境，`data/sequoia_v2.db` 是运行时生成的数据库。

## 数据源现状（重要）

**默认的 baostock 在本机已不可用**（登录能过，但任何查询都卡死超时；
端口 baostock.com:10030 不可达）。腾讯接口也已被反爬拦截（返回 JS 挑战 501）。
**当前可用数据源 = Tushare**，用 `backfill_tushare.py` 灌数据。

```bash
cd ~/.hermes/skills/research/sequoia-x

# 全量回填（2024-01 至今约 44 分钟，353 万行 / 5662 只）
.venv/bin/python backfill_tushare.py --start 2024-01-01

# 每日增量（只补最新交易日，几秒）
.venv/bin/python backfill_tushare.py
```

Tushare token 从 `~/ai-invest-agent/.env` 的 `TUSHARE_TOKEN` 读取。

### Tushare 的已知限制（本 token 档位）

| 接口 | 限制 | 影响 |
|---|---|---|
| `pro.daily(trade_date=...)` | **无限制**，全市场 5550 只/2 秒 | ✅ 主力数据来源 |
| `adj_factor` | 限流 **1 次/小时** | ❌ 无法做后复权 |
| `trade_cal` | 限流 1 次/小时 | 已降级为「自然日扫描 + 跳过空数据」 |
| `stock_basic` | 1 次/分钟，**连续调用会升级为 1 次/小时** | ⚠️ 已在用 `sync_valuation.py` 替代 |
| `concept` | 限流 1 次/分钟（且必填 `trade_date`） | ⚠️ 概念板块基本不可用 |
| `ths_index` / `index_member_all` / `sw_daily` | **无权限** | ❌ 拿不到同花顺概念 / 申万成分指数 |

> ⚠️ **配额会被"试探"烧掉**：本档位多个接口共享分钟/小时配额，随手多调几次
> `stock_basic` 或 `concept` 就会把窗口耗尽（症状是报「频率超限 1次/小时」）。
>
> ✅ **但行业分类不必再依赖 Tushare**：本机 `stock-valuation-lite` 投研系统
> 已提供**申万三级行业 + 市值 + 实时行情**（覆盖 5896 只 / 31 个 L1），
> 用 `sync_valuation.py` 同步即可。**Tushare 配额留给 `pro.daily` 日线。**

**因此数据是「不复权」的。** 影响评估（已实测）：全库仅 0.074% 的样本存在
除权跳空（2622/3531979，涉及 1063 只股票的孤立日期）。实测海龟策略 92 只命中中
**0 只**受除权影响 → 中短期形态分析结果可信。只有跨除权的长周期判断需人工复核。

### 分析（不推送飞书）

```bash
# 全市场扫描（7 个策略）
.venv/bin/python analyze.py                 # 全部策略
.venv/bin/python analyze.py turtle rps      # 指定策略
```

结果直接打印到 stdout（含形态指标 + 雪球链接），不走任何 webhook。
单票 / 行业 / 主题查询见下节 `query.py`。
`main.py` 仍保留原项目的飞书推送行为，**不要用它做分析**。

> 🔑 **用户偏好（已验证）**：用户说「不用按原来的 skill 重新配置，只要用这个 skill
> 分析股票形态即可」——意思是**不要为了"配齐原项目"去填 `FEISHU_WEBHOOK_URL`**，
> 直接把分析结论回复到对话里。`analyze.py`/`query.py` 内部用占位 webhook 构造
> `Settings()`，无需真实 webhook。遇到"帮我用这个 skill 分析"类请求，**默认走分析模式**，
> 不要先跑一遍配置向导。

### 单只 / 行业 / 主题查询（query.py）

原项目只有「全市场扫描」，没有单票/行业入口。`query.py` 补上了这几种模式：

```bash
.venv/bin/python query.py 600519              # 按代码（含实时行情）
.venv/bin/python query.py 茅台                # 按名称模糊匹配（短名/别名都行）
.venv/bin/python query.py --l1 电子            # 申万一级
.venv/bin/python query.py --l2 半导体          # 申万二级
.venv/bin/python query.py --l3 印制电路板      # 申万三级
.venv/bin/python query.py --l1 电子 --screen   # 行业内跑形态筛选
.venv/bin/python query.py --l3 印制电路板 --screen --min-turnover 5
.venv/bin/python query.py --grep 光电          # 在「名称 + 行业」里搜关键词
.venv/bin/python query.py --list-l1            # 列出申万一级行业
.venv/bin/python query.py --realtime 600519    # 只取实时行情
```

单票输出包含：**申万三级行业、市值**、成交额(亿)与 >1亿 判定、量比、20日新高突破与
距高点百分比、阳线、站上 MA20/MA60、均线多头、5/20/60/120 日涨幅、60 日区间，
以及**实时行情**（现价/涨跌幅/换手率/成交额/PE_TTM/PB）。
**前一组正是 7 个策略的判定条件**，可直接反推某票为何命中/未命中。

`--screen` 是新增的杀手锏：对选中的股票集合跑形态筛选，按 20 日涨幅排序，
并标注「突破20日高 / 均线多头 / 成交额」。例如 `--l3 印制电路板 --screen`
一次看全 PCB 板块 23 只的形态强弱。

**主题查询的能力边界**：`--grep` 是**名称+行业关键词模糊匹配**。
自从接入 stock-valuation-lite 的**申万三级分类**后，行业维度已相当精细
（如 `--l2 半导体`、`--l3 印制电路板`），但**仍不是同花顺等"概念板块"成分**
（本 token 无 `ths_index`/`index_member_all` 权限，东财概念接口被反爬）。
要真概念板块（"AI算力"/"固态电池"）：① 升级 Tushare 积分；② 本地自建概念映射表；
③ 对齐 `daily-watchlist` 已有分组。**先说清限制再动手。**

### 股票元数据：优先用 stock-valuation-lite 投研系统

本机运行着 **A 股股票价值投研系统**（`~/stock-valuation-lite`，FastAPI，监听
`127.0.0.1:8888`），提供**申万三级行业 + 市值 + 实时行情**，是元数据首选来源：

```bash
.venv/bin/python sync_valuation.py       # 同步元数据（申万L1/L2/L3 + 市值）
```

| 来源 | 覆盖 | 行业体系 | 说明 |
|---|---|---|---|
| **stock-valuation-lite** | **5896 只 / 31 个 L1** | **申万三级** ✅ | 首选；另有 `/api/realtime/quotes` 实时行情 |
| load_industry_sina.py | 2978 只 / 48 个 | 新浪板块 | 备用（免依赖，覆盖小） |
| build_meta.py | 5562 只 | Tushare industry | 备用（`stock_basic` 限流 1次/小时） |

投研系统关键接口：
- `GET /api/stocks` → 全市场列表，含 `l1/l2/l3`（申万三级）、`code`、`name`、`mcap`
- `GET /api/realtime/quotes?codes=600519,002815` → 实时现价/涨跌幅/换手/成交额/PE/PB/市值
- `GET /api/realtime/quote/{code}` → 单只实时
- `GET /api/stock?file=<文件名>` → 单只深度数据（估值/财报/排雷）

`sync_valuation.py` 用 `ON CONFLICT DO UPDATE` 写入，`industry` 字段存**最细一级**
（l3 优先），并新增 `sw_l1/sw_l2/sw_l3/mcap_yi/meta_src` 列，与新浪/Tushare 源可叠加互补。

服务检查：`ps aux | grep stock-valuation-lite`；
若未运行：`cd ~/stock-valuation-lite && ./venv/bin/python backend/server.py`。

> 📎 **该系统的完整能力清单**（全部 HTTP 接口、估值/排雷/同业对比能力、
> 单只 JSON 字段表、`verify.py` 闭环实现、排雷引擎已修的 3 个 bug、集成陷阱）见
> `references/stock-valuation-lite-integration.md`。**要往基本面方向扩展时先读它。**

新浪接口（无 token，实测可用）：
- 行业列表 `https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php`
- 成分股 `.../Market_Center.getHQNodeData?page=1&num=100&node=<行业代码>`

两者都返回 **GBK 编码**，需 `r.encoding = "gbk"`；行业列表是 `var X = {...}` 形式，
需正则取出 JSON。`stock_meta` 用 `ON CONFLICT DO UPDATE ... COALESCE(NULLIF(...))` 写入，
这样新浪的行业名不会被 Tushare 的空字段覆盖（两个脚本可互补叠加）。

### 结果可信度校验（必做）

策略返回 0 或结果偏多时，**先跑校验脚本再下结论** —— 它能区分「真没信号」和
「数据缺字段导致的假 0」（如腾讯无成交额 → 海龟恒 0）：

```bash
.venv/bin/python scripts/verify_hits.py turtle          # 跑策略 + 抽查前3只逐条核验
.venv/bin/python scripts/verify_hits.py turtle 000921   # 指定股票逐条核验
```

输出：全库除权跳空比例（评估不复权失真面）+ 命中股票的逐条件 ✅/❌ + 除权影响面。
实测参考值：除权跳空 ≈0.074% 时结论可信。

### 闭环：形态选股 → 基本面体检（verify.py）

**形态好看 ≠ 值得买**。`verify.py` 把选股结果送进本机投研系统做估值 + 排雷，
形成「技术面筛出 → 基本面证伪/确认」：

```bash
.venv/bin/python verify.py --from-strategy turtle,rps   # 策略结果 → 自动体检
.venv/bin/python verify.py --l3 印制电路板               # 按行业
.venv/bin/python verify.py 002815 002913                 # 指定标的
.venv/bin/python verify.py --no-audit                    # 只读本地估值，不调排雷接口
```

输出排雷分（✅/⚠️/🟡/🔴）+ 三大前提 + 现金流画像 + EPV/TC 隐含回报，
并分两层下结论：「形态+基本面双通过」vs「形态好看但有雷」。
明细落盘 `data/verify_last.json`。

> ⚠️ **排雷结论要交叉验证，别直接采信**。本次实测发现排雷引擎有 3 个字段/口径 bug，
> 曾把整个 PCB 板块误判为「一票否决」。**「整个板块集体被否决」本身就是误报信号。**
> bug 细节 + 财报比率的通用排查方法论见
> `references/stock-valuation-lite-integration.md`；
> 跨项目通用的「金融数据管道排查套路」（时点/期间、累计/单季、列表顺序、
> 反推参数、静默兜底、改他人代码的边界）见
> `references/financial-ratio-debugging.md`。**调任何财务比率/因子管道前先读它。**

## 前置配置

配置文件是本目录下的 `.env`（模板见 `.env.example`）：

| 变量 | 必填 | 说明 |
|------|:---:|------|
| `FEISHU_WEBHOOK_URL` | ✅ | 飞书群机器人 Webhook，作为默认推送目标 |
| `STRATEGY_WEBHOOK_<KEY>` | 可选 | 每个策略路由到独立机器人；未配置则 fallback 到上面那个 |
| `DB_PATH` | 可选 | SQLite 路径，默认 `data/sequoia_v2.db` |
| `START_DATE` | 可选 | 数据起始日期，默认 `2024-01-01` |

策略 webhook key 对照：`MA_VOLUME` `TURTLE` `FLAG` `SHAKEOUT` `LIMIT_DOWN` `RPS` `PRIVATE_PLACEMENT`。
环境变量会被 `Settings.model_post_init` 扫描 `STRATEGY_WEBHOOK_` 前缀自动收集。

## 内置策略

| 策略类 | webhook_key | 逻辑 |
|---|---|---|
| `TurtleTradeStrategy` | `turtle` | 海龟突破：20日新高 + 成交额过亿 + 阳线防诱多，按涨幅排序 |
| `MaVolumeStrategy` | `ma_volume` | 均线 + 放量突破 |
| `HighTightFlagStrategy` | `flag` | 高而窄的旗形整理突破 |
| `LimitUpShakeoutStrategy` | `shakeout` | 涨停洗盘回踩确认 |
| `UptrendLimitDownStrategy` | `limit_down` | 上升趋势中的跌停反包 |
| `RpsBreakoutStrategy` | `rps` | 欧奈尔 RPS 相对强度突破 |
| `PrivatePlacementStrategy` | `private_placement` | 定增公告监控 |

策略列表在 `main.py` 的 `strategies: list[BaseStrategy] = [...]` 处，**新增策略在此追加即可**。

## 目录结构

```
sequoia-x/
├── main.py                    # 入口：argparse 分发日常/回填模式
├── .env / .env.example        # 配置
├── data/sequoia_v2.db         # SQLite（运行时生成，可直接拷贝迁移）
└── sequoia_x/
    ├── core/config.py         # pydantic-settings 配置
    ├── core/logger.py         # rich 结构化日志
    ├── data/engine.py         # baostock 回填 + 增量同步 + SQLite
    ├── strategy/base.py       # 策略抽象基类（webhook_key + run()）
    ├── strategy/*.py          # 7 个具体策略
    └── notify/feishu.py       # 飞书卡片推送（含雪球链接）
```

## 数据说明

- **数据源**：默认 baostock（免费、无需注册）——规避了东方财富反爬。
- **复权**：后复权（hfq）——历史价格不变，适合增量存储，避免除权错乱。
- **增量**：`sync_today_bulk()` 单次 API 拉全市场当日快照；回填用多轮重跑 + 自动重连。

> ⚠️ baostock **并非无限流** —— 短时内反复 `login()` 会触发服务端限流，症状是
> `login` 返回 success 但任何 `query_*` 调用**永久卡死**（不是报错，是 hang 到超时）。
> 遇到这种情况不要反复重试加重限流，直接切替代源（见下）。

## 数据源 fallback（baostock 不可用时）

**数据源能力对比、Tushare 接入命令、字段单位映射、SQLite schema 对齐要求**，全部见
`references/a-share-data-sources.md`。该文件还包含**元数据（名称/行业/概念）数据源矩阵**
与新浪行业分类接口的实测细节。核心结论：

| 源 | 适用 | 关键限制 |
|---|---|---|
| baostock | 首选 | 短时多次 login 会限流至卡死 |
| **Tushare `pro.daily`** | **最稳替代** | 不复权（无 hfq）；每次调用拉全市场 ~5550 只 / 2s |
| Tushare `pro_bar(adj='hfq')` | ❌ 不可用于全市场 | `adj_factor` 限 **1 次/小时**，逐只调用等于数天 |
| 腾讯 `ifzq.gtimg.cn` | 小量可用 | **无成交额字段**；批量抓取触发 JS 挑战 (501) |
| akshare (东财) | ❌ | 反爬，`RemoteDisconnected` |

**两个最容易误判的陷阱**：
1. 腾讯数据没有成交额字段 → 海龟策略（要求 `turnover > 1亿`）**恒为 0**，
   看起来像"今天没信号"，实际是数据缺失。补齐：`(O+H+L+C)/4 * volume * 100`。
2. 盘中/盘后早期只有零星股票有当日 K 线（实测 5000+ 只里仅 31 只）。
   策略等于在拿不完整数据判断。**回填 `--end` 要钉在最后一个完整交易日**，不要用 `date.today()`。

写并发回填脚本的工程坑（SQLite 跨线程、`uv pip install VIRTUAL_ENV=`、
增量起点、空库静默失败）见 `references/backfill-engineering-patterns.md`。

## 定时任务（收盘后自动跑）

建议每个交易日收盘后（19:15）自动执行。Hermes cron 里用 `no_agent` 脚本模式最省：
脚本 `cd` 到 skill 目录、`.venv/bin/python main.py`、stdout 静默（无选股时不推送）。
参考创建命令（收盘后 = 北京时间 19:15，即 UTC 11:15）：

```bash
# 通过 cronjob 工具创建，no_agent=True + script 指向包装脚本
# 或直接用系统 crontab：
# 15 19 * * 1-5 cd ~/.hermes/skills/research/sequoia-x && .venv/bin/python main.py >> log.txt 2>&1
```

> ⚠️ 若用 Hermes cron 的 `no_agent` 脚本模式，注意默认 120s 超时——回填模式（12分钟）
> 必定超时，日常模式（2~3分钟）也可能逼近。需要 `cron.script_timeout_seconds` 调大。

## 常见坑（Pitfalls）

1. **数据源优先级** —— baostock（默认）已挂、腾讯已被反爬，**用 Tushare**
   （`backfill_tushare.py`）。三者切换只需换回填脚本，SQLite schema 完全一致。
2. **Tushare `adj_factor` / `trade_cal` 限流 1 次/小时** —— 所以拿不到后复权，
   `trade_cal` 已降级为自然日扫描。别写依赖这两个接口的循环。
3. **必须先回填** —— 空库跑策略不会报错，但全部返回 0，容易误判为"跑通了"。
4. **分析用 `analyze.py`，不要用 `main.py`** —— `main.py` 会真的往飞书 webhook 推送。
5. **`Settings()` 必填 `feishu_webhook_url`** —— 缺 `.env` 会抛 ValidationError。
   `analyze.py` 里用占位 URL 绕开了这点。
6. **依赖用 `uv`** —— 系统 python 是 PEP 668 管理的，用 `uv sync`；
   装额外包要 `VIRTUAL_ENV=$PWD/.venv uv pip install <pkg>`（直接 `uv pip install`
   可能装到别处，导致 `ModuleNotFoundError`）。
7. **跑测试别加 `--timeout=N`** —— dev 依赖里没有 `pytest-timeout`，会直接报
   `unrecognized arguments` 退出。
8. **`test_feishu.py` 3 个用例失败是上游已知问题** —— 它们没 mock `_get_stock_names()`，
   真去登录 baostock（~10 秒）撞 hypothesis 200ms deadline。**非代码 bug**。
9. **定增监控策略恒为 0** —— 它依赖 `akshare.stock_qbzf_em()`（东方财富），
   该接口在本机被拒。需要换数据源才能用。
10. **不复权的实测影响** —— 全库仅 0.074% 样本有除权跳空；中短期形态（均线放量/
    旗形/涨停洗盘/跌停反包/RPS）不受影响，海龟已实测 92 只命中 0 只失真。
11. **别用 Tushare 试探配额** —— `stock_basic`/`concept` 等共享分钟+小时配额，
    连续几次探测调用就会把窗口烧到「1次/小时」。要元数据先走免配额的新浪接口，
    把配额留给 `pro.daily`。
    > 🔬 **探测未知接口的正确姿势**：不要用一个接口一个接口地试权限（每试一次就消耗一次配额，
    > 且限流会从 1次/分钟**升级**为 1次/小时）。先查官方文档的频次表，或一次性写完
    > 探测脚本再跑（把 N 个接口放在一个脚本里，只烧一轮）。本次就是逐个试探
    > `stock_basic`→`adj_factor`→`trade_cal`→`concept`→`ths_index`，把 `stock_basic`
    > 从 1次/分钟 烧成了 1次/小时。
12. **拿不到真概念板块** —— `ths_index`/`index_member_all`/`sw_daily` 无权限。
    行业用新浪分类可解决；概念只能自建映射表。**别向用户承诺"能按概念板块筛"。**
13. **盘中/盘后早期数据不完整** —— 当日只有零星股票有 K 线（实测 5550 只里仅 31 只）。
    回填 `--end` 要钉在最后一个完整交易日，否则策略在拿残缺数据判断。
14. **别无条件采信投研系统的排雷结论** —— 它的 `caibao_audit_engine.py` 有过 3 个
    字段名/口径 bug（已修，见 integration 参考文件）。**遇到「整个板块被集体否决」
    先怀疑引擎口径，而不是行业集体暴雷**；务必拿原始数据手算一遍再采信。
15. **改 `~/stock-valuation-lite` 的代码后必须重启它的服务** —— FastAPI 无热重载，
    改完不重启等于没改（症状：规则数值纹丝不动，极易误判为「修复无效」）。
    改前先 `cp` 备份；重启后**用本地直调复现**（`./venv/bin/python -c` 里直接
    `E.audit_stock(...)`）比反复打 HTTP 接口更快定位。
16. **给别的项目改 bug 要守住边界** —— 本次只修能确证根因的 3 处；
    对字段名对不上但**无法确定正确替代名**的、以及**属业务判断**的（如重复规则），
    一律**不动 + 在汇报里说明**，不要擅自替用户做业务决策。
17. **`.gitignore` 里的 `data/` 会误伤 `sequoia_x/data/` 包目录** —— 未加根锚定的目录名
    会递归匹配，导致 `git add -A` 静默跳过 `sequoia_x/data/engine.py`，提交里出现
    「整文件删除」。**看到 diff 里有纯删除就要查。** 写 `.gitignore` 时目录名一律加
    `/` 锚定（`/data/*.db`）。详见 `references/publishing-to-github-fork.md`。
18. **`ssh -T git@github.com` 报的 `Hi <owner>/<repo>!` 是部署密钥被授权的那个仓库**，
    不是用户名 —— 说明 key 是**按仓库授权**的 deploy key，**推不了别的仓库**。
    必须做一次探针推送验证真实写权限，不要因为看到 "successfully authenticated" 就以为能推。
    （能 HTTPS 克隆公开上游 ≠ 有推送权限。）

## 与 GitHub 仓库同步（已完成，日常直接用）

**本 skill 目录本身就是 `yww520/Sequoia-X` 的 git clone**（本目录有 `.git`，
`origin` = HTTPS fork）。所以直接在本目录做 git 操作即可，不需要额外副本：

```bash
cd ~/.hermes/skills/research/sequoia-x

git pull origin master      # 拉取仓库更新（skill 即时生效）
git status                  # 检查本地改动
git add -A && git commit -m "..."
git push origin master      # 推送回 fork
```

**830MB 运行时资产不入库**（`.gitignore` 覆盖，勿提交）：

| 路径 | 大小 | 说明 |
|---|---|---|
| `data/sequoia_v2.db` | 580M | 日K 库，可 `backfill_tushare.py` 重建 |
| `.venv/` | 250M | `uv sync` 重建 |
| `.env` | — | 含飞书 webhook，**永不提交** |

删掉这些只会丢缓存，不会丢代码；但重建 DB 要 44 分钟，别误删。

> 📎 **发布流程与踩坑见 `references/publishing-to-github-fork.md`** —— 认证探针、
> graft 基底、`.gitignore` 根锚定陷阱、敏感信息扫描、推不动时的汇报话术。
> **给新仓库发布前先读它**，尤其「先探认证再构建」和「deploy key 按仓库授权」两条。

⚠️ 发布到**新**仓库时的要点（本仓库已完成，仅作参考）：
- **先探认证**（`gh auth status` + 探针推送），拿不到凭据就先问用户要 PAT，别先花时间构建
- 用 `git reset --soft $(git rev-parse origin/master)` 把改动 graft 到上游历史，得到干净的**单次提交**
- 有改动到 `README.md` 时**主动问用户**要不要保留上游原文

## 验证安装是否成功

```bash
cd ~/.hermes/skills/research/sequoia-x

# 1. 依赖 + import 自检
.venv/bin/python -c "from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy; print('OK')"

# 2. 库内容自检（应有百万级行数、多个交易日）
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('data/sequoia_v2.db')
print('rows:', c.execute('SELECT COUNT(*) FROM stock_daily').fetchone()[0])
print('symbols:', c.execute('SELECT COUNT(DISTINCT symbol) FROM stock_daily').fetchone()[0])
print('range:', c.execute('SELECT MIN(date),MAX(date) FROM stock_daily').fetchone())"

# 3. 跑策略（应有命中，且全是真实形态）
.venv/bin/python analyze.py turtle

# 4. 单元测试（test_feishu.py 3 例失败属上游已知问题）
.venv/bin/python -m pytest tests/ -q
```

