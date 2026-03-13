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
        
        for config in configs:
            if config.feature_type == FeatureType.SMA:
                df = self._calc_sma(df, config)
            elif config.feature_type == FeatureType.EMA:
                df = self._calc_ema(df, config)
            elif config.feature_type == FeatureType.BOLLINGER_BAND:
                df = self._calc_bb(df, config)
            elif config.feature_type == FeatureType.STOCHASTIC:
                df = self._calc_stoch(df, config)
            else:
                # F-PRC-047: Other features are stubs
                df = self._calc_stubs(df, config)
                
        return df
    
    def _get_ma_series(self, df: pd.DataFrame, column: str, window: int, ma_type: FeatureType) -> pd.Series:
        """Shared helper to calculate MA with prepending to handle early data."""
        if df.empty or window <= 0:
            return pd.Series(dtype=float)
            
        if window == 1:
            return df[column].reset_index(drop=True).bfill().fillna(0)
            
        # F-PRC-100: Prepend duplicated first row to handle early data / IPO
        first_row = df.iloc[[0]]
        prepended = pd.concat([first_row] * (window - 1), ignore_index=True)
        calc_df = pd.concat([prepended, df], ignore_index=True)
        
        if ma_type == FeatureType.EMA:
            ma_series = calc_df[column].ewm(span=window, adjust=False).mean()
        else:
            ma_series = calc_df[column].rolling(window=window).mean()
            
        # Slice back to original size and bfill/fillna(0) for safety
        result = ma_series.iloc[window-1:].reset_index(drop=True)
        return result.bfill().fillna(0)

    def _calc_sma(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        window = config.window or 10
        df[config.feature_id] = self._get_ma_series(df, 'close', window, FeatureType.SMA).values
        return df

    def _calc_ema(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        window = config.window or 10
        df[config.feature_id] = self._get_ma_series(df, 'close', window, FeatureType.EMA).values
        return df

    def _calc_bb(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        """Calculates Bollinger Bands (upper, lower, average, bandwidth)."""
        window = config.window or 20
        ma_type_str = config.additional_params.get("type", "SMA").upper()
        ma_type = FeatureType.EMA if ma_type_str == "EMA" else FeatureType.SMA
        
        # Average line (SMA or EMA)
        avg_line = self._get_ma_series(df, 'close', window, ma_type)
        
        # Prepend for std dev calculation consistency
        first_row = df.iloc[[0]]
        prepended = pd.concat([first_row] * (window - 1), ignore_index=True)
        calc_df = pd.concat([prepended, df], ignore_index=True)
        std_dev = calc_df['close'].rolling(window=window).std().iloc[window-1:].reset_index(drop=True).bfill().fillna(0)
        
        df[f"{config.feature_id}_avg"] = avg_line.values
        df[f"{config.feature_id}_upper"] = (avg_line + (std_dev * 2)).values
        df[f"{config.feature_id}_lower"] = (avg_line - (std_dev * 2)).values
        
        # Bandwidth: (Upper - Lower) / Average
        df[f"{config.feature_id}_bandwidth"] = ((df[f"{config.feature_id}_upper"] - df[f"{config.feature_id}_lower"]) / df[f"{config.feature_id}_avg"]).fillna(0)
        
        return df

    def _calc_stoch(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        """Calculates Stochastic %K and %D."""
        window = config.window or 14
        smooth_conf = config.additional_params.get("stoch_smooth", {})
        smooth_window = smooth_conf.get("window", 3)
        smooth_type_str = smooth_conf.get("type", "SMA").upper()
        smooth_type = FeatureType.EMA if smooth_type_str == "EMA" else FeatureType.SMA
        
        # %K = (Current Close - Lowest Low) / (Highest High - Lowest Low) * 100
        low_min = df['low'].rolling(window=window, min_periods=1).min()
        high_max = df['high'].rolling(window=window, min_periods=1).max()
        
        k_series = 100 * (df['close'] - low_min) / (high_max - low_min)
        k_series = k_series.fillna(0)
        
        df[f"{config.feature_id}_k"] = k_series.values
        
        # %D = MA of %K
        k_df = pd.DataFrame({'val': k_series})
        df[f"{config.feature_id}_d"] = self._get_ma_series(k_df, 'val', smooth_window, smooth_type).values
        
        return df
        
    def _calc_stubs(self, df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
        return df
