"""Small Tkinter front end for the interactive translation builder."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import queue
import threading
from typing import Callable

from . import builder
from .project import project_version, work_root
from .orchestration import build_request
from .specs import BuildRequest, release_profile_for_generation


@dataclass(frozen=True)
class GuiInputs:
    generation: int
    rom_paths: dict[str, Path]
    language: str
    font_profile: str
    output_dir: Path


# Named by game, not by generation, matching the CLI's _prompt_generation --
# "generation" is engine vocabulary the user does not need.
GENERATIONS = (
    (1, "Red, Blue and Yellow"),
    (2, "Gold, Silver and Crystal"),
)

ROMS_BY_GENERATION = {1: ("rb", "yellow"), 2: ("gs", "crystal")}

# Fixed wrap width (pixels) for every Hint.TLabel. Without it, hint labels
# with no wraplength request exactly as much width as their longest line
# needs -- since the games/ROM hint text changes with the selected
# generation, the window would otherwise grow or shrink width-wise every
# time the user picks a different option instead of staying put.
HINT_WRAPLENGTH = 640


def generation_label(value: int) -> str:
    value = int(value)
    for code, name in GENERATIONS:
        if code == value:
            return f"{name} (generation {code})"
    raise builder.BuildError(f"Invalid games selection: {value!r}")


def generation_code(value: str) -> int:
    raw = value.strip()
    for code, name in GENERATIONS:
        if raw == str(code) or raw == f"{name} (generation {code})":
            return code
    raise builder.BuildError(f"Invalid games selection: {value!r}")


def language_code(value: str, generation: int = 1) -> str:
    """Return a canonical language code from a combobox label or code."""
    raw = value.strip()
    for code, name in builder.languages_for_generation(generation):
        if raw == code or raw == f"{name} ({code})":
            return code
    raise builder.BuildError(f"Invalid language selection: {value!r}")


def languages_for_generation(generation: int) -> tuple[tuple[str, str], ...]:
    return builder.languages_for_generation(generation)


def language_label(code: str, generation: int = 1) -> str:
    code = builder.canonical_language(code)
    for candidate, name in languages_for_generation(generation):
        if candidate == code:
            return f"{name} ({candidate})"
    raise builder.BuildError(f"Invalid language selection: {code!r}")


def font_profile_label(profile: str, language: str = "fr") -> str:
    profile = str(profile).strip().lower()
    if profile == "fusion":
        size = 8 if builder.canonical_language(language) == "ja-Hrkt" else 10
        return f"Fusion Pixel by TakWolf, proportional {size}px (recommended)"
    if profile == "pokemon":
        return "Pokemon Font clone by Superpencil, 8px (some text may overflow)"
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


def available_font_profiles(language: str) -> tuple[str, ...]:
    """Profiles the form may offer for a language's glyph coverage."""
    return ("fusion",) if builder.canonical_language(language) in {"ja-Hrkt", "ko"} else ("fusion", "pokemon")


def validate_inputs(
    generation: int,
    rom_paths: dict[str, str | Path],
    language: str,
    output_dir: str | Path,
    font_profile: str = "fusion",
) -> GuiInputs:
    """Validate GUI values using the same ROM checks as the CLI.

    ``rom_paths`` is a game -> path association, not a positional
    rb/yellow pair: which games it must contain depends on ``generation``.
    """
    if generation not in ROMS_BY_GENERATION:
        raise builder.BuildError(f"Invalid games selection: {generation!r}")
    code = language_code(language, generation)
    profile = builder.validate_font_profile(code, font_profile_code(font_profile))
    if not str(output_dir).strip():
        raise builder.BuildError("An output directory is required.")
    output = Path(output_dir).expanduser()
    resolved: dict[str, Path] = {}
    for game in ROMS_BY_GENERATION[generation]:
        raw = rom_paths.get(game)
        if not raw or not str(raw).strip():
            display = {"gs": "Gold or Silver", "rb": "Red or Blue"}.get(game, game.capitalize())
            raise builder.BuildError(f"A Pokemon {display} ROM path is required.")
        path = Path(raw).expanduser()
        if not path.is_file():
            raise builder.BuildError(f"File not found: {path}")
        if game == "rb":
            builder.verify_rb_rom(path)
        elif game == "gs":
            builder.verify_gs_rom(path)
        elif game == "crystal":
            builder.verify_crystal_rom(path)
        else:
            builder.verify_rom(path, game)
        resolved[game] = path.resolve()
    return GuiInputs(generation, resolved, code, profile, output.resolve())


def coverage_lines(path: str | Path) -> list[str]:
    """Return the compact coverage text shown after a successful build."""
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    lines: list[str] = []
    # ROM aggregates first (broad Red/Blue, then narrower Yellow), then
    # engine-authored-text metrics from most to least specific: RBY/Gold's
    # own filtered scope reads before the unfiltered "All engine strings"
    # total, since that total is the least actionable number here.
    section = report.get("rom") or {}
    lines.append(
        f"Red Blue ROM aggregate: {int(section.get('translated', 0))}/{int(section.get('total', 0))} "
        f"({float(section.get('percent', 0.0)):.2f}%)"
    )
    yellow = (report.get("yellow") or {}).get("coverage", {}).get("rom") or {}
    if yellow.get("total"):
        lines.append(f"Yellow ROM aggregate: {int(yellow.get('translated', 0))}/{int(yellow.get('total', 0))} ({float(yellow.get('percent', 0.0)):.2f}%)")
    section = report.get("engine_rby") or {}
    if section.get("available", True) and section.get("total"):
        lines.append(
            "RBY-related engine strings: "
            f"{int(section.get('translated', 0))}/{int(section.get('total', 0))} "
            f"({float(section.get('percent', 0.0)):.2f}%)"
        )
    elif report.get("engine_rby_warning"):
        lines.append(f"RBY-related engine strings: unavailable ({report['engine_rby_warning']})")
    section = report.get("engine_gen2") or {}
    if section.get("total"):
        lines.append(
            "Gold and Silver-related engine strings: "
            f"{int(section.get('translated', 0))}/{int(section.get('total', 0))} "
            f"({float(section.get('percent', 0.0)):.2f}%)"
        )
    section = report.get("engine") or {}
    lines.append(
        f"All engine strings: {int(section.get('translated', 0))}/{int(section.get('total', 0))} "
        f"({float(section.get('percent', 0.0)):.2f}%)"
    )
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
        # _build_widgets() lands on generation 1 (Red, Blue and Yellow), the
        # tallest ROM-row layout; lock that in as the floor so switching to
        # Gold's single ROM row hides rows without the window itself
        # shrinking around them -- the static 720x620 guess above was
        # smaller than this layout actually needs.
        self.root.update_idletasks()
        self.root.minsize(self.root.winfo_width(), self.root.winfo_height())
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
            font=("TkDefaultFont", 9), wraplength=HINT_WRAPLENGTH,
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
        self.generation_var = tk.StringVar(value=generation_label(1))
        self.rom_vars = {game: tk.StringVar() for game in ("rb", "yellow", "gs", "crystal")}
        self.language_var = tk.StringVar(value=language_label("fr"))
        self.font_profile_var = tk.StringVar(value=font_profile_label("fusion"))
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        self.games_hint_var = tk.StringVar(value="Which games do you want to translate?")
        ttk.Label(frame, textvariable=self.games_hint_var, style="Hint.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Label(frame, text="Games").grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.generation_box = ttk.Combobox(
            frame, textvariable=self.generation_var,
            values=[generation_label(code) for code, _ in GENERATIONS], state="readonly",
        )
        self.generation_box.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8)
        self.generation_box.bind("<<ComboboxSelected>>", lambda _event: self._sync_generation())
        self._controls.append((self.generation_box, "readonly"))

        # Gold occupies the same grid row as Red/Blue: only one game's ROM
        # row is ever shown at a time, toggled by _sync_generation (the GUI
        # is a flat form, not a wizard). Crystal takes row 4 (formerly
        # Blue's own field, free since Red and Blue share byte-identical
        # game text and only need one field between them).
        rom_fields = (
            ("rb", 2, "Required to extract shared Pokémon Red/Blue game text and data. Either ROM works: Red and Blue share identical text.", "Pokemon Red or Blue ROM (US)"),
            ("gs", 2, "Required to extract Pokémon Gold and Silver game text and data.", "Pokemon Gold or Silver ROM (US)"),
            ("crystal", 4, "Required to extract Pokémon Crystal-specific game text and data.", "Pokemon Crystal ROM (US)"),
            ("yellow", 6, "Required to extract Pokémon Yellow-specific game text and data.", "Pokemon Yellow ROM (US)"),
        )
        self.rom_widgets: dict[str, tuple] = {}
        for game, row, description, label in rom_fields:
            hint = ttk.Label(frame, text=description, style="Hint.TLabel")
            hint.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 2))
            name = ttk.Label(frame, text=label)
            name.grid(row=row + 1, column=0, sticky="w", pady=(0, 6))
            entry = ttk.Entry(frame, textvariable=self.rom_vars[game])
            entry.grid(row=row + 1, column=1, sticky="ew", padx=8)
            browse = ttk.Button(frame, text="Browse…", command=lambda v=self.rom_vars[game]: self._browse_file(v))
            browse.grid(row=row + 1, column=2)
            self.rom_widgets[game] = (hint, name, entry, browse)
            self._controls.extend(((entry, "normal"), (browse, "normal")))

        ttk.Label(frame, text="The generated translation mod ZIP will be placed here.", style="Hint.TLabel").grid(row=12, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Label(frame, text="Output directory").grid(row=13, column=0, sticky="w", pady=(0, 6))
        output_entry = ttk.Entry(frame, textvariable=self.output_var)
        output_entry.grid(row=13, column=1, sticky="ew", padx=8)
        output_browse = ttk.Button(frame, text="Browse…", command=lambda: self._browse_directory(self.output_var))
        output_browse.grid(row=13, column=2)
        self._controls.extend(((output_entry, "normal"), (output_browse, "normal")))

        ttk.Label(frame, text="Select the language used for the generated translation mod.", style="Hint.TLabel").grid(row=8, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Label(frame, text="Language").grid(row=9, column=0, sticky="w", pady=(0, 6))
        self.language_box = ttk.Combobox(
            frame, textvariable=self.language_var,
            values=[language_label(code, 1) for code, _ in languages_for_generation(1)], state="readonly",
        )
        self.language_box.grid(row=9, column=1, columnspan=2, sticky="ew", padx=8)
        self.language_box.bind("<<ComboboxSelected>>", lambda _event: self._sync_font_profile())
        self._controls.append((self.language_box, "readonly"))
        ttk.Label(frame, text="Select the font used for translated text.", style="Hint.TLabel").grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Label(frame, text="Font profile").grid(row=11, column=0, sticky="w", pady=(0, 6))
        self.font_profile_box = ttk.Combobox(
            frame, textvariable=self.font_profile_var,
            values=[font_profile_label(profile, self.language_var.get()) for profile in ("fusion", "pokemon")],
            state="readonly",
        )
        self.font_profile_box.grid(row=11, column=1, columnspan=2, sticky="ew", padx=8)
        self._controls.append((self.font_profile_box, "readonly"))
        self._sync_font_profile()
        self.log_toggle = ttk.Button(frame, text="Show log", command=self.toggle_log)
        self.log_toggle.grid(row=14, column=0, sticky="w", pady=(16, 4))
        self.log_text = tk.Text(frame, height=8, bg="#111315", fg="#d8dee9", insertbackground="#f1f3f4", state="disabled")
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=15, column=0, columnspan=3, sticky="ew", pady=8)
        self.status_label = ttk.Label(frame, textvariable=self.status_var)
        self.status_label.grid(row=16, column=0, columnspan=2, sticky="w")
        self.start_button = ttk.Button(frame, text="Build translation mod", command=self.start)
        self.start_button.grid(row=16, column=2, sticky="e")
        self._controls.append((self.start_button, "normal"))
        frame.columnconfigure(1, weight=1)
        self.toggle_log()
        self._sync_generation()

    def _sync_generation(self):
        generation = generation_code(self.generation_var.get())
        active = set(ROMS_BY_GENERATION[generation])
        if generation == 1:
            self.games_hint_var.set(
                "Which games do you want to translate?\n"
                "Red, Blue and Yellow share one translation: select the two "
                "ROMs below (Red or Blue, whichever you own, plus Yellow)."
            )
        elif generation == 2:
            self.games_hint_var.set(
                "Which games do you want to translate?\n"
                "Gold, Silver and Crystal share one translation: select the "
                "two ROMs below (Gold or Silver, whichever you own, plus "
                "Crystal)."
            )
        else:
            self.games_hint_var.set("Which games do you want to translate?")
        for game, widgets in self.rom_widgets.items():
            for widget in widgets:
                if game in active:
                    widget.grid()
                else:
                    widget.grid_remove()
        languages = languages_for_generation(generation)
        current = builder.canonical_language(self.language_var.get())
        allowed = {code for code, _ in languages}
        if current not in allowed:
            current = languages[0][0]
            self.language_var.set(language_label(current, generation))
        self.language_box.configure(values=[language_label(code, generation) for code, _ in languages])
        # Setting language_var programmatically (above) does not fire the
        # language box's own <<ComboboxSelected>> handler, so the font
        # profile box must be resynced here too -- otherwise switching
        # generation away from a language that locks the font profile
        # (Japanese, Korean) leaves it showing the locked value and
        # disabled even after the language reset to one that allows a
        # choice.
        self._sync_font_profile()

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
        generation = generation_code(self.generation_var.get())
        language = language_code(self.language_var.get(), generation)
        profiles = available_font_profiles(language)
        if len(profiles) == 1:
            self.font_profile_var.set(font_profile_label("fusion", language))
        elif font_profile_code(self.font_profile_var.get()) == "fusion":
            self.font_profile_var.set(font_profile_label("fusion", self.language_var.get()))
        self.font_profile_box.configure(
            values=[font_profile_label(profile, language) for profile in profiles],
        )
        self.font_profile_box.configure(state="disabled" if len(profiles) == 1 else "readonly")

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
            self.log_text.grid(row=15, column=0, columnspan=3, sticky="nsew")
            self.progress.grid(row=16, column=0, columnspan=3, sticky="ew", pady=8)
            self.status_label.grid(row=17, column=0, columnspan=2, sticky="w")
            self.start_button.grid(row=17, column=2, sticky="e")
            self.log_toggle.configure(text="Hide log")
        else:
            self.log_text.grid_remove()
            self.progress.grid(row=15, column=0, columnspan=3, sticky="ew", pady=8)
            self.status_label.grid(row=16, column=0, columnspan=2, sticky="w")
            self.start_button.grid(row=16, column=2, sticky="e")
            self.log_toggle.configure(text="Show log")

    def start(self):
        from tkinter import messagebox
        try:
            generation = generation_code(self.generation_var.get())
            rom_paths = {game: self.rom_vars[game].get() for game in ROMS_BY_GENERATION[generation]}
            inputs = validate_inputs(generation, rom_paths, self.language_var.get(), self.output_var.get(), self.font_profile_var.get())
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
            language_name = dict(languages_for_generation(inputs.generation))[inputs.language]
            request = BuildRequest(
                inputs.rom_paths,
                release_profile_for_generation(inputs.generation),
                inputs.language, inputs.output_dir, inputs.font_profile,
            )
            # The workspace (gen1recomp/poke-corpus checkouts, LuaJIT-driven
            # mod validation) stays anchored near the executable rather than
            # inside the user's chosen output directory: LuaJIT's io.open
            # takes narrow (ANSI-codepage) paths on Windows, so a non-ASCII
            # output folder name -- reported as "Build failed", exit code 1,
            # from a real folder with an accent in it -- silently broke
            # every file the loader driver tried to open. Anchoring here
            # also means the (multi-hundred-MB) dependency downloads are
            # reused across builds no matter what output folder is picked,
            # instead of being tied to one and re-fetched if it changes.
            workspace = work_root() / ".cache"
            output = build_request(
                request, language_name=language_name, luajit=luajit,
                workspace_root=workspace, output_dir=inputs.output_dir,
                log_fn=lambda message: self._append_log(message),
                status_fn=lambda message: self._post(lambda: self.status_var.set(message)),
            )
            build_cache = "interactive" if inputs.generation == 1 else "interactive-gs"
            coverage = workspace / build_cache / inputs.language / "coverage.json"
            self._post(lambda: self._complete(output, coverage))
        except (RuntimeError, ValueError, OSError) as error:
            message = str(error)
            self._post(lambda: self._failed(message))
        except Exception as error:  # GUI boundary: never strand the disabled form.
            message = f"Unexpected build error: {error}"
            self._append_log(message)
            self._post(lambda: self._failed(message))

    def _complete(self, output: Path, coverage: Path | None):
        from tkinter import messagebox
        self._finish()
        details = f"File generated at:\n{output}"
        if coverage is not None and coverage.is_file():
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
