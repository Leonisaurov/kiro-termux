#!/usr/bin/env bash
# shellcheck shell=bash
# Entorno comun para los scripts y workflows de kiro-termux.
set -euo pipefail

export KIRO_TERMUX_REPO="${KIRO_TERMUX_REPO:-Leonisaurov/kiro-termux}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

# Ruta del lock dentro del workspace actual.
kiro_lock_path() {
    printf '%s/ci/upstream/kiro-cli.lock.json\n' "${GITHUB_WORKSPACE:-$(pwd)}"
}

# Version fijada, leida del lock.
kiro_locked_version() {
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$(kiro_lock_path)"
}
