import threading
import webbrowser
from datetime import datetime
import tkinter as tk
from tkinter import messagebox

from ui_theme import PALETTE
from zerodha_auth import (
    DEFAULT_REDIRECT_URL,
    ZerodhaAuthStore,
    ZerodhaCallbackServer,
    extract_request_token_from_url,
)
from zerodha_client import ZerodhaClient


class ZerodhaAuthMixin:
    def _init_zerodha_auth(self):
        self.zerodha_auth_store = ZerodhaAuthStore()
        self.zerodha_session_api_settings = {
            "PAPER": {
                "api_key": "",
                "api_secret": "",
                "redirect_url": DEFAULT_REDIRECT_URL,
            },
            "LIVE": {
                "api_key": "",
                "api_secret": "",
                "redirect_url": DEFAULT_REDIRECT_URL,
            },
        }
        self.zerodha_clients_by_mode = {"PAPER": None, "LIVE": None}
        self.zerodha_auth_profiles = {"PAPER": None, "LIVE": None}
        self.zerodha_auth_login_times = {"PAPER": "", "LIVE": ""}
        self.zerodha_auth_profile = None
        self.zerodha_auth_login_at = ""
        self.zerodha_callback_server = None

    def startup_zerodha_auth_check(self):
        self.set_status("Ready. Enter Zerodha API details when connecting.")

    def open_zerodha_auth_wizard(self, auto_started=False):
        mode = self._zerodha_auth_mode()
        if self._zerodha_connection_blocked(mode, show_message=True):
            return
        self.zerodha_session_api_settings[mode] = {
            "api_key": "",
            "api_secret": "",
            "redirect_url": DEFAULT_REDIRECT_URL,
        }
        self._show_auth_step_api_setup(mode, auto_started=auto_started)

    def _load_saved_zerodha_client(self):
        return None, None

    def _show_auth_step_api_setup(self, mode, auto_started=False):
        if self._zerodha_connection_blocked(mode, show_message=True):
            return
        settings = self.zerodha_session_api_settings[mode]
        popup = self._auth_popup(f"{self._auth_label(mode)} Authentication - Step 1")
        body = self._auth_body(popup, f"{self._auth_label(mode)} API Setup")

        api_key = self._auth_field(body, "API Key", settings["api_key"], 1)
        api_secret = self._auth_field(body, "API Secret", settings["api_secret"], 2, show="*")
        tk.Label(
            body,
            text=f"Redirect URL: {DEFAULT_REDIRECT_URL}",
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 10, "bold"),
            wraplength=520,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 4))

        actions = self._auth_actions(body, 5)

        def save_and_next():
            key = api_key.get().strip()
            secret = api_secret.get().strip()
            if not key or not secret:
                messagebox.showerror("Zerodha", "API Key and API Secret are required.")
                return
            self.zerodha_session_api_settings[mode] = {
                "api_key": key,
                "api_secret": secret,
                "redirect_url": DEFAULT_REDIRECT_URL,
            }
            popup.destroy()
            self._start_zerodha_login_flow(mode)

        self.make_button(actions, "SAVE AND NEXT", save_and_next, PALETTE["primary"], 18).grid(row=0, column=0, padx=(0, 8))
        self.make_button(actions, "CLOSE", popup.destroy, "#6b7280", 10).grid(row=0, column=1, padx=8)

    def _start_zerodha_login_flow(self, mode):
        if self._zerodha_connection_blocked(mode, show_message=True):
            return
        settings = self.zerodha_session_api_settings[mode]
        popup = self._auth_popup(f"{self._auth_label(mode)} Authentication - Step 2")
        body = self._auth_body(popup, f"{self._auth_label(mode)} Login")

        status = tk.StringVar(value="Opening Zerodha login in your browser...")
        tk.Label(
            body,
            textvariable=status,
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 10, "bold"),
            wraplength=520,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 12))

        try:
            client = ZerodhaClient(settings["api_key"], settings["api_secret"])
            login_url = client.login_url()
        except Exception as exc:
            messagebox.showerror("Zerodha", str(exc))
            popup.destroy()
            self._show_auth_step_api_setup(mode)
            return
        try:
            self.zerodha_callback_server = ZerodhaCallbackServer(settings["redirect_url"]).start()
        except Exception:
            self.zerodha_callback_server = None
            status.set("Callback server could not start. You can still paste the redirected URL in the fallback step.")
        popup.protocol("WM_DELETE_WINDOW", lambda: (self._stop_zerodha_callback_server(), popup.destroy()))

        actions = self._auth_actions(body, 3)

        def continue_to_capture():
            webbrowser.open(login_url)
            popup.destroy()
            self._show_auth_step_capture_request_token(mode)

        self.make_button(actions, "PASTE REDIRECTED URL", lambda: (popup.destroy(), self._show_auth_fallback_url(mode)), PALETTE["warning"], 22).grid(row=0, column=0, padx=(0, 8))
        self.make_button(actions, "BACK", lambda: (self._stop_zerodha_callback_server(), popup.destroy(), self._show_auth_step_api_setup(mode)), "#6b7280", 10).grid(row=0, column=1, padx=8)
        popup.after(300, continue_to_capture)

    def _show_auth_step_capture_request_token(self, mode):
        popup = self._auth_popup(f"{self._auth_label(mode)} Authentication - Step 3")
        body = self._auth_body(popup, f"{self._auth_label(mode)} Token Capture")
        popup.protocol("WM_DELETE_WINDOW", lambda: (self._stop_zerodha_callback_server(), popup.destroy()))

        status = tk.StringVar(value="Waiting for Zerodha redirect...")
        tk.Label(
            body,
            textvariable=status,
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 10, "bold"),
            wraplength=560,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 12))

        elapsed = {"ticks": 0}

        def poll():
            server = self.zerodha_callback_server
            if server and server.request_token:
                status.set("Request token received")
                self.root.after(700, lambda: self._generate_access_token_silently(mode, server.request_token, popup, status))
                return
            elapsed["ticks"] += 1
            if elapsed["ticks"] >= 120:
                status.set("Auto-capture timed out. Paste the redirected URL to continue.")
                return
            popup.after(1000, poll)

        actions = self._auth_actions(body, 3)
        self.make_button(actions, "PASTE REDIRECTED URL", lambda: (popup.destroy(), self._show_auth_fallback_url(mode)), PALETTE["warning"], 22).grid(row=0, column=0, padx=(0, 8))
        self.make_button(actions, "CANCEL", lambda: (self._stop_zerodha_callback_server(), popup.destroy()), "#6b7280", 10).grid(row=0, column=1, padx=8)
        poll()

    def _show_auth_fallback_url(self, mode):
        popup = self._auth_popup(f"{self._auth_label(mode)} Authentication - Fallback")
        body = self._auth_body(popup, f"{self._auth_label(mode)} Fallback URL")
        url_entry = self._auth_field(body, "Redirected URL", "", 1, width=58)
        actions = self._auth_actions(body, 3)

        def extract_and_next():
            try:
                request_token = extract_request_token_from_url(url_entry.get())
            except Exception as exc:
                messagebox.showerror("Zerodha", str(exc))
                return
            self._stop_zerodha_callback_server()
            popup.destroy()
            self._generate_access_token_silently(mode, request_token)

        self.make_button(actions, "CONTINUE", extract_and_next, PALETTE["primary"], 14).grid(row=0, column=0, padx=(0, 8))
        self.make_button(actions, "CANCEL", lambda: (self._stop_zerodha_callback_server(), popup.destroy()), "#6b7280", 10).grid(row=0, column=1, padx=8)

    def _generate_access_token_silently(self, mode, request_token, popup=None, status=None):
        self._stop_zerodha_callback_server()
        if self._zerodha_connection_blocked(mode, show_message=True):
            if popup:
                popup.destroy()
            return
        settings = self.zerodha_session_api_settings[mode]
        owned_popup = popup is None
        if owned_popup:
            popup = self._auth_popup(f"{self._auth_label(mode)} Authentication - Connecting")
            body = self._auth_body(popup, f"Connecting {self._auth_label(mode)}")
            status = tk.StringVar(value="Generating access token...")
            tk.Label(
                body,
                textvariable=status,
                bg=PALETTE["surface"],
                fg=PALETTE["muted"],
                font=("Segoe UI", 10, "bold"),
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 12))
        elif status:
            status.set("Generating access token...")

        def worker():
            try:
                client = ZerodhaClient(settings["api_key"], settings["api_secret"])
                access_token = client.generate_session(request_token)
                self.zerodha_auth_store.save_access_token(access_token)
                profile = client.profile()
                error = None
            except Exception as exc:
                client = None
                profile = None
                error = str(exc)

            def apply_result():
                if error:
                    messagebox.showerror("Zerodha", error)
                    if status:
                        status.set("Access token generation failed.")
                    return
                self.zerodha_session_api_settings[mode] = {
                    "api_key": "",
                    "api_secret": "",
                    "redirect_url": DEFAULT_REDIRECT_URL,
                }
                popup.destroy()
                self._apply_zerodha_connection(client, profile, mode=mode)
                self._show_auth_step_verify(profile, mode)

            self.root.after(0, apply_result)

        threading.Thread(target=worker, name="tradebot_zerodha_generate_session", daemon=True).start()

    def _show_auth_step_verify(self, profile, mode):
        popup = self._auth_popup(f"{self._auth_label(mode)} Authentication - Connected")
        body = self._auth_body(popup, f"{self._auth_label(mode)} Connection Verified")
        user_name = profile.get("user_name") or profile.get("user_shortname") or "-"
        user_id = profile.get("user_id") or profile.get("client_id") or "-"
        login_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        text = (
            f"Connected to {self._auth_label(mode)}\n"
            f"User name: {user_name}\n"
            f"User ID / client ID: {user_id}\n"
            f"Login date/time: {login_time}"
        )
        tk.Label(
            body,
            text=text,
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=("Segoe UI", 11, "bold"),
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 16))
        actions = self._auth_actions(body, 3)
        self.make_button(actions, "DONE", popup.destroy, PALETTE["success"], 12).grid(row=0, column=0, padx=(0, 8))

    def _apply_zerodha_connection(self, client, profile, mode=None):
        mode = mode or self._zerodha_auth_mode()
        if self._zerodha_connection_blocked(mode, show_message=True):
            return
        self.zerodha_clients_by_mode[mode] = client
        self.zerodha_auth_profiles[mode] = profile or {}
        self.zerodha_auth_login_times[mode] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if self._zerodha_auth_mode() == mode:
            self._sync_zerodha_client_for_mode(mode)
        if hasattr(self, "zerodha_status_text"):
            self._update_zerodha_status_for_mode(self._zerodha_auth_mode())
        if mode == "LIVE" and getattr(self, "live_mode", None) == "LIVE":
            self._refresh_real_margin(show_errors=False)

    def _sync_zerodha_client_for_mode(self, mode=None):
        mode = mode or self._zerodha_auth_mode()
        self.executor.zerodha = self.zerodha_clients_by_mode.get(mode)
        self.zerodha_client = self.executor.zerodha
        self.zerodha_auth_profile = self.zerodha_auth_profiles.get(mode)
        self.zerodha_auth_login_at = self.zerodha_auth_login_times.get(mode, "")

    def _update_zerodha_status_for_mode(self, mode=None):
        if not hasattr(self, "zerodha_status_text"):
            return
        mode = mode or self._zerodha_auth_mode()
        profile = self.zerodha_auth_profiles.get(mode) or {}
        if not self.zerodha_clients_by_mode.get(mode):
            self.zerodha_status_text.set(f"{self._auth_label(mode)}: not connected")
            return
        user_name = profile.get("user_name") or profile.get("user_shortname") or "Zerodha"
        user_id = profile.get("user_id") or profile.get("client_id") or ""
        suffix = f" ({user_id})" if user_id else ""
        self.zerodha_status_text.set(f"{self._auth_label(mode)} connected to {user_name}{suffix}")

    def _zerodha_auth_mode(self):
        return "LIVE" if getattr(self, "live_mode", "LIVE") == "LIVE" else "PAPER"

    def _auth_label(self, mode=None):
        mode = mode or self._zerodha_auth_mode()
        return "Real Money Zerodha" if mode == "LIVE" else "Paper Trading Data"

    def _other_zerodha_mode(self, mode=None):
        mode = mode or self._zerodha_auth_mode()
        return "PAPER" if mode == "LIVE" else "LIVE"

    def _zerodha_connection_blocked(self, mode=None, show_message=False):
        mode = mode or self._zerodha_auth_mode()
        other_mode = self._other_zerodha_mode(mode)
        if not self.zerodha_clients_by_mode.get(other_mode):
            return False
        if show_message:
            messagebox.showwarning(
                "Zerodha Connection",
                (
                    f"{self._auth_label(other_mode)} is already connected.\n\n"
                    f"Disconnect or restart the app before connecting {self._auth_label(mode)}."
                ),
            )
        return True

    def _stop_zerodha_callback_server(self):
        if self.zerodha_callback_server:
            self.zerodha_callback_server.stop()
            self.zerodha_callback_server = None

    def _auth_popup(self, title):
        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.geometry("660x300")
        popup.resizable(False, False)
        popup.configure(bg=PALETTE["bg"])
        popup.transient(self.root)
        popup.grab_set()
        return popup

    def _auth_body(self, popup, title):
        body = self._card(popup, padx=18, pady=16)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        self._section_title(body, title)
        return body

    def _auth_field(self, frame, label, value, row, width=34, show=None):
        return self._field(frame, label, value, row, column=1, width=width, show=show)

    def _auth_actions(self, frame, row):
        actions = tk.Frame(frame, bg=PALETTE["surface"])
        actions.grid(row=row, column=0, columnspan=2, pady=(16, 0), sticky="w")
        return actions
