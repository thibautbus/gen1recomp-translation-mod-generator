"""What every release's mod ships besides its catalogs.

The font profiles and their assets (a pixel TTF per language, registered
through mod.content.font) and the load priority a translation mod takes,
shared by the Red/Blue (pipeline/rby/mod.py), Gold/Silver/Crystal
(pipeline/gsc/mod.py) and FireRed (pipeline/frlg/mod.py) writers.
"""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import tempfile

from .corpus import canonical_language


FONT_PROFILES = {
    "fusion": {
        "warning": None,
        "files": {
            "latin": ("fusion-pixel-10px-proportional-latin.ttf", 10),
            "ja": ("fusion-pixel-8px-proportional-ja.ttf", 8),
            "ko": ("fusion-pixel-10px-proportional-ko.ttf", 10),
        },
        "licenses": (
            Path("OFL.txt"),
            Path("LICENSES/boutique-bitmap-9x9/OFL.txt"),
            Path("LICENSES/ark-pixel/OFL.txt"),
            Path("LICENSES/galmuri/LICENSE.txt"),
        ),
    },
    "pokemon": {
        "warning": "Pokemon Font is 8px; some translated text may overflow.",
        "files": {"latin": ("pokemon-font.ttf", 8), "ja": None, "ko": None},
        "licenses": (Path("LICENSES/pokemon-font/LICENSE.md"),),
    },
}


def _font_variant(language: str) -> str:
    language = canonical_language(language)
    if language == "ja-Hrkt":
        return "ja"
    if language == "ko":
        return "ko"
    return "latin"


def validate_font_profile(language: str, font_profile: str = "fusion") -> str:
    profile = str(font_profile or "fusion").strip().lower()
    if profile not in FONT_PROFILES:
        raise ValueError(f"Unsupported font profile: {font_profile!r}")
    variant = _font_variant(language)
    if FONT_PROFILES[profile]["files"].get(variant) is None:
        raise ValueError(
            f"Font profile {profile!r} has no {canonical_language(language)} glyph variant; "
            "use Fusion Pixel for this language; Pokemon Font is only available "
            "for French, German, Spanish, and Italian."
        )
    return profile


def font_profile_warning(font_profile: str) -> str | None:
    profile = str(font_profile or "fusion").strip().lower()
    if profile not in FONT_PROFILES:
        raise ValueError(f"Unsupported font profile: {font_profile!r}")
    return FONT_PROFILES[profile]["warning"]


def plain_pixel_registration() -> str:
    return '  mod.content.font:register("ttf", {})'


def ttf_registration(
    language: str,
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
) -> str:
    """Return the selected font registration, or Plain Pixel without a source."""
    if font_source is None:
        return plain_pixel_registration()
    profile = validate_font_profile(language, font_profile)
    filename, size = FONT_PROFILES[profile]["files"][_font_variant(language)]
    return (
        '  mod.content.font:register("ttf", '
        f'{{ file = mod.assets:path("fonts/{filename}"), size = {size} }})'
    )


def _font_source_file(source_root: Path, relative: Path) -> Path:
    """Resolve a selected file from either a checkout or extracted archive."""
    direct = source_root / relative
    if direct.is_file():
        return direct
    candidates = [path for path in source_root.rglob(relative.name) if path.is_file()]
    if relative.name == "OFL.txt":
        candidates = [
            path for path in candidates
            if "Fusion Pixel Font" in path.read_text(encoding="utf-8", errors="replace")
        ] or candidates
    suffix = relative.as_posix()
    candidates = [path for path in candidates if path.as_posix().endswith(suffix)] or candidates
    if len(candidates) != 1:
        raise FileNotFoundError(f"font dependency file not found: {relative}")
    return candidates[0]


def install_font_assets(
    destination: Path,
    language: str,
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
) -> None:
    """Copy only the selected font and its applicable release notices."""
    if font_source is None:
        return
    source_root = Path(font_source)
    if not source_root.is_dir():
        raise FileNotFoundError(f"font dependency directory not found: {source_root}")
    profile = validate_font_profile(language, font_profile)
    variant = _font_variant(language)
    selected_files = [
        (relative, _font_source_file(source_root, relative))
        for relative in FONT_PROFILES[profile]["licenses"]
    ]
    selected, _ = FONT_PROFILES[profile]["files"][variant]
    selected_files.append((Path(selected), _font_source_file(source_root, Path(selected))))
    destination.mkdir(parents=True, exist_ok=True)
    target_root = destination / "fonts"
    temporary = Path(tempfile.mkdtemp(prefix=".fonts-", dir=destination))
    try:
        for relative, source in selected_files:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        backup = destination.parent / f".{destination.name}.fonts-old"
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        had_target = target_root.exists()
        if had_target:
            os.replace(target_root, backup)
        try:
            os.replace(temporary, target_root)
        except Exception:
            if had_target and backup.exists() and not target_root.exists():
                os.replace(backup, target_root)
            raise
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(destination / "assets" / "fonts", ignore_errors=True)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


# Keep every language at the same priority; language choice must not affect load order.
TRANSLATION_MOD_PRIORITY = 100
