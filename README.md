# kiro-termux

Port de **Kiro CLI** (el agente de IA de AWS para terminal) a **Termux/Android aarch64**.

Kiro CLI upstream se distribuye como binario **estatico musl** para `aarch64-unknown-linux-musl`.
Al no depender de `glibc`, el binario corre nativo en Android/Bionic sin `proot`, sin
`patchelf` y sin recompilar. Este repositorio mantiene:

- un **lock** con la version y los checksums SHA-256 del artefacto oficial,
- un **instalador deterministico** que descarga, verifica, extrae e instala,
- **CI** que valida el lock, el artefacto y el arranque real del binario.

Probado en dispositivo: `kiro-cli --version` -> `kiro-cli 2.21.4`.

## Requisitos

- Termux en Android `aarch64` / `arm64-v8a`.
- Python 3 (`pkg install python`), que ya viene en Termux.
- **~1.3 GB** libres: ~530 MB de descarga y ~1.0 GB de binarios instalados.
- Conexion a internet para la descarga y para el login de Kiro.

## Instalacion

```sh
curl -fsSL https://raw.githubusercontent.com/Leonisaurov/kiro-termux/main/install.sh | sh
```

Opciones utiles:

```sh
# Desde un checkout
./install.sh --archive tar.gz --yes

# Solo validar (descarga, checksum, ELF estatico aarch64), sin instalar
python3 ci/scripts/installer.py verify --no-smoke

# Dry-run
python3 ci/scripts/installer.py install --dry-run --yes

# Desinstalar
python3 ci/scripts/installer.py uninstall
```

El instalador instala `kiro-cli`, `kiro-cli-chat` y `kiro-cli-term` en `$PREFIX/bin`,
valida que cada uno sea un `ELF 64-bit aarch64` sin `PT_INTERP`/`PT_DYNAMIC`
(es decir, estatico), deja un recibo en `$PREFIX/lib/kiro-termux/receipt.json` y
hace rollback si algo falla.

No ejecuta el instalador de upstream: aquel asume `glibc`, valida versiones de
libc y lanza `kiro-cli setup` (interactivo). Tampoco toca `~/.kiro` ni tus dotfiles.

## Uso

```sh
kiro-cli                 # chat interactivo (TUI)
kiro-cli chat            # idem
kiro-cli --classic       # interfaz clasica
kiro-cli login           # autenticacion (AWS Builder ID / Google / GitHub)
kiro-cli doctor          # diagnostico
kiro-cli --help-all      # todos los subcomandos
```

El arranque inicial `--version` funciona sin red. El chat y el login requieren
conexion y credenciales de Kiro.

## Avisos

- **Tamano**: son tres binarios grandes (`kiro-cli-chat` ~813 MB descomprimido).
  Instalar en almacenamiento interno; considera el impacto en RAM al abrir la TUI.
- **Auto-update**: Kiro CLI detecta que no esta instalado en su ubicacion
  esperada y delega la actualizacion al gestor de paquetes. Actualiza subiendo
  el pin en `ci/upstream/kiro-cli.lock.json` (o via el workflow `Sync upstream`),
  no desde `kiro-cli update`.
- **Sin fuente**: Kiro CLI es software propietario de AWS. Este repo no
  redistribuye los binarios; solo fija su checksum y los descarga desde el
  servidor oficial. Ver [docs/PORTING.md](docs/PORTING.md).

## Estructura

```text
install.sh                         entrada (checkout o curl | sh)
ci/upstream/kiro-cli.lock.json     pin de version + sha256 + tamano
ci/scripts/installer.py            instalador/verificador/desinstalador
ci/scripts/upstream.py             status / verify / update contra upstream
ci/scripts/test-installer.py       pruebas sin red ni Android
.github/workflows/validate.yml     checks estaticos, tests y actionlint
.github/workflows/build-kiro.yml   descarga, verifica, smoke con qemu y empaqueta
.github/workflows/sync-upstream.yml detecta nuevas versiones y abre PR
```

## Desarrollo

```sh
python3 ci/scripts/test-installer.py
python3 ci/scripts/upstream.py status
python3 ci/scripts/upstream.py verify
python3 ci/scripts/installer.py manifest
actionlint .github/workflows/*.yml
```

Las pruebas usan un archivo sintetico (`tar.xz`/`zip`) con ELF falsos, de modo
que no requieren red ni un dispositivo Android.

## Actualizar el pin

1. El workflow `Sync upstream` corre a diario, refresca el lock y abre un PR.
2. O manualmente: `python3 ci/scripts/upstream.py update`.
3. El PR dispara `Validate`; al mergear, el proximo build verifica el artefacto.

## Licencia

Los scripts, workflows y documentacion de este repositorio estan bajo MIT
(ver [LICENSE](LICENSE)). Kiro CLI es una marca y un producto de Amazon Web
Services; este proyecto no esta afiliado ni patrocinado por AWS.
