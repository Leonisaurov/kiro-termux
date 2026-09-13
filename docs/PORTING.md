# PORTING.md — como se porto Kiro CLI a Termux

## 1. Distribucion upstream

Kiro CLI no publica codigo fuente. El instalador oficial (`https://cli.kiro.dev/install`)
descarga un archivo desde:

```text
https://prod.download.cli.kiro.dev/stable/latest/manifest.json
https://prod.download.cli.kiro.dev/stable/<version>/<archivo>
```

El manifiesto lista paquetes por `targetTriple`, `fileType` y `variant`. Para
Termux interesa:

```text
targetTriple: aarch64-unknown-linux-musl
variant:      headless
fileTypes:    tar.xz | tar.zst | tar.gz | zip | rpm
```

`variant: full` (AppImage/deb/msi) y los paquetes `*-linux-gnu` no aplican:
dependen de glibc y/o de un escritorio.

## 2. Por que funciona en Android sin proot

Se verifico el `bin/kiro-cli` del archivo musl por *HTTP range requests*, sin
descargar los ~600 MB: se leyo la cabecera local del `zip`/`tar`, se
descomprimio el inicio del miembro y se parseo la cabecera ELF. Resultado:

```text
ELF 64-bit LSB executable, ARM aarch64, statically linked
e_type  = ET_EXEC (2)
PT_INTERP  ausente
PT_DYNAMIC ausente
```

Un ejecutable ELF **estatico** no necesita `ld.so` ni una libc del sistema: el
kernel de Android ejecuta el binario directamente. Por eso no hace falta
`proot`, `patchelf`, glibc ni musl del host. Los tres ejecutables
(`kiro-cli`, `kiro-cli-chat`, `kiro-cli-term`) comparten esta propiedad.

Comprobacion en dispositivo:

```sh
$ ./kiro-cli --version
kiro-cli 2.21.4
```

## 3. Artefacto preferido

`tar.xz` es el mas pequeno (~530 MB frente a ~610 MB de `tar.gz`/`zip` y
~549 MB de `tar.zst`). La extraccion se hace con `tarfile`/`lzma` de la
libreria estandar de Python, por lo que **no requiere `xz` del sistema**. Los
checksums de todas las variantes quedan fijados en el lock por si se necesita
una alternativa (`--archive tar.gz`).

## 4. Layout de instalacion

```text
$PREFIX/bin/kiro-cli          binario real (no symlink)
$PREFIX/bin/kiro-cli-chat
$PREFIX/bin/kiro-cli-term
$PREFIX/lib/kiro-termux/receipt.json
```

Los tres binarios viven en el mismo directorio que espera el launcher para
localizar a sus hermanos (`kiro-cli-chat`, `kiro-cli-term`), evitando depender de
como resuelva `current_exe`/`argv[0]`.

La instalacion es transaccional: se respaldan los binarios previos, se hace
`os.replace` y, ante cualquier error, se restauran. El recibo permite un
`uninstall` exacto.

## 5. Por que no se usa el `install.sh` upstream

El `install.sh` interno del archivo:

- comprueba version de glibc (con `is_target_triple_gnu`; para musl lo omite),
- instala en `$HOME/.local/bin` (fuera del `PATH` de Termux),
- ejecuta `kiro-cli setup` (interactivo) salvo que se exporte `KIRO_CLI_SKIP_SETUP`.

Nuestro instalador hace solo lo necesario: extrae, valida y coloca los binarios
sin tocar dotfiles ni lanzar asistentes.

## 6. CI

- **Validate**: sintaxis Python/shell, tests unitarios, validacion del lock,
  `upstream.py verify` y `actionlint`.
- **Build Kiro CLI**: descarga el artefacto fijado, verifica SHA-256 y tamano,
  extrae solo los tres binarios, valida el ELF estatico, ejecuta
  `kiro-cli --version` bajo **QEMU aarch64** (`docker/setup-qemu-action`) e
  instala en un prefijo temporal. Empaqueta `manifest.json` + bundle del
  instalador y, bajo `workflow_dispatch` con `publish=true`, publica un release.
- **Sync upstream**: diario, detecta version nueva, refresca el lock y abre PR.

## 7. Actualizar la version

```sh
python3 ci/scripts/upstream.py status    # compara lock vs latest
python3 ci/scripts/upstream.py update    # regenera artifacts desde el manifiesto
python3 ci/scripts/upstream.py verify    # confirma checksums
python3 ci/scripts/test-installer.py
```

El `build.date`/`build.hash` del lock original se reemplazan por `synced_at`
porque el manifiesto upstream no expone el `BUILD-INFO`.

## 8. Diagnostico

- `checksum invalido`: el upstream movio "latest" a otra version. Corre
  `upstream.py update`.
- `es dinamico (tiene PT_INTERP)`: se eligio por error un artefacto `gnu`; usa el
  lock que apunta a `aarch64-unknown-linux-musl`.
- `tamano invalido`: descarga truncada; el instalador reanuda por `Range` al
  reintentar.
- El chat TUI no arranca o muere: revisa RAM disponible; `kiro-cli-chat` es
  grande. Prueba `kiro-cli --classic`.
