"""
NVCPP Core Math Engine: CLINE-L1-B24M-TRAIL-v1
Implements prior-only trailing 24-hour median baseline, relative signed delta,
and unclipped canonical chi_B24M.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass

# EXPLICIT PROTOCOL DECLARATIONS
PROTOCOL_ID = "CLINE-L1-B24M-TRAIL-v1"
PROTOCOL_VERSION = "1.0.0"

@dataclass
class ProtocolConfig:
    """Immutable configuration for the CLINE-L1 canonical baseline."""
    id: str = PROTOCOL_ID
    version: str = PROTOCOL_VERSION
    baseline_type: str = "trailing_median"
    window_hours: int = 24
    closed_boundary: str = "left"
    min_coverage: float = 0.95
    clipping_allowed: bool = False

def calculate_trailing_median_baseline(
    df: pd.DataFrame, 
    time_col: str, 
    b_mag_col: str, 
    window_hours: int = 24,
    min_coverage: float = 0.95
) -> pd.DataFrame:
    """
    Computes prior-only (closed='left') trailing 24-hour median baseline (B0).
    Enforces full elapsed time window and minimum sample coverage.
    """
    df = df.sort_values(time_col).copy()
    df.set_index(time_col, inplace=True)
    
    # Calculate rolling count and median using prior-only window (closed='left')
    rolling_obj = df[b_mag_col].rolling(f'{window_hours}h', closed='left')
    rolling_median = rolling_obj.median()
    rolling_count = rolling_obj.count()
    
    # Verify elapsed window time
    start_time = df.index[0]
    elapsed_time = df.index - start_time
    full_window_mask = elapsed_time >= pd.Timedelta(hours=window_hours)
    
    # Expected samples per window based on median sample rate
    if len(df) > 1:
        median_interval_sec = pd.Series(df.index).diff().dt.total_seconds().median()
        if median_interval_sec > 0:
            expected_samples = (window_hours * 3600) / median_interval_sec
            coverage_mask = (rolling_count / expected_samples) >= min_coverage
        else:
            coverage_mask = pd.Series(True, index=df.index)
    else:
        coverage_mask = pd.Series(False, index=df.index)
        
    # Baseline is valid only when full window has elapsed and coverage is sufficient
    valid_baseline_mask = full_window_mask & coverage_mask
    df['B0'] = np.where(valid_baseline_mask, rolling_median, np.nan)
    
    # Compute distinct metrics: Ratio, Signed Delta, and Absolute Chi
    df['ratio_B24M'] = df[b_mag_col] / df['B0']
    df['delta_B24M'] = (df[b_mag_col] - df['B0']) / df['B0']
    df['chi_B24M'] = np.abs(df['delta_B24M'])
    
    df.reset_index(inplace=True)
    return df

def run_chain(telemetry_df: pd.DataFrame, time_col: str, b_mag_col: str) -> pd.DataFrame:
    """
    Entry point for NVCPP L1 physics computation chain.
    """
    return calculate_trailing_median_baseline(telemetry_df, time_col, b_mag_col)
