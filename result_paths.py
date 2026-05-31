import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_FOLDER = os.path.join(BASE_DIR, "results")

RESULT_CATEGORY_FOLDERS = {
    "backtest": "backtest",
    "backtest_live": "backtest_live",
    "market_cue": "market_cue",
    "paper_trading": "paper_trading",
    "real_money_trading": "real_money_trading",
}


def result_category_folder(base_folder, category, create=True):
    folder_name = RESULT_CATEGORY_FOLDERS.get(str(category or ""), str(category or ""))
    path = os.path.join(base_folder, folder_name) if folder_name else base_folder
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def live_result_category(mode):
    return "real_money_trading" if str(mode or "").upper() == "LIVE" else "paper_trading"


def unique_paths(paths):
    seen = set()
    result = []
    for path in paths:
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(path)
    return result
