# AGENTS.md

Guia operativa para agentes que trabajen en `kiro-termux`.

## Que es este repositorio

Un **port de empaquetado**, no de compilacion. Kiro CLI (AWS) es propietario y se
distribuye solo como binario estatico `aarch64-unknown-linux-musl`; no hay fuente
publica. No intentes compilar Kiro ni añadir toolchain: no existe upstream.

El instalador: descarga el artefacto oficial (SHA-256 del lock), valida ELF
estatico aarch64, parchea DNS, embebe el Bun Android y deja wrappers. Es
transaccional (rollback + recibo en `$PREFIX/etc/kiro-termux/receipt.json`).
Razonamiento completo en [docs/PORTING.md](docs/PORTING.md).

## Comandos

```sh
# Verificacion local (no necesita red ni Android)
python3 ci/scripts/test-installer.py                    # toda la suite
python3 ci/scripts/test-installer.py InstallerTests.test_install_and_receipt  # un test
python3 -m py_compile ci/scripts/*.py
bash -n install.sh ci/scripts/env.sh ci/scripts/ci-summary.sh
actionlint .github/workflows/*.yml

# Lock / upstream (red)
python3 ci/scripts/upstream.py status      # compara lock vs latest (exit 2 con --fail-on-update)
python3 ci/scripts/upstream.py verify      # checksums del lock contra el manifiesto
python3 ci/scripts/upstream.py update      # re-pinea version/artefactos (preserva runtime/embedded_bun)

# Instalador
python3 ci/scripts/installer.py manifest   # imprime el manifiesto resuelto
python3 ci/scripts/installer.py verify --no-smoke
./install.sh --yes                         # desde checkout; tambien curl | sh
./install.sh --force --yes                 # reinstala aunque el recibo diga la misma version
python3 ci/scripts/installer.py uninstall
```

## Invariantes que un agente romperia sin querer

- **Solo aarch64 / `arm64-v8a`**, Termux/Bionic. Nada de `glibc`, `proot` ni
  `patchelf`. No ejecutes el `install.sh` interno del archivo upstream: asume
  glibc y es interactivo.
- **Layout**: binarios reales en `$PREFIX/libexec/kiro-termux/`, wrappers
  generados en `$PREFIX/bin/`. No muevas los binarios reales a `bin`: rompe la
  deteccion de CAs/DNS y la resolucion de hermanos.
- **DNS musl**: `/etc/resolv.conf` no existe en Android. El instalador parchea la
  ruta a `$PREFIX/etc/r`, reescribiendo la ruta y los literales
  `options`/`ndots:`/`attempts:`/`timeout:` justo hasta `nameserver`. No uses un
  fd heredado (`/proc/self/fd/9`): el hijo `kiro-cli-chat acp` no hereda fds y
  reutiliza el fd 9 para logs, con lo que todas las peticiones fallan
  (`/model`, mensajes, `dispatch failure`).
- **TUI = Bun**: `kiro-cli-chat` embebe un Bun glibc + `tui.js` y hace
  `bun tui.js`. En Android el glibc muere con `No such file or directory (os
  error 2)`. El instalador sustituye el blob ELF por el **Bun Android de
  `../opencode-termux`** con padding; no quites ese paso.
- **CAs**: los wrappers exportan `SSL_CERT_FILE=$PREFIX/etc/tls/cert.pem`
  (paquete `ca-certificates`). Sin el, `rustls-native-certs` no encuentra roots.
- **Lock**: `ci/upstream/kiro-cli.lock.json` es la fuente de verdad de version,
  SHA-256 y runtime Bun. Cambialo solo con `upstream.py update` o un PR explicito;
  nunca "alinees" versiones por inferencia. El asset de Bun del release de
  `opencode-termux` es **mutable** (lo re-suben): si CI falla con
  `runtime Bun: tamano/checksum invalido`, re-pinea size/sha256 en el lock.
- **Sin re-hospedaje de Kiro**: los binarios se descargan de
  `prod.download.cli.kiro.dev` y se verifican por SHA-256. El Bun (MIT) sí se toma
  del release de `opencode-termux`.
- Usa `$TMPDIR`, nunca `/tmp`, en scripts de Termux. `install.sh` y los scripts
  respetan `TMPDIR`/`$PREFIX`. Python solo con libreria estandar (el instalador se
  ejecuta via `curl | sh`).
- No compiles binarios pesados en el dispositivo. La validacion real corre en CI
  (descarga + QEMU). En local usa la suite sin red.

## Pruebas (quirks)

- `KIRO_TERMUX_TEST_MODE=1` permite instalar/validar fuera de Termux y aceptar
  URLs `file://`/locales del runtime (lo usan tests y CI). No lo actives en uso real.
- La ruta parcheada debe caber antes del literal `nameserver` (~50 bytes). Por eso
  en Termux la base de tests es `$PREFIX` (no `$TMPDIR`). Si ves
  `el prefijo es demasiado largo para el parche resolv.conf`, es que el prefijo
  de pruebas es largo.
- Los tests usan archivos sinteticos (tar.xz/zip con ELF falsos) y un runtime Bun
  falso; no requieren red.

## CI

- `validate.yml`: en push/PR a `main`. Corre py_compile, `bash -n`, tests,
  validacion del lock, `upstream.py verify` y actionlint.
- `build-kiro.yml`: en push (cuando cambian `ci/**`, `install.sh` o el propio
  workflow) y en `workflow_dispatch`. Descarga/verifica el artefacto, embebe el
  Bun, hace smoke aarch64 con QEMU, instala en un prefijo temporal y empaqueta.
  **Solo publica release** con `workflow_dispatch` + `publish=true` (o tag
  `kiro-cli-v*`); un push normal no publica.
- `sync-upstream.yml`: cron diario + dispatch; si hay version nueva, refresca el
  lock y abre PR.
- Monitoriza con `gita --repo Leonisaurov/kiro-termux notify build-kiro.yml`
  (bloquea hasta terminar; no hagas polling con `gh run view`).

## Convenciones

- Mensajes de commit: `fix(installer): ...`, `feat(bun): ...`, `ci: ...`,
  `docs: ...`, `chore(upstream): ...`.
- El instalador debe seguir siendo determinista, transaccional y con verificacion
  de checksum/tamano/ELF; todo cambio de archivos necesita rollback y recibo.
- No hagas commit de `__pycache__` ni de artefactos generados (ver `.gitignore`).
