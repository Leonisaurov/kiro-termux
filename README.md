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
- **~1.7 GB** libres en el pico de instalacion (~530 MB de Kiro + ~36 MB del Bun
  Android de descarga, ~1.1 GB instalados).
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

El instalador:

1. descarga el artefacto oficial fijado por SHA-256 y lo valida,
2. comprueba que cada binario sea un `ELF 64-bit aarch64` estatico
   (sin `PT_INTERP`/`PT_DYNAMIC`),
3. **parchea la ruta `/etc/resolv.conf` de musl** hacia un fichero real y corto
   (`$PREFIX/etc/r`), reescribiendo la ruta y los literales que le siguen hasta
   `nameserver` (ver [Red](#red-dns-y-certificados)),
4. descarga el **runtime Bun Android** de
   [opencode-termux](https://github.com/Leonisaurov/opencode-termux) y lo
   **embebe en `kiro-cli-chat`**, sustituyendo el Bun glibc que trae Kiro
   (ver [TUI y Bun](#tui-y-bun)),
5. instala los binarios reales en `$PREFIX/libexec/kiro-termux/` y deja
   **wrappers** en `$PREFIX/bin/`, y
6. genera `$PREFIX/etc/r` (resolv.conf de musl) y
   `$PREFIX/etc/kiro-termux/receipt.json`.

Todo es transaccional (rollback) y el recibo permite desinstalar.

No ejecuta el instalador de upstream: aquel asume `glibc`, valida versiones de
libc y lanza `kiro-cli setup` (interactivo). Tampoco toca `~/.kiro` ni tus dotfiles.

## Red (DNS y certificados)

Los binarios son musl estatico. En Android eso rompe dos cosas que el port
resuelve por separado (CAs en los wrappers, DNS parcheando el binario):

- **Certificados**: `rustls-native-certs` busca `/etc/ssl/certs/...`, que no
  existe. El wrapper exporta `SSL_CERT_FILE=$PREFIX/etc/tls/cert.pem`
  (paquete `ca-certificates`).
- **DNS**: musl lee `/etc/resolv.conf`, que Android no expone (solo
  `/system/etc`, de solo lectura); al no encontrarlo manda las consultas a
  `127.0.0.1:53` y falla con `error sending request for url (...)` o
  `no native root CA certificates found!`. El instalador parchea la cadena
  `/etc/resolv.conf` del binario hacia `$PREFIX/etc/r` (una ruta real y corta):
  reescribe la ruta y los literales de musl que le siguen
  (`options`/`ndots:`/`attempts:`/`timeout:`) justo hasta `nameserver`.

Se usa un fichero real (no un fd heredado) porque el subproceso `kiro-cli-chat
acp` que lanza la TUI **no hereda fds** y reutiliza el fd 9 para sus logs, con lo
que perdia el resolv.conf y todas las peticiones fallaban.

El instalador genera `$PREFIX/etc/r` a partir de `$PREFIX/etc/resolv.conf`,
`/system/etc/resolv.conf` o, en su defecto, `8.8.8.8`/`8.8.4.4`/`1.1.1.1`.
Puedes forzar los resolvers al instalar:

```sh
KIRO_TERMUX_NAMESERVERS="1.1.1.1 9.9.9.9" ./install.sh --force --yes
```

## TUI y Bun

La interfaz interactiva de Kiro no es nativa: `kiro-cli-chat` embebe un runtime
**Bun** (glibc, con `interpreter /lib/ld-linux-aarch64.so.1`) mas el bundle
`tui.js` (tambien embebido), y al arrancar los extrae a
`~/.local/share/kiro-cli/` y ejecuta `bun tui.js`. Ese Bun glibc no arranca en
Android (`error: No such file or directory (os error 2)`).

El port resuelve esto embebiendo el **Bun Android (bionic)** de opencode-termux
en lugar del glibc: el instalador localiza el blob ELF embebido, lo sustituye
por el Bun Android con padding de ceros hasta la misma longitud y verifica el
SHA-256 resultante. Kiro extrae entonces un Bun que si corre en Termux
(probado con 1.2.13 del release `stack-v1.18.11` de `opencode-termux`).

- Runtime fijado en `ci/upstream/kiro-cli.lock.json` (`runtime`), con URL y
  SHA-256 del artefacto de opencode-termux.
- El parche se describe en `embedded_bun` y queda registrado en el recibo
  (`patches.embedded_bun`) con offset, tamano y sha256.

## Uso

```sh
kiro-cli                        # chat interactivo (TUI)
kiro-cli chat                   # idem
kiro-cli --classic              # interfaz clasica
kiro-cli login                  # autenticacion (AWS Builder ID / Google / GitHub)
kiro-cli login --use-device-flow  # si el navegador no puede redirigir a Termux
kiro-cli doctor                 # diagnostico
kiro-cli --help-all             # todos los subcomandos
```

El arranque inicial `--version` funciona sin red. El chat y el login requieren
conexion y credenciales de Kiro. En Termux suele funcionar mejor
`kiro-cli login --use-device-flow` (te da un codigo para pegar en el navegador).

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
