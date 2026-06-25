from core.processor import SAPDataProcessor

p = SAPDataProcessor('./data')
p.load_data()
cpu_df = p.clean_cpu_data()
windows = p.identify_peak_windows(cpu_df)
print('windows:', len(windows))

top_sql, top_locks, global_top = p.get_high_priority_analysis(windows)
print('top_sql rows:', 0 if top_sql is None else len(top_sql))
print('top_locks rows:', 0 if top_locks is None else len(top_locks))
print('global_top rows:', 0 if global_top is None else len(global_top))

if top_locks is not None and not top_locks.empty:
    cols = [c for c in ['PEAK_PERIOD','PROGRAM_LABEL','TOTAL_LOCK_WAIT_SEC_peak','LOCK_WAIT_RATIO_peak','LOCK_COUNT'] if c in top_locks.columns]
    print(top_locks[cols].head(10).to_string(index=False))
else:
    print('top_locks is empty')
