import os
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, scrolledtext
from typing import TYPE_CHECKING

from backtest import run_backtest
from engine import parse_option_metadata_from_text
from reporting import timestamped_file
from ui_theme import PALETTE

if TYPE_CHECKING:
    from ui_shared import SharedUIMixin as _BacktestViewBase
else:
    _BacktestViewBase = object

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_FOLDER = os.path.join(BASE_DIR, "results")
os.makedirs(RESULT_FOLDER, exist_ok=True)

SAMPLE_NIFTY_PATH = r"c:\Users\ravku\Documents\Market\07 May 2026\NIFTY 50 (20260507152700000 _ 20260506140300000).csv"
SAMPLE_CE_PATH = r"c:\Users\ravku\Documents\Market\Testing\CE\CE070526.csv"
SAMPLE_PE_PATH = r"c:\Users\ravku\Documents\Market\Testing\PE\PE070526.csv"


class BacktestViewMixin(_BacktestViewBase):
    def _backtest_symbol(self, symbol_entry, path_entry, fallback):
        symbol = symbol_entry.get().strip()
        default_symbol = fallback.replace(" ", "_")
        if symbol and symbol != default_symbol:
            return symbol
        filename = os.path.splitext(os.path.basename(path_entry.get()))[0].strip()
        return filename or symbol or fallback

    def _backtest_option_type(self, path_entry, symbol_entry):
        parsed = parse_option_metadata_from_text(os.path.basename(path_entry.get()))
        if parsed.get("option_type"):
            return parsed["option_type"]
        parsed = parse_option_metadata_from_text(symbol_entry.get())
        return parsed.get("option_type", "")

    def show_backtest(self):
        self.clear_window()
        self.set_status("Backtest workspace ready")
        self.header("Backtest Mode", "Repeatable CSV research with settings, metadata, Excel, and SQLite exports")
        frame = self.content()

        form = self._card(frame, padx=18, pady=16)
        form.pack(fill="x")

        self._section_title(form, "Dataset And Contract Metadata", "Strike, expiry, and type can be auto-detected from option filenames.")
        self.backtest_nifty = self._file_field(form, "NIFTY CSV", 2)
        labels = ["CALL 1", "PUT 1"]
        self.backtest_options = [self._option_field(form, label, i + 4, with_metadata=True) for i, label in enumerate(labels)]
        tk.Label(form, text="Tradingsymbol", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9, "bold")).grid(row=3, column=3)
        tk.Label(form, text="Strike", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9, "bold")).grid(row=3, column=4)
        tk.Label(form, text="Expiry", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9, "bold")).grid(row=3, column=5)

        settings_card = self._card(frame, padx=18, pady=14)
        settings_card.pack(fill="x", pady=(12, 0))
        self._section_title(settings_card, "Strategy And Risk Parameters", "Open settings to review, save, or restore defaults before running the backtest.")
        self.backtest_settings_summary = tk.StringVar(
            value=self._settings_summary_text(self._ensure_settings_values("backtest_settings_values"))
        )
        tk.Label(
            settings_card,
            textvariable=self.backtest_settings_summary,
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.make_button(
            settings_card,
            "RISK SETTINGS",
            lambda: self._open_settings_dialog(
                "Backtest Risk Settings",
                "backtest_settings_values",
                on_save=lambda values, _parsed: self.backtest_settings_summary.set(self._settings_summary_text(values)),
            ),
            PALETTE["primary"],
            16,
        ).grid(row=3, column=4, sticky="e", padx=8)

        actions = tk.Frame(frame, bg=PALETTE["bg"])
        actions.pack(pady=12)
        self.make_button(actions, "LOAD SAMPLE DATASET", self.load_sample_backtest_dataset, "#0ea5e9", 22).grid(row=0, column=0, padx=6)
        self.make_button(actions, "START BACKTEST", self.run_backtest, PALETTE["success"]).grid(row=0, column=1, padx=6)
        self.make_button(actions, "RE-RUN BACKTEST", self.run_backtest, PALETTE["primary"]).grid(row=0, column=2, padx=6)
        self.make_button(actions, "HOME", self.show_home, PALETTE["neutral"], 14).grid(row=0, column=3, padx=6)

        output_panel = tk.LabelFrame(frame, text="Backtest log", bg=PALETTE["bg"], fg=PALETTE["text"], font=("Segoe UI", 10, "bold"))
        output_panel.pack(fill="both", expand=True, pady=(8, 0))
        self.output = scrolledtext.ScrolledText(output_panel, height=10, font=("Consolas", 10), bg="#020617", fg="#d1fae5", insertbackground="#d1fae5", relief="flat")
        self.output.pack(fill="both", expand=True, padx=6, pady=6)
        self.status_bar()

    def run_backtest(self):
        try:
            self.set_status("Running backtest...")
            self.output.delete("1.0", tk.END)
            nifty = self._load_df(self.backtest_nifty)
            options = [
                self._load_df(
                    path,
                    self._backtest_symbol(symbol, path, label),
                    option_data=True,
                    strike=strike.get().strip(),
                    expiry=expiry.get().strip(),
                    option_type=self._backtest_option_type(path, symbol),
                )
                for (path, symbol, strike, expiry), label in zip(
                    self.backtest_options,
                    ["CALL 1", "PUT 1"]
                )
            ]
            settings = self._settings_from_values(self._ensure_settings_values("backtest_settings_values"))
            settings["lot_size"] = settings["lot_size"]

            path = os.path.join(
                RESULT_FOLDER,
                f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            )

            balance, trades = run_backtest(nifty, options, settings, path)

            self.output.insert(tk.END, "Backtest completed\n")
            self.output.insert(tk.END, f"Balance: {balance:.2f}\n")
            self.output.insert(tk.END, f"Trades: {len(trades)}\n")
            self.output.insert(tk.END, f"Saved: {path}\n")
            self.set_status(f"Backtest complete: {len(trades)} trades, saved {os.path.basename(path)}")
        except Exception as exc:
            self.set_status("Backtest failed")
            messagebox.showerror("ERROR", str(exc))

    def load_sample_backtest_dataset(self):
        self.backtest_nifty.delete(0, tk.END)
        self.backtest_nifty.insert(0, SAMPLE_NIFTY_PATH)
        sample_map = [
            (SAMPLE_CE_PATH, "NIFTY_CE", "", ""),
            (SAMPLE_PE_PATH, "NIFTY_PE", "", ""),
        ]
        for (path_entry, symbol_entry, strike_entry, expiry_entry), (path, symbol, strike, expiry) in zip(self.backtest_options, sample_map):
            path_entry.delete(0, tk.END)
            path_entry.insert(0, path)
            symbol_entry.delete(0, tk.END)
            symbol_entry.insert(0, symbol)
            strike_entry.delete(0, tk.END)
            strike_entry.insert(0, strike)
            expiry_entry.delete(0, tk.END)
            expiry_entry.insert(0, expiry)
        self.backtest_settings_values = self._default_settings_values()
        self._save_settings_profile("backtest_settings_values", self.backtest_settings_values)
        if hasattr(self, "backtest_settings_summary"):
            self.backtest_settings_summary.set(self._settings_summary_text(self.backtest_settings_values))
        self.set_status("Sample dataset loaded")
