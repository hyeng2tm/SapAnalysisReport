from core.processor import SAPDataProcessor
import pandas as pd

processor = SAPDataProcessor('./data')
processor.load_data()
cpu_df = processor.clean_cpu_data()
windows = processor.identify_peak_windows(cpu_df)
top_sql, _, _ = processor.get_high_priority_analysis(windows)

# Focus on Window 1
window_1_data = top_sql[top_sql['PEAK_PERIOD'] == '08:58~09:11'].copy()

print("\n" + "="*80)
print("Window 1 (08:58~09:11) SQL 필터링 분석")
print("="*80)

print(f"\n전체 SQL 수: {len(window_1_data)}")
print(f"필터링 기준:")
print(f"  - is_top_10_time: 총 실행시간 >= Q90 (상위 10%)")
print(f"  - is_slow_avg: 평균 >= {processor.peak_slow_avg_sec_threshold:.1f}초")
print(f"  - is_heavy_mem: 최대 메모리 >= 1GB")

print(f"\n세부 분석:")
for idx, row in window_1_data.iterrows():
    prog = row['PROGRAM_LABEL']
    total_time = row['TOTAL_EXEC_TIME_peak']
    avg_time = row['AVG_EXEC_TIME_peak']
    max_mem = row['MAX_MEM_peak'] / (1024**3)
    
    print(f"\n▶ {prog}")
    print(f"  총 실행시간: {total_time:.1f}s")
    print(f"  평균 실행시간: {avg_time:.1f}s (기준: {processor.peak_slow_avg_sec_threshold:.1f}s) → {avg_time >= processor.peak_slow_avg_sec_threshold}")
    print(f"  최대 메모리: {max_mem:.2f}GB (기준: 1.0GB) → {max_mem >= 1.0}")
    print(f"  → is_slow_avg OR is_heavy_mem? {(avg_time >= processor.peak_slow_avg_sec_threshold) or (max_mem >= 1.0)}")
    print(f"     (is_top_10_time는 Q90 계산으로 결정)")
