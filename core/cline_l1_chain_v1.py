"""
NVCPP Core Baseline Protocol V1
Strict, unclipped mathematical engine for physical telemetry processing.
Calculates trailing B0 baselines and chi_B24M without clipping, saturation, or generic aliasing.
"""

import pandas as pd
import numpy as np

PROTOCOL_ID = "CLINE_L1_BASELINE_PROTOCOL"
PROTOCOL_VERSION = "1.0.0"

class ProtocolConfig:
    def __init__(self, baseline_hours=24):
        self.baseline_hours = baseline_hours
        self.chi_label = "chi_B24M"
        self.clipping_allowed = False

def calculate_trailing_baseline(df: pd.DataFrame, time_col: str, b_mag_col: str, baseline_hours: int = 24) -> pd.DataFrame:
    """
    Calculates the trailing B0 baseline using prior-only data.
    Ensures zero future-data leakage into current physical states.
    """
    df = df.sort_values(time_col).copy()
    df.set_index(time_col, inplace=True)
    
    # Calculate rolling mean using exactly the preceding baseline_hours window
    rolling_b0 = df[b_mag_col].rolling(f'{baseline_hours}h', closed='left').mean()
    df['B0'] = rolling_b0.values
    
    df.reset_index(inplace=True)
    return df

def apply_chi_b24m(df: pd.DataFrame, b_mag_col: str = 'B_mag', b0_col: str = 'B0') -> pd.DataFrame:
    """
    Computes unclipped chi_B24M from instantaneous B and trailing B0.
    Strictly forbids generic 'chi' naming and data winsorization.
    """
    if 'chi' in df.columns:
        raise ValueError("Generic 'chi' detected. NVCPP enforces explicit 'chi_B24M' labeling.")
        
    df['chi_B24M'] = df[b_mag_col] / df[b0_col]
    
    # NVCPP Constraint Check
    if df['chi_B24M'].max() <= 1.0 or df['chi_B24M'].min() < 0:
        print("[WARNING] Abnormal chi_B24M range detected. Ensure data is unclipped.")
        
    return df

def run_chain(telemetry_df: pd.DataFrame, time_col: str, b_mag_col: str) -> pd.DataFrame:
    """
    Executes the full CLINE L1 verification chain on raw telemetry.
    """
    df = calculate_trailing_baseline(telemetry_df, time_col, b_mag_col)
    df = apply_chi_b24m(df)
    return df
