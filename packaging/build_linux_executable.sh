#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON=${PYTHON:-python3}
LUA_REPO=https://luajit.org/git/luajit.git
LUA_COMMIT=faaf663340347a78b22ed94c63c24fe090bd9784
RUNTIME="$ROOT/packaging/runtime/luajit"
SPEC="$ROOT/packaging/translation_builder.spec"
DIST="$ROOT/dist"
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/gen1recomp-linux-build.XXXXXX")
LUA_SOURCE="$TMP_ROOT/luajit-source"
RUNTIME_MARKER="$RUNTIME/.linux-build-runtime"

cleanup() {
  rm -rf -- "$TMP_ROOT"
  if [[ -f "$RUNTIME_MARKER" ]]; then
    rm -rf -- "$RUNTIME"
  fi
}
trap cleanup EXIT

cd "$ROOT"
"$PYTHON" -m pip install --requirement packaging/requirements-windows.txt
"$PYTHON" -m unittest discover -s tests

if [[ -e "$RUNTIME" ]]; then
  echo "refusing to overwrite existing runtime: $RUNTIME" >&2
  exit 1
fi
mkdir -p "$RUNTIME"
touch "$RUNTIME_MARKER"

git clone --no-checkout "$LUA_REPO" "$LUA_SOURCE"
git -C "$LUA_SOURCE" checkout --detach "$LUA_COMMIT"
LUA_HEAD=$(git -C "$LUA_SOURCE" rev-parse HEAD)
[[ "$LUA_HEAD" == "$LUA_COMMIT" ]]
make -C "$LUA_SOURCE/src"
install -m 0755 "$LUA_SOURCE/src/luajit" "$RUNTIME/luajit"
cp -R "$LUA_SOURCE/src/jit" "$RUNTIME/jit"

[[ -x "$RUNTIME/luajit" ]]
file "$RUNTIME/luajit" | grep -Eq 'ELF 64-bit.*x86-64'
ldd "$RUNTIME/luajit" | tee "$TMP_ROOT/ldd.txt"
! grep -q 'not found' "$TMP_ROOT/ldd.txt"

VERSION=$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' pyproject.toml | head -n 1)
[[ -n "$VERSION" ]]

for variant in cli gui; do
  GEN1RECOMP_VARIANT="$variant" "$PYTHON" -m PyInstaller --clean --noconfirm "$SPEC"
  binary="$DIST/gen1recomp-translation-mod-generator-$variant"
  "$binary" --self-check
  if [[ "$variant" == gui ]]; then
    xvfb-run -a "$binary" --gui-self-check
  fi
  versioned="$DIST/gen1recomp-translation-mod-generator-${VERSION}-$variant-linux-x86_64"
  rm -f -- "$versioned" "$versioned.tar.gz"
  cp "$binary" "$versioned"
  chmod 0755 "$versioned"
  tar -czf "$versioned.tar.gz" -C "$DIST" "$(basename "$versioned")"
  echo "Built $versioned.tar.gz"
done
