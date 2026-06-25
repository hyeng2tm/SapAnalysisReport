from core.processor import SAPDataProcessor
import pandas as pd
from datetime import datetime

processor = SAPDataProcessor('./data')
processor.load_data()

# Manually process SQL like processor does
sql_df = processor.dfs["SQL"].copy() if "SQL" in processor.dfs else pd.DataFrame()
sql_df.columns = [c.upper() for c in sql_df.columns]

print(f"Original SQL shape: {sql_df.shape}")

# Parse timestamp like processor does
sql_df['TIMESTAMP'] = pd.to_datetime(sql_df['LAST_EXEC_TS'].astype(str).str[:14], format='%Y%m%d%H%M%S', errors='coerce')

print(f"After TIMESTAMP parsing:")
print(f"Valid timestamps: {sql_df['TIMESTAMP'].notna().sum()} / {len(sql_df)}")

# Check more precise timestamp range around 09:00
print(f"\nTimestamps in 09:00~09:30 range:")
mask_0900 = (sql_df['TIMESTAMP'] >= datetime(2026, 6, 22, 9, 0)) & (sql_df['TIMESTAMP'] < datetime(2026, 6, 22, 9, 30))
print(f"  Found: {mask_0900.sum()} rows")

if mask_0900.sum() > 0:
    print(sql_df[mask_0900][['LAST_EXEC_TS', 'TIMESTAMP', 'ABAP_PROGRAM', 'TCODE']].head(10))

print(f"\nTimestamps in 08:00~10:00 range:")
mask_0800_1000 = (sql_df['TIMESTAMP'] >= datetime(2026, 6, 22, 8, 0)) & (sql_df['TIMESTAMP'] < datetime(2026, 6, 22, 10, 0))
print(f"  Found: {mask_0800_1000.sum()} rows")
if mask_0800_1000.sum() > 0:
    unique_times = sql_df[mask_0800_1000]['TIMESTAMP'].unique()
    print(f"  Unique minutes: {sorted(unique_times)}")

