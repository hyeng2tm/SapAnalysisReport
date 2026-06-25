from core.processor import SAPDataProcessor
import pandas as pd

processor = SAPDataProcessor('./data')
processor.load_data()

# Check what's loaded
print(f"Loaded dataframes: {list(processor.dfs.keys())}")

if "SQL" in processor.dfs:
    sql_df = processor.dfs["SQL"]
    print(f"\nSQL dataframe shape: {sql_df.shape}")
    print(f"SQL columns: {sql_df.columns.tolist()}")
    
    if 'LAST_EXEC_TS' in sql_df.columns:
        print(f"\nLAST_EXEC_TS samples:")
        print(sql_df['LAST_EXEC_TS'].head(10))
        
        # Check timestamp range
        ts = pd.to_datetime(sql_df['LAST_EXEC_TS'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')
        print(f"\nTimestamp range:")
        print(f"  Min: {ts.min()}")
        print(f"  Max: {ts.max()}")
        
        # Check 09시대 데이터
        from datetime import datetime
        start_09 = datetime(2026, 6, 22, 8, 0)
        end_09 = datetime(2026, 6, 22, 10, 0)
        count_09 = ((ts >= start_09) & (ts <= end_09)).sum()
        print(f"\nData in 08:00~10:00 range: {count_09} rows")

if "LockWait" in processor.dfs:
    lock_df = processor.dfs["LockWait"]
    print(f"\nLock dataframe shape: {lock_df.shape}")
    print(f"Lock columns: {lock_df.columns.tolist()}")
