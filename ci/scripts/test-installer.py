#!/usr/bin/env python3
"""Pruebas del instalador y del sincronizador de Kiro CLI (sin red ni Android)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
INSTALLER = HERE / "installer.py"
UPSTREAM = HERE / "upstream.py"
EXECUTABLES = ("kiro-cli", "kiro-cli-chat", "kiro-cli-term")


def load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_module(INSTALLER, "kiro_installer")
upstream = load_module(UPSTREAM, "kiro_upstream")


def fake_static_aarch64(size: int = 1_100_000) -> bytes:
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    header = bytearray(64)
    header[0:16] = ident
    header[16:18] = (2).to_bytes(2, "little")
    header[18:20] = (0xB7).to_bytes(2, "little")
    header[20:24] = (1).to_bytes(4, "little")
    header[32:40] = (64).to_bytes(8, "little")
    header[52:54] = (64).to_bytes(2, "little")
    header[54:56] = (56).to_bytes(2, "little")
    header[56:58] = (1).to_bytes(2, "little")
    phdr = bytearray(56)
    phdr[0:4] = (1).to_bytes(4, "little")
    payload = bytes(header) + bytes(phdr)
    return payload + b"\x00" * (size - len(payload))


def build_tar_xz(path: pathlib.Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:xz") as archive:
        for name, data in members.items():
            tmp = path.parent / (pathlib.PurePosixPath(name).name + ".bin")
            tmp.write_bytes(data)
            info = archive.gettarinfo(str(tmp), arcname=name)
            info.mode = 0o755
            with open(tmp, "rb") as handle:
                archive.addfile(info, handle)
            tmp.unlink()


def build_zip(path: pathlib.Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o755 << 16
            archive.writestr(info, data)


def make_archive(tmp: pathlib.Path, kind: str = "tar.xz") -> tuple[pathlib.Path, dict]:
    members = {f"kirocli/bin/{name}": fake_static_aarch64(1_100_000 + index) for index, name in enumerate(EXECUTABLES)}
    path = tmp / ("kirocli-aarch64-linux-musl." + kind.split(".")[-1])
    if kind == "zip":
        build_zip(path, members)
    else:
        build_tar_xz(path, members)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    lock = {
        "schema": "kiro-termux/upstream-lock/v1",
        "product": "kiro-cli",
        "channel": "stable",
        "version": "9.9.9",
        "base_url": "https://example.invalid/stable",
        "platform": {"os": "linux", "architecture": "aarch64", "libc": "musl", "target_triple": "aarch64-unknown-linux-musl"},
        "executables": list(EXECUTABLES),
        "archive_members": [f"kirocli/bin/{name}" for name in EXECUTABLES],
        "default_artifact": kind,
        "artifacts": {kind: {"file": path.name, "sha256": digest, "size": path.stat().st_size}},
    }
    return path, lock


def run_installer(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    base = dict(os.environ)
    base["KIRO_TERMUX_TEST_MODE"] = "1"
    base["TMPDIR"] = base.get("TMPDIR") or tempfile.gettempdir()
    if env:
        base.update(env)
    return subprocess.run([sys.executable, str(INSTALLER), *args], capture_output=True, text=True, env=base, timeout=120)


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="kiro-test."))
        self.archive, self.lock = make_archive(self.tmp)
        self.lock_path = self.tmp / "lock.json"
        self.lock_path.write_text(json.dumps(self.lock))
        self.prefix = self.tmp / "usr"

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_install_and_receipt(self) -> None:
        result = run_installer([
            "install", "--lock", str(self.lock_path), "--asset", str(self.archive),
            "--prefix", str(self.prefix), "--yes", "--no-smoke",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in EXECUTABLES:
            target = self.prefix / "bin" / name
            self.assertTrue(target.is_file(), name)
            self.assertTrue(os.access(target, os.X_OK))
        receipt = json.loads((self.prefix / "lib/kiro-termux/receipt.json").read_text())
        self.assertEqual(receipt["version"], "9.9.9")

    def test_idempotent_without_force(self) -> None:
        args = ["install", "--lock", str(self.lock_path), "--asset", str(self.archive),
                "--prefix", str(self.prefix), "--yes", "--no-smoke"]
        self.assertEqual(run_installer(args).returncode, 0)
        second = run_installer(args)
        self.assertEqual(second.returncode, 0)
        self.assertIn("ya esta instalado", second.stderr)

    def test_dry_run_writes_nothing(self) -> None:
        result = run_installer([
            "install", "--lock", str(self.lock_path), "--asset", str(self.archive),
            "--prefix", str(self.prefix), "--yes", "--no-smoke", "--dry-run",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.prefix / "bin").exists())

    def test_checksum_mismatch(self) -> None:
        self.lock["artifacts"]["tar.xz"]["sha256"] = "0" * 64
        self.lock_path.write_text(json.dumps(self.lock))
        result = run_installer([
            "install", "--lock", str(self.lock_path), "--asset", str(self.archive),
            "--prefix", str(self.prefix), "--yes", "--no-smoke",
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("checksum invalido", result.stderr)

    def test_uninstall(self) -> None:
        install = ["install", "--lock", str(self.lock_path), "--asset", str(self.archive),
                   "--prefix", str(self.prefix), "--yes", "--no-smoke"]
        self.assertEqual(run_installer(install).returncode, 0)
        result = run_installer(["uninstall", "--prefix", str(self.prefix)])
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in EXECUTABLES:
            self.assertFalse((self.prefix / "bin" / name).exists())

    def test_manifest_output(self) -> None:
        result = run_installer(["manifest", "--lock", str(self.lock_path), "--asset", str(self.archive)])
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["version"], "9.9.9")
        self.assertEqual(payload["artifact"]["kind"], "tar.xz")


class ExtractionTests(unittest.TestCase):
    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            archive = tmp / "evil.tar.xz"
            build_tar_xz(archive, {"../evil": b"x"})
            with self.assertRaises(SystemExit):
                installer.extract_members(archive, "tar.xz", ["../evil"], tmp / "out")

    def test_rejects_symlink_member(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            archive = tmp / "link.tar.xz"
            with tarfile.open(archive, "w:xz") as tar:
                info = tarfile.TarInfo("kirocli/bin/kiro-cli")
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            with self.assertRaises(SystemExit):
                installer.extract_members(archive, "tar.xz", ["kirocli/bin/kiro-cli"], tmp / "out")


class UpstreamTests(unittest.TestCase):
    def test_select_artifacts_and_errors(self) -> None:
        manifest = {
            "version": "1.2.3",
            "packages": [
                {"targetTriple": "aarch64-unknown-linux-musl", "fileType": "tarXz",
                 "download": "1.2.3/kirocli-aarch64-linux-musl.tar.xz", "sha256": "a" * 64, "size": 10},
                {"targetTriple": "x86_64-unknown-linux-gnu", "fileType": "tarZst",
                 "download": "1.2.3/kirocli-x86_64-linux.tar.zst", "sha256": "b" * 64, "size": 20},
            ],
        }
        selected = upstream.select_artifacts(manifest)
        self.assertEqual(list(selected), ["tar.xz"])
        self.assertEqual(selected["tar.xz"]["size"], 10)
        with self.assertRaises(SystemExit):
            upstream.select_artifacts({"version": "1.0.0", "packages": []})

    def test_verify_detects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            lock_path = pathlib.Path(raw) / "lock.json"
            lock_path.write_text(json.dumps({
                "schema": "kiro-termux/upstream-lock/v1",
                "version": "1.0.0",
                "base_url": "https://example.invalid/stable",
                "platform": {},
                "artifacts": {"tar.xz": {"file": "a.tar.xz", "sha256": "c" * 64, "size": 123}},
            }))
            manifest = {
                "version": "1.0.0",
                "packages": [{"targetTriple": "aarch64-unknown-linux-musl", "fileType": "tarXz",
                              "download": "1.0.0/a.tar.xz", "sha256": "d" * 64, "size": 123}],
            }
            args = type("Args", (), {"lock": lock_path})()
            with self.assertRaises(SystemExit):
                upstream.cmd_verify(manifest, args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
