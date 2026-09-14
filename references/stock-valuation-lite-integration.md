# stock-valuation-lite · 本机 A 股价值投研系统

本机常驻运行的 **A 股股票价值投研系统**（精简云端版），是 Sequoia-X 的**元数据与
基本面数据上游**。Sequoia-X 负责「技术形态」，它负责「行业分类 + 估值 + 财报 + 排雷」，
两者是互补的两个视角，可以打通。

## 基本事实

| 项 | 值 |
|---|---|
| 路径 | `~/stock-valuation-lite` |
| 技术栈 | FastAPI 后端 + 静态前端，Docker 部署（`docker-compose.yml`） |
| 监听 | `127.0.0.1:8888`（`.env` 里 `PORT`/`HOST`） |
| 启动 | `cd ~/stock-valuation-lite && ./venv/bin/python backend/server.py` |
| 检查 | `ps aux \| grep stock-valuation-lite` |
| 鉴权 | 全局访问密码（`ACCESS_PASSWORD`）；本机直连通常无需带 token |
| 源码 | `backend/server.py`(2312行) `data_engine.py` `realtime_engine.py` `caibao_audit_engine.py` |
| 数据 | `backend/data/*.json` 每票一个文件，**5487 个**，命名：`L1--L2--L3-代码.SH-简称-日期.json` |
| 内存 | 80~120MB，Docker 硬配额 300MB / 0.5 核（设计上不与同机服务抢资源） |

> 文件名本身就是申万三级分类的载体：`电子--半导体--集成电路封测-688403.SH-汇成股份-20260328.json`
> 即使 API 不可用，也能靠 `ls backend/data/` + 文件名解析出全量行业映射。

## 核心能力（README 摘要）

1. **选股工作台** — 全 A 股 5400+ 多因子筛选（申万行业、老唐 ROE≥15% 核心圈、千亿蓝筹、自选池、实时排序）
2. **个股深度分析**
   - 估值模型：EPV_T0、TC、DCF 两阶段自由现金流、**10 年 PB 历史估值带**、营收/净利走势
   - 财报穿透：唐门 **48 条疑点规则**红黄绿灯、价值投资三大前提、连续 8 期三大报表全科目、
     6 大排雷附注看板（应收账龄/存贷双高/商誉减值/垃圾筐科目）、MD&A 拆解、前十大流通股东
   - **AI 深度法务排雷研报**：DeepSeek 联网推理 + 财报审计

## 与 Sequoia-X 的集成（已落地）

`sync_valuation.py` 拉 `/api/stocks` 写进 `stock_meta`：

- 新增列 `sw_l1 / sw_l2 / sw_l3 / mcap_yi / meta_src`
- `industry` 字段存**最细一级**（l3 优先），兼容旧查询
- 用 `ON CONFLICT DO UPDATE`，与新浪/Tushare 源可叠加互补，互不覆盖

`query.py` 已接入 `/api/realtime/quotes`，单票输出自动带上实时现价/换手/PE/PB。

## 闭环已打通：`verify.py`（形态选股 → 估值 + 排雷）

```bash
cd ~/.hermes/skills/research/sequoia-x
.venv/bin/python verify.py --from-strategy turtle,rps   # 策略结果 → 自动体检
.venv/bin/python verify.py 002815 002913 600183          # 指定标的
.venv/bin/python verify.py --l3 印制电路板               # 按行业
.venv/bin/python verify.py --no-audit                    # 只读本地估值，不调排雷接口
```

数据来源（都不需要鉴权）：
- **本地文件直读**（快）：`~/stock-valuation-lite/backend/data/*.json` 的 `meta`
  （EPV_T0 / TC估值 / EPV当期 的投资收益率 + ROE/PE/PB/市值）。
  文件名解析见下节「文件名即索引」。
- **排雷走 API**（慢，一只约 2~10s）：`GET /api/caibao/audit?code=<6位>.<SH|SZ|BJ>`

输出：排雷分（✅/⚠️/🟡/🔴）+ 三大前提 + 现金流画像 + 高危/中危计数 +
EPV/TC 隐含回报 + 分两层结论（「形态+基本面双通过」vs「形态好看但有雷」）。
明细落盘 `data/verify_last.json`。

**排序与判级规则**：`verdict_score()` 把 `overall_verdict` 压成 0~3 分
（含「一票否决/高危」→0；`profit_real == 不通过` →0；有 high →1；有 medium →2；否则 3）。
先按排雷分、再按 EPV 隐含回报降序。

### 文件名即索引（不依赖 API 也能拿全量元数据）

`backend/data/<L1>--<L2>--<L3>-<代码>.<交易所>-<简称>-<日期>.json`，
用 `stem.rsplit("-", 3)` 切出 `[L1--L2--L3, 代码.交易所, 简称, 日期]`。
同一代码多份快照时取日期最新那份。

## 🔧 排雷引擎（caibao_audit_engine.py）已修的 3 个 bug（重要）

**背景**：初次接入时，PCB 板块三只票（崇达技术 002815 / 奥士康 002913 / 生益科技 600183）
**全部被判「🔴 一票否决」**。这种「整板块集体否决」的形态本身就是**误报信号** ——
一个行业的所有公司同时犯同一个致命问题，比「引擎算错了」更可疑。追查后确认是**三个连锁
bug**，且都是**字段名/口径**问题，不是业务逻辑问题。

| # | 位置 | Bug | 症状 | 修复 |
|---|---|---|---|---|
| 1 | 现金流字段提取 | 找 `GOODS_SALE_SERVICE` / `SALE_GOODS_SERVICE`，**实际字段名是 `SALES_SERVICES`** | 销售收现比恒为 **0.00** → R014 假触发 | 补上 `SALES_SERVICES`（放最前，保留旧名兜底） |
| 2 | `cur_rev` 口径 | 应收是**时点余额**，营收是**年初至今累计**，直接相除分母偏小 | 崇达真实 24% 被算成 **48%** | 按报告期年化：Q1×4 / 中报×2 / Q3×4/3 / 年报×1 |
| 3 | 报告期取值 | `income_sheet[-1]` 取到的是**最老一期**（该列表**最新期在前**），月份取错 → 年化因子错 | 年化后仍算成 36% | 改取 `[0]`；优先读 `statements.income.periods[-1]` |

修复后：崇达技术 / 奥士康 🔴 → **🟡 具备观察价值**（仅剩 2 项中危）；
生益科技仍 🔴，但依据变成真实的 **34.8%** 应收占比（手算复核：
`132.24亿 ÷ (190.26亿 × 2) = 34.8%` ✅ 引擎输出一致）。

备份：`/tmp/caibao_audit_engine.py.bak`。改后**必须重启服务**才生效。

### 两条可复用的调试方法论（比 bug 本身更值钱）

1. **「整板块集体否决」= 误报信号**。当一份诊断把某个行业的**所有**成员都判死刑时，
   先怀疑引擎的字段/口径，而不是行业真的集体暴雷。反向也成立：整板块全部满分同样可疑。
2. **口径不一致是财报分析的头号 bug 源**。查任何比率异常时，先问三件事：
   ① 分子分母是**时点**还是**期间**？② 分母是**累计**还是**单季**？
   ③ 列表是**新→旧**还是**旧→新**？（本次 3 个 bug 分别命中这三问）
   **验证手段**：拿原始数据**手算一遍**再比对引擎输出。`19.49亿` 这类中间值能反推出
   引擎用了哪期、哪个因子（本次靠 `57.75 = 43.30 × 4/3` 反推出月份取错）。

### 已知未修 / 待用户决策

- **`ACCEPT_INVEST_CASH` / `ASSIGN_DIVIDEND_PORFIT` 两个字段在该数据源不存在**
  （确认过，不是写错名）→ 相关规则取到 0。**故意没动**：无法确定正确替代名，
  瞎改会引入新 bug。要修得先查东财原始字段表。
- **R002 与 R023 是重复规则** —— `tangmen_pailei_kb/rules_detection.json` 里
  rule_name / trigger_condition 完全相同，只有 severity 不同（medium / high），
  同一条事实被计两次。**属业务判断，留给用户决定是否合并**（不擅自删规则）。

## HTTP 接口（2026-09 实测）

### 元数据 / 行情（Sequoia-X 已接入这两个）

```bash
# 全市场列表 —— 申万三级行业 + 市值。实测 5482 只，含 l1/l2/l3/code/name/mcap/date/file
curl -s "http://127.0.0.1:8888/api/stocks"

# 实时行情（批量）—— 现价/涨跌幅/换手率/成交额/PE_TTM/PB/市值
curl -s "http://127.0.0.1:8888/api/realtime/quotes?codes=600519,002815"

# 实时行情（单只）
curl -s "http://127.0.0.1:8888/api/realtime/quote/600519"
```

`/api/stocks` 返回体形如：

```json
{"stocks":[{"file":"电子--元件--被动元件-300408.SZ-三环集团-20260328.csv",
            "l1":"电子","l2":"元件","l3":"被动元件",
            "code":"300408.SZ","name":"三环集团","date":"20260328","mcap":2515.87}],
 "taxonomy": {...}}
```

覆盖：**31 个申万一级 / 5482 只**（对比：新浪行业分类只 2978 只 / 48 个粗行业）。

### 基本面 / 估值 / 排雷（`verify.py` 已接入 `caibao/audit`；其余仍是富矿）

| 接口 | 用途 |
|---|---|
| `GET /api/stock?file=<文件名>` | 单只深度数据（meta 含申万行业/估值/ROE/分红/PE/PB） |
| `GET /api/stock/industry-compare?file=<文件名>` | **同业对比**（按申万行业自动找 peers + 对比指标） |
| `GET /api/stock/pb-history?file=...` | 10 年 PB 历史估值带 |
| `GET /api/caibao/audit?code=` / `?file=` | 唐门排雷审核结果 |
| `GET /api/caibao/fetch/{code}` | 抓取最新财报 |
| `GET /api/caibao/full/{code}` | 完整财报穿透（三大报表全科目） |
| `GET /api/findata/{secucode}` | 财务数据 |
| `POST /api/deepseek/analyze` | AI 个股深度分析 |
| `POST /api/deepseek/industry-audit` | AI 行业审计 |
| `POST /api/deepseek/industry-leader-audit` | AI 行业龙头审核 |
| `POST /api/deepseek/peer-compare` | AI 同业对比 |

单只 JSON 的 `meta` 关键字段（可用于估值筛选）：
`申万行业` `数据日期` `股票代码` `公司简称` `当前股价` `市值` `PE_TTM` `PB_LF`
`EPV_T0` `EPV_T0_投资收益率` `TC估值` `TC估值_投资收益率` `EPV_当期` `近3年分红率` `迄今分红率`
`累计分红` `累计募资` `注册资本` `ROE_PB` `公司简介` `证券债券汇总`

## 陷阱

1. **别把它的估值源当实时** —— `backend/data/` 的 JSON 是快照（文件名带日期，如 `20260328`），
   不是每日更新；只有 `/api/realtime/*` 是实时行情。
2. **`/api/stocks` 的 `code` 带交易所后缀**（`300408.SZ`），写进 Sequoia-X 的 `stock_meta`
   前必须 `split(".")[0]` 取 6 位数字。
3. **它是独立服务，不是 Sequoia-X 的一部分** —— 改它的代码属于另一个项目；
   集成只走 HTTP API，不要在 Sequoia-X 里 import 它的模块。
4. **端口占用/未启动会让 `sync_valuation.py` 直接失败** —— 脚本会打印明确提示，
   先按上面的命令确认服务在跑再重试。
