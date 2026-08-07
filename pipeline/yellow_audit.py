"""Yellow engine audit: the versioned dialogue matrix for the universal mod.

Writes ``.cache/audit/yellow/<lang>.json`` next to the coverage report.  The
audit classifies every Yellow text label into the same status families the
Red/Blue engine audit uses:

- shared-safe: identical English and identical target translation in Red and
  Yellow (the Red/Blue dialogue translation applies);
- translation-variant: identical English but a different localized Yellow
  translation;
- versioned-required: same label, different English in Yellow (the Yellow
  layer overrides it at runtime);
- yellow-only: label absent from Red (Yellow layer only);
- unmatched: no Yellow corpus translation (English fallback in Yellow);
- deferred-engine: engine-authored Yellow-only strings (Surfing Pikachu
  minigame, ``%s is refusing!``) not reachable through the dialogue layer;
  their corpus qids are listed so a later engine-strings layer can wire them.

No metric is folded into the Red/Blue engine coverage: Yellow counts stay
separate (see ``report["yellow"]``).
"""

from __future__ import annotations

import json
from pathlib import Path

from .yellow import parse_text_catalog, yellow_dialogue_layer

# Engine-authored Yellow-only strings, shared across languages.  These are
# Strings lookups (not ROM dialogue labels) so the dialogue layer cannot carry
# them; the HUD labels are translated manually in
# overrides/<language>/yellow_engine_overrides.json and the refusing message
# is covered through the _RefusingText dialogue label.
DEFERRED_ENGINE_STRINGS = [
    {"engine_key": "%s is refusing!", "corpus_qid": "y.text_9.RefusingText", "status": "covered-by-dialogue-layer", "note": "romText fallback; _RefusingText label ships in dialogue_yellow.lua"},
    {"engine_key": "A: done", "corpus_qid": None, "status": "manual-translation", "note": "yellow_engine_overrides.json"},
    {"engine_key": "HI    %d", "corpus_qid": None, "status": "manual-translation", "note": "yellow_engine_overrides.json"},
    {"engine_key": "New record!", "corpus_qid": None, "status": "manual-translation", "note": "yellow_engine_overrides.json"},
    {"engine_key": "SCORE %d", "corpus_qid": None, "status": "manual-translation", "note": "yellow_engine_overrides.json"},
]


def write_yellow_audit(
    red_text_path: str | Path,
    yellow_text_path: str | Path,
    rows,
    language: str,
    destination: str | Path,
    red_text: dict[str, str] | None = None,
    yellow_text: dict[str, str] | None = None,
    red_translation: dict[str, str] | None = None,
    layer: dict[str, str] | None = None,
    stats: dict | None = None,
) -> Path:
    """Write the Yellow audit JSON and return its path."""
    red_text = red_text if red_text is not None else parse_text_catalog(red_text_path)
    yellow_text = yellow_text if yellow_text is not None else parse_text_catalog(yellow_text_path)
    if layer is None or stats is None:
        layer, stats = yellow_dialogue_layer(
            red_text, yellow_text, rows, language, red_translation=red_translation
        )

    yellow_only = []
    versioned = []
    translation_variants = []
    unmatched = []
    for label, content in yellow_text.items():
        if not content:
            continue
        red_content = red_text.get(label)
        if red_content == content and label not in layer:
            continue
        fr = layer.get(label)
        # A versioned-required label without a corpus match ships the ROM's
        # own Yellow content (the English fallback), not a translation — the
        # audit must not report that as translated.
        is_rom_fallback = fr is not None and red_content is not None and fr == yellow_text.get(label)
        entry = {
            "label": label,
            "yellow_only": red_content is None,
            "translated": bool(fr) and not is_rom_fallback,
            "rom_fallback": is_rom_fallback,
            "english": content[:160],
        }
        if red_content == content:
            entry["translation_variant"] = True
        if red_content is not None:
            entry["red_english"] = red_content[:160]
        if red_content is None:
            yellow_only.append(entry)
        elif red_content == content:
            translation_variants.append(entry)
        else:
            versioned.append(entry)
        if not fr or is_rom_fallback:
            unmatched.append(label)

    deferred = []
    for item in DEFERRED_ENGINE_STRINGS:
        deferred.append({
            "engine_key": item["engine_key"],
            "corpus_qid": item["corpus_qid"],
            "status": item["status"],
            "note": item.get("note", ""),
        })

    audit = {
        "language": language,
        "yellow_labels": stats["yellow_labels"],
        "stats": {k: v for k, v in stats.items() if k not in ("join_report", "yellow_labels")},
        "statuses": {
            "shared-safe": stats["shared_safe"],
            "translation-variant": stats.get("translation_variant", 0),
            "versioned-required": len(versioned),
            "yellow-only": len(yellow_only),
            "matched": stats["matched"],
            "unmatched": len(unmatched),
        },
        "versioned": versioned,
        "translation_variants": translation_variants,
        "yellow_only": yellow_only,
        "unmatched_labels": sorted(unmatched),
        "deferred_engine_strings": deferred,
        "note": "Yellow coverage is reported separately from the Red/Blue engine metrics; unmatched versioned labels keep Yellow ROM English.",
    }
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"{language}_audit.json"
    path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
