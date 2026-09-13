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


def fake_elf(etype: int, interpreter: bytes, size: int) -> bytes:
    """ELF aarch64 minimo con un PT_INTERP; el tamano coincide con las secciones."""
    header = bytearray(64)
    header[0:16] = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    header[16:18] = etype.to_bytes(2, "little")
    header[18:20] = (0xB7).to_bytes(2, "little")
    header[20:24] = (1).to_bytes(4, "little")
    header[32:40] = (64).to_bytes(8, "little")
    header[52:54] = (64).to_bytes(2, "little")
    header[54:56] = (56).to_bytes(2, "little")
    header[56:58] = (1).to_bytes(2, "little")
    header[58:60] = (64).to_bytes(2, "little")
    header[60:62] = (1).to_bytes(2, "little")
    shoff = size - 64
    header[40:48] = shoff.to_bytes(8, "little")
    phdr = bytearray(56)
    phdr[0:4] = (3).to_bytes(4, "little")
    phdr[8:16] = (120).to_bytes(8, "little")
    phdr[32:40] = (len(interpreter)).to_bytes(8, "little")
    body = bytes(header) + bytes(phdr) + interpreter
    return body + b"\x00" * (shoff - len(body)) + bytes(64)


def fake_glibc_bun(size: int = 500_000) -> bytes:
    return fake_elf(2, b"/lib/ld-linux-aarch64.so.1\x00", size)


def fake_android_bun(size: int = 300_000) -> bytes:
    return fake_elf(3, b"/system/bin/linker64\x00", size)


def fake_static_aarch64(size: int = 1_400_000, with_bun: bool = True) -> bytes:
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
    payload = bytes(header) + bytes(phdr) + b"\x00\x00" + installer.RESOLV_MARKER
    if with_bun:
        payload += installer.DEFAULT_BUN_MARKER + fake_glibc_bun()
    return payload + b"\x00" * (size - len(payload))


def build_tar_gz(path: pathlib.Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            tmp = path.parent / (pathlib.PurePosixPath(name).name + ".bin")
            tmp.write_bytes(data)
            info = archive.gettarinfo(str(tmp), arcname=name)
            info.mode = 0o755
            with open(tmp, "rb") as handle:
                archive.addfile(info, handle)
            tmp.unlink()


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


def make_archive(tmp: pathlib.Path, kind: str = "tar.xz", with_bun: bool = True) -> tuple[pathlib.Path, dict]:
    members = {f"kirocli/bin/{name}": fake_static_aarch64(1_400_000 + index, with_bun) for index, name in enumerate(EXECUTABLES)}
    path = tmp / ("kirocli-aarch64-linux-musl." + kind.split(".")[-1])
    if kind == "zip":
        build_zip(path, members)
    else:
        build_tar_xz(path, members)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    bun = tmp / "bun-runtime.tar.gz"
    build_tar_gz(bun, {"bun": fake_android_bun()})
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
        "runtime": {
            "product": "bun",
            "version": "1.2.13",
            "url": bun.as_uri(),
            "sha256": hashlib.sha256(bun.read_bytes()).hexdigest(),
            "size": bun.stat().st_size,
            "member": "bun",
            "interpreter": "/system/bin/linker64",
        },
        "embedded_bun": {
            "marker": installer.DEFAULT_BUN_MARKER.decode(),
            "interpreter": installer.DEFAULT_BUN_INTERP.decode(),
            "required_binary": "kiro-cli-chat",
        },
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
            wrapper = self.prefix / "bin" / name
            real = self.prefix / "libexec/kiro-termux" / name
            self.assertTrue(wrapper.is_file(), name)
            self.assertTrue(os.access(wrapper, os.X_OK))
            self.assertTrue(real.is_file(), name)
            self.assertTrue(os.access(real, os.X_OK))
            data = real.read_bytes()
            self.assertIn(installer.RESOLV_REPLACEMENT, data)
            self.assertNotIn(installer.RESOLV_MARKER, data)
        chat = (self.prefix / "libexec/kiro-termux" / "kiro-cli-chat").read_bytes()
        padded = fake_android_bun() + b"\x00" * (len(fake_glibc_bun()) - len(fake_android_bun()))
        self.assertIn(padded, chat)
        self.assertNotIn(installer.DEFAULT_BUN_MARKER + fake_glibc_bun(), chat)
        self.assertTrue((self.prefix / "etc/kiro-termux/resolv.conf").is_file())
        receipt = json.loads((self.prefix / "etc/kiro-termux/receipt.json").read_text())
        self.assertEqual(receipt["version"], "9.9.9")
        self.assertEqual(receipt["patches"]["resolv_conf"]["counts"]["kiro-cli"], 1)
        self.assertEqual(receipt["patches"]["embedded_bun"]["counts"]["kiro-cli-chat"], 1)

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
            self.assertFalse((self.prefix / "libexec/kiro-termux" / name).exists())
        self.assertFalse((self.prefix / "etc/kiro-termux/receipt.json").exists())

    def test_stage_is_cleaned_up(self) -> None:
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        result = run_installer([
            "install", "--lock", str(self.lock_path), "--asset", str(self.archive),
            "--prefix", str(self.prefix), "--yes", "--no-smoke",
        ], env={"TMPDIR": str(scratch)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(scratch.iterdir()), [])

    def test_manifest_output(self) -> None:
        result = run_installer(["manifest", "--lock", str(self.lock_path), "--asset", str(self.archive)])
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["version"], "9.9.9")
        self.assertEqual(payload["artifact"]["kind"], "tar.xz")
        self.assertEqual(payload["runtime"]["version"], "1.2.13")

    def test_install_requires_embedded_bun(self) -> None:
        nobun = self.tmp / "nobun"
        nobun.mkdir(exist_ok=True)
        archive, lock = make_archive(nobun, with_bun=False)
        lock_path = self.tmp / "nobun-lock.json"
        lock_path.write_text(json.dumps(lock))
        result = run_installer([
            "install", "--lock", str(lock_path), "--asset", str(archive),
            "--prefix", str(self.tmp / "usr2"), "--yes", "--no-smoke",
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("no se encontro el Bun glibc embebido", result.stderr)


class ExtractionTests(unittest.TestCase):
    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            archive = tmp / "evil.tar.xz"
            build_tar_xz(archive, {"../evil": b"x"})
            with self.assertRaises(SystemExit):
                installer.extract_members(archive, "tar.xz", ["../evil"], tmp / "out")

    def test_patch_requires_resolv_marker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            binary = tmp / "kiro-cli"
            binary.write_bytes(fake_static_aarch64().replace(installer.RESOLV_MARKER, b"\x00" * len(installer.RESOLV_MARKER)))
            with self.assertRaises(SystemExit):
                installer.patch_resolv_conf(binary)

    def test_patch_replaces_every_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            binary = pathlib.Path(raw) / "kiro-cli"
            binary.write_bytes(fake_static_aarch64() + b"\x00\x00" + installer.RESOLV_MARKER + b"\x00" * 16)
            count = installer.patch_resolv_conf(binary)
            self.assertEqual(count, 2)
            self.assertNotIn(installer.RESOLV_MARKER, binary.read_bytes())

    def test_embedded_bun_patch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            binary = pathlib.Path(raw) / "kiro-cli-chat"
            binary.write_bytes(fake_static_aarch64())
            blobs = installer.patch_embedded_bun(
                binary, fake_android_bun(), installer.DEFAULT_BUN_MARKER, installer.DEFAULT_BUN_INTERP
            )
            self.assertEqual(len(blobs), 1)
            padded = fake_android_bun() + b"\x00" * (len(fake_glibc_bun()) - len(fake_android_bun()))
            self.assertEqual(blobs[0]["sha256"], hashlib.sha256(padded).hexdigest())
            self.assertIn(padded, binary.read_bytes())

    def test_embedded_bun_patch_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            binary = pathlib.Path(raw) / "kiro-cli"
            binary.write_bytes(fake_static_aarch64(with_bun=False))
            blobs = installer.patch_embedded_bun(
                binary, fake_android_bun(), installer.DEFAULT_BUN_MARKER, installer.DEFAULT_BUN_INTERP
            )
            self.assertEqual(blobs, [])

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
