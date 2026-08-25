"""Declarative game and release policies shared by CLI, GUI and builders."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class GameSpec:
    game: str
    generation: int
    corpus_collection: str


@dataclass(frozen=True)
class ReleaseProfile:
    id: str
    generation: int
    games: tuple[str, ...]

    @property
    def corpus_collections(self) -> tuple[str, ...]:
        """Collections derived from the profile's GameSpecs (one source)."""
        return tuple(dict.fromkeys(GAME_SPECS[game].corpus_collection for game in self.games))


@dataclass(frozen=True)
class BuildRequest:
    """Resolved input shared by CLI/GUI orchestration adapters."""
    sources: Mapping[str, Path]
    profile: ReleaseProfile
    language: str
    output_dir: Path | None = None
    font_profile: str = "fusion"

    def validate(self) -> None:
        required = set(self.profile.games)
        provided = set(self.sources)
        missing = sorted(required - provided)
        unexpected = sorted(provided - required)
        if missing or unexpected:
            details = []
            if missing:
                details.append("missing ROM sources: " + ", ".join(missing))
            if unexpected:
                details.append("unexpected ROM sources: " + ", ".join(unexpected))
            raise ValueError("invalid build request: " + "; ".join(details))
        generations = {GAME_SPECS[game].generation for game in self.profile.games}
        if generations != {self.profile.generation}:
            raise ValueError(f"release profile {self.profile.id!r} mixes game generations")

    def source_for(self, game: str) -> Path:
        try:
            return self.sources[game]
        except KeyError as exc:
            raise ValueError(f"missing ROM source for {game!r}") from exc


GAME_SPECS: Mapping[str, GameSpec] = {
    "rb": GameSpec("rb", 1, "RedBlue"),
    "yellow": GameSpec("yellow", 1, "Yellow"),
    "gs": GameSpec("gs", 2, "GoldSilver"),
}

RELEASE_PROFILES: Mapping[str, ReleaseProfile] = {
    "rby": ReleaseProfile("rby", 1, ("rb", "yellow")),
    "gs": ReleaseProfile("gs", 2, ("gs",)),
}

# The collection is the source of truth for the UI language domain.  Keeping
# this mapping here avoids a second, subtly different list in the GUI and CLI.
COLLECTION_LANGUAGES: Mapping[str, tuple[tuple[str, str], ...]] = {
    "RedBlue": (("fr", "French"), ("de", "German"), ("es", "Spanish"),
                ("it", "Italian"), ("ja-Hrkt", "Japanese")),
    "Yellow": (("fr", "French"), ("de", "German"), ("es", "Spanish"),
               ("it", "Italian"), ("ja-Hrkt", "Japanese")),
    "GoldSilver": (("fr", "French"), ("de", "German"), ("es", "Spanish"),
                   ("it", "Italian"), ("ja-Hrkt", "Japanese"), ("ko", "Korean")),
}


def game_spec(game: str) -> GameSpec:
    try:
        return GAME_SPECS[str(game).lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported game: {game!r}") from exc


def release_profile(profile: str) -> ReleaseProfile:
    try:
        return RELEASE_PROFILES[str(profile).lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported release profile: {profile!r}") from exc


def release_profile_for_generation(generation: int) -> ReleaseProfile:
    """Resolve the release policy without duplicating collection mappings."""
    for profile in RELEASE_PROFILES.values():
        if profile.generation == int(generation):
            return profile
    raise ValueError(f"unsupported generation: {generation!r}")


def languages_for_collection(collection: str) -> tuple[tuple[str, str], ...]:
    try:
        return COLLECTION_LANGUAGES[collection]
    except KeyError as exc:
        raise ValueError(f"unsupported corpus collection: {collection!r}") from exc
