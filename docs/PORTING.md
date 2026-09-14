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
$PREFIX/bin/kiro-cli                    wrapper (shell) -> libexec
$PREFIX/bin/kiro-cli-chat
$PREFIX/bin/kiro-cli-term
$PREFIX/libexec/kiro-termux/kiro-cli    binario real (parcheado)
$PREFIX/libexec/kiro-termux/kiro-cli-chat
$PREFIX/libexec/kiro-termux/kiro-cli-term
$PREFIX/etc/r                           DNS para musl (ruta corta, parcheada)
$PREFIX/etc/kiro-termux/receipt.json    recibo
```

Los tres binarios reales viven juntos en `libexec` para que el launcher
encuentre a sus hermanos (`kiro-cli-chat`, `kiro-cli-term`) sin depender de como
resuelva `current_exe`/`argv[0]`. Los wrappers de `$PREFIX/bin` hacen de
lanzadera.

La instalacion es transaccional: se respaldan los binarios/wrappers previos, se
hace `os.replace`/escritura y, ante cualquier error, se restauran. El recibo
permite un `uninstall` exacto (incluye el layout legado).

## 4b. Red: CAs y DNS (los dos bloqueos reales de Android)

Al correr el binario musl estatico aparecen dos fallos distintos:

1. `no native root CA certificates found!` -> `rustls-native-certs` no encuentra
   `/etc/ssl/certs/...`. Se resuelve con `SSL_CERT_FILE=$PREFIX/etc/tls/cert.pem`.
2. `error sending request for url (...)` -> musl abre `/etc/resolv.conf` (que no
   existe: Android usa `/system/etc`, de solo lectura), no lee ningun
   nameserver y hace fallback a `127.0.0.1:53`. `strace` lo confirma:

   ```text
   openat("/etc/hosts", O_RDONLY) = 19
   openat("/etc/resolv.conf", O_RDONLY) = -1 ENOENT
   sendto(..., sin_port=htons(53), sin_addr=inet_addr("127.0.0.1"))
   ```

No se puede crear `/etc/resolv.conf` (particion de sistema, sin root),
`unshare -Urm` esta deshabilitado (Android desactiva los user namespaces) y
`LD_PRELOAD` no aplica a binarios estaticos. Soluciones evaluadas:

- `RESOLV_CONF`: musl no lo soporta.
- nameserver en `127.0.0.1:53`: requeriria root para el puerto 53.
- `/etc/hosts` parcheado: fragil (IPs dinamicas).
- `/sdcard/...`: permiso de almacenamiento y scoped storage, no portable.
- `proot`: funciona, pero agrega una dependencia que el port evita.
- **fd heredado `/proc/self/fd/9`**: falla con la TUI. El subproceso
  `kiro-cli-chat acp` que lanza Bun **no hereda el fd 9** y lo reutiliza para su
  propio log; musl lee entonces el log como resolv.conf, cae a `127.0.0.1:53` y
  todas las peticiones de la TUI (`/model`, mensajes) fallan con `dispatch
  failure`.

Solucion del port: **parchear la ruta `/etc/resolv.conf` de musl a una ruta real
y corta** (`$PREFIX/etc/r`). En el binario, esa ruta precede a los literales
`options`, `ndots:`, `attempts:`, `timeout:` y `nameserver`. Se reescriben la
ruta y esos cuatro literales (hasta justo antes de `nameserver`, que musl
necesita para parsear) con la ruta nueva. La ruta debe caber en ese hueco
(<= 50 bytes); con el prefijo por defecto de Termux son 37.

`apply_resolv_patches()` escanea por bloques (sin cargar los ~1 GB en RAM) y
falla en voz alta si la cadena no aparece, de modo que un cambio de musl en
upstream se detecta en CI en vez de romper en silencio. El `resolv.conf` se
genera a partir de `$PREFIX/etc/resolv.conf` / `/system/etc/resolv.conf` o, en su
defecto, `8.8.8.8`, `8.8.4.4`, `1.1.1.1`; se puede overridear al instalar con
`KIRO_TERMUX_NAMESERVERS`.

## 4c. La TUI es Bun (y en Android no arranca)

`kiro-cli-chat` **no renderiza la TUI por si mismo**. Es un binario estatico que
embebe dos blobs:

```text
"Extracting bun and tui to: "  <ELF glibc de Bun>   (blob, ~101 MB)
<hash>                         <tui.js>             (bundle Bun de ~13 MB)
```

y en runtime los escribe en `$HOME/.local/share/kiro-cli/{bun,tui.js}` y ejecuta
`bun tui.js`. El Bun embebido es **glibc**
(`interpreter /lib/ld-linux-aarch64.so.1`), asi que en Android falla con
`error: No such file or directory (os error 2)` justo despues del login. Los
`<hash>` son los ficheros `bun.sha256`/`tui.js.sha256` que Kiro usa como marca
para saltarse la extraccion si ya existen.

Port: se usa el **Bun Android (bionic)** que compila
[opencode-termux](https://github.com/Leonisaurov/opencode-termux)
(`bun-1.2.13-android-aarch64.tar.gz`, fijado por SHA-256 en `runtime`). El
instalador:

1. localiza el blob con el patron `Extracting bun and tui to: ` seguido de un
   ELF aarch64 con `PT_INTERP` glibc;
2. calcula su longitud a partir de la propia cabecera ELF
   (`e_shoff + e_shentsize * e_shnum`), asi que no depende de offsets fijos;
3. escribe el Bun Android con padding de ceros hasta esa longitud (el cargador
   ignora los bytes finales) y
4. registra offset/tamano/sha256 en el recibo.

Como Kiro extrae el blob tal cual y no valida su contenido contra el hash de la
tabla (el `bun.sha256` en disco ya no coincide con el blob extraido en el
binario original), la sustitucion sobrevive a cada extraccion. Si el marker
desaparece en una version futura, `verify`/`install` fallan en CI.

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
  extrae solo los tres binarios, valida el ELF estatico, parchea la ruta del
  resolv.conf de musl, descarga/verifica el Bun Android de `opencode-termux`, lo
  embebe en `kiro-cli-chat`, ejecuta `kiro-cli --version` bajo **QEMU aarch64**
  (`docker/setup-qemu-action`), instala en un prefijo temporal, comprueba el
  sha256/offset del blob embebido y el wrapper instalado, y empaqueta
  `manifest.json` + bundle. Bajo `workflow_dispatch` con `publish=true` (o tag
  `kiro-cli-v*`) publica el release; un push normal solo valida.
- **Sync upstream**: diario, detecta version nueva, refresca el lock y abre PR
  (preserva `runtime`/`embedded_bun`).

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
- `tamano invalido` / `runtime Bun: tamano/checksum invalido`: descarga truncada
  (se reanuda por `Range`) o asset de Bun re-subido en el release de
  `opencode-termux`; re-pinea size/sha256 en el lock.
- `error sending request for url (...)` o `dispatch failure` en `/model`/mensajes:
  el `acp` no encontro resolv.conf (parche DNS). Verifica que existe
  `$PREFIX/etc/r` y que el binario contiene la ruta; si ves `el prefijo es
  demasiado largo para el parche resolv.conf`, el prefijo no cabe en el hueco.
- `No such file or directory (os error 2)` tras el login: el blob del Bun glibc no
  se sustituyo; reinstala y comprueba `patches.embedded_bun` en el recibo.
- `no native root CA certificates found!`: falta el wrapper/`SSL_CERT_FILE`.
- El chat TUI no arranca o muere: revisa RAM disponible; `kiro-cli-chat` es
  grande. Prueba `kiro-cli --classic`.
