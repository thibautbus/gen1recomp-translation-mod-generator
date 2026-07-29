#!/bin/sh
# Regenerate a translation mod from the local corpus and worksheet.
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)

POKE_CORPUS=${POKE_CORPUS:-"$ROOT_DIR/../poke-corpus"}
MODKIT_WORKSHEET=${MODKIT_WORKSHEET:-"$ROOT_DIR/.cache/complete-modkit-worksheet"}
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
    OVERRIDES="$ROOT_DIR/review/overrides.json"
    ENGINE_OVERRIDES="$ROOT_DIR/review/engine_overrides.json"
else
    LANG_DIR="$ROOT_DIR/.cache/build/$TARGET_LANG"
    OVERRIDES=${OVERRIDES:-"$ROOT_DIR/review/$TARGET_LANG/overrides.json"}
    ENGINE_OVERRIDES=${ENGINE_OVERRIDES:-"$ROOT_DIR/review/$TARGET_LANG/engine_overrides.json"}
fi

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
[ -f "$OVERRIDES" ] || OVERRIDES=""
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
if [ -n "$OVERRIDES" ]; then
    python3 "$PIPELINE" align "$RECORDS" --target-lang "$TARGET_LANG" --overrides "$OVERRIDES" -o "$ALIGNED"
else
    python3 "$PIPELINE" align "$RECORDS" --target-lang "$TARGET_LANG" -o "$ALIGNED"
fi

printf '%s\n' "Generating mod: $MOD"
set -- --target-lang "$TARGET_LANG" --modkit-worksheet "$MODKIT_WORKSHEET" --report "$COVERAGE"
[ -n "$OVERRIDES" ] && set -- "$@" --overrides "$OVERRIDES"
[ -n "$ENGINE_OVERRIDES" ] && set -- "$@" --engine-overrides "$ENGINE_OVERRIDES"
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
    print("  %-7s : %s/%s (%.2f%%)" % (name, translated, total, percent))
PY
