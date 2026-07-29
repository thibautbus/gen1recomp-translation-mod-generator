from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Iterable, Mapping

from .model import Alignment
from .tokens import check_placeholders, corpus_to_engine
from .tokens import TOKEN_RE


def validate(items: Iterable[Alignment], glyphs: Mapping[str, int] | None = None, expected_version: str | None = None) -> list[dict]:
    items = list(items)
    findings: list[dict] = []
    for item in items:
        if item.translation is None:
            findings.append({"rule": "missing-translation", "qid": item.qid, "severity": "error"})
            continue
        for message in check_placeholders(item.english.text, item.translation):
            findings.append({"rule": "placeholder", "qid": item.qid, "severity": "error", "message": message})
        if glyphs is not None:
            plain = TOKEN_RE.sub("", corpus_to_engine(item.translation))
            for char in plain:
                if char not in glyphs and not char.isspace():
                    findings.append({"rule": "glyph", "qid": item.qid, "severity": "error", "message": repr(char)})
    if expected_version:
        games = {x.game for x in items}
        if games - {expected_version, "both"}:
            findings.append({"rule": "version-conflict", "severity": "error", "message": sorted(games)})
    return findings


def release_gate(
    items: Iterable[Alignment],
    findings: Iterable[dict],
    charmap: Mapping[str, int] | None = None,
    coverage: Mapping | None = None,
) -> tuple[bool, dict]:
    items = list(items)
    findings = list(findings)
    if not charmap:
        findings.append({"rule": "charmap-required", "severity": "error", "message": "release requires an explicit charmap/glyph coverage"})
    if coverage is None:
        findings.append({"rule": "coverage-required", "severity": "error", "message": "release requires the modkit join coverage report"})
    else:
        for status in ("unmatched", "ambiguous"):
            count = sum(len(rows) for rows in coverage.get(status, {}).values())
            if count:
                findings.append({"rule": f"coverage-{status}", "severity": "error", "message": count})
        for kind in ("rom", "engine"):
            section = coverage.get(kind)
            valid = isinstance(section, Mapping)
            translated = section.get("translated") if valid else None
            total = section.get("total") if valid else None
            percent = section.get("percent") if valid else None
            coherent = (type(translated) is int and type(total) is int
                        and type(percent) in (int, float) and math.isfinite(float(percent))
                        and total > 0 and translated >= 0
                        and translated <= total and abs(percent - (translated * 100 / total)) < 0.011)
            if not coherent:
                findings.append({"rule": f"coverage-{kind}-invalid", "severity": "error",
                                 "message": {"translated": translated, "total": total, "percent": percent}})
            elif translated != total or percent < 100.0:
                findings.append({"rule": f"coverage-{kind}-incomplete", "severity": "error",
                                 "message": {"translated": translated, "total": total, "percent": percent}})
            if kind == "engine" and valid:
                for status in ("unmatched", "ambiguous"):
                    value = section.get(status)
                    if value:
                        findings.append({"rule": f"coverage-engine-{status}", "severity": "error", "message": len(value)})
    # Release is gated exclusively by technical checks.  Editorial decisions
    # discovered during playtesting are represented as qid overrides and never
    # become a release-wide review counter.
    ok = bool(items) and not any(x.get("severity") == "error" for x in findings)
    return ok, {"total": len(items), "findings": len(findings)}


def sha1(path: str | Path) -> str:
    h = hashlib.sha1()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
