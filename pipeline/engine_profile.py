"""Explicit engine compatibility profiles for the Gen 2 pipeline.

The published pipeline uses the stable ``pinned`` profile, whose dependency
is currently gen1recomp v0.2.41.  New Gen 2 registries are an opt-in developer
overlay and must never be selected merely because a newer checkout happens to
be present in the workspace.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


PINNED_PROFILE = "pinned"
UPSTREAM_PROFILE = "upstream-local"
ENGINE_PROFILES = (PINNED_PROFILE, UPSTREAM_PROFILE)


@dataclass(frozen=True)
class EngineProfile:
    name: str
    supports_engine_strings: bool
    supports_rom_text: bool
    supports_gen2_registries: bool


PROFILES = {
    # The pinned profile still resolves engine Strings()/Strings.source()
    # callsites (match_gs_engine_strings verifies the checkout against the
    # pin itself instead of trusting it outright) -- only the genuinely new
    # Gen 2 registries and RomText support are upstream-local exclusives.
    PINNED_PROFILE: EngineProfile(PINNED_PROFILE, True, False, False),
    UPSTREAM_PROFILE: EngineProfile(UPSTREAM_PROFILE, True, True, True),
}


def normalize_engine_profile(value: str | None) -> str:
    """Validate the explicit profile, defaulting to the published pin."""
    name = PINNED_PROFILE if value is None else str(value).strip().lower()
    if name not in PROFILES:
        raise ValueError(
            f"unsupported engine profile {value!r}; choose {PINNED_PROFILE!r} or {UPSTREAM_PROFILE!r}"
        )
    return name


def profile_for(value: str | None) -> EngineProfile:
    return PROFILES[normalize_engine_profile(value)]


def validate_engine_profile_and_source(engine_profile: str, engine_source: str | Path | None) -> str:
    """Validate the engine_profile/engine_source pairing every build entry
    point (builder.build(), gs_mod.build_gs()) requires identically.

    Raises ValueError -- callers that need a BuildError (the CLI-facing
    ones) catch it and re-raise with the same message, keeping this helper
    free of a dependency on builder.py's BuildError.
    """
    profile = normalize_engine_profile(engine_profile)
    if profile == UPSTREAM_PROFILE:
        if engine_source is None:
            raise ValueError(
                "the upstream-local engine profile requires an explicit --engine-source checkout"
            )
    elif engine_source is not None:
        raise ValueError(
            "engine_source requires the explicit 'upstream-local' engine profile"
        )
    return profile


def validate_upstream_checkout(path: str | Path) -> Path:
    """Validate a local developer checkout without turning it into a pin."""
    root = Path(path).resolve()
    if not (root / "src").is_dir():
        raise ValueError(f"upstream engine checkout has no src directory: {root}")
    if not (root / "tools" / "modkit.py").is_file():
        raise ValueError(f"upstream engine checkout has no tools/modkit.py: {root}")
    return root


def checkout_revision(path: str | Path) -> str:
    """Return an informational local revision, never written to project config."""
    try:
        value = subprocess.check_output(
            ["git", "-C", str(Path(path).resolve()), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "local-unversioned"
    return value or "local-unversioned"
