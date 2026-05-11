import pandas as pd


# ==================================================
# CLEAN COLUMN NAMES
# ==================================================

def clean_columns(df):

    rename_map = {}

    for col in df.columns:

        c = col.lower()

        # PRICE COLUMNS

        if "open" == c:
            rename_map[col] = "open"

        elif "high" == c:
            rename_map[col] = "high"

        elif "low" == c:
            rename_map[col] = "low"

        elif "close" == c:
            rename_map[col] = "close"

        # VOLUME

        elif "volume" in c:
            rename_map[col] = "volume"

        # RSI

        elif "rsi" in c:
            rename_map[col] = "RSI"

        # EMA 20

        elif "20,ema" in c:
            rename_map[col] = "EMA20"

        # EMA 50

        elif "50,ema" in c:
            rename_map[col] = "EMA50"

        # DATE

        elif "date" in c:
            rename_map[col] = "date"

    df.rename(columns=rename_map, inplace=True)

    return df


# ==================================================
# LOAD SINGLE CSV
# ==================================================

def load_csv(path):

    df = pd.read_csv(path)

    # CLEAN COLUMNS

    df = clean_columns(df)

    # LOWERCASE PRICE COLUMNS

    required = [
        "open",
        "high",
        "low",
        "close"
    ]

    for col in required:

        if col not in df.columns:
            raise Exception(
                f"{col} column missing in {path}"
            )

    # REMOVE COMMAS FROM VOLUME

    if "volume" in df.columns:

        df["volume"] = (
            df["volume"]
            .astype(str)
            .str.replace(",", "")
        )

        df["volume"] = pd.to_numeric(
            df["volume"],
            errors="coerce"
        )

    # NUMERIC CONVERSION

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "RSI",
        "EMA20",
        "EMA50"
    ]

    for col in numeric_cols:

        if col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    # DROP EMPTY ROWS

    df.dropna(inplace=True)

    # RESET INDEX

    df.reset_index(drop=True, inplace=True)

    return df


# ==================================================
# SMART LOADER
# ==================================================

def load_from_smart(paths):

    # NIFTY

    nifty = load_csv(
        paths["nifty"]
    )

    options = []

    # CE OPTIONS

    for path in paths["ce"]:

        options.append(
            load_csv(path)
        )

    # PE OPTIONS

    for path in paths["pe"]:

        options.append(
            load_csv(path)
        )

    return nifty, options