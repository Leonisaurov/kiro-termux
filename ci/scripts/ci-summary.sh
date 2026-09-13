#!/usr/bin/env bash
# shellcheck shell=bash
# Renderiza un resumen de CI reutilizable.
set -euo pipefail

ci_summary() {
    local version="${VERSION:-desconocida}"
    local artifact="${ARTIFACT:-desconocido}"
    local sha="${SHA256:-desconocido}"
    local smoke="${SMOKE:-no ejecutado}"
    local update="${UPDATE_AVAILABLE:-desconocido}"
    local out="${GITHUB_STEP_SUMMARY:-/dev/stdout}"
    {
        echo "## Kiro CLI para Termux (aarch64)"
        echo
        echo "| Campo | Valor |"
        echo "| --- | --- |"
        echo "| Version upstream | \`${version}\` |"
        echo "| Artefacto | \`${artifact}\` |"
        echo "| sha256 | \`${sha}\` |"
        echo "| Smoke test (qemu aarch64) | ${smoke} |"
        echo "| Actualizacion upstream disponible | ${update} |"
    } >>"$out"
}
