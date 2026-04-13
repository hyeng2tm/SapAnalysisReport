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
    peak_sql = processor.get_peak_sql(windows)
    top_locks = processor.get_top_locks()
    
    # 2. AI Analysis
    analyzer = SAPAIAnalyzer()
    
    # Summaries for AI prompt
    peak_sql_summary = peak_sql[['ABAP_PROGRAM', 'TOTAL_EXEC_TIME_SEC', 'SQL_TEXT', 'peak_window']].to_string() if peak_sql is not None else "None"
    lock_summary = top_locks[['TOTAL_LOCK_WAIT_SEC', 'ACCESSED_TABLES', 'SQL_TEXT']].head(5).to_string() if top_locks is not None else "None"
    
    ai_insights = analyzer.analyze_performance(stats, windows, peak_sql_summary, lock_summary)
    
    # 3. Generate Report
    reporter = SAPReporter(output_dir)
    chart_path = reporter.generate_dual_axis_chart(cpu_df, windows)
    
    analysis_data = {
        'chart_path': chart_path,
        'ai_insights': ai_insights
    }
    
    # Generate the report focusing on CSV data only
    report_path = reporter.create_pdf_report(analysis_data, stats, windows, peak_sql, top_locks)
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
