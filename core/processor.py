import pandas as pd
import os
import re
import logging
import numpy as np
from datetime import datetime

logger = logging.getLogger(__name__)

class SAPDataProcessor:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.dfs = {}

    def load_data(self):
        """Discovers and loads SAP CSV/XLSX exports with auto-detection."""
        files = os.listdir(self.data_dir)
        for f in files:
            path = os.path.join(self.data_dir, f)
            if 'CPU' in f: self.dfs["CPU"] = self._read_any(path)
            elif 'SQLPLAN' in f: self.dfs["SQL"] = self._read_any(path)
            elif 'LockWait' in f: self.dfs["LockWait"] = self._read_any(path)

    def _read_any(self, path):
        try:
            if path.endswith('.csv'):
                # Optimized for performance and Korean SAP exports
                encodings = ['utf-8', 'cp949', 'utf-16']
                for enc in encodings:
                    try:
                        # Try C engine first for speed
                        df = pd.read_csv(path, sep='\t', engine='c', quoting=3, encoding=enc)
                        if len(df.columns) >= 2:
                            logger.info(f"Successfully loaded {path} with {enc} using C engine")
                            return df
                    except Exception:
                        continue
                
                # Ultimate fallback: Python engine with auto-separator
                logger.warning(f"Falling back to Python engine for {path}")
                return pd.read_csv(path, sep=None, engine='python', quoting=3)
            return pd.read_excel(path)
        except Exception as e:
            logger.error(f"Error loading {path}: {e}")
            return pd.DataFrame()

    def _to_num(self, s):
        """Standard helper to clean SAP numeric strings."""
        return pd.to_numeric(str(s).replace(',', ''), errors='coerce')

    def clean_cpu_data(self):
        if "CPU" not in self.dfs: return None
        df = self.dfs["CPU"].copy()
        df.columns = [c.upper() for c in df.columns]
        
        mapping = {'HOST_CPU_UTILIZATION': 'CPU', 'CPU_ALLOCATION_LIMIT_PCT': 'CPU', 'CPU_UTILIZATION': 'CPU'}
        for k, v in mapping.items():
            if k in df.columns and v not in df.columns: df[v] = df[k]
        
        if 'CPU' in df.columns:
            df['CPU'] = df['CPU'].apply(self._to_num).fillna(0)
            
        if 'TIME' in df.columns:
            df['TIMESTAMP'] = pd.to_datetime(df['TIME'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')
        
        if 'MEMORY_USED' in df.columns and 'MEMORY_ALLOCATION_LIMIT' in df.columns:
            used = df['MEMORY_USED'].apply(self._to_num).fillna(0)
            lim = df['MEMORY_ALLOCATION_LIMIT'].apply(self._to_num).replace(0, 1).fillna(1)
            df['MEMORY_PCT'] = (used / lim) * 100
        elif 'MEM_USED_PCT' in df.columns:
            df['MEMORY_PCT'] = df['MEM_USED_PCT'].apply(self._to_num).fillna(0)

        # Apply Guideline: Filter 09:00 ~ 18:00 (KST)
        if 'TIMESTAMP' in df.columns:
            df = df.dropna(subset=['TIMESTAMP', 'CPU'])
            df = df[(df['TIMESTAMP'].dt.hour >= 9) & (df['TIMESTAMP'].dt.hour < 18)]

        return df

    def identify_peak_windows(self, cpu_df):
        """Hybrid Peak Windows: Using CPU P95 OR Lock Surge (>60s) to match other AI windows."""
        if cpu_df.empty: return []
        
        # 1. Strict CPU Signal (P95 Threshold based on RAW data exactly as requested)
        from datetime import timedelta
        thr_cpu = cpu_df['CPU'].quantile(0.95)
        peak_candidates = cpu_df[cpu_df["CPU"] >= thr_cpu].sort_values("TIMESTAMP")
        
        if peak_candidates.empty: return []

        windows = []
        current = None

        for _, row in peak_candidates.iterrows():
            if current is None:
                current = {
                    "start": row["TIMESTAMP"],
                    "end": row["TIMESTAMP"],
                    "cpus": [row["CPU"]]
                }
            elif (row["TIMESTAMP"] - current["end"]) <= timedelta(minutes=5):
                # ✅ 여기서 current["end"]가 '이전 Peak 시점'
                current["end"] = row["TIMESTAMP"]
                current["cpus"].append(row["CPU"])
            else:
                windows.append(current)
                current = {
                    "start": row["TIMESTAMP"],
                    "end": row["TIMESTAMP"],
                    "cpus": [row["CPU"]]
                }
        if current:
            windows.append(current)

        # 2. Add extra context (Reason) natively without Lock Wait
        final_windows = []

        for w in windows:
            end_ts = w['end'] + pd.Timedelta(seconds=59)
            avg_cpu = np.mean(w['cpus'])
            max_cpu = max(w['cpus'])
            
            reasons = [f"CPU({max_cpu:.1f}%)"]
                
            sample_count = len(w['cpus'])
            impact = (avg_cpu * sample_count)
            
            final_windows.append({
                'start': w['start'],
                'end': end_ts,
                'max_cpu': max_cpu,
                'avg_cpu': avg_cpu,
                'total_lock': 0, # Kept for API compatibility if needed elsewhere
                'impact': impact,
                'reason': ", ".join(reasons)[:50],
                'sample_count': sample_count
            })

        # Select the TOP 3 most impactful peak windows
        top_windows = sorted(final_windows, key=lambda x: x['impact'], reverse=True)[:3]
        return sorted(top_windows, key=lambda x: x['start'])

    def normalize_sql(self, sql):
        sql = str(sql).strip().upper()
        sql = re.sub(r'SAPHANADB\.', '', sql)
        sql = re.sub(r'\((?:\d+|PARTITION\s+\d+)\)', '', sql) 
        sql = re.sub(r'"', '', sql)
        sql = re.sub(r'\s+', ' ', sql)
        sql = re.sub(r'\'[^\']*\'', '?', sql) 
        sql = re.sub(r'\b\d+\b', '?', sql)    
        # Normalization focused on extracting structure
        return sql[:250].strip()

    def get_high_priority_analysis(self, windows, limit=5):
        if not windows: return None, None, None
        
        def guess_module_from_sql(sql_text):
            if not sql_text or pd.isna(sql_text): return ""
            sql = str(sql_text).upper()
            patterns = {
                'SD:매출/출하': ['VBRK', 'VBRP', 'LIKP', 'LIPS', 'VBAK', 'VBAP'],
                'MM:구매/자재': ['EKKO', 'EKPO', 'MARA', 'MARC', 'MSEG', 'MKPF'],
                'FI:회계/전표': ['BSEG', 'BKPF', 'ACCTIT', 'ACDOCA'],
                'CO:관리회계': ['COEP', 'COBK'],
                'PP:생산관리': ['AFKO', 'AFPO', 'PLKO', 'PLPO']
            }
            for module, tables in patterns.items():
                if any(t in sql for t in tables): return f"[{module}]"
            if sql.startswith('SELECT'): return "[조회성 쿼리]"
            if any(k in sql for k in ['INSERT', 'UPDATE', 'DELETE']): return "[변경성 작업]"
            return ""

        def get_label(row):
            # Guideline: Priority TCODE > ABAP_PROGRAM > APPLICATION_NAME > USER_NAME + SQL Hint
            for col in ['TCODE', 'ABAP_PROGRAM', 'APPLICATION_NAME', 'MODULE', 'APP_NAME']:
                val = row.get(col)
                if pd.isna(val) or not str(val).strip(): continue
                val_str = str(val).strip()
                # Filter out generic noise (Improved: skip '0' and numeric empty strings)
                if val_str.upper() not in ['UNKNOWN', 'ABAP:EEP', 'SAPHANADB', '-', 'N/A', 'NAN', 'NULL', 'GENERIC', '0', 'NONE']: 
                    return val_str
            
            # Fallback: User + SQL Hint
            user = str(row.get('USER_NAME', '')).strip()
            hint = guess_module_from_sql(row.get('SQL_TEXT', ''))
            
            if user and user.upper() not in ['SAPHANADB', 'UNKNOWN']:
                return f"{hint} User:{user}" if hint else f"User:{user}"
            
            return hint if hint else "Unknown (System/Internal)"

        # 1. SQL Execution Analysis (Global Top 5 across all peaks)
        top_sql = pd.DataFrame()
        if "SQL" in self.dfs:
            df = self.dfs["SQL"].copy()
            df.columns = [c.upper() for c in df.columns]
            
            # Preprocessing: Sort, Unique, and Calculate GLOBAL Deltas for cumulative data
            df = df.sort_values(['SQL_TEXT', 'LAST_EXEC_TS'])
            df = df.drop_duplicates(subset=['SQL_TEXT', 'LAST_EXEC_TS'])
            
            # Data is ALREADY delta-based per snapshot (Confirmed via raw grep analysis)
            # Just ensure they are numeric and map to target delta names
            num_cols = {
                'TOTAL_EXEC_TIME_SEC': 'TIME_DELTA',
                'TOTAL_EXECUTION_MEMORY_SIZE': 'MEM_DELTA',
                'EXEC_COUNT': 'COUNT_DELTA'
            }
            for src, target in num_cols.items():
                if src in df.columns:
                    df[target] = df[src].apply(self._to_num).fillna(0)
            
            # Ensure other metrics are numeric too
            for c in ['MAX_EXECUTION_MEMORY_SIZE', 'MAX_EXEC_TIME_SEC']:
                if c in df.columns:
                    df[c] = df[c].apply(self._to_num).fillna(0)
            
            # Standardize Column Names
            df.columns = [c.upper() for c in df.columns]
            col_map = {
                'EXEC_COUNT': 'TOTAL_EXEC_COUNT',
                'TOTAL_EXECUTION_MEMORY_SIZE': 'TOTAL_EXECUTION_MEMORY',
                'MAX_EXECUTION_MEMORY_SIZE': 'MAX_EXECUTION_MEMORY'
            }
            for k, v in col_map.items():
                if k in df.columns and v not in df.columns:
                    df[v] = df[k]
            
            df['TIMESTAMP'] = pd.to_datetime(df['LAST_EXEC_TS'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')
            for c in ['TOTAL_EXEC_TIME_SEC', 'TOTAL_EXEC_COUNT', 'TOTAL_EXECUTION_MEMORY', 'MAX_EXECUTION_MEMORY', 'MAX_EXEC_TIME_SEC', 'MAX_EXECUTION_MEMORY_SIZE']:
                if c in df.columns: df[c] = df[c].apply(self._to_num).fillna(0)

            # Prepare Delta data
            sql_df = df
            
        lock_df = pd.DataFrame()
        if "LockWait" in self.dfs:
            lock_df = self.dfs["LockWait"].copy()
            lock_df.columns = [c.upper() for c in lock_df.columns]
            lock_df['TIMESTAMP'] = pd.to_datetime(lock_df['LAST_EXEC_TS'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')
            for c in ['TOTAL_LOCK_WAIT_SEC', 'EXEC_COUNT', 'TOTAL_EXEC_MEM_MB', 'TOTAL_EXEC_TIME_SEC']:
                if c in lock_df.columns: lock_df[c] = lock_df[c].apply(self._to_num).fillna(0)
            lock_df['LOCK_DELTA'] = lock_df['TOTAL_LOCK_WAIT_SEC']

        # 2. Peak-based Unified Analysis
        unified_candidates = []
        for w in windows:
            # Buffer: 10 mins (Reduced from 30m to avoid noise) to ensure we catch relevant snapshots
            w_start = w['start']
            w_end_ext = w['end']
            p_label = f"{w['start'].strftime('%H:%M')}~{w['end'].strftime('%H:%M')}"
            
            # SQL candidates in this window
            w_sql = sql_df[(sql_df['TIMESTAMP'] >= w_start) & (sql_df['TIMESTAMP'] <= w_end_ext)].copy() if not sql_df.empty else pd.DataFrame()
            if not w_sql.empty:
                w_sql['PROGRAM_LABEL'] = w_sql.apply(get_label, axis=1)
                w_sql['SQL_LABEL'] = w_sql['SQL_TEXT'].apply(self.normalize_sql)
            
            # Lock candidates in this window 
            w_lock = lock_df[(lock_df['TIMESTAMP'] >= w_start) & (lock_df['TIMESTAMP'] <= w_end_ext)].copy() if not lock_df.empty else pd.DataFrame()
            if not w_lock.empty:
                w_lock['PROGRAM_LABEL'] = w_lock.apply(get_label, axis=1)
                w_lock['SQL_LABEL'] = w_lock['SQL_TEXT'].apply(self.normalize_sql)

            # Merge SQL and Lock metrics on the same logical transaction
            # Note: We take the SUM of impacts if there are multiple snapshots in the buffer
            if not w_sql.empty:
                g_sql = w_sql.groupby(['PROGRAM_LABEL', 'SQL_LABEL']).agg(
                    EXEC_TIME_win=('TIME_DELTA', 'sum'),
                    EXEC_COUNT_win=('COUNT_DELTA', 'sum'),
                    MEM_win=('MEM_DELTA', 'sum'),
                    MAX_MEM_win=('MAX_EXECUTION_MEMORY_SIZE', 'max'),
                    MAX_EXEC_TIME_win=('MAX_EXEC_TIME_SEC', 'max')
                ).reset_index()
                # Compute Averages
                g_sql['AVG_EXEC_TIME_win'] = g_sql['EXEC_TIME_win'] / g_sql['EXEC_COUNT_win'].replace(0, 1)
                g_sql['AVG_MEM_win'] = g_sql['MEM_win'] / g_sql['EXEC_COUNT_win'].replace(0, 1)
            else:
                g_sql = pd.DataFrame(columns=['PROGRAM_LABEL', 'SQL_LABEL', 'EXEC_TIME_win', 'EXEC_COUNT_win', 'MEM_win', 'MAX_MEM_win', 'MAX_EXEC_TIME_win', 'AVG_EXEC_TIME_win', 'AVG_MEM_win'])

            if not w_lock.empty:
                g_lock = w_lock.groupby(['PROGRAM_LABEL', 'SQL_LABEL']).agg(
                    LOCK_TIME_win=('LOCK_DELTA', 'sum'),
                    LOCK_COUNT_win=('EXEC_COUNT', 'sum'),
                    LOCK_EXEC_TIME_win=('TOTAL_EXEC_TIME_SEC', 'sum')
                ).reset_index()
            else:
                g_lock = pd.DataFrame(columns=['PROGRAM_LABEL', 'SQL_LABEL', 'LOCK_TIME_win', 'LOCK_COUNT_win', 'LOCK_EXEC_TIME_win'])
            
            # Outer Join to catch programs that might be in either or both
            merged = pd.merge(g_sql, g_lock, on=['PROGRAM_LABEL', 'SQL_LABEL'], how='outer').fillna(0)
            merged['PEAK_PERIOD'] = p_label
            
            # Unified Priority Formula (Sophisticated Multi-Factor Scoring)
            if not merged.empty:
                # 1) Derived Metrics
                merged['spike_ratio'] = (merged['MAX_EXEC_TIME_win'] / merged['AVG_EXEC_TIME_win'].replace(0, 1e-6)).clip(upper=10)
                merged['lock_ratio'] = (merged['LOCK_TIME_win'] / merged['LOCK_EXEC_TIME_win'].replace(0, 1e-6))
                merged['peak_share'] = merged['EXEC_TIME_win'] / merged['EXEC_TIME_win'].sum() if merged['EXEC_TIME_win'].sum() > 0 else 0
                
                # 2) Min-Max Normalization (Internal Window Context)
                norm_cols = ['MAX_MEM_win', 'MEM_win', 'spike_ratio', 'EXEC_COUNT_win', 'LOCK_TIME_win', 'lock_ratio']
                for col in norm_cols:
                    c_min, c_max = merged[col].min(), merged[col].max()
                    merged[f'norm_{col}'] = (merged[col] - c_min) / (c_max - c_min + 1e-9)
                
                # 3) Sub-Score Components
                merged['mem_score'] = 0.6 * merged['norm_MAX_MEM_win'] + 0.4 * merged['norm_MEM_win']
                merged['spike_score'] = merged['norm_spike_ratio']
                merged['chatty_score'] = (merged['norm_EXEC_COUNT_win'] * merged['AVG_EXEC_TIME_win']).rank(pct=True)
                merged['lock_score'] = 0.5 * merged['norm_LOCK_TIME_win'] + 0.5 * merged['norm_lock_ratio']
                
                # 4) Final Weighted PRIORITY (50/20/15/10/5)
                merged['PRIORITY'] = (0.50 * merged['peak_share'] +
                                     0.20 * merged['mem_score'] +
                                     0.15 * merged['spike_score'] +
                                     0.10 * merged['chatty_score'] +
                                     0.05 * merged['lock_score'])

            
            # Final filtering: Noise reduction (Apply window-specific rules)
            if not merged.empty:
                # 1. First, define what constitutes 'Active SQL' in this window
                is_active_sql = (merged['EXEC_TIME_win'] > 0) | (merged['EXEC_COUNT_win'] > 0)
                
                # 2. Apply the 'Big 3' baselines only to active SQL
                active_sql_df = merged[is_active_sql]
                if not active_sql_df.empty:
                    # Quantile 0.9 of total execution time within THIS window's active set
                    time_q90 = active_sql_df['EXEC_TIME_win'].quantile(0.9)
                    is_top_10_time = (merged['EXEC_TIME_win'] >= time_q90) & is_active_sql
                    is_slow_avg = (merged['AVG_EXEC_TIME_win'] >= 1.0) & is_active_sql
                    is_heavy_mem = (merged['MAX_MEM_win'] >= 512 * 1024 * 1024) & is_active_sql
                    
                    # Also include any item that has significant Lock Wait impact even if SQL is 0?
                    # No, this loop is for top_sql. Pure lock items go to top_locks later via full_peak_df.
                    merged['is_sql_candidate'] = is_top_10_time | is_slow_avg | is_heavy_mem
                else:
                    merged['is_sql_candidate'] = False
                
                unified_candidates.append(merged)

        if unified_candidates:
            full_peak_df = pd.concat(unified_candidates)
            
            def map_rca(r):
                if r['LOCK_TIME_win'] > 100 or "NRIV" in str(r['SQL_LABEL']): return "동시성(락): 테이블 경합"
                if r['MAX_MEM_win'] > 512*1024*1024: return "대량 집계 / 전체 스캔"
                return "고부하 트랜잭션 도출"

            full_peak_df['CAUSE'] = full_peak_df.apply(map_rca, axis=1)
            full_peak_df['ACTION'] = full_peak_df.apply(lambda r: "NRIV 버퍼링 및 직렬화" if "락" in r['CAUSE'] else "SQL 튜닝 및 인덱스 최적화", axis=1)
            
            # Renaming for report compatibility
            full_peak_df.rename(columns={
                'EXEC_TIME_win': 'TOTAL_EXEC_TIME_peak',
                'EXEC_COUNT_win': 'EXEC_COUNT_peak',
                'MEM_win': 'TOTAL_MEM_peak',
                'MAX_MEM_win': 'MAX_MEM_peak',
                'AVG_MEM_win': 'AVG_MEM_peak',
                'MAX_EXEC_TIME_win': 'MAX_EXEC_TIME_peak',
                'AVG_EXEC_TIME_win': 'AVG_EXEC_TIME_peak',
                'LOCK_TIME_win': 'TOTAL_LOCK_WAIT_SEC_peak',
                'LOCK_COUNT_win': 'LOCK_COUNT',
                'LOCK_EXEC_TIME_win': 'TOTAL_EXEC_TIME_SEC_lock'
            }, inplace=True)
            
            # Calculate Lock Wait Ratio strictly based on window totals
            full_peak_df['LOCK_WAIT_RATIO_peak'] = full_peak_df['TOTAL_LOCK_WAIT_SEC_peak'] / full_peak_df['TOTAL_EXEC_TIME_SEC_lock'].replace(0, 1e-6)
            
            # 1. Top SQL: Top 3 per peak window as requested for visual cell merging
            # We already tagged candidates inside the loop relative to their windows
            top_sql = full_peak_df[full_peak_df.get('is_sql_candidate', False) == True].sort_values(['PEAK_PERIOD', 'PRIORITY'], ascending=[True, False]).groupby('PEAK_PERIOD').head(3).reset_index(drop=True)
            
            # 2. Top Locks: Based on strict AND Baselines
            lock_thr = full_peak_df['TOTAL_LOCK_WAIT_SEC_peak'].quantile(0.9)
            is_positive_lock = (full_peak_df['TOTAL_LOCK_WAIT_SEC_peak'] > 0)
            is_high_ratio_lock = (full_peak_df['LOCK_WAIT_RATIO_peak'] >= 0.3)
            is_top_10_lock = (full_peak_df['TOTAL_LOCK_WAIT_SEC_peak'] >= lock_thr)
            
            is_lock_candidate = is_positive_lock & is_high_ratio_lock & is_top_10_lock
            top_locks = full_peak_df[is_lock_candidate].copy()
            top_locks = top_locks.sort_values(['PEAK_PERIOD', 'TOTAL_LOCK_WAIT_SEC_peak'], ascending=[True, False]).groupby('PEAK_PERIOD').head(3).reset_index(drop=True)

            # 3. Global Top 10: For Section 8 (Comprehensive Diagnosis)
            # Strategy: Strictly use only the items ALREADY displayed in Section 6 and Section 7
            technical_candidates = pd.concat([top_sql, top_locks]).copy()
            
            if not technical_candidates.empty:
                # Group by Program+SQL to get the best representative (highest priority) for each unique transaction seen in 6/7
                global_top = technical_candidates.sort_values('PRIORITY', ascending=False).groupby(['PROGRAM_LABEL','SQL_LABEL']).head(1).head(10).reset_index(drop=True)
            else:
                global_top = pd.DataFrame()
            
            return top_sql, top_locks, global_top
        else:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
            
        return top_sql, top_locks
    def get_summary_stats(self, cpu_df):
        if cpu_df is None or cpu_df.empty: return {}
        return {
            'date': cpu_df['TIMESTAMP'].min().strftime('%Y-%m-%d'),
            'cpu_avg': cpu_df['CPU'].mean(), 'cpu_max': cpu_df['CPU'].max(),
            'cpu_p95': cpu_df['CPU'].quantile(0.95),
            'mem_avg_pct': cpu_df['MEMORY_PCT'].mean() if 'MEMORY_PCT' in cpu_df.columns else 0,
            'high_load_count': len(cpu_df[cpu_df['CPU'] >= 80]),
            'sample_count': len(cpu_df)
        }

