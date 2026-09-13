# AGENTS.md

Guia operativa para agentes que trabajen en `kiro-termux`.

## Que es este repositorio

Un **port de empaquetado**, no de compilacion. Kiro CLI (AWS) se distribuye como
binario estatico `aarch64-unknown-linux-musl`; no hay fuente publica. El objetivo
es instalarlo en Termux/Android de forma reproducible, verificada y reversible.

No introduzcas compilacion de Kiro: no existe toolchain ni codigo fuente upstream.

## Reglas

- **Solo aarch64 / `arm64-v8a`**, Termux/Bionic. Nada de `glibc`, `proot` ni
  `patchelf`.
- **No** ejecutar el `install.sh` interno del archivo upstream: asume glibc y es
  interactivo. El contrato es extraer los tres binarios y validarlos.
- El **lock** (`ci/upstream/kiro-cli.lock.json`) es la fuente de verdad de la
  version y los SHA-256. Cambiarlo solo con `ci/scripts/upstream.py update` o un
  PR explicito; nunca "alinear" versiones por inferencia.
- Los binarios se descargan del servidor oficial de AWS (`prod.download.cli.kiro.dev`)
  y se verifican por SHA-256; no se re-hospedan en releases.
- Usa `$TMPDIR`, nunca `/tmp` en scripts de Termux. `install.sh` y los scripts
  respetan `TMPDIR`/`$PREFIX`.
- En este workspace no se compilan binarios pesados en el dispositivo. La
  validacion real ocurre en CI (descarga + qemu). En local usa
  `python3 ci/scripts/test-installer.py` (sin red).

## Flujo de cambio

1. Edita el instalador/CI y corre `python3 ci/scripts/test-installer.py`.
2. `bash -n` sobre los scripts shell y `actionlint` sobre los workflows.
3. `python3 ci/scripts/upstream.py verify` para confirmar el lock.
4. CI (`Validate`) debe pasar en el PR. `Build` solo corre bajo demanda.

## Convenciones

- Python solo con libreria estandar (el instalador se ejecuta via `curl | sh`).
- Mensajes de commit: `fix(installer): ...`, `ci: ...`, `docs: ...`,
  `chore(upstream): ...`.
- El instalador es transaccional: todo cambio de archivos debe tener rollback y
  recibo. Mantenlo determinista y con verificacion de checksum/tamano/ELF.
