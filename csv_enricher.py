import pandas as pd
import numpy as np

def enrich_option_csv(input_path, output_path):
    """
    Enriches option CSV with all scoring columns based on the Excel formula logic
    """
    df = pd.read_csv(input_path)
    
    # Ensure numeric columns
    df['Open'] = pd.to_numeric(df['Open'], errors='coerce')
    df['High'] = pd.to_numeric(df['High'], errors='coerce')
    df['Low'] = pd.to_numeric(df['Low'], errors='coerce')
    df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
    df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce').fillna(0)
    
    n = len(df)
    
    # Calculate base metrics
    df['Candle Body'] = (df['Close'] - df['Open']).abs()
    df['Candle Range'] = df['High'] - df['Low']
    df['Upper Wick'] = df['High'] - df[['Open', 'Close']].max(axis=1)
    df['Lower Wick'] = df[['Open', 'Close']].min(axis=1) - df['Low']
    df['Close Position Score'] = ((df['Close'] - df['Low']) / df['Candle Range']).fillna(0.5)
    
    # Volume ratio
    df['Volume Ratio'] = 1.0
    for i in range(n):
        if i == 0:
            df.loc[i, 'Volume Ratio'] = 1.0
        else:
            avg_vol = df.loc[:i, 'Volume'].mean()
            if avg_vol > 0:
                df.loc[i, 'Volume Ratio'] = df.loc[i, 'Volume'] / avg_vol
            else:
                df.loc[i, 'Volume Ratio'] = 1.0
    
    # Scoring components
    df['Bullish Close Score'] = df['Close Position Score'].apply(
        lambda x: 20 if x > 0.8 else (10 if x > 0.6 else (5 if x > 0.5 else 0))
    )
    
    df['Bearish Close Score'] = df['Close Position Score'].apply(
        lambda x: 20 if x < 0.2 else (10 if x < 0.4 else (5 if x < 0.5 else 0))
    )
    
    df['Volume Strength Score'] = df['Volume Ratio'].apply(
        lambda x: 30 if x > 3 else (20 if x > 2 else (10 if x > 1.5 else 0))
    )
    
    df['Candle Body Strength Score'] = 0.0
    for i in range(n):
        candle_range = df.loc[i, 'Candle Range']
        if candle_range > 0:
            body_ratio = df.loc[i, 'Candle Body'] / candle_range
            if body_ratio > 0.7:
                df.loc[i, 'Candle Body Strength Score'] = 20
            elif body_ratio > 0.5:
                df.loc[i, 'Candle Body Strength Score'] = 10
    
    # Breakout/Breakdown
    df['Breakout Score'] = 0.0
    df['Breakdown Score'] = 0.0
    df['Higher Low Score'] = 0.0
    df['Lower High Score'] = 0.0
    
    for i in range(1, n):
        if df.loc[i, 'High'] > df.loc[i-1, 'High']:
            df.loc[i, 'Breakout Score'] = 20
        if df.loc[i, 'Low'] < df.loc[i-1, 'Low']:
            df.loc[i, 'Breakdown Score'] = 20
        if df.loc[i, 'Low'] > df.loc[i-1, 'Low']:
            df.loc[i, 'Higher Low Score'] = 15
        if df.loc[i, 'High'] < df.loc[i-1, 'High']:
            df.loc[i, 'Lower High Score'] = 15
    
    # Compression and Expansion (need 5 candles history)
    df['Compression Score'] = 0.0
    df['Expansion Score'] = 0.0
    
    for i in range(5, n):
        recent_ranges = df.loc[i-5:i-1, 'Candle Range']
        avg_range = recent_ranges.mean()
        current_range = df.loc[i, 'Candle Range']
        
        if current_range < avg_range * 0.7:
            df.loc[i, 'Compression Score'] = 15
        if current_range > avg_range * 1.8:
            df.loc[i, 'Expansion Score'] = 25
    
    # Trap penalties
    df['Bull Trap penalty'] = 0.0
    df['Bear Trap Penalty'] = 0.0
    
    for i in range(n):
        candle_body = df.loc[i, 'Candle Body']
        upper_wick = df.loc[i, 'Upper Wick']
        lower_wick = df.loc[i, 'Lower Wick']
        close = df.loc[i, 'Close']
        open_p = df.loc[i, 'Open']
        
        if upper_wick > candle_body and close < open_p:
            df.loc[i, 'Bull Trap penalty'] = -25
        if lower_wick > candle_body and close > open_p:
            df.loc[i, 'Bear Trap Penalty'] = -25
    
    # Final Scores
    df['Buy Score'] = (
        df['Bullish Close Score'] + 
        df['Volume Strength Score'] + 
        df['Candle Body Strength Score'] + 
        df['Breakout Score'] + 
        df['Higher Low Score'] + 
        df['Compression Score'] + 
        df['Expansion Score'] + 
        df['Bear Trap Penalty'] + 
        df['Bull Trap penalty']
    )
    
    df['Sell Score'] = (
        df['Bearish Close Score'] + 
        df['Volume Strength Score'] + 
        df['Candle Body Strength Score'] + 
        df['Breakdown Score'] + 
        df['Lower High Score'] + 
        df['Compression Score'] + 
        df['Expansion Score'] + 
        df['Bull Trap penalty'] + 
        df['Bear Trap Penalty']
    )
    
    # Buy/Sell Entry
    df['Buy Entry'] = df['Buy Score'].apply(
        lambda x: 'BUY' if x > 80 else ('WATCH' if x > 60 else '')
    )
    
    df['Sell Entry'] = df['Sell Score'].apply(
        lambda x: 'SELL' if x > 80 else ('WATCH' if x > 60 else '')
    )

    df['Momentum Acceleration Score'] = (
        ((df['Close'] - df['Close'].shift(1)) / df['Close'].shift(1).replace(0, np.nan))
        * 100
        * df['Volume Ratio']
    ).fillna(0)

    df['Early Breakout Probability Score'] = (
        df['Compression Score']
        + df['Volume Strength Score']
        + df['Higher Low Score']
        + df['Bullish Close Score']
        + (df['Upper Wick'] < df['Upper Wick'].shift(1)).astype(int) * 10
    )

    df['High Probability Buy'] = np.where(
        (df['Buy Score'] > 80)
        & (df['Early Breakout Probability Score'] > 60)
        & (df['Momentum Acceleration Score'] > 0),
        'HIGH PROB BUY',
        ''
    )
    
    # Save to CSV
    df.to_csv(output_path, index=False)
    print(f"Enriched CSV saved to: {output_path}")
    print(f"Total rows: {len(df)}")
    print(f"Rows with BUY signal: {(df['Buy Entry'] == 'BUY').sum()}")
    print(f"Rows with WATCH signal: {(df['Buy Entry'] == 'WATCH').sum()}")
    
    return df

if __name__ == "__main__":
    # Enrich PE CSV
    pe_input = r"c:\Users\ravku\Documents\Market\Testing\PE\PE070526.csv"
    pe_output = r"c:\Users\ravku\Documents\Market\Testing\PE\PE070526_enriched.csv"
    print("Processing PE...")
    enrich_option_csv(pe_input, pe_output)
    
    # Enrich CE CSV
    ce_input = r"c:\Users\ravku\Documents\Market\Testing\CE\CE070526.csv"
    ce_output = r"c:\Users\ravku\Documents\Market\Testing\CE\CE070526_enriched.csv"
    print("\nProcessing CE...")
    enrich_option_csv(ce_input, ce_output)
