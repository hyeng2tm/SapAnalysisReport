from core.processor import SAPDataProcessor
import pandas as pd

processor = SAPDataProcessor('./data')
processor.load_data()
cpu_df = processor.clean_cpu_data()
windows = processor.identify_peak_windows(cpu_df)

# Get high priority analysis for identifying problematic programs
top_sql, top_locks, global_top = processor.get_high_priority_analysis(windows)

print('\n' + '='*80)
print(f'Total Peak Windows Detected: {len(windows)}')
print('='*80 + '\n')

for i, w in enumerate(windows, 1):
    start_str = w['start'].strftime('%Y-%m-%d %H:%M:%S')
    end_str = w['end'].strftime('%Y-%m-%d %H:%M:%S')
    peak_label = f"{w['start'].strftime('%H:%M')}~{w['end'].strftime('%H:%M')}"
    duration = (w['end'] - w['start']).total_seconds() / 60
    
    print(f'[Window {i:2d}] {start_str} ~ {end_str}')
    print(f'  Duration: {duration:.1f} min')
    print(f'  Max CPU: {w["max_cpu"]:6.2f}%  |  Avg CPU: {w["avg_cpu"]:6.2f}%  |  Samples: {w["sample_count"]}')
    print(f'  Peak Score: {w["peak_score"]:.4f}')
    
    # Find problematic programs for this window
    window_sql = top_sql[top_sql['PEAK_PERIOD'] == peak_label] if not top_sql.empty else pd.DataFrame()
    window_locks = top_locks[top_locks['PEAK_PERIOD'] == peak_label] if not top_locks.empty else pd.DataFrame()
    
    if not window_sql.empty:
        print(f'  ┣ Top SQL Programs:')
        for idx, row in window_sql.iterrows():
            prog = row.get('PROGRAM_LABEL', 'Unknown')
            exec_time = row.get('TOTAL_EXEC_TIME_peak', 0)
            priority = row.get('PRIORITY', 0)
            cause = row.get('CAUSE', 'Unknown')
            print(f'     ├─ {prog}: {exec_time:.1f}sec (Priority: {priority:.4f}, Cause: {cause})')
    
    if not window_locks.empty:
        print(f'  ┗ Top Lock Programs:')
        for idx, row in window_locks.iterrows():
            prog = row.get('PROGRAM_LABEL', 'Unknown')
            lock_time = row.get('TOTAL_LOCK_WAIT_SEC_peak', 0)
            lock_ratio = row.get('LOCK_WAIT_RATIO_peak', 0)
            print(f'     └─ {prog}: {lock_time:.1f}sec lock wait ({lock_ratio:.2%})')
    
    if window_sql.empty and window_locks.empty:
        print(f'  (No significant SQL/Lock issues detected)')
    
    print()
