import pandas as pd
import os

file_path = "/home/daniel/stock-data-node/data/parquet/ORCL/1D.parquet"
if os.path.exists(file_path):
    df = pd.read_parquet(file_path)
    print("Columns:", df.columns)
    print("Tail of the data:")
    print(df.tail(20))
    
    # Filter for March 2026
    df['date'] = pd.to_datetime(df.index) if not 'date' in df.columns else pd.to_datetime(df['date'])
    df = df.set_index('date')
    march_2026 = df['2026-03']
    print("\nData for March 2026:")
    print(march_2026)
else:
    print(f"File {file_path} not found")
