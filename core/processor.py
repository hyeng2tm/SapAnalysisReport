import pandas as pd
import os
import re
import logging
import numpy as np
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

class SAPDataProcessor:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.dfs = {}
        self.loaded_file_paths = []
        self.lock_ratio_threshold = self._get_env_float("LOCK_WAIT_RATIO_THRESHOLD", 0.3)
        self.lock_top_quantile = self._get_env_float("LOCK_WAIT_TOP_QUANTILE", 0.9)
        self.lock_top_per_peak = max(1, int(self._get_env_float("LOCK_WAIT_TOP_PER_PEAK", 3)))
        self.peak_weight_max = self._get_env_float("PEAK_WEIGHT_MAX", 0.45)
        self.peak_weight_avg = self._get_env_float("PEAK_WEIGHT_AVG", 0.35)
        self.peak_weight_duration = self._get_env_float("PEAK_WEIGHT_DURATION", 0.20)
        self.peak_reliability_samples = max(1, int(self._get_env_float("PEAK_RELIABILITY_SAMPLES", 5)))
        self.peak_window_limit = self._get_env_int("PEAK_WINDOW_LIMIT", 0)
        self.peak_min_samples = max(1, self._get_env_int("PEAK_MIN_SAMPLES", 5))
        self.peak_min_duration_minutes = max(0, int(self._get_env_int("PEAK_MIN_DURATION_MINUTES", 10)))
        # Heavy memory threshold (bytes): default 1GB, configurable via PEAK_HEAVY_MEM_GB_THRESHOLD
        heavy_mem_gb = max(0.1, float(self._get_env_float("PEAK_HEAVY_MEM_GB_THRESHOLD", 1.0)))
        self.peak_heavy_mem_threshold = int(heavy_mem_gb * 1024 * 1024 * 1024)
        # Slow SQL average execution time threshold (seconds): default 10s, configurable via PEAK_SLOW_AVG_SEC_THRESHOLD
        self.peak_slow_avg_sec_threshold = max(0.1, float(self._get_env_float("PEAK_SLOW_AVG_SEC_THRESHOLD", 10.0)))

    def _get_env_float(self, key, default):
        raw = os.getenv(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    def _get_env_int(self, key, default):
        raw = os.getenv(key)
        if raw is None:
            return default
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return default

    def load_data(self):
        """Discovers and loads SAP CSV/XLSX exports with auto-detection."""
        files = os.listdir(self.data_dir)
        for f in files:
            path = os.path.join(self.data_dir, f)
            if not os.path.isfile(path):
                continue

            source_key = None
            if 'CPU' in f:
                source_key = "CPU"
            elif 'SQLPLAN' in f:
                source_key = "SQL"
            elif 'LockWait' in f:
                source_key = "LockWait"

            if source_key is None:
                continue

            loaded_df = self._read_any(path)
            self.dfs[source_key] = loaded_df
            if loaded_df is not None and not loaded_df.empty:
                self.loaded_file_paths.append(path)

    def archive_loaded_files(self, analysis_date=None, archive_root=None):
        """Moves analyzed source files into archive/<analysis_date>/ and returns moved paths."""
        if not self.loaded_file_paths:
            return []

        base_archive_root = archive_root
        if not base_archive_root:
            data_dir_abs = os.path.abspath(self.data_dir)
            base_archive_root = os.path.join(os.path.dirname(data_dir_abs), "archive")

        date_folder = analysis_date or datetime.now().strftime("%Y-%m-%d")
        target_dir = os.path.join(base_archive_root, date_folder)
        os.makedirs(target_dir, exist_ok=True)

        moved_paths = []
        for src_path in dict.fromkeys(self.loaded_file_paths):
            if not os.path.exists(src_path):
                continue

            dest_path = os.path.join(target_dir, os.path.basename(src_path))
            if os.path.abspath(src_path) == os.path.abspath(dest_path):
                continue

            if os.path.exists(dest_path):
                os.remove(dest_path)

            shutil.move(src_path, dest_path)
            moved_paths.append(dest_path)

        self.loaded_file_paths = []
        return moved_paths

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
        elif 'TIMESTAMP' in df.columns:
            df['TIMESTAMP'] = pd.to_datetime(df['TIMESTAMP'], errors='coerce')
        elif 'LAST_EXEC_TS' in df.columns:
            df['TIMESTAMP'] = pd.to_datetime(df['LAST_EXEC_TS'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')
        else:
            # Keep a stable schema for downstream consumers even when source time columns are absent.
            df['TIMESTAMP'] = pd.NaT
        
        if 'MEMORY_USED' in df.columns and 'MEMORY_ALLOCATION_LIMIT' in df.columns:
            used = df['MEMORY_USED'].apply(self._to_num).fillna(0)
            lim = df['MEMORY_ALLOCATION_LIMIT'].apply(self._to_num).replace(0, 1).fillna(1)
            df['MEMORY_PCT'] = (used / lim) * 100
        elif 'MEM_USED_PCT' in df.columns:
            df['MEMORY_PCT'] = df['MEM_USED_PCT'].apply(self._to_num).fillna(0)

        # Apply Guideline: Filter 09:00 ~ 18:00 (KST)
        #if 'TIMESTAMP' in df.columns:
        #    df = df.dropna(subset=['TIMESTAMP', 'CPU'])
        #    df = df[(df['TIMESTAMP'].dt.hour >= 9) & (df['TIMESTAMP'].dt.hour < 18)]

        return df

    def identify_peak_windows(self, cpu_df):
        """Hybrid Peak Windows: Using CPU P95 OR Lock Surge (>60s) to match other AI windows."""
        if cpu_df.empty or 'CPU' not in cpu_df.columns or 'TIMESTAMP' not in cpu_df.columns:
            return []

        cpu_df = cpu_df.dropna(subset=['TIMESTAMP', 'CPU'])
        if cpu_df.empty:
            return []
        
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
            min_cpu = min(w['cpus'])
            
            reasons = [f"CPU({max_cpu:.1f}%)"]
                
            sample_count = len(w['cpus'])
            impact = (avg_cpu * sample_count)
            
            final_windows.append({
                'start': w['start'],
                'end': end_ts,
                'max_cpu': max_cpu,
                'min_cpu': min_cpu,
                'avg_cpu': avg_cpu,
                'total_lock': 0, # Kept for API compatibility if needed elsewhere
                'impact': impact,
                'reason': ", ".join(reasons)[:50],
                'sample_count': sample_count
            })

        # Select by mixed score to balance sustained load and short spikes.
        # Score = weighted(max, avg, duration) with a reliability adjustment for very short windows.
        if not final_windows:
            return []

        weight_sum = self.peak_weight_max + self.peak_weight_avg + self.peak_weight_duration
        if weight_sum <= 0:
            w_max, w_avg, w_dur = 0.45, 0.35, 0.20
        else:
            w_max = self.peak_weight_max / weight_sum
            w_avg = self.peak_weight_avg / weight_sum
            w_dur = self.peak_weight_duration / weight_sum

        max_values = np.array([w['max_cpu'] for w in final_windows], dtype=float)
        avg_values = np.array([w['avg_cpu'] for w in final_windows], dtype=float)
        dur_values = np.array([w['sample_count'] for w in final_windows], dtype=float)

        def minmax(values):
            v_min = np.min(values)
            v_max = np.max(values)
            if v_max - v_min < 1e-9:
                return np.zeros(len(values), dtype=float)
            return (values - v_min) / (v_max - v_min)

        norm_max = minmax(max_values)
        norm_avg = minmax(avg_values)
        norm_dur = minmax(dur_values)

        for idx, w in enumerate(final_windows):
            raw_score = (w_max * norm_max[idx]) + (w_avg * norm_avg[idx]) + (w_dur * norm_dur[idx])
            reliability = min(1.0, w['sample_count'] / float(self.peak_reliability_samples))
            w['peak_score'] = raw_score * (0.6 + 0.4 * reliability)

        ranked_windows = sorted(final_windows, key=lambda x: x['peak_score'], reverse=True)

        # Filter: keep only sustained peak windows (sample_count >= threshold)
        # to exclude single/double-point transient spikes, and minimum duration threshold.
        filtered_windows = []
        for w in ranked_windows:
            if w['sample_count'] >= self.peak_min_samples:
                duration_minutes = (w['end'] - w['start']).total_seconds() / 60.0
                if duration_minutes >= self.peak_min_duration_minutes:
                    filtered_windows.append(w)
        if not filtered_windows:
            filtered_windows = ranked_windows[:1] if ranked_windows else []

        if self.peak_window_limit > 0:
            filtered_windows = filtered_windows[:self.peak_window_limit]
        return sorted(filtered_windows, key=lambda x: x['start'])

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
        sql_df = pd.DataFrame()
        if "SQL" in self.dfs:
            df = self.dfs["SQL"].copy()
            df.columns = [c.upper() for c in df.columns]

            if 'SQL_TEXT' not in df.columns or 'LAST_EXEC_TS' not in df.columns:
                df = pd.DataFrame()
            else:
                # Ensure required metric columns exist to avoid KeyError during groupby/aggregation.
                for required in ['TOTAL_EXEC_TIME_SEC', 'TOTAL_EXECUTION_MEMORY_SIZE', 'EXEC_COUNT', 'MAX_EXECUTION_MEMORY_SIZE', 'MAX_EXEC_TIME_SEC']:
                    if required not in df.columns:
                        df[required] = 0

            if not df.empty:
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
                    if c in df.columns:
                        df[c] = df[c].apply(self._to_num).fillna(0)

                # Prepare Delta data
                sql_df = df
            
        lock_df = pd.DataFrame()
        if "LockWait" in self.dfs:
            lock_df = self.dfs["LockWait"].copy()
            lock_df.columns = [c.upper() for c in lock_df.columns]
            if 'LAST_EXEC_TS' in lock_df.columns:
                lock_df['TIMESTAMP'] = pd.to_datetime(lock_df['LAST_EXEC_TS'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')
                for c in ['TOTAL_LOCK_WAIT_SEC', 'EXEC_COUNT', 'TOTAL_EXEC_MEM_MB', 'TOTAL_EXEC_TIME_SEC']:
                    if c in lock_df.columns:
                        lock_df[c] = lock_df[c].apply(self._to_num).fillna(0)
                if 'TOTAL_LOCK_WAIT_SEC' in lock_df.columns:
                    lock_df['LOCK_DELTA'] = lock_df['TOTAL_LOCK_WAIT_SEC']
                else:
                    lock_df['LOCK_DELTA'] = 0
            else:
                lock_df = pd.DataFrame()

        # 2. Peak-based Unified Analysis
        unified_candidates = []
        for w in windows:
            w_start = w['start']
            w_end_ext = w['end']
            p_label = f"{w['start'].strftime('%H:%M')}~{w['end'].strftime('%H:%M')}"
            
            # SQL candidates in this window
            w_sql = sql_df[(sql_df['TIMESTAMP'] >= w_start) & (sql_df['TIMESTAMP'] <= w_end_ext)].copy() if not sql_df.empty else pd.DataFrame()
            
            # Fallback: if no data in exact window, expand buffer to ±30 minutes to catch snapshot data
            if w_sql.empty and not sql_df.empty:
                from datetime import timedelta
                w_start_ext = w_start - timedelta(minutes=30)
                w_end_ext_wide = w_end_ext + timedelta(minutes=30)
                w_sql = sql_df[(sql_df['TIMESTAMP'] >= w_start_ext) & (sql_df['TIMESTAMP'] <= w_end_ext_wide)].copy()
            
            if not w_sql.empty:
                w_sql['PROGRAM_LABEL'] = w_sql.apply(get_label, axis=1)
                w_sql['SQL_LABEL'] = w_sql['SQL_TEXT'].apply(self.normalize_sql)
            
            # Lock candidates in this window 
            w_lock = lock_df[(lock_df['TIMESTAMP'] >= w_start) & (lock_df['TIMESTAMP'] <= w_end_ext)].copy() if not lock_df.empty else pd.DataFrame()
            
            # Fallback: if no data in exact window, expand buffer to ±30 minutes
            if w_lock.empty and not lock_df.empty:
                from datetime import timedelta
                w_start_ext = w_start - timedelta(minutes=30)
                w_end_ext_wide = w_end_ext + timedelta(minutes=30)
                w_lock = lock_df[(lock_df['TIMESTAMP'] >= w_start_ext) & (lock_df['TIMESTAMP'] <= w_end_ext_wide)].copy()
            
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
                    is_slow_avg = (merged['AVG_EXEC_TIME_win'] >= self.peak_slow_avg_sec_threshold) & is_active_sql
                    is_heavy_mem = (merged['MAX_MEM_win'] >= self.peak_heavy_mem_threshold) & is_active_sql
                    
                    # Selection logic:
                    # 1. Top 10% by total execution time (high-volume queries), OR
                    # 2. Both slow average AND heavy memory (resource-intensive queries)
                    # This filters out system metadata queries that are slow but use minimal memory.
                    merged['is_sql_candidate'] = is_top_10_time | (is_slow_avg & is_heavy_mem)
                else:
                    merged['is_sql_candidate'] = False
                
                unified_candidates.append(merged)

        if unified_candidates:
            full_peak_df = pd.concat(unified_candidates)
            
            def map_rca(r):
                if r['LOCK_TIME_win'] > 100 or "NRIV" in str(r['SQL_LABEL']): return "동시성(락): 테이블 경합"
                if r['MAX_MEM_win'] > 1024*1024*1024: return "대량 집계 / 전체 스캔"
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
            lock_quantile = min(1.0, max(0.0, self.lock_top_quantile))
            lock_thr = full_peak_df['TOTAL_LOCK_WAIT_SEC_peak'].quantile(lock_quantile)
            is_positive_lock = (full_peak_df['TOTAL_LOCK_WAIT_SEC_peak'] > 0)
            is_high_ratio_lock = (full_peak_df['LOCK_WAIT_RATIO_peak'] >= self.lock_ratio_threshold)
            is_top_10_lock = (full_peak_df['TOTAL_LOCK_WAIT_SEC_peak'] >= lock_thr)
            
            is_active = (full_peak_df['EXEC_COUNT_peak'] > 0) | (full_peak_df['TOTAL_MEM_peak'] > 0)
            is_lock_candidate = is_positive_lock & is_high_ratio_lock & is_top_10_lock & is_active
            top_locks = full_peak_df[is_lock_candidate].copy()
            top_locks = top_locks.sort_values(['PEAK_PERIOD', 'TOTAL_LOCK_WAIT_SEC_peak'], ascending=[True, False]).groupby('PEAK_PERIOD').head(self.lock_top_per_peak).reset_index(drop=True)
            lock_selection_mode = "strict"

            # Fallback: if strict lock baselines remove all rows, still report strongest lock events.
            if top_locks.empty:
                relaxed_lock_df = full_peak_df[is_positive_lock].copy()
                if not relaxed_lock_df.empty:
                    top_locks = (
                        relaxed_lock_df
                        .sort_values(['PEAK_PERIOD', 'TOTAL_LOCK_WAIT_SEC_peak', 'LOCK_WAIT_RATIO_peak'], ascending=[True, False, False])
                        .groupby('PEAK_PERIOD')
                        .head(self.lock_top_per_peak)
                        .reset_index(drop=True)
                    )
                    lock_selection_mode = "relaxed"
                else:
                    lock_selection_mode = "none"

            top_locks.attrs['selection_mode'] = lock_selection_mode
            top_locks.attrs['ratio_threshold'] = self.lock_ratio_threshold
            top_locks.attrs['quantile_threshold'] = lock_quantile
            top_locks.attrs['top_per_peak'] = self.lock_top_per_peak

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
        date_value = datetime.now().strftime('%Y-%m-%d')
        if 'TIMESTAMP' in cpu_df.columns:
            ts_series = pd.to_datetime(cpu_df['TIMESTAMP'], errors='coerce').dropna()
            if not ts_series.empty:
                date_value = ts_series.min().strftime('%Y-%m-%d')
        return {
            'date': date_value,
            'cpu_avg': cpu_df['CPU'].mean(), 'cpu_max': cpu_df['CPU'].max(), 'cpu_min': cpu_df['CPU'].min(),
            'cpu_p95': cpu_df['CPU'].quantile(0.95),
            'mem_avg_pct': cpu_df['MEMORY_PCT'].mean() if 'MEMORY_PCT' in cpu_df.columns else 0,
            'high_load_count': len(cpu_df[cpu_df['CPU'] >= 80]),
            'sample_count': len(cpu_df)
        }

