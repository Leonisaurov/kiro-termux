#!/usr/bin/env python3
"""Instalador determinista y transaccional de Kiro CLI para Termux/Android.

Kiro CLI es un agente de IA de AWS distribuido como binario estatico (musl)
para aarch64. Este instalador descarga el artefacto oficial fijado por
checksum, valida que sea un ELF estatico aarch64, y lo instala en el prefijo
de Termux sin ejecutar el instalador upstream (que asume glibc y es interactivo).

Solo depende de la libreria estandar de Python 3.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile

SCHEMA = "kiro-termux/upstream-lock/v1"
REPO = os.environ.get("KIRO_TERMUX_REPO", "Leonisaurov/kiro-termux")
REF = os.environ.get("KIRO_TERMUX_REF", "main")
DEFAULT_PREFIX = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
DEFAULT_TMP = os.environ.get("TMPDIR", "/data/data/com.termux/files/usr/tmp")
DEFAULT_LOCK_URL = f"https://raw.githubusercontent.com/{REPO}/{REF}/ci/upstream/kiro-cli.lock.json"
BINARY_PREFIX = "kiro-cli"
RECEIPT_REL = "lib/kiro-termux/receipt.json"
CHUNK = 1 << 20
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_ARCHIVES = ("tar.xz", "tar.zst", "tar.gz", "zip")


def fail(message: str) -> "None":
    print(f"[x] {message}", file=sys.stderr)
    raise SystemExit(1)


def info(message: str) -> None:
    print(message, file=sys.stderr)


def test_mode() -> bool:
    return os.environ.get("KIRO_TERMUX_TEST_MODE") == "1"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kiro-termux-installer",
        description="Instala Kiro CLI (estatico aarch64-musl) en Termux/Android",
    )
    parser.add_argument("command", nargs="?", default="install",
                        choices=("install", "verify", "manifest", "uninstall"))
    parser.add_argument("--lock", help="ruta o URL del lock de upstream (por defecto: el del repo)")
    parser.add_argument("--archive", choices=("auto",) + SUPPORTED_ARCHIVES, default="auto",
                        help="formato de archivo a descargar (por defecto: el del lock)")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help=f"prefijo Termux (default: {DEFAULT_PREFIX})")
    parser.add_argument("--dest", help="directorio destino de los binarios (por defecto: <prefix>/bin)")
    parser.add_argument("--asset", help="artefacto local o file:// para probar el flujo sin red")
    parser.add_argument("--yes", action="store_true", help="no pedir confirmacion")
    parser.add_argument("--dry-run", action="store_true", help="validar sin instalar")
    parser.add_argument("--force", action="store_true", help="reinstalar aunque la version coincida")
    parser.add_argument("--no-smoke", action="store_true", help="omitir el smoke test de --version")
    parser.add_argument("--stage-dir", help="directorio persistente para descarga/extraccion (util en CI)")
    parser.add_argument("--keep-archive", action="store_true", help="conservar el archivo descargado")
    parser.add_argument("--version", action="version", version="kiro-termux-installer 1.0.0")
    return parser.parse_args(argv)


def preflight(args: argparse.Namespace) -> pathlib.Path:
    device_commands = ("install", "uninstall")
    if args.command in device_commands and not test_mode():
        if not pathlib.Path("/data/data/com.termux").is_dir():
            fail("Este instalador requiere Termux/Android")
        if os.uname().machine not in ("aarch64", "arm64"):
            fail(f"Arquitectura no soportada: {os.uname().machine}")
    tmp = pathlib.Path(DEFAULT_TMP)
    if not test_mode() and not os.access(tmp.parent, os.W_OK):
        tmp = pathlib.Path(tempfile.gettempdir())
        os.environ["TMPDIR"] = str(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    if not os.access(tmp, os.W_OK):
        fail(f"TMPDIR no escribible: {tmp}")
    if args.command in ("install",) and not args.dry_run:
        dest = pathlib.Path(args.dest or pathlib.Path(args.prefix) / "bin")
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            fail(f"No se puede crear el destino {dest}: {exc}")
    return tmp


def load_lock(args: argparse.Namespace, tmp: pathlib.Path) -> dict:
    source = args.lock or os.environ.get("KIRO_TERMUX_LOCK") or DEFAULT_LOCK_URL
    local = pathlib.Path(source)
    raw: bytes
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in ("http", "https"):
        info(f"Leyendo lock: {source}")
        raw = download_bytes(source, tmp)
    elif parsed.scheme == "file":
        raw = pathlib.Path(urllib.parse.unquote(parsed.path)).read_bytes()
    else:
        if not local.is_file():
            fail(f"No existe el lock: {source}")
        raw = local.read_bytes()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"Lock invalido: {exc}")
    return validate_lock(data)


def validate_lock(data: dict) -> dict:
    if data.get("schema") != SCHEMA:
        fail(f"schema de lock no soportado: {data.get('schema')!r}")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.-]+)?", str(data.get("version", ""))):
        fail("version del lock invalida")
    base = str(data.get("base_url", ""))
    if not base.startswith("https://"):
        fail("base_url del lock debe ser HTTPS")
    platform = data.get("platform", {})
    if platform.get("architecture") != "aarch64" or platform.get("os") != "linux":
        fail("el lock no apunta a linux/aarch64")
    arts = data.get("artifacts")
    if not isinstance(arts, dict) or not arts:
        fail("artifacts del lock invalidos")
    for kind, art in arts.items():
        if kind not in SUPPORTED_ARCHIVES:
            fail(f"formato de archivo no soportado en el lock: {kind}")
        if not isinstance(art, dict):
            fail(f"artefacto invalido: {kind}")
        if not isinstance(art.get("file"), str) or not art["file"]:
            fail(f"{kind}: file invalido")
        if not HEX64.fullmatch(str(art.get("sha256", ""))):
            fail(f"{kind}: sha256 invalido")
        if not isinstance(art.get("size"), int) or art["size"] <= 0:
            fail(f"{kind}: size invalido")
    default = data.get("default_artifact")
    if default not in arts:
        fail("default_artifact no esta en artifacts")
    members = data.get("archive_members")
    if not isinstance(members, list) or not members:
        fail("archive_members invalidos")
    return data


def select_artifact(args: argparse.Namespace, lock: dict) -> tuple[str, dict]:
    kind = lock["default_artifact"] if args.archive == "auto" else args.archive
    if kind not in lock["artifacts"]:
        fail(f"el lock no publica el formato {kind}")
    return kind, lock["artifacts"][kind]


def archive_url(lock: dict, art: dict) -> str:
    return f"{lock['base_url'].rstrip('/')}/{lock['version']}/{art['file']}"


def download_bytes(url: str, tmp: pathlib.Path) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    except Exception as exc:
        fail(f"No se pudo leer {url}: {exc}")


def _resume_offset(dst: pathlib.Path) -> int:
    return dst.stat().st_size if dst.is_file() else 0


def download(url: str, dst: pathlib.Path, expected_size: int) -> pathlib.Path:
    """Descarga con reanudacion por Range y progreso en stderr."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        shutil.copyfile(pathlib.Path(urllib.parse.unquote(parsed.path)), dst)
        return dst
    if parsed.scheme == "":
        shutil.copyfile(pathlib.Path(url), dst)
        return dst
    offset = _resume_offset(dst)
    if offset == expected_size:
        info(f"Descarga completa en cache: {dst.name}")
        return dst
    headers = {"User-Agent": "kiro-termux/1"}
    mode = "wb"
    if offset:
        headers["Range"] = f"bytes={offset}-"
        info(f"Reanudando descarga en {offset} bytes...")
        mode = "ab"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status = getattr(response, "status", 200)
            if offset and status != 206:
                offset = 0
                mode = "wb"
                dst.unlink(missing_ok=True)
            total = expected_size
            done = offset
            last = -1
            with open(dst, mode) as handle:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    pct = int(done * 100 / total) if total else 0
                    if pct != last and pct % 2 == 0:
                        info(f"  {pct:3d}%  {done}/{total} bytes")
                        last = pct
    except Exception as exc:
        fail(f"Fallo la descarga de {url}: {exc} (se puede reintentar y reanudar)")
    return dst


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> str:
    pure = pathlib.PurePosixPath(name)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        fail(f"miembro inseguro en el archivo: {name}")
    return name


def extract_members(archive: pathlib.Path, kind: str, members: list[str], dest: pathlib.Path) -> dict[str, pathlib.Path]:
    """Extrae solo los miembros indicados, rechazando enlaces y rutas inseguras."""
    dest.mkdir(parents=True, exist_ok=True)
    wanted = {_safe_member(m): m for m in members}
    found: dict[str, pathlib.Path] = {}
    if kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            for name in wanted:
                if name not in names:
                    fail(f"falta {name} en el archivo")
                mode = (zf.getinfo(name).external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    fail(f"el archivo contiene un enlace: {name}")
                target = dest / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
                found[name] = target
        return found
    tar_mode = {"tar.gz": "r:gz", "tar.xz": "r:xz", "tar.zst": "r:zst"}[kind]
    with tarfile.open(archive, tar_mode) as tf:
        for member in tf:
            if member.name not in wanted:
                continue
            if not member.isfile() or member.issym() or member.islnk():
                fail(f"miembro no regular en el archivo: {member.name}")
            target = dest / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                fail(f"no se puede leer {member.name}")
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
            found[member.name] = target
            try:
                os.chmod(target, member.mode | 0o600)
            except OSError:
                pass
    for name in wanted:
        if name not in found:
            fail(f"falta {name} en el archivo")
    return found


def elf_is_static_aarch64(path: pathlib.Path) -> tuple[bool, str]:
    with open(path, "rb") as handle:
        head = handle.read(64)
    if head[:4] != b"\x7fELF":
        return False, "no es ELF"
    if head[4] != 2:
        return False, "no es ELF de 64 bits"
    if head[5] != 1:
        return False, "no es little-endian"
    machine = int.from_bytes(head[18:20], "little")
    if machine != 0xB7:
        return False, f"maquina {machine:#x} no es aarch64"
    etype = int.from_bytes(head[16:18], "little")
    if etype not in (2, 3):
        return False, f"tipo ELF invalido {etype:#x}"
    phoff = int.from_bytes(head[32:40], "little")
    phentsize = int.from_bytes(head[54:56], "little")
    phnum = int.from_bytes(head[56:58], "little")
    with open(path, "rb") as handle:
        handle.seek(phoff)
        table = handle.read(phentsize * phnum)
    for index in range(phnum):
        entry = table[index * phentsize:(index + 1) * phentsize]
        if len(entry) < 4:
            break
        p_type = int.from_bytes(entry[0:4], "little")
        if p_type == 3:
            return False, "es dinamico (tiene PT_INTERP)"
        if p_type == 6:
            return False, "es dinamico (tiene PT_DYNAMIC)"
    return True, "ELF estatico aarch64"


def validate_binaries(extracted: dict[str, pathlib.Path], lock: dict) -> None:
    for member in lock["archive_members"]:
        path = extracted[member]
        ok, detail = elf_is_static_aarch64(path)
        if not ok:
            fail(f"{path.name}: {detail}")
        if path.stat().st_size < 1 << 20:
            fail(f"{path.name}: tamano inesperado")
        info(f"  [ok] {path.name}: {detail} ({path.stat().st_size} bytes)")


def smoke(path: pathlib.Path, expected: str) -> bool:
    try:
        result = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=60)
    except OSError as exc:
        info(f"[!] smoke test no ejecutable: {path}: {exc}")
        return False
    except subprocess.TimeoutExpired:
        info(f"[!] smoke test excedio 60s: {path}")
        return False
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0 and expected in output:
        info(f"  [ok] smoke test: {output.splitlines()[0] if output else path.name}")
        return True
    info(f"[!] smoke test fallo ({result.returncode}): {output or '<vacio>'}")
    return False


def receipt_path(prefix: pathlib.Path) -> pathlib.Path:
    return prefix / RECEIPT_REL


def current_receipt(prefix: pathlib.Path) -> dict:
    path = receipt_path(prefix)
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def install_binaries(extracted: dict[str, pathlib.Path], lock: dict, args: argparse.Namespace, tmp: pathlib.Path) -> None:
    prefix = pathlib.Path(args.prefix)
    dest = pathlib.Path(args.dest or prefix / "bin")
    dest.mkdir(parents=True, exist_ok=True)
    backup = pathlib.Path(tempfile.mkdtemp(prefix="kiro-backup.", dir=tmp))
    moved: list[tuple[pathlib.Path, pathlib.Path]] = []
    receipt = receipt_path(prefix)
    try:
        plan = []
        for member in lock["archive_members"]:
            name = pathlib.PurePosixPath(member).name
            plan.append((name, extracted[member]))
        for name, _ in plan:
            target = dest / name
            if target.exists():
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
        for name, source in plan:
            target = dest / name
            os.replace(source, target)
            target.chmod(0o755)
            moved.append((target, backup / name))
        receipt.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "kiro-termux/receipt/v1",
            "version": lock["version"],
            "artifact": lock["artifacts"][lock["default_artifact"]]["file"],
            "executables": [name for name, _ in plan],
            "dest": str(dest),
        }
        receipt.write_text(json.dumps(payload, indent=2) + "\n")
        info(f"[ok] Instalado Kiro CLI {lock['version']} en {dest}")
    except BaseException:
        for target, saved in reversed(moved):
            if target.exists():
                target.unlink()
            if saved.is_file():
                shutil.copy2(saved, target)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)


def stage_and_validate(args: argparse.Namespace, tmp: pathlib.Path) -> tuple[dict, dict, str, dict[str, pathlib.Path]]:
    lock = load_lock(args, tmp)
    kind, art = select_artifact(args, lock)
    if args.stage_dir:
        work = pathlib.Path(args.stage_dir)
        work.mkdir(parents=True, exist_ok=True)
    else:
        work = pathlib.Path(tempfile.mkdtemp(prefix="kiro-stage.", dir=tmp))
    if args.asset:
        source = pathlib.Path(args.asset)
        if not source.is_file():
            fail(f"No existe --asset: {source}")
        archive = work / art["file"]
        shutil.copyfile(source, archive)
    else:
        url = archive_url(lock, art)
        info(f"Artefacto: {url} ({art['size']} bytes)")
        archive = download(url, work / art["file"], art["size"])
    if archive.stat().st_size != art["size"]:
        fail(f"tamano invalido: {archive.stat().st_size} != {art['size']}")
    info("Verificando sha256...")
    actual = sha256_file(archive)
    if actual != art["sha256"]:
        fail(f"checksum invalido: {actual} != {art['sha256']}")
    info("Extrayendo binarios...")
    extracted = extract_members(archive, kind, lock["archive_members"], work / "extract")
    validate_binaries(extracted, lock)
    return lock, art, kind, extracted


def command_install(args: argparse.Namespace, tmp: pathlib.Path) -> None:
    prefix = pathlib.Path(args.prefix)
    lock = load_lock(args, tmp)
    if not args.force:
        receipt = current_receipt(prefix)
        if receipt.get("version") == lock["version"] and not args.dry_run:
            info(f"Kiro CLI {lock['version']} ya esta instalado (usa --force para reinstalar)")
            return
    lock, art, kind, extracted = stage_and_validate(args, tmp)
    if args.dry_run:
        info("[ok] Dry-run valido: checksum, extraccion y ELF estatico aarch64.")
        return
    if not args.yes:
        if not sys.stdin.isatty():
            fail("Entrada no interactiva; usa --yes")
        if input(f"Instalar Kiro CLI {lock['version']} en {args.dest or prefix/'bin'}? [s/N] ").lower() not in ("s", "si", "y", "yes"):
            return
    install_binaries(extracted, lock, args, tmp)
    if not args.no_smoke:
        dest = pathlib.Path(args.dest or prefix / "bin")
        smoke(dest / "kiro-cli", lock["version"])


def command_verify(args: argparse.Namespace, tmp: pathlib.Path) -> None:
    lock, art, kind, extracted = stage_and_validate(args, tmp)
    launcher_member = f"kirocli/bin/{BINARY_PREFIX}"
    launcher = extracted.get(launcher_member)
    ok = True
    if launcher is not None and not args.no_smoke:
        ok = smoke(launcher, lock["version"])
    if launcher is not None:
        info(f"Launcher extraido: {launcher}")
    info("[ok] verify completo" if ok else "[!] verify con fallos")
    raise SystemExit(0 if ok else 1)


def command_manifest(args: argparse.Namespace, tmp: pathlib.Path) -> None:
    lock = load_lock(args, tmp)
    kind, art = select_artifact(args, lock)
    payload = {
        "schema": "kiro-termux/install-manifest/v1",
        "product": "kiro-cli",
        "version": lock["version"],
        "channel": lock.get("channel"),
        "platform": lock["platform"],
        "artifact": {"kind": kind, **art},
        "url": archive_url(lock, art),
        "executables": lock["executables"],
    }
    print(json.dumps(payload, indent=2))


def command_uninstall(args: argparse.Namespace, tmp: pathlib.Path) -> None:
    prefix = pathlib.Path(args.prefix)
    receipt = current_receipt(prefix)
    dest = pathlib.Path(args.dest or prefix / "bin")
    names = receipt.get("executables") or [pathlib.PurePosixPath(m).name for m in ("kiro-cli", "kiro-cli-chat", "kiro-cli-term")]
    for name in names:
        target = dest / name
        if target.is_file():
            target.unlink()
            info(f"Eliminado {target}")
    receipt_path(prefix).unlink(missing_ok=True)
    info("[ok] Desinstalado.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    tmp = preflight(args)
    if args.command == "install":
        command_install(args, tmp)
    elif args.command == "verify":
        command_verify(args, tmp)
    elif args.command == "manifest":
        command_manifest(args, tmp)
    elif args.command == "uninstall":
        command_uninstall(args, tmp)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        fail("Interrumpido por el usuario")
