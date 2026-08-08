#!/bin/sh
# Regenerate a translation mod from the local corpus and worksheet.
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)

POKE_CORPUS=${POKE_CORPUS:-"$ROOT_DIR/../poke-corpus"}
MODKIT_WORKSHEET=${MODKIT_WORKSHEET:-"$ROOT_DIR/.cache/complete-modkit-worksheet"}
ENGINE_SOURCE=${ENGINE_SOURCE:-"$ROOT_DIR/.cache/dependencies/gen1recomp/src"}
TARGET_LANG=${TARGET_LANG:-fr}
case "$TARGET_LANG" in
  ja|JA|jpn|JPN|ja-Hrkt|ja-hrkt) TARGET_LANG=ja-Hrkt ;;
  DEU|deu) TARGET_LANG=de ;;
  SPA|spa) TARGET_LANG=es ;;
  ITA|ita) TARGET_LANG=it ;;
  FR|fra|FRA) TARGET_LANG=fr ;;
esac
TARGET_NAME=${TARGET_NAME:-}
MOD_ID=${MOD_ID:-}

if [ "$TARGET_LANG" = fr ]; then
    LANG_DIR="$ROOT_DIR/.cache/build"
else
    LANG_DIR="$ROOT_DIR/.cache/build/$TARGET_LANG"
fi
CORPUS_OVERRIDES=${CORPUS_OVERRIDES:-"$ROOT_DIR/overrides/$TARGET_LANG/corpus_overrides.json"}
ENGINE_OVERRIDES=${ENGINE_OVERRIDES:-"$ROOT_DIR/overrides/$TARGET_LANG/shared_engine_overrides.json"}

BUILD_DIR=${BUILD_DIR:-"$LANG_DIR"}
if [ -n "${REPORT_DIR:-}" ]; then :; elif [ "$TARGET_LANG" = fr ]; then REPORT_DIR="$ROOT_DIR/.cache/reports"; else REPORT_DIR="$ROOT_DIR/.cache/reports/$TARGET_LANG"; fi

RECORDS="$BUILD_DIR/records.json"
ALIGNED="$BUILD_DIR/aligned.json"
MOD="$BUILD_DIR/mod"
COVERAGE="$REPORT_DIR/coverage.json"
PIPELINE="$ROOT_DIR/scripts/pipeline.py"

die() {
    printf '%s\n' "build-mod.sh: $*" >&2
    exit 1
}

[ -d "$POKE_CORPUS" ] || die "corpus not found: $POKE_CORPUS (set POKE_CORPUS)"
[ -f "$CORPUS_OVERRIDES" ] || CORPUS_OVERRIDES=""
[ -f "$ENGINE_OVERRIDES" ] || ENGINE_OVERRIDES=""
[ -d "$MODKIT_WORKSHEET" ] || die "modkit worksheet not found: $MODKIT_WORKSHEET (set MODKIT_WORKSHEET)"

# A complete worksheet is sufficient to regenerate the mod; no ROM import is
# performed here.  Fail early with the missing catalogue name if it is partial.
for worksheet_file in dialogue.txt species_names.txt move_names.txt item_names.txt trainer_names.txt status_labels.txt strings.lua
do
    [ -f "$MODKIT_WORKSHEET/$worksheet_file" ] || die "incomplete modkit worksheet: missing file $MODKIT_WORKSHEET/$worksheet_file"
done

mkdir -p "$BUILD_DIR" "$REPORT_DIR"

printf '%s\n' "Parsing corpus: $POKE_CORPUS"
python3 "$PIPELINE" parse "$POKE_CORPUS" --target-lang "$TARGET_LANG" -o "$RECORDS"

printf '%s\n' "Aligning translations: $RECORDS"
if [ -n "$CORPUS_OVERRIDES" ]; then
    python3 "$PIPELINE" align "$RECORDS" --target-lang "$TARGET_LANG" --corpus-overrides "$CORPUS_OVERRIDES" -o "$ALIGNED"
else
    python3 "$PIPELINE" align "$RECORDS" --target-lang "$TARGET_LANG" -o "$ALIGNED"
fi

printf '%s\n' "Generating mod: $MOD"
set -- --target-lang "$TARGET_LANG" --modkit-worksheet "$MODKIT_WORKSHEET" --report "$COVERAGE"
[ -n "$CORPUS_OVERRIDES" ] && set -- "$@" --corpus-overrides "$CORPUS_OVERRIDES"
[ -n "$ENGINE_OVERRIDES" ] && set -- "$@" --engine-overrides "$ENGINE_OVERRIDES"
[ -d "$ENGINE_SOURCE" ] && set -- "$@" --engine-source "$ENGINE_SOURCE" --engine-scope "$ROOT_DIR/config/engine_scope.json"
[ -n "$MOD_ID" ] && set -- "$@" --mod-id "$MOD_ID"
[ -n "$TARGET_NAME" ] && set -- "$@" --target-name "$TARGET_NAME"
python3 "$PIPELINE" generate "$ALIGNED" -o "$MOD" "$@"

printf '%s\n' "Build complete."
printf '  records : %s\n  aligned : %s\n  mod     : %s\n  report  : %s\n' \
    "$RECORDS" "$ALIGNED" "$MOD" "$COVERAGE"

python3 - "$COVERAGE" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as stream:
    report = json.load(stream)
for name in ("rom", "engine"):
    section = report.get(name) or {}
    translated = section.get("translated", 0)
    total = section.get("total", 0)
    percent = section.get("percent", 0.0)
    label = "ROM catalog" if name == "rom" else "All engine strings"
    print("  %-28s : %s/%s (%.2f%%)" % (label, translated, total, percent))
if report.get("engine_rby"):
    section = report["engine_rby"]
    print("  %-28s : %s/%s (%.2f%%)" % ("RBY-related engine strings", section.get("translated", 0), section.get("total", 0), section.get("percent", 0.0)))
PY
