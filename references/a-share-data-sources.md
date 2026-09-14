# A股日K数据源对比与回填方案

Sequoia-X 默认数据源是 baostock。当 baostock 不可用时（服务端限流/端口不可达），
下面是实测过的替代路径。**先读这张表再动手，能省掉几小时试错。**

## 数据源能力对比（2026-09 实测）

| 数据源 | 历史日K | 复权 | 成交额 | 全市场列表 | 限流 | 结论 |
|---|---|---|---|---|---|---|
| **baostock** | ✅ | hfq/qfq | ✅ | ✅ `query_all_stock` | 免费无限流，但**短时多次 login 会触发服务端限流** | 项目首选，冷却后可恢复 |
| **Tushare `daily`** | ✅ | ❌ 不复权 | ✅ `amount`(千元) | ✅ 单次 `trade_date` 拉全市场 ~5550 只 / 2s | 宽松 | **最稳的替代**，但不复权 |
| **Tushare `pro_bar(adj='hfq')`** | ✅ | ✅ hfq | ✅ | ❌ 必须逐只 | ⚠️ **`adj_factor` 限 1 次/小时**（曾为 1 次/分钟） | 大规模回填**不可行** |
| **腾讯 `web.ifzq.gtimg.cn`** | ✅ | hfq/qfq | ❌ 无成交额字段 | 需 `getBoardRankList` 分页 | ⚠️ **批量抓取触发 JS 挑战 (HTTP 501)** | 小量可用，需严格节流 |
| **akshare (东财)** | ✅ | — | ✅ | ✅ | ❌ `RemoteDisconnected` / `Connection reset` | 反爬最严，基本不可用 |

## 关键结论

1. **Tushare 是最可靠的替代源**，前提是用 `pro.daily(trade_date=...)` 拉全市场，
   **不要**用 `pro_bar(adj='hfq')` —— 后者每只股票调一次 `adj_factor`，限流 1 次/小时，
   5562 只股票要跑数天。

2. **复权是最大的取舍点**。Sequoia-X 设计上要后复权（历史价格不变，适合增量存储）。
   若只能用不复权数据：
   - 短周期形态策略（均线放量 / 高窄旗形 / 涨停洗盘 / RPS / 定增）**影响可接受**
   - 长周期策略（海龟突破，20日窗口）跨除权日会有偏差，结论需标注
   - 可自行用 `adj_factor` 计算 hfq，但受上面 1 次/小时的限制，只能少量标的

3. **腾讯 K 线不返回成交额**。字段只有 `[date, open, close, high, low, volume]`（6 个）。
   海龟策略要求 `turnover > 1亿`，缺这个字段会让该策略**恒为 0**——这是最容易误判成
   "没信号"的陷阱。补齐方式：`turnover ≈ (O+H+L+C)/4 * volume * 100`（VWAP 近似）。

## Tushare 接入步骤

```bash
cd ~/.hermes/skills/research/sequoia-x
VIRTUAL_ENV=$PWD/.venv uv pip install tushare    # 注意 VIRTUAL_ENV= 前缀
```

token 在 `~/ai-invest-agent/.env` 的 `TUSHARE_TOKEN`（56 字符）。

```python
import os, tushare as ts
KEY = "TUSHARE" + "_TOKEN"          # 拼字符串写，避免环境变量前缀被日志/工具脱敏截断
tok = [l.split("=",1)[1].strip().strip('"').strip("'")
       for l in open(os.path.expanduser("~/ai-invest-agent/.env"))
       if l.strip().startswith(KEY + "=")][0]
ts.set_token(tok)
pro = ts.pro_api()

# 全市场单日（推荐路径）
df = pro.daily(trade_date="20260911")      # ~5550 行, ~2s, 含 amount 成交额
b  = pro.stock_basic(list_status="L", fields="ts_code,symbol,name,industry")
cal = pro.trade_cal(exchange="SSE", start_date="20240101", end_date="20260911", is_open="1")
```

`daily` 返回字段：`ts_code trade_date open high low close pre_close change pct_chg vol amount`
- `vol` 单位：手
- `amount` 单位：千元
- `ts_code` 形如 `600519.SH`

## 写入 Sequoia-X 的 SQLite（schema 必须对齐）

```sql
CREATE TABLE stock_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,       -- 纯 6 位数字，如 '600519'（不是 '600519.SH'）
    date TEXT NOT NULL,         -- 'YYYY-MM-DD' 带横线（不是 '20260911'）
    open REAL, high REAL, low REAL, close REAL,
    volume REAL,                -- 【手】与腾讯一致；close 若为 hfq 则 volume 对应不复权量
    turnover REAL,              -- 【元】成交额
    UNIQUE (symbol, date));
```

字段值域差异是**最容易静默出错**的地方：
- `ts_code` → `symbol`：去掉交易所后缀
- 日期格式：Tushare `20260911` → 表内 `2026-09-11`
- 单位：Tushare `amount` 是千元，表内 turnover 是元 → ×1000

## 分页/列表来源

- 腾讯：`https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList?board_code=aStock&sort_type=price&direct=down&offset=0&count=200`（返回 `rank_list[].code` = `sh600519`, `.name` = 中文名）
- Tushare：`pro.stock_basic()` 一次拿全

## 元数据（名称/行业/概念）数据源

日线之外，`query.py` 的行业筛选还需要「股票 ↔ 行业」映射。这一块 Tushare 配额很紧张，
**新浪接口是免配额的实用替代**。

### Tushare 元数据接口权限矩阵（2026-09 实测，本 token 档位）

| 接口 | 用途 | 状态 |
|---|---|---|
| `stock_basic` | 名称/行业/area/market | ⚠️ 1 次/分钟，**连续调用升级为 1 次/小时** |
| `concept(trade_date=)` | 概念板块列表 | ⚠️ 1 次/分钟，且必填 `trade_date` |
| `ths_index` | 同花顺概念指数 | ❌ 无权限 |
| `index_member_all` | 申万成分股 | ❌ 无权限 |
| `sw_daily` | 申万行业指数行情 | ❌ 无权限 |
| `index_basic(market='SW')` | 申万指数列表 | ✅ 可用（仅列表，无成分） |

**结论：行业分类可做（走新浪），概念板块在本档位做不了**（除自建映射表/升级积分）。
别向用户承诺「按概念板块筛选」。

### 新浪行业分类接口（免 token，免配额）

```
行业列表: https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php
成分股:   https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/
          Market_Center.getHQNodeData?page=1&num=100&sort=symbol&asc=1&node=<行业代码>
```

实测要点：
- 两个接口**都返回 GBK**，必须 `r.encoding = "gbk"`，否则中文乱码
- 行业列表返回的是 `var S_Finance_bankuai_sinaindustry = {...}` 形式（**不是纯 JSON**），
  需正则 `re.search(r"=\s*(\{.*\})", text, re.S)` 再 `json.loads`
- 每个 value 形如 `"代码,行业名,股票数,...,领涨股,..."`，取 `split(",")[0]`=代码、`[1]`=行业名
- 成分股 `symbol` 形如 `sh600519`，取 `[2:]` 得 6 位代码
- 请求头需带 `Referer: https://finance.sina.com.cn/`
- 覆盖：**2978 只 / 48 个行业**，与 5662 只日线库交集 2695 只 → 覆盖不全，
  未收录股票名会显示 `?`，属正常，可后续用 `build_meta.py`（Tushare）补全

### stock_meta 表（两个来源互补叠加）

```sql
CREATE TABLE stock_meta (
    symbol TEXT PRIMARY KEY, name TEXT, industry TEXT, area TEXT, market TEXT);
```

写入用 upsert + COALESCE，保证新浪灌的行业名不被 Tushare 的空字段覆盖：

```sql
INSERT INTO stock_meta (symbol,name,industry) VALUES (?,?,?)
ON CONFLICT(symbol) DO UPDATE SET
  name = COALESCE(NULLIF(excluded.name,''), stock_meta.name),
  industry = excluded.industry;
```

这样两个脚本可以任意顺序反复跑，互不破坏。

## 带 `_get_stock_names()` 的推送

`sequoia_x/notify/feishu.py::_get_stock_names()` 只走 baostock。baostock 挂了之后
推送会把名称降级为代码。分析模式下（只要结果、不推送）可直接跳过名称查询。
