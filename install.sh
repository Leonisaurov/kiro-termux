#!/bin/sh
set -eu

# Instalador de Kiro CLI para Termux.
# Uso:  curl -fsSL https://raw.githubusercontent.com/Leonisaurov/kiro-termux/main/install.sh | sh
# tambien funciona desde un checkout:  ./install.sh [opciones]

REPO="${KIRO_TERMUX_REPO:-Leonisaurov/kiro-termux}"
REF="${KIRO_TERMUX_REF:-main}"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)"
if [ -n "${SCRIPT_DIR:-}" ] && [ -f "$SCRIPT_DIR/ci/scripts/installer.py" ]; then
    exec python3 "$SCRIPT_DIR/ci/scripts/installer.py" "$@"
fi

: "${TMPDIR:=/data/data/com.termux/files/usr/tmp}"
export TMPDIR
mkdir -p "$TMPDIR"
[ -d "$TMPDIR" ] && [ -w "$TMPDIR" ] || { echo "TMPDIR no escribible: $TMPDIR" >&2; exit 1; }

INSTALLER="$(mktemp "$TMPDIR/kiro-termux-installer.XXXXXX")"
trap 'rm -f "$INSTALLER"' EXIT HUP INT TERM

echo "Descargando instalador de Kiro CLI ($REPO@$REF)..." >&2
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "https://raw.githubusercontent.com/$REPO/$REF/ci/scripts/installer.py" -o "$INSTALLER"
elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$INSTALLER" "https://raw.githubusercontent.com/$REPO/$REF/ci/scripts/installer.py"
else
    echo "Se requiere curl o wget." >&2
    exit 1
fi

exec python3 "$INSTALLER" "$@"
