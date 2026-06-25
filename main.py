import os
import argparse
import logging
from datetime import datetime
from dotenv import load_dotenv

from core.processor import SAPDataProcessor
from core.analyzer import SAPAIAnalyzer
from core.reporter import SAPReporter
from core.mailer import SAPMailer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Suppress verbose internal logs and warnings
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
for logger_name in ['fontTools', 'absl', 'grpc', 'google', 'google.api_core', 'google.cloud']:
    logging.getLogger(logger_name).setLevel(logging.ERROR)


def _build_peak_sql_prompt_summary(top_sql):
    if top_sql is None or top_sql.empty:
        return "No significant SQL activity detected during peaks."

    # Map internal report columns to canonical metric names for AI reasoning.
    preferred_cols = [
        'PEAK_PERIOD',
        'PROGRAM_LABEL',
        'SQL_LABEL',
        'EXEC_COUNT_peak',
        'TOTAL_EXEC_TIME_peak',
        'AVG_EXEC_TIME_peak',
        'MAX_EXEC_TIME_peak',
        'TOTAL_MEM_peak',
        'MAX_MEM_peak',
        'CAUSE',
        'PRIORITY',
    ]
    avail_cols = [c for c in preferred_cols if c in top_sql.columns]
    if not avail_cols:
        return top_sql.head(10).to_string(index=False)

    ai_df = top_sql[avail_cols].copy()
    ai_df = ai_df.rename(
        columns={
            'EXEC_COUNT_peak': 'EXECUTION_COUNT',
            'TOTAL_EXEC_TIME_peak': 'TOTAL_EXEC_TIME_SEC',
            'AVG_EXEC_TIME_peak': 'AVG_EXEC_TIME_SEC',
            'MAX_EXEC_TIME_peak': 'MAX_EXEC_TIME_SEC',
            'TOTAL_MEM_peak': 'TOTAL_EXECUTION_MEMORY_SIZE',
            'MAX_MEM_peak': 'MAX_EXECUTION_MEMORY_SIZE',
        }
    )

    # Preserve peak order from incoming rows, then apply priority ordering in each peak window.
    peak_order = []
    if 'PEAK_PERIOD' in ai_df.columns:
        for peak in ai_df['PEAK_PERIOD'].astype(str).tolist():
            if peak not in peak_order:
                peak_order.append(peak)

    if 'PRIORITY' in ai_df.columns:
        ai_df['PRIORITY'] = ai_df['PRIORITY'].fillna(0.0).astype(float)
    if 'TOTAL_EXEC_TIME_SEC' in ai_df.columns:
        ai_df['TOTAL_EXEC_TIME_SEC'] = ai_df['TOTAL_EXEC_TIME_SEC'].fillna(0.0).astype(float)

    sort_cols = []
    asc = []
    if 'PEAK_PERIOD' in ai_df.columns:
        ai_df['PEAK_PERIOD'] = ai_df['PEAK_PERIOD'].astype(str)
        peak_rank_map = {name: idx for idx, name in enumerate(peak_order)}
        ai_df['__peak_rank'] = ai_df['PEAK_PERIOD'].map(lambda v: peak_rank_map.get(v, 10**6))
        sort_cols.append('__peak_rank')
        asc.append(True)
    if 'PRIORITY' in ai_df.columns:
        sort_cols.append('PRIORITY')
        asc.append(False)
    if 'TOTAL_EXEC_TIME_SEC' in ai_df.columns:
        sort_cols.append('TOTAL_EXEC_TIME_SEC')
        asc.append(False)
    if sort_cols:
        ai_df = ai_df.sort_values(sort_cols, ascending=asc).reset_index(drop=True)

    if 'PEAK_PERIOD' in ai_df.columns:
        ai_df['WINDOW_RANK'] = ai_df.groupby('PEAK_PERIOD').cumcount() + 1
    else:
        ai_df['WINDOW_RANK'] = range(1, len(ai_df) + 1)

    # Stabilize numeric rendering to improve model parsing reliability.
    for c in ['TOTAL_EXEC_TIME_SEC', 'AVG_EXEC_TIME_SEC', 'MAX_EXEC_TIME_SEC']:
        if c in ai_df.columns:
            ai_df[c] = ai_df[c].fillna(0).map(lambda v: f"{float(v):.4f}")
    for c in ['EXECUTION_COUNT', 'WINDOW_RANK']:
        if c in ai_df.columns:
            ai_df[c] = ai_df[c].fillna(0).map(lambda v: f"{int(v):d}")
    for c in ['TOTAL_EXECUTION_MEMORY_SIZE', 'MAX_EXECUTION_MEMORY_SIZE']:
        if c in ai_df.columns:
            ai_df[c] = ai_df[c].fillna(0).map(lambda v: f"{float(v):.0f}")
    if 'PRIORITY' in ai_df.columns:
        ai_df['PRIORITY'] = ai_df['PRIORITY'].fillna(0).map(lambda v: f"{float(v):.4f}")

    if '__peak_rank' in ai_df.columns:
        ai_df = ai_df.drop(columns=['__peak_rank'])

    summary_parts = [
        "[정량 SQL 지표: 시간대별 영향 프로그램 우선순위 (단위: sec, memory는 bytes)]",
        "",
    ]

    # Group by peak window for clarity
    if 'PEAK_PERIOD' in ai_df.columns:
        for peak_period in ai_df['PEAK_PERIOD'].unique():
            window_data = ai_df[ai_df['PEAK_PERIOD'] == peak_period]
            summary_parts.append(f"◆ Peak Window: {peak_period}")
            summary_parts.append(window_data.to_string(index=False))
            summary_parts.append("")
    else:
        summary_parts.append(ai_df.head(15).to_string(index=False))

    if 'TOTAL_EXEC_TIME_SEC' in ai_df.columns:
        total_exec_time = ai_df['TOTAL_EXEC_TIME_SEC'].astype(float).sum()
        summary_parts.append(f"[전체 합계] TOTAL_EXEC_TIME_SEC={total_exec_time:.4f}sec")
    if 'EXECUTION_COUNT' in ai_df.columns:
        total_exec_count = ai_df['EXECUTION_COUNT'].astype(float).sum()
        summary_parts.append(f"[전체 합계] EXECUTION_COUNT={int(total_exec_count)}")

    return "\n".join(summary_parts)


def _build_lock_prompt_summary(top_locks):
    if top_locks is None or top_locks.empty:
        return "No heavy locking detected."

    preferred_cols = [
        'PEAK_PERIOD',
        'PROGRAM_LABEL',
        'SQL_LABEL',
        'TOTAL_LOCK_WAIT_SEC_peak',
        'LOCK_WAIT_RATIO_peak',
        'LOCK_COUNT',
        'TOTAL_EXEC_TIME_SEC_lock',
    ]
    avail_cols = [c for c in preferred_cols if c in top_locks.columns]
    if not avail_cols:
        return top_locks.head(10).to_string(index=False)

    lock_df = top_locks[avail_cols].copy()
    lock_df = lock_df.rename(
        columns={
            'SQL_LABEL': 'SQL_TEXT',
            'TOTAL_LOCK_WAIT_SEC_peak': 'TOTAL_LOCK_WAIT_SEC',
            'LOCK_WAIT_RATIO_peak': 'LOCK_WAIT_RATIO',
        }
    )

    if 'TOTAL_LOCK_WAIT_SEC' in lock_df.columns:
        lock_df['TOTAL_LOCK_WAIT_SEC'] = lock_df['TOTAL_LOCK_WAIT_SEC'].fillna(0).map(lambda v: f"{float(v):.4f}")
    if 'LOCK_WAIT_RATIO' in lock_df.columns:
        lock_df['LOCK_WAIT_RATIO'] = lock_df['LOCK_WAIT_RATIO'].fillna(0).map(lambda v: f"{float(v):.6f}")
    if 'LOCK_COUNT' in lock_df.columns:
        lock_df['LOCK_COUNT'] = lock_df['LOCK_COUNT'].fillna(0).map(lambda v: f"{int(v):d}")
    if 'TOTAL_EXEC_TIME_SEC_lock' in lock_df.columns:
        lock_df['TOTAL_EXEC_TIME_SEC_lock'] = lock_df['TOTAL_EXEC_TIME_SEC_lock'].fillna(0).map(lambda v: f"{float(v):.4f}")

    summary_parts = [
        "[정량 Lock Wait 지표: Peak Window별 분석 (단위: sec, ratio는 0~1)]",
        "",
    ]

    # Group by peak window for clarity
    if 'PEAK_PERIOD' in lock_df.columns:
        for peak_period in lock_df['PEAK_PERIOD'].unique():
            window_data = lock_df[lock_df['PEAK_PERIOD'] == peak_period]
            summary_parts.append(f"◆ Peak Window: {peak_period}")
            summary_parts.append(window_data.to_string(index=False))
            summary_parts.append("")
    else:
        summary_parts.append(lock_df.head(15).to_string(index=False))

    if 'TOTAL_LOCK_WAIT_SEC' in lock_df.columns:
        total_lock_wait = lock_df['TOTAL_LOCK_WAIT_SEC'].astype(float).sum()
        summary_parts.append(f"[전체 합계] TOTAL_LOCK_WAIT_SEC={total_lock_wait:.4f}sec")

    return "\n".join(summary_parts)

def run_analysis(data_dir, output_dir, recipient_email=None):
    """Orchestrates the technical deep-dive SAP analysis and reporting flow."""
    logger.info(f"Starting Professional SAP Load Analysis for directory: {data_dir}")
    
    # 1. Process Data
    processor = SAPDataProcessor(data_dir)
    processor.load_data()
    
    cpu_df = processor.clean_cpu_data()
    if cpu_df is None or cpu_df.empty:
        logger.error("No valid CPU data found. Analysis aborted.")
        return

    windows = processor.identify_peak_windows(cpu_df)
    stats = processor.get_summary_stats(cpu_df)
    
    # NEW: Advanced Priority Analysis (CPU/Mem & Locks)
    top_sql, top_locks, global_top = processor.get_high_priority_analysis(windows)
    
    # 2. AI Analysis
    top_cpu_label = "N/A"
    top_lock_label = "N/A"
    
    if top_sql is not None and not top_sql.empty:
        peak_sql_summary = _build_peak_sql_prompt_summary(top_sql)
        # Explicitly extract the top business impacts for AI
        top_cpu_label = str(top_sql.iloc[0]['PROGRAM_LABEL'])
    else:
        peak_sql_summary = "No significant SQL activity detected during peaks."

    if top_locks is not None and not top_locks.empty:
        lock_summary = _build_lock_prompt_summary(top_locks)
        top_lock_label = str(top_locks.iloc[0]['PROGRAM_LABEL'])
    else:
        lock_summary = "No heavy locking detected."
        
    ai_insights = ""
    analyzer = None
    try:
        analyzer = SAPAIAnalyzer()
        ai_insights = analyzer.analyze_performance(stats, windows, peak_sql_summary, lock_summary, top_cpu_label, top_lock_label)
        logger.info(f"\n{'='*80}")
        logger.info(f"[AI INSIGHTS LENGTH] {len(ai_insights) if ai_insights else 0} chars")
        logger.info(f"[AI INSIGHTS PREVIEW (first 1000 chars)]:")
        logger.info(f"{ai_insights[:1000] if ai_insights else 'EMPTY'}")
        logger.info(f"{'='*80}\n")
    except Exception as e:
        logger.error(f"AI Analysis failed, proceeding with technical data only: {e}")
        import traceback
        logger.error(traceback.format_exc())
        ai_insights = """1. Summary (요약):
시스템에서 부하 및 정체 현상이 감지되었습니다. 상세 데이터 테이블을 참조하십시오.

3. 차트 해석:
AI 분석이 일시적으로 제한되어 기술적 수치 위주로 리포트를 제공합니다.

    5. 부하 시간대 영향 SQL 및 프로그램 분석:
상위 SQL 및 Lock 대기 항목의 상세 분석은 데이터 테이블을 참조하십시오.

    6. 서비스 대기(Lock Wait) 분석:
    Lock 대기 항목과 동시성 리스크는 상세 테이블 기준으로 확인이 필요합니다.

7. 종합 진단 및 운영 시사점:
    [부하 원인 유형]:
    배치성 또는 동시성 부하 가능성이 있어 추가 확인이 필요합니다.

    [개선 포인트]:
    상위 SQL 튜닝, 배치 시간 분산, Lock 경합 완화 조치를 우선 검토하십시오.

    [최종 진단]:
    지속적인 모니터링과 성능 최적화가 필요합니다."""
    
    # 3. Generate Report
    # NEW: AI Specific Actions for Section 7
    if analyzer is not None and global_top is not None and not global_top.empty:
        specific_actions = analyzer.generate_specific_actions(global_top)
        if specific_actions:
            global_top['ACTION'] = specific_actions

    reporter = SAPReporter(output_dir)
    chart_path = reporter.generate_unified_axis_chart(cpu_df, windows)
    
    analysis_data = {
        'chart_path': chart_path,
        'ai_insights': ai_insights,
        'top_locks': top_locks
    }
    
    logger.info(f"\n{'='*80}")
    logger.info("[ANALYSIS DATA PREPARED FOR PDF]")
    logger.info(f"  chart_path: {chart_path}")
    logger.info(f"  ai_insights type: {type(ai_insights)}")
    logger.info(f"  ai_insights length: {len(ai_insights) if isinstance(ai_insights, str) else 'N/A'}")
    logger.info(f"  ai_insights (first 500 chars): {str(ai_insights)[:500]}")
    logger.info(f"{'='*80}\n")
    
    # Use dynamic naming: SAP_Analysis_{DataDate}_{RunTime}.pdf
    data_date = stats.get('date', datetime.now().strftime('%Y-%m-%d'))
    run_dt = datetime.now().strftime('%H%M%S')
    report_title = f"SAP_Analysis_Report_{data_date}_{run_dt}.pdf"
    
    # Pass the dynamic filename to the reporter
    report_path = reporter.create_pdf_report(analysis_data, stats, windows, top_sql, top_locks, global_top, output_filename=report_title)
    logger.info(f"Technical Deep Dive Report complete: {report_path}")

    # Archive source files after analysis
    moved_files = processor.archive_loaded_files(analysis_date=data_date)
    if moved_files:
        logger.info(f"Archived {len(moved_files)} analyzed source file(s) to archive/{data_date}")
        for moved in moved_files:
            logger.info(f"  - {moved}")
    
    # 4. Notify (if recipient and credentials provided)
    if recipient_email:
        mailer = SAPMailer()
        mailer.send_report(recipient_email, report_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Professional SAP Load Analysis Tool")
    parser.add_argument("--data", default="./data", help="Directory containing SAP CSV files")
    parser.add_argument("--output", default="./reports", help="Directory to save reports")
    parser.add_argument("--email", help="Recipient email address")
    
    args = parser.parse_args()
    
    # Create directories if they don't exist
    os.makedirs(args.output, exist_ok=True)
    
    run_analysis(args.data, args.output, args.email)
