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
        avail_cols = [c for c in ['PEAK_PERIOD', 'PROGRAM_LABEL', 'SQL_LABEL', 'peak_share', 'EXEC_COUNT'] if c in top_sql.columns]
        peak_sql_summary = top_sql[avail_cols].head(15).to_string()
        # Explicitly extract the top business impacts for AI
        top_cpu_label = str(top_sql.iloc[0]['PROGRAM_LABEL'])
    else:
        peak_sql_summary = "No significant SQL activity detected during peaks."

    if top_locks is not None and not top_locks.empty:
        lock_summary = top_locks.head(10).to_string()
        top_lock_label = str(top_locks.iloc[0]['PROGRAM_LABEL'])
    else:
        lock_summary = "No heavy locking detected."
        
    ai_insights = ""
    try:
        analyzer = SAPAIAnalyzer()
        ai_insights = analyzer.analyze_performance(stats, windows, peak_sql_summary, lock_summary, top_cpu_label, top_lock_label)
    except Exception as e:
        logger.error(f"AI Analysis failed, proceeding with technical data only: {e}")
        ai_insights = {
            'executive_summary': "시스템 부하 및 정체 현상이 감지되었습니다. 상세 데이터 테이블을 참조하십시오.",
            'detailed_analysis': "AI 실시간 분석이 일시적으로 제한되어 기술적 수치 위주로 리포트를 제공합니다.",
            'recommendations': ["상위 SQL 및 Lock 대기 항목에 대한 즉각적인 튜닝 권고", "시스템 모니터링 강화"]
        }
    
    # 3. Generate Report
    # NEW: AI Specific Actions for Section 7
    if global_top is not None and not global_top.empty:
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
    
    # Use dynamic naming: SAP_Analysis_{DataDate}_{RunTime}.pdf
    data_date = stats.get('date', datetime.now().strftime('%Y-%m-%d'))
    run_dt = datetime.now().strftime('%H%M%S')
    report_title = f"SAP_Analysis_Report_{data_date}_{run_dt}.pdf"
    
    # Pass the dynamic filename to the reporter
    report_path = reporter.create_pdf_report(analysis_data, stats, windows, top_sql, top_locks, global_top, output_filename=report_title)
    logger.info(f"Technical Deep Dive Report complete: {report_path}")
    
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
