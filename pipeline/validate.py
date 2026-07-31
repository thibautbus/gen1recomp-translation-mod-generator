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
    """Apply the release gate to technical ROM checks and report diagnostics.

    The ROM aggregate is the only coverage completeness gate.  Engine and RBY
    coverage remain useful diagnostics, but their incompleteness (including
    malformed sections) is informational because untranslated strings safely
    fall back to English at runtime.  The returned summary includes the full
    diagnostic list under ``findings`` (and its count under ``finding_count``).
    """
    items = list(items)
    # Keep a caller-provided list live so the CLI/reporting layer can print
    # release diagnostics (including informational engine warnings).
    findings = findings if isinstance(findings, list) else list(findings)
    if not charmap:
        findings.append({"rule": "charmap-required", "severity": "error", "message": "release requires an explicit charmap/glyph coverage"})
    if coverage is None:
        findings.append({"rule": "coverage-required", "severity": "error", "message": "release requires the modkit join coverage report"})
    elif not isinstance(coverage, Mapping):
        findings.append({"rule": "coverage-invalid", "severity": "error", "message": "coverage report must be an object"})
    else:
        # These are the joiner's ROM-level diagnostics.  They remain release
        # errors even though engine diagnostics below are informational.
        for status in ("unmatched", "ambiguous"):
            if status not in coverage:
                findings.append({"rule": f"coverage-{status}-invalid", "severity": "error",
                                 "message": f"ROM coverage report requires a {status} catalog map"})
                continue
            value = coverage.get(status)
            if value is None:
                findings.append({"rule": f"coverage-{status}-invalid", "severity": "error",
                                 "message": f"ROM {status} must be a catalog map"})
                continue
            if not isinstance(value, Mapping):
                findings.append({"rule": f"coverage-{status}-invalid", "severity": "error",
                                 "message": f"ROM {status} must be a catalog map"})
                continue
            count = 0
            malformed = False
            for rows in value.values():
                if isinstance(rows, (str, bytes)) or not isinstance(rows, (list, tuple, set, frozenset)):
                    malformed = True
                    continue
                count += len(rows)
            if malformed:
                findings.append({"rule": f"coverage-{status}-invalid", "severity": "error",
                                 "message": f"ROM {status} entries must be arrays"})
            if count:
                findings.append({"rule": f"coverage-{status}", "severity": "error", "message": count})

        def coverage_shape(section: object) -> tuple[bool, dict[str, object]]:
            if not isinstance(section, Mapping):
                return False, {"translated": None, "total": None, "percent": None}
            translated = section.get("translated")
            total = section.get("total")
            percent = section.get("percent")
            try:
                numeric_percent = float(percent) if type(percent) in (int, float) else None
            except (OverflowError, TypeError, ValueError):
                numeric_percent = None
            coherent = (type(translated) is int and type(total) is int
                        and numeric_percent is not None and math.isfinite(numeric_percent)
                        and total > 0 and translated >= 0
                        and translated <= total and abs(numeric_percent - (translated * 100 / total)) < 0.011)
            return coherent, {"translated": translated, "total": total, "percent": percent}

        rom = coverage.get("rom")
        rom_coherent, rom_values = coverage_shape(rom)
        if not rom_coherent:
            findings.append({"rule": "coverage-rom-invalid", "severity": "error", "message": rom_values})
        elif rom_values["translated"] != rom_values["total"] or rom_values["percent"] < 100.0:
            findings.append({"rule": "coverage-rom-incomplete", "severity": "error", "message": rom_values})

        # Full engine coverage is deliberately informational.  Keep emitting
        # diagnostics so callers/CI can see unresolved work, but never turn
        # these findings into release failures.
        engine = coverage.get("engine")
        if engine is None:
            findings.append({"rule": "coverage-engine-missing", "severity": "warning", "message": "full engine coverage is informational and unavailable"})
        else:
            engine_coherent, engine_values = coverage_shape(engine)
            if not engine_coherent:
                findings.append({"rule": "coverage-engine-invalid", "severity": "warning", "message": engine_values})
            elif engine_values["translated"] != engine_values["total"] or engine_values["percent"] < 100.0:
                findings.append({"rule": "coverage-engine-incomplete", "severity": "warning", "message": engine_values})
            if isinstance(engine, Mapping):
                for status in ("unmatched", "ambiguous"):
                    value = engine.get(status)
                    if value in (None, [], {}, (), set(), frozenset()):
                        continue
                    if isinstance(value, Mapping):
                        count = sum(len(rows) for rows in value.values()
                                     if isinstance(rows, (list, tuple, set, frozenset)) and not isinstance(rows, (str, bytes)))
                        malformed = any(isinstance(rows, (str, bytes)) or not isinstance(rows, (list, tuple, set, frozenset))
                                        for rows in value.values())
                    elif isinstance(value, (list, tuple, set, frozenset)) and not isinstance(value, (str, bytes)):
                        count, malformed = len(value), False
                    else:
                        count, malformed = 0, True
                    if malformed:
                        findings.append({"rule": f"coverage-engine-{status}-invalid", "severity": "warning",
                                         "message": f"engine {status} has malformed entries"})
                    elif count:
                        findings.append({"rule": f"coverage-engine-{status}", "severity": "warning", "message": count})

        # RBY scope is optional and informational.  Validate it only when a
        # section is present; a missing source is represented by a warning key.
        rby = coverage.get("engine_rby")
        if rby is not None:
            rby_coherent, rby_values = coverage_shape(rby)
            if not rby_coherent:
                findings.append({"rule": "coverage-engine-rby-invalid", "severity": "warning", "message": rby_values})
            elif rby_values["translated"] != rby_values["total"] or rby_values["percent"] < 100.0:
                findings.append({"rule": "coverage-engine-rby-incomplete", "severity": "warning", "message": rby_values})
            if isinstance(rby, Mapping):
                for status in ("unmatched", "ambiguous"):
                    value = rby.get(status)
                    if value in (None, [], {}, (), set(), frozenset()):
                        continue
                    if isinstance(value, Mapping):
                        count = sum(len(rows) for rows in value.values()
                                     if isinstance(rows, (list, tuple, set, frozenset)) and not isinstance(rows, (str, bytes)))
                        malformed = any(isinstance(rows, (str, bytes)) or not isinstance(rows, (list, tuple, set, frozenset))
                                        for rows in value.values())
                    elif isinstance(value, (list, tuple, set, frozenset)) and not isinstance(value, (str, bytes)):
                        count, malformed = len(value), False
                    else:
                        count, malformed = 0, True
                    if malformed:
                        findings.append({"rule": f"coverage-engine-rby-{status}-invalid", "severity": "warning",
                                         "message": f"RBY engine {status} has malformed entries"})
                    elif count:
                        findings.append({"rule": f"coverage-engine-rby-{status}", "severity": "warning", "message": count})
        if coverage.get("engine_rby_warning"):
            findings.append({"rule": "coverage-engine-rby-warning", "severity": "warning", "message": coverage.get("engine_rby_warning")})
        elif rby is None:
            findings.append({"rule": "coverage-engine-rby-missing", "severity": "warning", "message": "RBY engine coverage is informational and unavailable"})
    # Release is gated exclusively by technical checks.  Editorial decisions
    # discovered during playtesting are represented as qid overrides and never
    # become a release-wide review counter.
    ok = bool(items) and not any(x.get("severity") == "error" for x in findings)
    return ok, {"total": len(items), "findings": findings, "finding_count": len(findings)}


def sha1(path: str | Path) -> str:
    h = hashlib.sha1()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
