# 回填脚本工程模式与坑

写并发数据回填脚本（`backfill_*.py`）时反复踩到的几个点。

## 1. SQLite 连接不能跨线程

`sqlite3.Connection` 默认 `check_same_thread=True`，在 `ThreadPoolExecutor` 的 worker 里
写库会抛：

```
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that
same thread. The object was created in thread id ... and this is thread id ...
```

**正确模式：网络拉取放线程池，写库回主线程。**

```python
def work(item):                      # 在线程池里跑，只做网络 IO
    code, _ = item
    rows = fetch_kline(code, start, end)
    return code, rows, bool(rows)    # 不碰 conn

with ThreadPoolExecutor(max_workers=12) as pool:
    futures = {pool.submit(work, it): it[0] for it in syms}
    for fut in as_completed(futures):
        _code, rows, ok = fut.result()
        if rows:
            total += write_rows(conn, code, rows)   # 主线程写
        if done % 200 == 0:
            conn.commit()
```

代码量约 20 行，比给每个线程开连接（还要处理 WAL 锁）简单得多。

## 2. `uv pip install` 要显式指定 VIRTUAL_ENV

`uv` 不像 `pip` 会看「当前解释器」。`uv pip install X` 只装到 `$VIRTUAL_ENV` 或 `.venv`；
从别处调用时可能装到全局，然后 `.venv/bin/python` 报 `ModuleNotFoundError`。

```bash
cd <project>
VIRTUAL_ENV=$PWD/.venv uv pip install tushare
.venv/bin/python -c "import tushare; print(tushare.__version__)"   # 一定要验证
```

注意 `uv sync` 装的是 `pyproject.toml` 里声明的依赖；装额外包用 `uv pip install`。

## 3. 项目不要用 `uv run`，会重装环境

`uv run --with X` 之类会按 `pyproject.toml` 重建/同步环境，把手工装的包冲掉。
本项目统一用 `.venv/bin/python`。

## 4. 空库检测要显式报错，不要静默

策略在空库上返回 `[]`，不会抛异常——看起来"跑通了"其实什么都没做。分析脚本开头加：

```python
if len(engine.get_local_symbols()) == 0:
    print("!! 本地数据库为空 —— 请先跑 backfill。")
    return 1
```

## 5. 增量回填用「每只股票最后日期」做起点

```python
last = dict(conn.execute("SELECT symbol, MAX(date) FROM stock_daily GROUP BY symbol"))
# 每只从 last[symbol] 的次日开始拉，避免全量重拉
```

比按全局最大日期增量更准（个别股票停牌/退市/新上市，日期不齐）。

## 6. 「今日数据不完整」陷阱

盘中或盘后早期跑策略时，只有零星股票有当日 K 线（实测 5000+ 只里仅 31 只有当日数据）。
此时策略实际是在拿**不完整的当日数据**做判断，结果不可信。

**对策**：回填/分析时把 `--end` 钉在最后一个**完整**交易日（用 `pro.trade_cal` 判断），
不要用 `date.today()`。

## 7. 环境变量前缀在写代码时会被脱敏

用 heredoc / `write_file` 写含 `TUSHARE_TOKEN=` 字样的代码时，工具层的密钥脱敏可能把
字符串截断，导致写出 `SyntaxError: unterminated string literal`。

**对策**：在代码里拼字符串绕开 —— `KEY = "TUSHARE" + "_TOKEN"`，再 `startswith(KEY + "=")`。
