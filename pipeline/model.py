from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CorpusRecord:
    qid: str | None
    language: str
    text: str
    game: str = "red"
    source: str = ""
    english: str | None = None
    override: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str | None:
        return self.qid

    @property
    def value(self) -> str:
        return self.override if self.override is not None else self.text


@dataclass
class Alignment:
    qid: str
    game: str
    english: CorpusRecord
    french: CorpusRecord | None
    method: str
    override: str | None = None
    target_lang: str = "fr"
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def target(self) -> CorpusRecord | None:
        """Generic name for the target record (``french`` remains a legacy API)."""
        return self.french

    @property
    def translation(self) -> str | None:
        if self.override is not None:
            return self.override
        return self.french.value if self.french else None

    def as_dict(self) -> dict[str, Any]:
        row = {
            "qid": self.qid,
            "game": self.game,
            "english": self.english.text,
            "target_lang": self.target_lang,
            "translation": self.translation,
            "method": self.method,
        }
        # Keep the old key in French aligned files so existing worksheets and
        # scripts remain readable.  New languages use only translation.
        if self.target_lang == "fr":
            row["french"] = self.translation
        if self.override is not None:
            row["override"] = self.override
        if self.provenance:
            row["provenance"] = self.provenance
        return row
