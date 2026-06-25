from core.processor import SAPDataProcessor
import pandas as pd

processor = SAPDataProcessor('./data')
processor.load_data()
cpu_df = processor.clean_cpu_data()
windows = processor.identify_peak_windows(cpu_df)
top_sql, top_locks, global_top = processor.get_high_priority_analysis(windows)

print("\n" + "="*80)
print("메모리 사용량 분석 (MAX_EXECUTION_MEMORY_SIZE)")
print("="*80)

if top_sql is not None and not top_sql.empty:
    # Rename for clarity
    analysis_df = top_sql[['PEAK_PERIOD', 'PROGRAM_LABEL', 'TOTAL_EXEC_TIME_peak', 'MAX_MEM_peak']].copy()
    
    # Convert to GB for display
    analysis_df['MAX_MEM_GB'] = analysis_df['MAX_MEM_peak'] / (1024**3)
    analysis_df['MAX_MEM_MB'] = analysis_df['MAX_MEM_peak'] / (1024**2)
    
    # Sort by memory usage
    analysis_df = analysis_df.sort_values('MAX_MEM_peak', ascending=False)
    
    print(f"\n전체 SQL ({len(analysis_df)}개):")
    print(analysis_df[['PEAK_PERIOD', 'PROGRAM_LABEL', 'MAX_MEM_GB', 'MAX_MEM_MB']].head(20).to_string(index=False))
    
    # Statistics
    print(f"\n메모리 통계:")
    print(f"  최대: {analysis_df['MAX_MEM_GB'].max():.2f} GB")
    print(f"  최소: {analysis_df['MAX_MEM_GB'].min():.2f} GB")
    print(f"  평균: {analysis_df['MAX_MEM_GB'].mean():.2f} GB")
    print(f"  중위: {analysis_df['MAX_MEM_GB'].median():.2f} GB")
    
    # 1GB 이상 SQL 개수
    gb_1_count = (analysis_df['MAX_MEM_peak'] >= 1024**3).sum()
    print(f"\n1GB 이상: {gb_1_count}개")
    
    # 100MB 이상 SQL 개수
    mb_100_count = (analysis_df['MAX_MEM_peak'] >= 100 * 1024**2).sum()
    print(f"100MB 이상: {mb_100_count}개")
    
    # 실제 필터링되는 SQL
    print(f"\n현재 필터 기준 (1GB 이상):")
    filtered = analysis_df[analysis_df['MAX_MEM_peak'] >= 1024**3]
    if not filtered.empty:
        print(filtered[['PEAK_PERIOD', 'PROGRAM_LABEL', 'MAX_MEM_GB']].to_string(index=False))
    else:
        print("(1GB 이상인 SQL 없음)")
