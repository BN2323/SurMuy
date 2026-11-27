"""Provider preferences and their Windows Credential Manager storage."""

import json
import threading
import tkinter as tk
from tkinter import ttk

from pywintypes import error
import win32cred

from api_context import DEFAULT_CONTEXT_BUDGET, MAX_CONTEXT_BUDGET, MIN_CONTEXT_BUDGET, RESPONSE_RESERVE
import win32timezone  # Imported by pywin32 when Credential Manager returns a timestamp.

from vision_agents import ACCOUNT, API_KEY, PROVIDER_CHOICES, compatible_base, list_models
from window_chrome import use_native_caption


def read_record(name):
    try:
        record = win32cred.CredRead(f"Screen Reader/{name}", win32cred.CRED_TYPE_GENERIC)
    except error as problem:
        if problem.winerror == 1168:
            return None
        raise
    blob = record["CredentialBlob"]
    if isinstance(blob, bytes):
        encoding = "utf-16-le" if blob[1:2] == b"\0" else "utf-8"
        blob = blob.decode(encoding).rstrip("\0")
    return json.loads(blob)


def load_preferences(provider):
    saved = read_record(f"Providers/{provider}")
    if saved:
        return saved

    # Import existing key and model choices from the earlier provider labels.
    connections = {}
    for mode, backend in PROVIDER_CHOICES[provider].items():
        if provider not in ("OpenAI", "Gemini"):
            continue
        saved = read_record("Gemini" if backend == "Gemini API" else backend)
        if saved and saved.get("model"):
            connections[mode] = {"model": saved["model"]}
            if mode == API_KEY and saved.get("key"):
                connections[mode]["key"] = saved["key"]
    if connections:
        return {"mode": API_KEY if API_KEY in connections else ACCOUNT, "connections": connections}
    return None


def save_preferences(provider, preferences):
    win32cred.CredWrite({
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": f"Screen Reader/Providers/{provider}",
        "CredentialBlob": json.dumps(preferences),
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        "UserName": provider,
    })


class ProviderSettings:
    def __init__(self, root, selected_provider, profiles, events, on_saved):
        self.profiles = profiles
        self.events = events
        self.on_saved = on_saved
        self.verified = None
        self.loading = False
        self.cancel = threading.Event()
        self.window = tk.Toplevel(root)
        self.window.title("AI settings")
        self.window.transient(root)
        self.window.attributes("-toolwindow", True)
        use_native_caption(self.window)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())

        self.provider = tk.StringVar(value=selected_provider)
        self.mode = tk.StringVar()
        self.key = tk.StringVar()
        self.base_url = tk.StringVar()
        self.model = tk.StringVar()
        self.context_budget = tk.StringVar(value=str(DEFAULT_CONTEXT_BUDGET))
        self.status = tk.StringVar()

        panel = ttk.Frame(self.window, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Provider").pack(anchor="w")
        self.provider_box = ttk.Combobox(panel, values=tuple(PROVIDER_CHOICES), state="readonly",
                                         textvariable=self.provider, width=40)
        self.provider_box.pack(fill="x", pady=(3, 8))
        self.provider_box.bind("<<ComboboxSelected>>", self.change_provider)
        self.mode_form = ttk.Frame(panel)
        ttk.Label(self.mode_form, text="Connection").pack(anchor="w")
        self.mode_box = ttk.Combobox(self.mode_form, state="readonly", textvariable=self.mode)
        self.mode_box.pack(fill="x", pady=(3, 8))
        self.mode_box.bind("<<ComboboxSelected>>", self.change_mode)

        self.key_form = ttk.Frame(panel)
        self.key_label = ttk.Label(self.key_form, text="API key")
        self.key_label.pack(anchor="w")
        self.key_entry = ttk.Entry(self.key_form, textvariable=self.key, show="•")
        self.key_entry.pack(fill="x", pady=(3, 8))
        self.url_form = ttk.Frame(panel)
        ttk.Label(self.url_form, text="OpenAI-compatible base URL").pack(anchor="w")
        self.url_entry = ttk.Entry(self.url_form, textvariable=self.base_url)
        self.url_entry.pack(fill="x", pady=(3, 8))

        self.provider_actions = ttk.Frame(panel)
        self.provider_actions.pack(fill="x", pady=(0, 8))
        self.load_button = ttk.Button(self.provider_actions, text="Load models", command=self.fetch_models)
        self.load_button.pack(side="left")
        self.login_button = ttk.Button(self.provider_actions,
                                       command=lambda: self.fetch_models(connect=True))
        ttk.Label(panel, text="Model").pack(anchor="w")
        self.model_box = ttk.Combobox(panel, textvariable=self.model)
        self.model_box.pack(fill="x", pady=(3, 8))
        self.budget_form = ttk.Frame(panel)
        ttk.Label(self.budget_form, text="Context budget (tokens)").pack(side="left")
        ttk.Entry(self.budget_form, textvariable=self.context_budget, width=9).pack(side="right")
        self.status_label = ttk.Label(panel, textvariable=self.status, style="Muted.TLabel", wraplength=340)
        self.status_label.pack(anchor="w", fill="x", pady=(0, 10))
        actions = ttk.Frame(panel)
        actions.pack(fill="x")
        self.remove_button = ttk.Button(actions, style="Quiet.TButton", command=self.remove)
        self.remove_button.pack(side="left")
        self.save_button = ttk.Button(actions, text="Save", style="Accent.TButton", command=self.save)
        self.save_button.pack(side="right")
        self.key.trace_add("write", self.input_changed)
        self.base_url.trace_add("write", self.input_changed)
        self.model.trace_add("write", self.update_save_button)
        self.model.trace_add("write", self.update_budget)
        self.change_provider()

        self.window.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - self.window.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + 10
        self.window.geometry(f"+{x}+{y}")
        self.window.grab_set()
        self.provider_box.focus_set()

    def saved_connection(self):
        return self.profiles.get(self.provider.get(), {}).get("connections", {}).get(self.mode.get(), {})

    def update_budget(self, *_args):
        budgets = self.saved_connection().get("context_budgets", {})
        self.context_budget.set(str(budgets.get(self.model.get(), DEFAULT_CONTEXT_BUDGET)))

    def change_provider(self, _event=None):
        provider = self.provider.get()
        modes = tuple(PROVIDER_CHOICES[provider])
        self.mode_box.configure(values=modes)
        if len(modes) > 1:
            self.mode_form.pack(fill="x", before=self.provider_actions)
        else:
            self.mode_form.pack_forget()
        self.mode.set(self.profiles.get(provider, {}).get("mode", modes[0]))
        self.change_mode()

    def change_mode(self, _event=None):
        provider = self.provider.get()
        mode = self.mode.get()
        saved = self.saved_connection()
        self.key.set("")
        self.base_url.set(saved.get("base_url", ""))
        self.model.set(saved.get("model", ""))
        self.remove_button.configure(text="Remove key" if mode == API_KEY else "Reset model")
        self.remove_button.state(["!disabled"] if saved else ["disabled"])
        self.login_button.pack_forget()
        self.key_form.pack_forget()
        self.url_form.pack_forget()
        custom = provider == "Custom API"
        self.model_box.configure(state="normal" if custom else "readonly")
        if mode == API_KEY:
            self.budget_form.pack(fill="x", before=self.status_label, pady=(0, 8))
            if custom:
                self.url_form.pack(fill="x", before=self.provider_actions)
            self.key_label.configure(text="API key (if required)" if custom else "API key")
            self.key_form.pack(fill="x", before=self.provider_actions)
            self.status.set("Use Load models or enter a model ID." if custom else
                            ("Saved key will be used." if saved.get("key") else
                             "Paste your API key, then load models."))
        else:
            self.budget_form.pack_forget()
            label = "Sign in with Google" if provider == "Gemini" else "Sign in with ChatGPT"
            self.login_button.configure(text=label)
            self.login_button.pack(side="left", padx=(7, 0))
            self.status.set("Checking your existing sign-in…" if provider == "Gemini" else
                            "Use your local Codex sign-in, then load models.")
            if provider == "Gemini":
                self.fetch_models()
        self.update_save_button()

    def input_changed(self, *_args):
        self.verified = None
        self.model_box.configure(values=())
        if self.provider.get() != "Custom API":
            self.model.set("")
        self.update_save_button()

    def update_save_button(self, *_args):
        manual_model = self.provider.get() == "Custom API" and self.base_url.get().strip()
        ready = not self.loading and self.model.get().strip() and (self.verified is not None or manual_model)
        self.save_button.state(["!disabled"] if ready else ["disabled"])

    def connection_input(self):
        provider = self.provider.get()
        if self.mode.get() == ACCOUNT:
            return "", ""
        saved = self.saved_connection()
        url = self.base_url.get().strip()
        key = self.key.get().strip()
        if not key and (provider != "Custom API" or url == saved.get("base_url", "")):
            key = saved.get("key", "")
        if provider == "Custom API":
            url = compatible_base(provider, url)
        elif not key:
            raise RuntimeError("Enter an API key first.")
        return key, url

    def set_loading(self, loading):
        self.loading = loading
        for control in (self.provider_box, self.mode_box, self.key_entry, self.url_entry,
                        self.load_button, self.login_button, self.model_box):
            control.state(["disabled"] if loading else ["!disabled"])
        self.remove_button.state(["!disabled"] if not loading and self.saved_connection() else ["disabled"])
        self.update_save_button()

    def fetch_models(self, *, connect=False):
        if self.loading:
            return
        provider = self.provider.get()
        backend = PROVIDER_CHOICES[provider][self.mode.get()]
        try:
            key, url = self.connection_input()
        except RuntimeError as problem:
            self.status.set(str(problem))
            return
        self.verified = None
        self.set_loading(True)
        self.status.set("Checking your existing sign-in. Complete the browser prompt if it opens."
                        if connect else f"Loading models from {provider}…")

        def fetch():
            try:
                models = list_models(backend, key, base_url=url, connect=connect, cancel=self.cancel)
            except Exception as problem:
                message = str(problem)
                self.events.put((self.models_loaded, (None, (), message.replace(key, "[key]") if key else message)))
            else:
                self.events.put((self.models_loaded, ((key, url), models, "")))

        threading.Thread(target=fetch, daemon=True).start()

    def models_loaded(self, connection, models, message):
        if not self.window.winfo_exists():
            return
        self.verified = connection if models else None
        self.model_box.configure(values=models)
        saved_model = self.saved_connection().get("model", "")
        if self.provider.get() != "Custom API":
            self.model.set(saved_model if saved_model in models else models[0] if self.mode.get() == ACCOUNT and models else "")
        self.set_loading(False)
        if message:
            self.status.set(message)
        elif models:
            self.status.set("Connected. Choose a model, then save.")
        else:
            self.status.set("The provider returned no matching chat models.")

    def save(self):
        provider = self.provider.get()
        model = self.model.get().strip()
        if not model or self.loading:
            return
        try:
            if provider == "Custom API":
                key, url = self.connection_input()
            elif self.verified is not None and model in self.model_box["values"]:
                key, url = self.verified
            else:
                return
        except RuntimeError as problem:
            self.status.set(str(problem))
            return
        connection = {"model": model}
        if self.mode.get() == API_KEY:
            try:
                budget = int(self.context_budget.get())
            except ValueError:
                budget = 0
            if not MIN_CONTEXT_BUDGET <= budget <= MAX_CONTEXT_BUDGET:
                self.status.set(f"Enter {MIN_CONTEXT_BUDGET:,}–{MAX_CONTEXT_BUDGET:,} tokens. "
                                f"{RESPONSE_RESERVE:,} are reserved for the reply.")
                return
            connection["context_budgets"] = {**self.saved_connection().get("context_budgets", {}), model: budget}
        if key:
            connection["key"] = key
        if url:
            connection["base_url"] = url
        connections = dict(self.profiles.get(provider, {}).get("connections", {}))
        connections[self.mode.get()] = connection
        if self.persist(connections, self.mode.get(), activate=True):
            self.close()

    def remove(self):
        provider = self.provider.get()
        connections = dict(self.profiles.get(provider, {}).get("connections", {}))
        connections.pop(self.mode.get(), None)
        active = self.profiles.get(provider, {}).get("mode", self.mode.get())
        if active not in connections:
            active = next(iter(PROVIDER_CHOICES[provider]))
        if self.persist(connections, active, activate=False):
            self.change_mode()

    def persist(self, connections, active, *, activate):
        provider = self.provider.get()
        preferences = {"mode": active, "connections": connections}
        try:
            save_preferences(provider, preferences)
        except (error, TypeError) as problem:
            reason = f" (Windows error {problem.winerror})" if isinstance(problem, error) else ""
            self.status.set(f"Could not save to Windows Credential Manager{reason}.")
            return False
        self.on_saved(provider, preferences, activate, self.model_box["values"] if activate else ())
        return True

    def close(self):
        self.cancel.set()
        self.window.destroy()
