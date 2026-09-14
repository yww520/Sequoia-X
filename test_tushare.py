import os
import sys
import time

import tushare as ts

KEY = "TUSHARE" + "_TOKEN"
token = None
for line in open(os.path.expanduser("~/ai-invest-agent/.env"), encoding="utf-8"):
    line = line.strip()
    if line.startswith(KEY + "="):
        token = line.split("=", 1)[1].strip().strip('"').strip("'")
        break

if not token:
    sys.stderr.write("no token\n")
    raise SystemExit(1)

ts.set_token(token)
pro = ts.pro_api()

# 1) pro_bar 后复权 —— Sequoia-X 需要 hfq
try:
    df = ts.pro_bar(ts_code="600519.SH", adj="hfq", start_date="20240101", end_date="20260911")
    print("pro_bar hfq rows:", len(df), "cols:", list(df.columns))
    print(df.head(2).to_string())
    print(df.tail(2).to_string())
except Exception as e:
    sys.stderr.write(f"pro_bar FAIL {type(e).__name__}: {str(e)[:400]}\n")

# 2) 限流测试
print("\n=== 限流测试 (all-market daily x5) ===")
for i in range(5):
    t0 = time.time()
    try:
        d = pro.daily(trade_date="20260911")
        print(f"  call {i}: rows={len(d)} {time.time() - t0:.2f}s")
    except Exception as e:
        print(f"  call {i}: FAIL {str(e)[:150]}")
    time.sleep(0.5)

# 3) 股票基础信息
try:
    b = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,industry")
    print("\nstock_basic rows:", len(b))
    print(b.head(3).to_string())
except Exception as e:
    sys.stderr.write(f"stock_basic FAIL: {str(e)[:300]}\n")

# 4) 交易日历
try:
    cal = pro.trade_cal(exchange="SSE", start_date="20260101", end_date="20260914", is_open="1")
    print("\ntrade_cal rows:", len(cal))
    print(cal.tail(2).to_string())
except Exception as e:
    sys.stderr.write(f"trade_cal FAIL: {str(e)[:300]}\n")
