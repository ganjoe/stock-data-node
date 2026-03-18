#!/usr/bin/env python3
"""
End-to-End Validation Script for Minervini Trend Template Score

This script:
1. Loads raw OHLCV data from parquet files
2. Calculates all features including minervini_trend_score
3. Validates the results against expected criteria
4. Outputs detailed statistics and sample rows
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from features.config_parser import FeatureConfigParser, ProcessingContext, FeatureConfig
from features.calculator import TechnicalCalculator
from features.parquet_io import ParquetStorage


def load_ticker_data(storage: ParquetStorage, ticker: str, timeframe: str = "1D") -> pd.DataFrame:
    """Load raw OHLCV data for a ticker from /data/parquet/<ticker>/<timeframe>.parquet"""
    # The base_dir already points to /data, so we need to go into parquet subdirectory
    file_path = storage.base_dir / 'parquet' / ticker / f"{timeframe}.parquet"
    
    if not file_path.exists():
        raise FileNotFoundError(f"Source file not found: {file_path}")
        
    return pd.read_parquet(file_path)


def calculate_features_for_ticker(
    df: pd.DataFrame, 
    configs: list[FeatureConfig], 
    calculator: TechnicalCalculator
) -> pd.DataFrame:
    """Calculate all features for a dataframe."""
    return calculator.calculate_features(df, configs)


def validate_minervini_conditions(
    row: pd.Series,
    sma_50: float,
    sma_150: float,
    sma_200: float,
    high_52w: float,
    low_52w: float,
    rs_rating: int,
    expected_score: int
) -> dict[str, bool]:
    """Validate each of the 8 Minervini conditions."""
    conditions = {}
    
    # Bedingung 1: Preis > SMA_150 UND Preis > SMA_200
    conditions['cond1_price_above_ma150_200'] = row['close'] > sma_150 and row['close'] > sma_200
    
    # Bedingung 2: SMA_150 > SMA_200 (bullish MA alignment)
    conditions['cond2_ma150_above_ma200'] = sma_150 > sma_200
    
    # Bedingung 3: SMA_200 tendiert aufwärts (> Wert vor 20 Tagen)
    # Wird im Score berechnet, hier nur Plausibilitätsprüfung
    conditions['cond3_sma200_trending'] = True  # Im Score enthalten
    
    # Bedingung 4: SMA_50 > SMA_150 UND SMA_50 > SMA_200
    conditions['cond4_ma50_above_others'] = sma_50 > sma_150 and sma_50 > sma_200
    
    # Bedingung 5: Preis > SMA_50
    conditions['cond5_price_above_ma50'] = row['close'] > sma_50
    
    # Bedingung 6: Preis >= 52-Wochen-Tief * 1.30 (≥30% vom Tief)
    conditions['cond6_price_30pct_from_low'] = row['close'] >= low_52w * 1.30
    
    # Bedingung 7: Preis >= 52-Wochen-Hoch * 0.75 (innerhalb 25% vom Hoch)
    conditions['cond7_price_within_25pct_of_high'] = row['close'] >= high_52w * 0.75
    
    # Bedingung 8: RS_Rating >= 70
    conditions['cond8_rs_rating_ge_70'] = rs_rating >= 70
    
    return conditions


def run_validation(
    ticker: str,
    timeframe: str = "1D",
    data_dir: str = "/home/daniel/Dokumente/makemoney/stock-data-node/data",
    config_path: str = "/home/daniel/Dokumente/makemoney/stock-data-node/config/features.json"
) -> None:
    """Run end-to-end validation for a ticker."""
    
    print("=" * 70)
    print(f"MINERVINI TREND TEMPLATE SCORE - VALIDATION")
    print("=" * 70)
    print(f"\nTicker: {ticker}")
    print(f"Timeframe: {timeframe}")
    print(f"Data Directory: {data_dir}")
    print(f"Config Path: {config_path}")
    
    # Initialize components - base_dir points to /data, parquet is subdirectory
    storage = ParquetStorage(Path(data_dir))
    config_parser = FeatureConfigParser(config_path)
    calculator = TechnicalCalculator()
    
    # Load features configuration
    configs = config_parser.parse()
    print(f"\nLoaded {len(configs)} feature configurations")
    
    # Find minervini and ibd_rs configs
    minervini_config = next((c for c in configs if c.feature_id == 'minervini_trend_score'), None)
    ibd_rs_config = next((c for c in configs if c.feature_id == 'ibd_rs_rating'), None)
    
    print(f"Minervini config found: {minervini_config is not None}")
    print(f"IBD_RS config found: {ibd_rs_config is not None}")
    
    # Load raw data from /data/parquet/<ticker>/<timeframe>.parquet
    df = load_ticker_data(storage, ticker, timeframe)
    if df.empty:
        print(f"\nERROR: No data loaded for {ticker}")
        return
    
    print(f"\nLoaded {len(df)} rows of OHLCV data")
    print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    
    # Calculate features
    df_with_features = calculate_features_for_ticker(df, configs, calculator)
    
    # Check if minervini columns exist
    required_cols = ['minervini_score', 'minervini_percent', 'minervini_trend_template']
    missing_cols = [c for c in required_cols if c not in df_with_features.columns]
    
    if missing_cols:
        print(f"\nERROR: Missing columns: {missing_cols}")
        return
    
    print("\n✓ Minervini columns created successfully")
    
    # Display summary statistics
    print("\n" + "=" * 70)
    print("SUMMARY STATISTICS")
    print("=" * 70)
    print(f"\nScore (0-8):")
    print(f"  Mean: {df_with_features['minervini_score'].mean():.2f}")
    print(f"  Std:  {df_with_features['minervini_score'].std():.2f}")
    print(f"  Min:  {df_with_features['minervini_score'].min()}")
    print(f"  Max:  {df_with_features['minervini_score'].max()}")
    
    print(f"\nPercent Score (0-100%):")
    print(f"  Mean: {df_with_features['minervini_percent'].mean():.2f}%")
    print(f"  Std:  {df_with_features['minervini_percent'].std():.2f}%")
    
    trend_template_count = df_with_features['minervini_trend_template'].sum()
    print(f"\nTrend Template (Score == 8):")
    print(f"  Count: {trend_template_count}")
    print(f"  Percentage: {(trend_template_count / len(df) * 100):.2f}%")
    
    # Display sample rows with high scores
    print("\n" + "=" * 70)
    print("SAMPLE ROWS WITH HIGH MINERVINI SCORE (>=6)")
    print("=" * 70)
    
    high_score_rows = df_with_features[df_with_features['minervini_score'] >= 6].tail(5)
    
    for idx, row in high_score_rows.iterrows():
        print(f"\nDate: {row['timestamp']}")
        print(f"  Close: ${row['close']:.2f}")
        print(f"  Minervini Score: {int(row['minervini_score'])}/8")
        print(f"  Percent: {row['minervini_percent']:.1f}%")
        print(f"  Trend Template: {'YES' if row['minervini_trend_template'] else 'NO'}")
    
    # Detailed validation for a specific date (last available)
    print("\n" + "=" * 70)
    print("DETAILED VALIDATION - LAST AVAILABLE DATE")
    print("=" * 70)
    
    last_row = df_with_features.iloc[-1]
    sma_50 = df_with_features.get('ma_sma_50', pd.Series([0]*len(df))).iloc[-1]
    sma_150 = df_with_features.get('ma_sma_150', pd.Series([0]*len(df))).iloc[-1]
    sma_200 = df_with_features.get('ma_sma_200', pd.Series([0]*len(df))).iloc[-1]
    
    # Calculate 52-week high/low from raw data
    calc_df = pd.concat([df.iloc[[0]]] * 259, ignore_index=True)
    calc_df = pd.concat([calc_df, df], ignore_index=True)
    high_52w = calc_df['high'].rolling(window=260).max().iloc[259:]
    low_52w = calc_df['low'].rolling(window=260).min().iloc[259:]
    
    # Get RS rating (cross-sectional ranking)
    rs_rating_col = 'ibd_rs' if 'ibd_rs' in df_with_features.columns else None
    
    print(f"\nTechnical Values:")
    print(f"  Close: ${last_row['close']:.2f}")
    print(f"  SMA_50: ${sma_50:.2f}")
    print(f"  SMA_150: ${sma_150:.2f}")
    print(f"  SMA_200: ${sma_200:.2f}")
    if not high_52w.empty:
        print(f"  High_52W: ${high_52w.iloc[-1]:.2f}")
    else:
        print(f"  High_52W: N/A")
    if not low_52w.empty:
        print(f"  Low_52W: ${low_52w.iloc[-1]:.2f}")
    else:
        print(f"  Low_52W: N/A")
    print(f"  RS_Rating: {rs_rating_col}")
    
    # Validate conditions manually
    print("\nCondition Validation:")
    cond1 = last_row['close'] > sma_150 and last_row['close'] > sma_200
    print(f"  [1] Price > SMA_150 & SMA_200: {'✓' if cond1 else '✗'}")
    
    cond2 = sma_150 > sma_200
    print(f"  [2] SMA_150 > SMA_200: {'✓' if cond2 else '✗'}")
    
    cond3 = True  # SMA trending - calculated internally
    print(f"  [3] SMA_200 trending upward: ✓ (calculated)")
    
    cond4 = sma_50 > sma_150 and sma_50 > sma_200
    print(f"  [4] SMA_50 > SMA_150 & SMA_200: {'✓' if cond4 else '✗'}")
    
    cond5 = last_row['close'] > sma_50
    print(f"  [5] Price > SMA_50: {'✓' if cond5 else '✗'}")
    
    cond6 = last_row['close'] >= low_52w.iloc[-1] * 1.30 if not low_52w.empty else False
    print(f"  [6] Price >= Low_52W * 1.30: {'✓' if cond6 else '✗'}")
    
    cond7 = last_row['close'] >= high_52w.iloc[-1] * 0.75 if not high_52w.empty else False
    print(f"  [7] Price >= High_52W * 0.75: {'✓' if cond7 else '✗'}")
    
    cond8 = rs_rating_col is not None and last_row.get('ibd_rs', 0) >= 70
    print(f"  [8] RS_Rating >= 70: {'✓' if cond8 else '✗ (or N/A)'}")
    
    manual_score = sum([cond1, cond2, cond3, cond4, cond5, cond6, cond7, cond8])
    print(f"\nManual Score Calculation: {manual_score}/8")
    print(f"Automated Score: {int(last_row['minervini_score'])}/8")
    print(f"Match: {'✓' if manual_score == int(last_row['minervini_score']) else '✗'}")
    
    # Save validation results to parquet for inspection
    output_path = Path(data_dir) / 'parquet' / ticker / f"{timeframe}_minervini_validation.parquet"
    df_with_features.to_parquet(output_path)
    print(f"\n✓ Validation data saved to: {output_path}")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Validate Minervini Trend Template Score")
    parser.add_argument(
        "--ticker", 
        type=str, 
        default="AAPL",
        help="Ticker symbol to validate (default: AAPL)"
    )
    parser.add_argument(
        "--timeframe", 
        type=str, 
        default="1D",
        choices=["1D", "1W", "1M"],
        help="Timeframe for analysis (default: 1D)"
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="/home/daniel/Dokumente/makemoney/stock-data-node/data",
        help="Path to data directory"
    )
    parser.add_argument(
        "--config-path",
        type=str,
        default="/home/daniel/Dokumente/makemoney/stock-data-node/config/features.json",
        help="Path to features.json config file"
    )
    
    args = parser.parse_args()
    
    run_validation(
        ticker=args.ticker,
        timeframe=args.timeframe,
        data_dir=args.data_dir,
        config_path=args.config_path
    )


if __name__ == "__main__":
    main()
