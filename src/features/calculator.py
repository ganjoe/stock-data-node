import pandas as pd
import numpy as np
from typing import List
from features.config_parser import FeatureConfig, FeatureType

class TechnicalCalculator:
    def calculate_features(self, df: pd.DataFrame, configs: List[FeatureConfig]) -> pd.DataFrame:
        """Applies technical indicators to the dataframe based on provided configs."""
        if df.empty:
            return df
            
        # Work on a copy to avoid SettingWithCopyWarning
        df = df.copy()
        
        # Ensure it is sorted by date if possible, but we assume it's already sorted for calculation
        
        for config in configs:
            if config.feature_type == FeatureType.SMA:
                df = self._calc_sma(df, config)
            elif config.feature_type == FeatureType.EMA:
                df = self._calc_ema(df, config)
            else:
                # F-PRC-047: Other features are stubs
                df = self._calc_stubs(df, config)
                
        return df
    
    def _backfill_data(self, series: pd.Series, window: int) -> pd.Series:
        """
        Backfills missing early data (NaNs) by duplicating the first valid calculated value backwards.
        Requirement F-PRC-100: ensure indicators are never Null or 0.
        """
        if series.empty:
            return series
            
        first_valid_index = series.first_valid_index()
        if first_valid_index is not None:
            first_value = series.loc[first_valid_index]
            # Replace early NaNs with the first calculated value
            series.iloc[:series.index.get_loc(first_valid_index)] = first_value
            
        # Also fill overall NaNs/Infs just in case
        return series.fillna(0).replace([np.inf, -np.inf], 0)

    def _calc_sma(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        window = config.window or 10
        if df.empty:
            return df
            
        # F-PRC-100: Prepend duplicated first row to handle early data / IPO
        first_row = df.iloc[[0]]
        prepended = pd.concat([first_row] * (window - 1), ignore_index=True)
        calc_df = pd.concat([prepended, df], ignore_index=True)
        
        # Calculate SMA on calc_df and slice back to original size
        sma_series = calc_df['close'].rolling(window=window).mean()
        df[config.feature_id] = sma_series.iloc[window-1:].values
        
        # Final safety fill
        df[config.feature_id] = df[config.feature_id].bfill().fillna(0)
        return df

    def _calc_ema(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        window = config.window or 10
        if df.empty:
            return df
            
        # EMA benefit from the same strategy for consistency and early values
        first_row = df.iloc[[0]]
        prepended = pd.concat([first_row] * (window - 1), ignore_index=True)
        calc_df = pd.concat([prepended, df], ignore_index=True)
        
        ema_series = calc_df['close'].ewm(span=window, adjust=False).mean()
        df[config.feature_id] = ema_series.iloc[window-1:].values
        
        df[config.feature_id] = df[config.feature_id].bfill().fillna(0)
        return df
        
    def _calc_stubs(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        # F-PRC-047: Placeholder for non-MA indicators
        return df
