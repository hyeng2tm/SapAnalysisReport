import pandas as pd

# Check SQL data directly
sql_df = pd.read_csv('data/2026-06-22_SQLPLANCAHEEXPORT.csv', nrows=5, encoding='latin1')
print("SQL 파일 헤더:")
print(sql_df.head())
print("\nColumn names:")
print(sql_df.columns.tolist())

# Check timestamp column
if 'LAST_EXEC_TS' in sql_df.columns:
    print("\nLAST_EXEC_TS samples:")
    print(sql_df['LAST_EXEC_TS'].head(10))

# Check Lock data
lock_df = pd.read_csv('data/2026-06-22_LockWaitEXPORT.csv', nrows=5, encoding='latin1')
print("\n\nLock 파일 헤더:")
print(lock_df.head())
print("\nColumn names:")
print(lock_df.columns.tolist())
