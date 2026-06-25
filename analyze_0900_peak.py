from core.processor import SAPDataProcessor
import pandas as pd

processor = SAPDataProcessor('./data')
processor.load_data()
cpu_df = processor.clean_cpu_data()
windows = processor.identify_peak_windows(cpu_df)

# Get the 09:00 window (Window 1)
w = windows[0]
peak_label = f"{w['start'].strftime('%H:%M')}~{w['end'].strftime('%H:%M')}"
w_start = w['start']
w_end_ext = w['end']

print(f"\n{'='*80}")
print(f"09시대 Peak Window 상세 분석")
print(f"기간: {w['start'].strftime('%Y-%m-%d %H:%M:%S')} ~ {w['end'].strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*80}\n")

# Load SQL and Lock data
processor.load_data()
sql_df = pd.DataFrame()
if "SQLPLAN" in processor.dfs:
    sql_df = processor.dfs["SQLPLAN"].copy()
    sql_df.columns = [c.upper() for c in sql_df.columns]
    if 'LAST_EXEC_TS' in sql_df.columns:
        sql_df['TIMESTAMP'] = pd.to_datetime(sql_df['LAST_EXEC_TS'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')

lock_df = pd.DataFrame()
if "LockWait" in processor.dfs:
    lock_df = processor.dfs["LockWait"].copy()
    lock_df.columns = [c.upper() for c in lock_df.columns]
    if 'LAST_EXEC_TS' in lock_df.columns:
        lock_df['TIMESTAMP'] = pd.to_datetime(lock_df['LAST_EXEC_TS'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')

# Get window data
w_sql = sql_df[(sql_df['TIMESTAMP'] >= w_start) & (sql_df['TIMESTAMP'] <= w_end_ext)].copy() if not sql_df.empty else pd.DataFrame()
w_lock = lock_df[(lock_df['TIMESTAMP'] >= w_start) & (lock_df['TIMESTAMP'] <= w_end_ext)].copy() if not lock_df.empty else pd.DataFrame()

print(f"SQL 데이터: {len(w_sql)} rows")
if not w_sql.empty:
    print(f"  Columns: {list(w_sql.columns)}")
    numeric_cols = ['TOTAL_EXEC_TIME_SEC', 'TOTAL_EXECUTION_MEMORY_SIZE', 'EXEC_COUNT', 'MAX_EXECUTION_MEMORY_SIZE', 'MAX_EXEC_TIME_SEC']
    for col in numeric_cols:
        if col in w_sql.columns:
            total = w_sql[col].apply(lambda x: float(str(x).replace(',', '')) if x else 0).sum()
            print(f"  {col}: {total:.1f}")
    
    print("\n  Top SQL by EXEC_COUNT:")
    if 'EXEC_COUNT' in w_sql.columns:
        top = w_sql.nlargest(5, 'EXEC_COUNT')[['LAST_EXEC_TS', 'EXEC_COUNT', 'TOTAL_EXEC_TIME_SEC']].head()
        for idx, row in top.iterrows():
            print(f"    {row.get('EXEC_COUNT', 0)}: {row.get('TOTAL_EXEC_TIME_SEC', 0)}sec")

print(f"\nLock 데이터: {len(w_lock)} rows")
if not w_lock.empty:
    print(f"  Columns: {list(w_lock.columns)}")
    if 'TOTAL_LOCK_WAIT_SEC' in w_lock.columns:
        total_lock = w_lock['TOTAL_LOCK_WAIT_SEC'].apply(lambda x: float(str(x).replace(',', '')) if x else 0).sum()
        print(f"  Total Lock Wait: {total_lock:.1f}sec")

print("\n" + "="*80)
print("필터링 기준 확인:")
print(f"  PEAK_MIN_SAMPLES: {processor.peak_min_samples}")
print(f"  PEAK_MIN_DURATION_MINUTES: {processor.peak_min_duration_minutes}")
print(f"  Window Sample Count: {w['sample_count']}")
print(f"  Window Duration: {(w['end'] - w['start']).total_seconds()/60:.1f} min")
print("="*80)
