import pandas as pd
import os
import logging
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SAPDataProcessor:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        # Try to find files dynamically if possible, else fallback to hardcoded
        self.files = {
            "CPU": "2026-03-04CPUEXPORT.csv",
            "LockWait": "2026-03-04LockWaitEXPORT.csv",
            "SQL": "2026-03-04SQLPLANCAHEEXPORT.csv"
        }
        self.dfs = {}

    def load_data(self):
        """Loads all CSV files from the data directory."""
        for key, filename in self.files.items():
            path = os.path.join(self.data_dir, filename)
            if not os.path.exists(path):
                # Try to look for any file ending with the suffix if specific date fails
                suffix = filename.split('4')[-1] if '4' in filename else filename
                possible_files = [f for f in os.listdir(self.data_dir) if f.endswith(suffix)]
                if possible_files:
                    path = os.path.join(self.data_dir, possible_files[0])
                else:
                    logger.warning(f"File not found: {path}")
                    continue
            
            try:
                # SAP CSVs are usually tab-delimited
                df = pd.read_csv(path, sep='\t', on_bad_lines='skip')
                self.dfs[key] = df
                logger.info(f"Loaded {key} data: {df.shape} from {path}")
            except Exception as e:
                logger.error(f"Failed to load {filename}: {e}")
        return self.dfs

    def clean_cpu_data(self):
        """Processes CPU and Memory data and filters peaks."""
        if "CPU" not in self.dfs:
            return None
        
        df = self.dfs["CPU"].copy()
        # Standardize column names
        df.columns = [c.upper() for c in df.columns]
        
        # Convert numeric columns
        for col in ['CPU', 'MEMORY_USED', 'MEMORY_ALLOCATION_LIMIT']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
        
        # Calculate Memory Utilization %
        if 'MEMORY_USED' in df.columns and 'MEMORY_ALLOCATION_LIMIT' in df.columns:
            df['MEMORY_PCT'] = (df['MEMORY_USED'] / df['MEMORY_ALLOCATION_LIMIT']) * 100
        else:
            df['MEMORY_PCT'] = np.nan

        # Convert Time if possible
        if 'TIME' in df.columns:
            df['TIMESTAMP'] = pd.to_datetime(df['TIME'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')
            
        return df

    def identify_peak_windows(self, df, threshold=80):
        """Identifies contiguous blocks of time where CPU is above threshold."""
        if df is None or df.empty:
            return []
            
        df = df.sort_values('TIMESTAMP').reset_index(drop=True)
        high_load = df[df['CPU'] >= threshold].copy()
        
        if high_load.empty:
            # Fallback to top 5% if no 80% peaks found
            threshold = df['CPU'].quantile(0.95)
            high_load = df[df['CPU'] >= threshold].copy()
            
        if high_load.empty:
            return []
            
        # Group contiguous timestamps (assuming 1-min or 5-min intervals)
        high_load['diff'] = high_load['TIMESTAMP'].diff().dt.total_seconds() / 60
        high_load['group'] = (high_load['diff'] > 15).cumsum() # 15 min gap starts new window
        
        windows = []
        for _, group in high_load.groupby('group'):
            start_t = group['TIMESTAMP'].min()
            end_t = group['TIMESTAMP'].max()
            windows.append({
                'start': start_t,
                'end': end_t,
                'avg_cpu': group['CPU'].mean(),
                'max_cpu': group['CPU'].max(),
                'duration': (end_t - start_t).total_seconds() / 60
            })
            
        # Sort by Max CPU descendently and take top 3
        windows = sorted(windows, key=lambda x: x['max_cpu'], reverse=True)[:3]
        return windows

    def get_summary_stats(self, df):
        """Calculates specific statistics required for the report table."""
        if df is None or df.empty:
            return None
            
        stats = {
            'date': df['TIMESTAMP'].iloc[0].strftime('%Y-%m-%d') if not df['TIMESTAMP'].empty else "N/A",
            'cpu_min': df['CPU'].min(),
            'cpu_avg': df['CPU'].mean(),
            'cpu_max': df['CPU'].max(),
            'cpu_p50': df['CPU'].median(),
            'cpu_p95': df['CPU'].quantile(0.95),
            'cpu_std': df['CPU'].std(),
            'high_load_count': len(df[df['CPU'] >= 80]),
            'mem_avg_pct': df['MEMORY_PCT'].mean(),
            'mem_max_pct': df['MEMORY_PCT'].max(),
            'sample_count': len(df)
        }
        return stats

    def format_bytes(self, b):
        """Formats bytes to MB or GB strings."""
        if pd.isna(b) or b == 0: return "0 B"
        if b < 1024 * 1024 * 1024:
            return f"{b / (1024*1024):.1f} MB"
        return f"{b / (1024*1024*1024):.1f} GB"

    def get_peak_sql(self, windows, limit=10):
        """Extracts SQL queries active during the peak windows."""
        if "SQL" not in self.dfs or not windows:
            return self.get_top_sql(limit) # Fallback to global top
            
        sql_df = self.dfs["SQL"].copy()
        sql_df.columns = [c.upper() for c in sql_df.columns]
        
        # Convert TS
        sql_df['TS'] = pd.to_datetime(sql_df['LAST_EXEC_TS'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')
        
        # Filter by windows
        peak_sqls = []
        for win in windows:
            mask = (sql_df['TS'] >= win['start']) & (sql_df['TS'] <= win['end'])
            win_sql = sql_df[mask].copy()
            win_sql['peak_window'] = f"{win['start'].strftime('%H:%M')}~{win['end'].strftime('%H:%M')}"
            peak_sqls.append(win_sql)
            
        if not peak_sqls:
            return self.get_top_sql(limit)
            
        combined = pd.concat(peak_sqls).drop_duplicates(subset=['ABAP_PROGRAM', 'SQL_TEXT'])
        
        # Numeric cleanup & Formatting
        for col in ['TOTAL_EXEC_TIME_SEC', 'MAX_EXECUTION_MEMORY_SIZE', 'TOTAL_EXECUTION_MEMORY_SIZE', 'EXEC_COUNT']:
            if col in combined.columns:
                combined[col] = pd.to_numeric(combined[col].astype(str).str.replace(',', ''), errors='coerce')
        
        combined = combined.sort_values('TOTAL_EXEC_TIME_SEC', ascending=False).head(limit)
        return combined

    def get_top_sql(self, limit=10):
        """Global top SQL fallback."""
        if "SQL" not in self.dfs: return None
        df = self.dfs["SQL"].copy()
        df.columns = [c.upper() for c in df.columns]
        time_col = 'TOTAL_EXEC_TIME_SEC'
        if time_col in df.columns:
            df[time_col] = pd.to_numeric(df[time_col].astype(str).str.replace(',', ''), errors='coerce')
            return df.sort_values(by=time_col, ascending=False).head(limit)
        return None

    def get_top_locks(self, limit=5):
        """Extracts top Lock Wait contributors."""
        if "LockWait" not in self.dfs:
            return None
        df = self.dfs["LockWait"].copy()
        df.columns = [c.upper() for c in df.columns]
        lock_col = 'TOTAL_LOCK_WAIT_SEC'
        if lock_col in df.columns:
            df[lock_col] = pd.to_numeric(df[lock_col].astype(str).str.replace(',', ''), errors='coerce')
            return df.sort_values(by=lock_col, ascending=False).head(limit)
        return None
