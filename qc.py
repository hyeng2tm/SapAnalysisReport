import os
import argparse
import logging
from datetime import datetime
from dotenv import load_dotenv

from core import processor
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

if __name__ == "__qc__":
    parser = argparse.ArgumentParser(description="Professional SAP Load Analysis Tool")
    parser.add_argument("--data", default="./data", help="Directory containing SAP CSV files")
    parser.add_argument("--output", default="./reports", help="Directory to save reports")
    parser.add_argument("--email", help="Recipient email address")
    
    args = parser.parse_args()
    
    cpu_df = processor.clean_cpu_data()

    thr_cpu = cpu_df['CPU'].quantile(0.95)
    print(f"P95 임계값: {thr_cpu:.1f}%")

    # Peak 후보 전체 확인
    from datetime import timedelta
    import numpy as np

    peak_candidates = cpu_df[cpu_df["CPU"] >= thr_cpu].sort_values("TIMESTAMP")
    print(f"\nPeak 후보 총 {len(peak_candidates)}개:")
    print(peak_candidates[['TIMESTAMP', 'CPU']].to_string())

    # Window 병합 결과 전체 확인 (Top 3 자르기 전)
    windows = []
    current = None
    for _, row in peak_candidates.iterrows():
        if current is None:
            current = {"start": row["TIMESTAMP"], "end": row["TIMESTAMP"], "cpus": [row["CPU"]]}
        elif (row["TIMESTAMP"] - current["end"]) <= timedelta(minutes=5):
            current["end"] = row["TIMESTAMP"]
            current["cpus"].append(row["CPU"])
        else:
            windows.append(current)
            current = {"start": row["TIMESTAMP"], "end": row["TIMESTAMP"], "cpus": [row["CPU"]]}
    if current:
        windows.append(current)

    print(f"\n병합된 Window 총 {len(windows)}개 (Top 3 자르기 전):")
    for w in windows:
        avg = np.mean(w['cpus'])
        impact = avg * len(w['cpus'])
        print(f"  {w['start'].strftime('%H:%M')} ~ {w['end'].strftime('%H:%M')} | avg={avg:.1f}% | samples={len(w['cpus'])} | impact={impact:.1f}")
