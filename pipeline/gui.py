"""Small Tkinter front end for the interactive translation builder."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import queue
import threading
from typing import Callable

from . import builder
from .project import project_version


@dataclass(frozen=True)
class GuiInputs:
    red_rom: Path
    blue_rom: Path
    language: str
    font_profile: str
    output_dir: Path


def language_code(value: str) -> str:
    """Return a canonical language code from a combobox label or code."""
    raw = value.strip()
    for code, name in builder.LANGUAGES:
        if raw == code or raw == f"{name} ({code})":
            return code
    raise builder.BuildError(f"Invalid language selection: {value!r}")


def language_label(code: str) -> str:
    code = builder.canonical_language(code)
    for candidate, name in builder.LANGUAGES:
        if candidate == code:
            return f"{name} ({candidate})"
    raise builder.BuildError(f"Invalid language selection: {code!r}")


def font_profile_label(profile: str) -> str:
    profile = str(profile).strip().lower()
    if profile == "fusion":
        return "Fusion Pixel proportional 8px (recommended)"
    if profile == "pokemon":
        return "Pokemon Font 8px (may overflow some text)"
    raise builder.BuildError(f"Invalid font profile selection: {profile!r}")


def font_profile_code(value: str) -> str:
    raw = value.strip().lower()
    if raw.startswith("fusion pixel"):
        return "fusion"
    if raw.startswith("pokemon font"):
        return "pokemon"
    if raw in builder.FONT_PROFILES:
        return raw
    raise builder.BuildError(f"Invalid font profile selection: {value!r}")


def validate_inputs(
    red_rom: str | Path,
    blue_rom: str | Path,
    language: str,
    output_dir: str | Path,
    font_profile: str = "fusion",
) -> GuiInputs:
    """Validate GUI values using the same ROM checks as the CLI."""
    code = language_code(language)
    profile = builder.validate_font_profile(code, font_profile_code(font_profile))
    red = Path(red_rom).expanduser()
    blue = Path(blue_rom).expanduser()
    if not str(output_dir).strip():
        raise builder.BuildError("An output directory is required.")
    output = Path(output_dir).expanduser()
    if not red.is_file():
        raise builder.BuildError(f"File not found: {red}")
    if not blue.is_file():
        raise builder.BuildError(f"File not found: {blue}")
    builder.verify_rom(red, "red")
    builder.verify_rom(blue, "blue")
    return GuiInputs(red.resolve(), blue.resolve(), code, profile, output.resolve())


def coverage_lines(path: str | Path) -> list[str]:
    """Return the compact coverage text shown after a successful build."""
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    lines: list[str] = []
    for key, label in (("rom", "ROM catalog"), ("engine", "All engine strings")):
        section = report.get(key) or {}
        lines.append(
            f"{label}: {int(section.get('translated', 0))}/{int(section.get('total', 0))} "
            f"({float(section.get('percent', 0.0)):.2f}%)"
        )
    section = report.get("engine_rby") or {}
    if section.get("available", True) and section.get("total"):
        lines.append(
            "RBY-related engine strings: "
            f"{int(section.get('translated', 0))}/{int(section.get('total', 0))} "
            f"({float(section.get('percent', 0.0)):.2f}%)"
        )
    elif report.get("engine_rby_warning"):
        lines.append(f"RBY-related engine strings: unavailable ({report['engine_rby_warning']})")
    return lines


class TranslationBuilderApp:
    """Tk app; all pipeline work runs on a worker thread."""

    def __init__(self, root=None):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root if root is not None else tk.Tk()
        self.root.title(f"Gen1Recomp Translation Mod Generator v{project_version()}")
        self.root.minsize(720, 620)
        self.building = False
        self._log_visible = False
        self._controls = []
        self._events = queue.SimpleQueue()
        self._configure_style()
        self._build_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._poll_events)

    def _configure_style(self):
        self.root.configure(bg="#202124")
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except self.tk.TclError:
            pass
        style.configure("TFrame", background="#202124")
        style.configure("TLabel", background="#202124", foreground="#f1f3f4")
        style.configure(
            "Hint.TLabel", background="#202124", foreground="#9aa0a6",
            font=("TkDefaultFont", 9),
        )
        style.configure("TButton", padding=6)
        style.configure("TEntry", fieldbackground="#303134", foreground="#f1f3f4")
        style.configure("TCombobox", fieldbackground="#303134", foreground="#f1f3f4")
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#303134")],
            foreground=[("readonly", "#f1f3f4")],
            selectbackground=[("readonly", "#303134")],
            selectforeground=[("readonly", "#f1f3f4")],
        )

    def _build_widgets(self):
        tk, ttk = self.tk, self.ttk
        self.red_var = tk.StringVar()
        self.blue_var = tk.StringVar()
        self.language_var = tk.StringVar(value=language_label("fr"))
        self.font_profile_var = tk.StringVar(value=font_profile_label("fusion"))
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        fields = (
            (0, "Required to extract shared and Pokémon Red-specific game text and data.", "Pokemon Red ROM (US)", self.red_var, self._browse_file),
            (2, "Required to extract Pokémon Blue-specific game text and data.", "Pokemon Blue ROM (US)", self.blue_var, self._browse_file),
            (8, "The generated translation mod ZIP and temporary .cache workspace will be placed here.", "Output directory", self.output_var, self._browse_directory),
        )
        for row, description, label, variable, command in fields:
            ttk.Label(frame, text=description, style="Hint.TLabel").grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 2))
            ttk.Label(frame, text=label).grid(row=row + 1, column=0, sticky="w", pady=(0, 6))
            entry = ttk.Entry(frame, textvariable=variable)
            entry.grid(row=row + 1, column=1, sticky="ew", padx=8)
            browse = ttk.Button(frame, text="Browse…", command=lambda v=variable, c=command: c(v))
            browse.grid(row=row + 1, column=2)
            self._controls.extend(((entry, "normal"), (browse, "normal")))
        ttk.Label(frame, text="Select the language used for the generated translation mod.", style="Hint.TLabel").grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Label(frame, text="Language").grid(row=5, column=0, sticky="w", pady=(0, 6))
        self.language_box = ttk.Combobox(
            frame, textvariable=self.language_var,
            values=[language_label(code) for code, _ in builder.LANGUAGES], state="readonly",
        )
        self.language_box.grid(row=5, column=1, columnspan=2, sticky="ew", padx=8)
        self.language_box.bind("<<ComboboxSelected>>", lambda _event: self._sync_font_profile())
        self._controls.append((self.language_box, "readonly"))
        ttk.Label(frame, text="Select the font used for translated text.", style="Hint.TLabel").grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Label(frame, text="Font profile").grid(row=7, column=0, sticky="w", pady=(0, 6))
        self.font_profile_box = ttk.Combobox(
            frame, textvariable=self.font_profile_var,
            values=[font_profile_label(profile) for profile in ("fusion", "pokemon")],
            state="readonly",
        )
        self.font_profile_box.grid(row=7, column=1, columnspan=2, sticky="ew", padx=8)
        self._controls.append((self.font_profile_box, "readonly"))
        self._sync_font_profile()
        self.log_toggle = ttk.Button(frame, text="Show log", command=self.toggle_log)
        self.log_toggle.grid(row=10, column=0, sticky="w", pady=(16, 4))
        self.log_text = tk.Text(frame, height=8, bg="#111315", fg="#d8dee9", insertbackground="#f1f3f4", state="disabled")
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=11, column=0, columnspan=3, sticky="ew", pady=8)
        self.status_label = ttk.Label(frame, textvariable=self.status_var)
        self.status_label.grid(row=12, column=0, columnspan=2, sticky="w")
        self.start_button = ttk.Button(frame, text="Build translation mod", command=self.start)
        self.start_button.grid(row=12, column=2, sticky="e")
        self._controls.append((self.start_button, "normal"))
        frame.columnconfigure(1, weight=1)
        self.toggle_log()

    def _browse_file(self, variable):
        from tkinter import filedialog
        value = filedialog.askopenfilename(title="Select ROM", filetypes=(("Game Boy ROM", "*.gb *.gbc"), ("All files", "*.*")))
        if value:
            variable.set(value)

    def _browse_directory(self, variable):
        from tkinter import filedialog
        value = filedialog.askdirectory(title="Select output directory")
        if value:
            variable.set(value)

    def _sync_font_profile(self):
        japanese = language_code(self.language_var.get()) == "ja-Hrkt"
        if japanese:
            self.font_profile_var.set(font_profile_label("fusion"))
        self.font_profile_box.configure(state="disabled" if japanese else "readonly")

    def _post(self, callback: Callable[[], None]):
        self._events.put(callback)

    def _poll_events(self):
        while not self._events.empty():
            self._events.get()()
        self.root.after(50, self._poll_events)

    def _append_log(self, message: str):
        def update():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + ("" if message.endswith("\n") else "\n"))
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self._post(update)

    def toggle_log(self):
        self._log_visible = not self._log_visible
        if self._log_visible:
            self.log_text.grid(row=11, column=0, columnspan=3, sticky="nsew")
            self.progress.grid(row=12, column=0, columnspan=3, sticky="ew", pady=8)
            self.status_label.grid(row=13, column=0, columnspan=2, sticky="w")
            self.start_button.grid(row=13, column=2, sticky="e")
            self.log_toggle.configure(text="Hide log")
        else:
            self.log_text.grid_remove()
            self.progress.grid(row=11, column=0, columnspan=3, sticky="ew", pady=8)
            self.status_label.grid(row=12, column=0, columnspan=2, sticky="w")
            self.start_button.grid(row=12, column=2, sticky="e")
            self.log_toggle.configure(text="Show log")

    def start(self):
        from tkinter import messagebox
        try:
            inputs = validate_inputs(self.red_var.get(), self.blue_var.get(), self.language_var.get(), self.output_var.get(), self.font_profile_var.get())
        except (builder.BuildError, OSError, ValueError) as error:
            messagebox.showerror("Unable to build", str(error), parent=self.root)
            return
        action = "downloaded" if builder.is_frozen() else "cloned"
        if not messagebox.askyesno(
            "Confirm build",
            f"Pinned Gen1Recomp and poke-corpus dependencies will be {action} into the private .cache directory.\n\nContinue?",
            parent=self.root,
        ):
            return
        self.building = True
        self._set_controls(True)
        self.progress.start(12)
        self.status_var.set("Checking prerequisites")
        threading.Thread(target=self._worker, args=(inputs,), daemon=True).start()

    def _worker(self, inputs: GuiInputs):
        try:
            luajit = builder.check_prerequisites()
            output = builder.build(
                inputs.red_rom, inputs.blue_rom, inputs.language,
                dict(builder.LANGUAGES)[inputs.language], luajit,
                font_profile=inputs.font_profile,
                workspace_root=inputs.output_dir / ".cache",
                output_dir=inputs.output_dir,
                log_fn=lambda message: self._append_log(message),
                status_fn=lambda message: self._post(lambda: self.status_var.set(message)),
            )
            coverage = inputs.output_dir / ".cache" / "interactive" / inputs.language / "coverage.json"
            self._post(lambda: self._complete(output, coverage))
        except (builder.BuildError, ValueError, OSError) as error:
            message = str(error)
            self._post(lambda: self._failed(message))
        except Exception as error:  # GUI boundary: never strand the disabled form.
            message = f"Unexpected build error: {error}"
            self._append_log(message)
            self._post(lambda: self._failed(message))

    def _complete(self, output: Path, coverage: Path):
        from tkinter import messagebox
        self._finish()
        details = f"File generated at:\n{output}"
        if coverage.is_file():
            details += "\n\n" + "\n".join(coverage_lines(coverage))
        self.status_var.set("Build complete")
        messagebox.showinfo("Build complete", details, parent=self.root)

    def _failed(self, message: str):
        from tkinter import messagebox
        self._finish()
        self.status_var.set("Build failed")
        messagebox.showerror("Build failed", message, parent=self.root)

    def _finish(self):
        self.building = False
        self.progress.stop()
        self._set_controls(False)

    def _set_controls(self, disabled: bool):
        for widget, enabled_state in self._controls:
            widget.configure(state="disabled" if disabled else enabled_state)
        if not disabled:
            self._sync_font_profile()

    def close(self):
        from tkinter import messagebox
        if self.building:
            messagebox.showwarning("Build in progress", "Please wait for the build to finish.", parent=self.root)
            return
        self.root.destroy()


def main() -> int:
    app = TranslationBuilderApp()
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
