#!/usr/bin/env python3
"""Herramientas de sincronizacion con el upstream de Kiro CLI.

El upstream solo publica un manifiesto en `stable/latest/manifest.json`; no hay
manifiesto por version. Por eso `verify` solo puede comprobar los checksums
mientras la version fijada siga siendo la ultima. Cuando el upstream avanza,
`update` regenera el lock con la nueva version y `status` lo detecta.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.request
from datetime import datetime, timezone

DEFAULT_MANIFEST = os.environ.get(
    "KIRO_TERMUX_MANIFEST",
    "https://prod.download.cli.kiro.dev/stable/latest/manifest.json",
)
DEFAULT_LOCK = pathlib.Path(__file__).resolve().parents[1] / "upstream" / "kiro-cli.lock.json"
TARGET_TRIPLE = "aarch64-unknown-linux-musl"
KIND_BY_FILETYPE = {"tarXz": "tar.xz", "tarZst": "tar.zst", "tarGz": "tar.gz", "zip": "zip"}


def fail(message: str) -> "None":
    print(f"[x] {message}", file=sys.stderr)
    raise SystemExit(1)


def fetch_manifest(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.loads(response.read())
    except Exception as exc:
        fail(f"No se pudo leer el manifiesto upstream {url}: {exc}")


def select_artifacts(manifest: dict) -> dict:
    artifacts = {}
    for package in manifest.get("packages", []):
        if package.get("targetTriple") != TARGET_TRIPLE:
            continue
        kind = KIND_BY_FILETYPE.get(package.get("fileType"))
        if kind is None:
            continue
        artifacts[kind] = {
            "file": package["download"].split("/")[-1],
            "sha256": package["sha256"],
            "size": int(package["size"]),
        }
    if not artifacts:
        fail(f"el manifiesto no contiene artefactos para {TARGET_TRIPLE}")
    return artifacts


def load_lock(path: pathlib.Path) -> dict:
    if not path.is_file():
        fail(f"No existe el lock: {path}")
    return json.loads(path.read_text())


def cmd_latest(manifest: dict, _args: argparse.Namespace) -> None:
    print(manifest["version"])


def cmd_status(manifest: dict, args: argparse.Namespace) -> None:
    lock = load_lock(args.lock)
    upstream = manifest["version"]
    payload = {
        "lock_version": lock["version"],
        "upstream_version": upstream,
        "update_available": lock["version"] != upstream,
    }
    print(json.dumps(payload, indent=2))
    if payload["update_available"] and args.fail_on_update:
        raise SystemExit(2)


def cmd_verify(manifest: dict, args: argparse.Namespace) -> None:
    lock = load_lock(args.lock)
    if manifest["version"] != lock["version"]:
        print(
            f"[!] lock fijado en {lock['version']}, upstream latest es {manifest['version']}; "
            "no se pueden verificar los checksums contra upstream",
            file=sys.stderr,
        )
        return
    upstream = select_artifacts(manifest)
    errors = 0
    for kind, pinned in lock["artifacts"].items():
        current = upstream.get(kind)
        if current is None:
            print(f"[!] {kind}: no esta en el manifiesto upstream", file=sys.stderr)
            errors += 1
            continue
        for field in ("file", "sha256", "size"):
            if pinned.get(field) != current.get(field):
                print(
                    f"[x] {kind}.{field}: lock={pinned.get(field)!r} upstream={current.get(field)!r}",
                    file=sys.stderr,
                )
                errors += 1
    if errors:
        raise SystemExit(1)
    print(f"[ok] lock {lock['version']} coincide con el manifiesto upstream")


def cmd_update(manifest: dict, args: argparse.Namespace) -> None:
    lock = load_lock(args.lock)
    version = args.to or manifest["version"]
    if version != manifest["version"] and not args.allow_stale:
        fail(
            f"el upstream solo publica la version {manifest['version']}; "
            f"no se puede fijar {version} (usa --allow-stale para forzarlo)"
        )
    artifacts = select_artifacts(manifest)
    lock["version"] = version
    lock["base_url"] = manifest.get("base_url") or lock.get("base_url")
    lock["artifacts"] = artifacts
    lock.setdefault("platform", {})
    lock["platform"]["architecture"] = "aarch64"
    lock["platform"]["libc"] = "musl"
    lock["platform"]["os"] = "linux"
    lock["platform"]["target_triple"] = TARGET_TRIPLE
    lock["default_artifact"] = "tar.xz" if "tar.xz" in artifacts else next(iter(artifacts))
    lock["archive_members"] = [
        f"kirocli/bin/{name}" for name in lock.get("executables", ["kiro-cli", "kiro-cli-chat", "kiro-cli-term"])
    ]
    lock["build"] = {
        "target_triple": TARGET_TRIPLE,
        "synced_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    args.lock.write_text(json.dumps(lock, indent=2) + "\n")
    print(f"[ok] lock actualizado a {version}: {args.lock}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincroniza el lock de Kiro CLI con el upstream")
    parser.add_argument("command", choices=("latest", "status", "verify", "update"))
    parser.add_argument("--lock", type=pathlib.Path, default=DEFAULT_LOCK)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--to", help="version objetivo para update")
    parser.add_argument("--allow-stale", action="store_true")
    parser.add_argument("--fail-on-update", action="store_true")
    args = parser.parse_args()
    manifest = fetch_manifest(args.manifest)
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.-]+)?", str(manifest.get("version", ""))):
        fail("version upstream invalida")
    {"latest": cmd_latest, "status": cmd_status, "verify": cmd_verify, "update": cmd_update}[args.command](manifest, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
