# proxmox-monitoring

CPU, RAM y disco de un nodo Proxmox y de cada uno de sus invitados, hacia Grafana.

Rehecho desde cero en **2026-11-06**: el diseño original era de julio de 2026 y su carpeta
desapareció del disco y de GitHub. Esta versión es más estrecha a propósito — solo métricas,
sin Loki ni Alertmanager.

## Por qué existe

Sin esto, la única forma de saber qué consume un nodo es medirlo a mano en el momento.
No sirve `top -bn1`: en un host con 86 días de encendido devuelve el **promedio desde el
arranque**, que no dice nada del ahora. Y no hay forma de responder "¿qué consumió la semana
pasada?", porque nadie lo guardó.

## Arquitectura

```
   NODO PROXMOX                          MAQUINA DEL STACK
   (lo que se vigila)                    (LXC, VM o un servidor que ya tengas)
  ┌────────────────────┐                ┌──────────────────────────────────┐
  │ node-exporter      │◄───── scrape ──┤ prometheus          :9090        │
  │   :9100            │                │   retención 15 días              │
  │ + 2 recolectores   │                │                                  │
  │                    │                │                                  │
  │ API :8006 ◄────────┼─── lectura ────┤ prometheus-pve-exporter  :9221   │
  │   token PVEAuditor │                │   (token de solo lectura)        │
  │   (SOLO LECTURA)   │                │                                  │
  └────────────────────┘                │ grafana             :3000        │
                                        └──────────────────────────────────┘
```

**En el hypervisor va un solo paquete** (`prometheus-node-exporter`), dos recolectores que son
scripts sin servicio propio, y un usuario de API de solo lectura. Todo lo demás vive en la
máquina del stack: si el stack se cae, o se borra, el nodo no se enteró.

**Dos exporters porque miden cosas distintas:**

| | node-exporter | pve-exporter |
|---|---|---|
| Qué ve | el sistema operativo del host | la API de Proxmox |
| CPU/RAM | del nodo, real | de cada máquina |
| Disco | cada filesystem del host, real | storages del nodo, real |
| Extra | ZFS ARC, presión de E/S, OOM kills | encendidas/apagadas, plantillas |

## Qué es cada pieza y dónde corre

Esto es lo que hay que tener claro antes de instalar nada: **cada script corre en un sitio y
solo en ese sitio.**

| Pieza | Dónde se ejecuta | Qué hace |
|---|---|---|
| `nodo/install-nodo.sh` | **en el nodo Proxmox** | instala `node-exporter`, crea el usuario `prometheus@pve` con rol `PVEAuditor` y saca un token |
| `nodo/install-colectores.sh` | **en el nodo Proxmox** | instala los dos recolectores propios y sus timers |
| `nodo/pve-cgroup-collector.py` | *(lo instala el anterior)* | PSI, RAM real y CPU de cada máquina, leyendo `/sys/fs/cgroup` |
| `nodo/pve-fsinfo-collector.py` | *(lo instala el anterior)* | disco real de dentro de cada VM, vía su `qemu-guest-agent` |
| `stack/crear-lxc.sh` | **en el nodo Proxmox**, opcional | atajo para crear un LXC donde meter el stack. **No hace falta si eliges otra cosa** |
| `stack/install-stack.sh` | **en la máquina del stack** | Prometheus + pve-exporter + Grafana, y provisiona los dashboards |
| `stack/actualizar-dashboards.sh` | **en la máquina del stack** | lleva paneles y alertas a Grafana sin tocar paquetes ni contraseñas |
| `stack/alertas.yaml` | **se copia a la máquina del stack** | las seis reglas de alerta |
| `stack/verificar.sh` | **en la máquina del stack** | comprueba que hay datos, no solo servicios arrancados |
| `stack/verificar-dashboard.py` | **en la máquina del stack** | lanza cada consulta de cada panel |
| `dashboards/*.json` | **se copian a la máquina del stack** | los paneles; los lee Grafana al arrancar |
| `dashboards/plantillas/` | **en tu PC** | generan esos `.json`. Los `.json` no se editan a mano |

### Qué se carga en cada sitio

| En el nodo | En la máquina del stack |
|---|---|
| `install-nodo.sh` | `install-stack.sh` |
| `install-colectores.sh` + los dos `.py` | los tres `dashboards/*.json` |
| | `verificar.sh` y `verificar-dashboard.py` |

El nodo **no** necesita los dashboards. La máquina del stack **no** necesita los recolectores.

## Dónde poner el stack: tú eliges

El stack es **Debian normal**: `apt`, `systemd` y tres servicios. `install-stack.sh` no sabe
ni le importa si está dentro de un contenedor, de una VM o de una máquina física.

**Requisitos de esa máquina, sea cual sea:**

- Debian 12 o 13, con acceso `root`
- **2 núcleos y 1,5 GB de RAM** (lo que usa `crear-lxc.sh` por defecto). Abajo está el desglose
  medido y cuándo hay que subirlo
- 8 GB de disco. **No es el histórico lo que pesa**: Grafana se lleva 620 MB de
  `/var/lib/grafana` recién instalada —son los plugins de datasource que trae el paquete— y la
  base de Prometheus iba por 37 MB tras un día con un nodo, o sea ~550 MB para los 15 días de
  retención. El techo de tamaño de la retención lo calcula el instalador solo
- salida a internet, para los paquetes de Debian y el repositorio de Grafana
- que alcance el `:9100` y el `:8006` del nodo

### Cuánta RAM hace falta, medido

Esto no es una estimación: son las cifras del laboratorio —**un nodo, 12 invitados, 2.873
series activas**— leídas del cgroup de cada servicio (`systemctl show -p MemoryCurrent`):

| Servicio | RAM | Por qué |
|---|---|---|
| **Grafana** | **470 MB** | el que más come, y con diferencia: el paquete arranca unos ocho plugins de datasource como procesos Go aparte (Loki, Elasticsearch, InfluxDB, Pyroscope…) aunque no uses ninguno |
| **Prometheus** | **68 MB** | con 2.873 series activas |
| **pve-exporter** | **45 MB** | consulta la API del nodo cada 30 s |

Con los tres en marcha, `free -m` dentro del contenedor marcaba **374 MB usados de 1.536**, y
el resto —1,1 GB— en caché de disco, que es justo lo que le viene bien a Prometheus. Así que
**1,5 GB va holgado para un nodo y sobra para dos o tres.**

**Cuándo hay que subirla.** De los tres, el único que crece es Prometheus, y crece con el
número de series activas. El reparto real de esas 2.873:

| Job | Series | Qué cuesta añadir |
|---|---|---|
| `node` | 1.891 | **cada nodo nuevo pesa ~1.900 series**: el node-exporter del host es el grueso |
| `prometheus` | 728 | fijas, se pagan una vez |
| `pve` | 254 | **cada invitado, ~20 series** de detalle de la API |

Es decir: lo que mueve la aguja es **cuántos nodos vigilas**, no cuántas máquinas tienen. Un
nodo con 50 VMs sigue siendo ~1.900 + 1.000 series; diez nodos ya son ~21.000. Con esa regla,
**hasta 3-4 nodos 1,5 GB aguanta; a partir de ahí 2 GB, y para una flota de diez nodos o más,
4 GB** y revisar el techo de tamaño de la retención.

**Si el stack va en un LXC, no reserves de más.** La memoria de un contenedor es un límite de
cgroup, no una reserva: lo que el stack no use se queda disponible para el host. En una VM, en
cambio, sin `balloon` esos 1,5 GB quedan apartados aunque estén libres.

| Opción | Cuándo elegirla | Qué cuesta |
|---|---|---|
| **LXC** en el propio nodo | quieres lo más ligero y el nodo va justo de RAM: un LXC **no reserva** memoria, es un límite de cgroup, así que lo que el stack no use queda para el host | comparte kernel con el nodo; si el nodo cae, el stack cae con él |
| **VM** en el propio nodo | la prefieres aislada del kernel del host, o quieres migrarla y respaldarla como cualquier otra VM. Con `qemu-guest-agent` dentro, el propio dashboard verá su disco real | sin `balloon` se queda la RAM fija; arranca más lento y ocupa más disco |
| **Una máquina que ya tengas** (otro servidor, una VPS) | **la más sensata si vigilas varios nodos** | una máquina más que mantener |

**Una observación que conviene no saltarse:** si el stack vive dentro del nodo que vigila, el
día que ese nodo se caiga te quedas **sin histórico justo cuando lo necesitas**, y sin la
gráfica que te diría qué pasó antes de la caída. Para un laboratorio da igual; para vigilar
producción, el stack debería estar fuera.

Si eliges LXC, `crear-lxc.sh` te lo monta. Si eliges VM o una máquina existente, **aquí no hay
script y no hace falta**: prepara la máquina como prepares cualquier otra y pasa al paso 3.

## ⚡ Instalación rápida (one-liner)

Un comando por pieza. Trae el repo a `/root/proxmox-monitoring` y lanza lo que le pidas.
Cada uno **comprueba que lo estás ejecutando donde toca** y aborta si no.

### Paso 1 — en el nodo Proxmox (un solo comando)

```bash
curl -fsSL https://raw.githubusercontent.com/mtandazo35/proxmox-monitoring/main/instalar.sh | bash -s -- nodo
```

Eso es **todo lo que se instala en el hypervisor**: node-exporter, el usuario `prometheus@pve`
con rol `PVEAuditor`, su token, y los dos recolectores con sus timers. Al terminar imprime
**los tres datos que necesitarás en el paso 3** y te explica qué hacer a continuación.

Si el nodo tiene UFW activo, pásale la IP de la máquina del stack para que le abra el `:9100`.
Se puede volver a correr más tarde, cuando ya sepas esa IP:

```bash
curl -fsSL https://raw.githubusercontent.com/mtandazo35/proxmox-monitoring/main/instalar.sh | bash -s -- nodo 192.0.2.20
```

*(`colectores` existe por separado solo para reinstalarlos sueltos; `nodo` ya los pone.)*

### Paso 2 — crear la máquina donde vivirá el stack

**El stack NO se instala en el hypervisor.** Necesita una máquina aparte: un **contenedor
LXC**, una **VM**, o un **servidor que ya tengas**. Lo que elijas, con tal de que cumpla los
requisitos de *Dónde poner el stack*.

Si eliges **LXC** en el propio nodo, hay atajo (`IP` y `GW` son obligatorias, `VLAN` solo si tu
red la usa):

```bash
curl -fsSL https://raw.githubusercontent.com/mtandazo35/proxmox-monitoring/main/instalar.sh | IP=<IP_LIBRE/24> GW=<TU_GATEWAY> VLAN=<N> bash -s -- lxc
```

Si eliges **VM** o una **máquina que ya tienes**, aquí no hay nada que correr: créala como
crees tus máquinas, con Debian 12 o 13. En una VM, déjale el `qemu-guest-agent` activado y esta
misma herramienta verá también el disco real de dentro de ella.

### Paso 3 — el stack, dentro de esa máquina

Los tres datos los imprimió el paso 1. El propio paso 1 te deja este comando ya montado, con
los valores sustituidos:

```bash
curl -fsSL https://raw.githubusercontent.com/mtandazo35/proxmox-monitoring/main/instalar.sh | bash -s -- stack <IP_NODO> 'prometheus@pve!monitoring' '<VALOR_DEL_TOKEN>'
```

### Paso 4 — comprobar que hay datos

Solo lectura. Corre las dos verificaciones y suma los fallos:

```bash
curl -fsSL https://raw.githubusercontent.com/mtandazo35/proxmox-monitoring/main/instalar.sh | bash -s -- verificar
```

### Lo que conviene saber antes de pegar eso en un servidor

- **El token del paso `stack` va en la línea de comandos**: es visible en `ps` mientras corre y
  queda en el historial del shell. Es lo mismo que pasarlo por `scp`. Si no te vale, clona el
  repo y lanza `install-stack.sh` leyendo el token de un fichero.
- **Los desinstaladores no se lanzan así**: preguntan antes de borrar, y con `curl | bash` la
  entrada estándar es el propio script. Para esos, clona y ejecútalos a mano.
- **`PROBAR=1`** trae el repo y dice qué ejecutaría, sin ejecutarlo:
  `curl -fsSL ... | PROBAR=1 bash -s -- nodo`
- **`RAMA=otra`** para probar una rama distinta de `main`.
- Si prefieres no canalizar nada a `bash`, clona y corre los scripts a mano: es exactamente lo
  mismo, y está más abajo.

## Instalación paso a paso

### 1. En el nodo Proxmox

```bash
scp -r nodo root@<IP_NODO>:/root/proxmox-monitoring/
ssh root@<IP_NODO> 'cd /root/proxmox-monitoring/nodo && chmod +x *.sh && ./install-nodo.sh'
```

Instala node-exporter, crea `prometheus@pve` con rol `PVEAuditor`, saca un token, **comprueba
que el token lee la API y que NO puede escribir**, e imprime los tres datos que necesita el
paso 3. Respalda `user.cfg` y `token.cfg` en `/root/backups/` antes de tocar nada.

Si el nodo tiene UFW activo, hay que pasarle la IP de la máquina del stack para que abra el
`:9100`: `./install-nodo.sh 192.0.2.20`.

Después, los recolectores por máquina (PSI, RAM real, disco de dentro de las VM):

```bash
ssh root@<IP_NODO> 'cd /root/proxmox-monitoring/nodo && ./install-colectores.sh'
```

### 2. La máquina donde vivirá el stack

Lo que decidiste en el apartado anterior. Si es un **LXC** en el propio nodo, hay atajo:

```bash
scp stack/crear-lxc.sh root@<IP_NODO>:/root/
ssh root@<IP_NODO> 'chmod +x /root/crear-lxc.sh && IP=192.0.2.20/24 GW=192.0.2.1 VLAN=<N> /root/crear-lxc.sh'
```

`IP` y `GW` son obligatorias a propósito: una IP por defecto acaba creando el contenedor en la
red de otro. Antes de crear nada comprueba que el VMID esté libre, que **la IP no responda a
ping**, y que al host le queden al menos 512 MB de margen.

Valores por defecto: VMID `111`, 2 núcleos, 1536 MB, 8 GB en `local-lvm`. Se cambian por
variable de entorno (`RAM=1024 VMID=112 NOMBRE=monitor ./crear-lxc.sh`). Los 1536 MB son los
del desglose medido de arriba: **no bajes de 1 GB** —Grafana sola se lleva 470 MB— y súbelos
si vas a vigilar más de 3-4 nodos.

Si es una **VM o una máquina que ya tienes**, no hay nada que correr aquí: basta con que
cumpla los requisitos de arriba.

### 3. El stack, en esa máquina

Vale para cualquiera de las tres opciones:

```bash
scp stack/install-stack.sh dashboards/*.json root@<IP_STACK>:/root/
ssh root@<IP_STACK> 'chmod +x /root/install-stack.sh && \
    /root/install-stack.sh <IP_NODO> "prometheus@pve!monitoring" "<VALOR_DEL_TOKEN>"'
```

Los tres datos los imprimió `install-nodo.sh` al terminar.

Si esa máquina es un LXC del propio nodo y aún no le has dado acceso por SSH, el mismo paso
sin red de por medio:

```bash
pct push <VMID> install-stack.sh /root/install-stack.sh
for d in dashboards/proxmox-*.json; do pct push <VMID> "$d" "/root/$(basename "$d")"; done
pct exec <VMID> -- chmod +x /root/install-stack.sh
pct exec <VMID> -- /root/install-stack.sh <IP_NODO> 'prometheus@pve!monitoring' '<VALOR_DEL_TOKEN>'
```

Al terminar imprime la URL de Grafana y una contraseña de admin generada al azar, que **no se
vuelve a mostrar**: al gestor de contraseñas.

### 4. Verificar (solo lectura)

```bash
scp stack/verificar.sh stack/verificar-dashboard.py root@<IP_STACK>:/root/
ssh root@<IP_STACK> 'bash /root/verificar.sh'
ssh root@<IP_STACK> 'python3 /root/verificar-dashboard.py'
```

`verificar.sh` comprueba el stack: servicios, con qué config corre Prometheus de verdad,
targets y ocho consultas con datos.

`verificar-dashboard.py` comprueba **el dashboard**: saca las expresiones de todos sus paneles
del JSON provisionado, resuelve la variable `$nodo` y lanza cada consulta contra Prometheus.
Sale con código distinto de cero si algún panel se quedaría vacío. Que un dashboard **cargue**
no significa que **muestre algo**: un panel con una consulta mal escrita sale en blanco y no
deja un solo error en ninguna parte.

Se le puede pasar otro dashboard y **forzar el valor de una variable**, útil cuando la primera
serie que devuelve Prometheus no es la que interesa (una máquina apagada no tiene ni cgroup ni
agente, así que medio dashboard saldría vacío con razón):

```bash
ssh root@<IP_STACK> 'VAR_MAQUINA=<maquina-encendida> python3 /root/verificar-dashboard.py \
    /var/lib/grafana/dashboards/proxmox-maquina.json'
```

**En una instalación recién hecha, media verificación va a salir vacía y no hay nada roto:**
las consultas con `rate(...[5m])` necesitan cinco minutos de histórico que aún no existen.
El script lo detecta y lo avisa; se vuelve a correr diez minutos después.

También valida **cada id de unidad** contra el build de Grafana. Si una unidad no existe,
Grafana no da error: escribe su nombre en crudo detrás del número (`634483 dtdurationhms`
en vez de `7d 08:14:43`).

## El usuario y el token de la API de Proxmox

`install-nodo.sh` lo crea todo solo, pero conviene saber **qué** crea, porque es lo único que
este montaje añade a la configuración de Proxmox. Y si prefieres hacerlo a mano, aquí está.

### Qué hace falta y por qué

`pve-exporter` consulta la API de Proxmox para sacar el detalle por máquina (CPU, RAM, disco,
encendida o apagada). Para eso necesita **credenciales**. Se usa un **token de API** y no una
contraseña porque un token se revoca solo, no sirve para entrar por la interfaz web, y se le
puede dar un permiso concreto.

| Qué | Valor | Por qué |
|---|---|---|
| Usuario | `prometheus@pve` | realm `pve`, no `pam`: no es un usuario del sistema operativo, no puede entrar por SSH |
| Rol | `PVEAuditor` sobre `/` | el rol de **solo lectura** de Proxmox: ve todo el datacenter, no cambia nada |
| Token | `monitoring` | el que usa el exporter |
| Privilege separation | **desactivada** | sin esto el token no hereda nada |

**El detalle que arruina la instalación si se falla:** un token se crea por omisión con
*privilege separation* **activada**, y entonces **no hereda los permisos del usuario**: tiene
los suyos propios, que son ninguno. El exporter responde, el `:9221` contesta, no hay ni un
error en ningún log… y **todas las métricas salen a cero**. Por eso el script lo crea con
`--privsep 0` y luego **comprueba de verdad** que lee y que no escribe.

### Lo que ejecuta el script

```bash
pveum user add prometheus@pve --comment "Solo lectura para prometheus-pve-exporter"
pveum acl modify / --users prometheus@pve --roles PVEAuditor
pveum user token add prometheus@pve monitoring --privsep 0 --output-format json
```

La última línea devuelve el **valor del token**, que es la parte secreta. **Solo se muestra una
vez**: Proxmox no lo guarda en claro y no hay forma de volver a leerlo. Si lo pierdes, se borra
el token y se crea otro — que es justo lo que hace el script si lo vuelves a ejecutar.

Después comprueba las dos mitades, porque una sola no basta:

```bash
# 1. que LEE: tiene que devolver la lista de nodos
curl -k -H "Authorization: PVEAPIToken=prometheus@pve!monitoring=<VALOR>" \
     https://127.0.0.1:8006/api2/json/nodes

# 2. que NO ESCRIBE: tiene que devolver 403
curl -k -X POST -H "Authorization: PVEAPIToken=prometheus@pve!monitoring=<VALOR>" \
     https://127.0.0.1:8006/api2/json/nodes/$(hostname)/stopall
```

Si el primero no devuelve nodos, el token no hereda permisos. Si el segundo **no** devuelve
403, la ACL está mal y ese token puede apagarte las máquinas.

### Hacerlo a mano desde la interfaz web

Si prefieres no correr el script, o quieres entender qué toca:

1. **Datacenter → Permissions → Users → Add**: usuario `prometheus`, realm `Proxmox VE
   authentication server` (`pve`). Sin contraseña: no la va a usar.
2. **Datacenter → Permissions → Add → User Permission**: *Path* `/`, *User* `prometheus@pve`,
   *Role* `PVEAuditor`. Eso es lo que le da la lectura de todo el datacenter.
3. **Datacenter → Permissions → API Tokens → Add**: *User* `prometheus@pve`, *Token ID*
   `monitoring`, y **desmarca `Privilege Separation`**. Este es el paso que se falla.
4. Copia el **Secret** que aparece: es el `<VALOR>`. **No se vuelve a mostrar.**

Con eso tienes los tres datos del paso 3:

```
IP del nodo     : la IP de tu nodo Proxmox
Usuario y token : prometheus@pve!monitoring
Valor del token : el Secret del punto 4
```

### Qué NO hace falta

- **Nada de `root@pam`.** Hay recetas por ahí que usan un token de root con la separación de
  privilegios desactivada: eso es un token con permiso para destruir el datacenter guardado en
  un fichero de un servidor de monitoreo. Aquí no.
- **Ninguna credencial dentro de las máquinas vigiladas.** El token es del nodo.
- **Ningún puerto nuevo abierto hacia fuera.** El exporter consulta la API; nadie entra.

## Grafana: qué se instala y cómo entrar

Grafana **no va en el hypervisor**: lo instala `install-stack.sh` en la máquina del stack, en
su paso 6. Del repositorio oficial, no del de Debian, que va muy por detrás:

```
clave  -> /etc/apt/keyrings/grafana.gpg      (desde https://apt.grafana.com/gpg.key)
origen -> /etc/apt/sources.list.d/grafana.list
paquete-> apt install grafana
```

Se escribe en un `.list` a propósito: `apt` solo lee `*.list` y `*.sources`, así que un
`.bak` quedaría inerte y llevaría a buscar el fallo donde no está.

**Queda configurado solo**, sin tocar nada a mano:

| | |
|---|---|
| Datasource | Prometheus en `http://127.0.0.1:9090`, uid `pmx-prometheus`, marcado por omisión |
| Dashboards | los tres `.json`, provisionados desde `/var/lib/grafana/dashboards` |
| Zona horaria | `default_timezone = America/Guayaquil` en `grafana.ini` |
| Puerto | `:3000` |

**La contraseña de admin se genera al azar** y se imprime **una sola vez** al terminar el
instalador, junto a la URL. Al gestor de contraseñas: no queda en ningún sitio legible. Grafana
te la hará cambiar en el primer acceso, que es lo correcto.

¿La perdiste? Desde dentro de la máquina del stack:

```bash
grafana cli --homepath /usr/share/grafana admin reset-admin-password '<NUEVA>'
systemctl restart grafana-server
```

En versiones más antiguas el binario se llama `grafana-cli`; el instalador prueba los dos.

**Los dashboards son de solo lectura en la práctica:** están provisionados desde fichero, así
que si los editas por la interfaz, el cambio se pierde cuando Grafana recarga la provisión. Lo
que se toca es la plantilla `.py` del repo y se regenera. Si quieres trastear sin perder nada,
haz **Save as…** con otro nombre y ese sí es tuyo.

## El dashboard

Una sola pantalla, en este orden:

| Franja | Qué muestra |
|---|---|
| Arriba | procesos en ejecución y bloqueados por E/S, medidores de disco/memoria/CPU, uptime del servidor |
| Segunda | estado del nodo, **máquinas** totales/encendidas/apagadas, disco total y uso por storage |
| **Máquinas** | cuántas VM y cuántos LXC, vCPU y RAM comprometidas, **sobrecompromiso** de vCPU y de RAM, y una tabla con una fila por máquina |
| **Arranque automático** | las máquinas que **no vuelven solas** si reinicias el nodo |
| El host | tráfico de las interfaces, CPU y memoria, carga del sistema, disco por filesystem, E/S y espera de disco |
| **Presión del sistema (PSI)** | cuánto tiempo hubo tareas **paradas** esperando CPU, disco o memoria |
| Plegado | histórico por máquina: las 10 que más consumen de CPU, RAM, red y E/S |

### Una sola tabla, con columna TIPO

VM y contenedores van juntos y se separan con el **embudo** de la columna `TIPO`. Importa
saber qué significa cada métrica en cada uno, porque **no es lo mismo**:

| | Máquina virtual (`VM`) | Contenedor (`LXC`) |
|---|---|---|
| `DISCO USADO` | la API devuelve **`0`** — el hypervisor no ve dentro del filesystem | **uso real**, lo gestiona el nodo |
| `RAM (%)` | lo que el hypervisor cree **vía balloon**; sin el driver marca casi toda la asignada | real |

Por eso **no hay columna de porcentaje de disco**: en la mayoría de las filas sería un `0`
que parece un dato.

### El disco real de dentro de una VM

`/cluster/resources` devuelve **`0`** en el campo `disk` de una máquina virtual: para el
hypervisor ese disco es un **bloque**, y el filesystem de dentro solo lo entiende el sistema
del invitado. `pve-exporter` únicamente lee ese endpoint, así que ese 0 es todo lo que llega.

El dato sí existe, en otro sitio: `qm agent <vmid> get-fsinfo` se lo pregunta al
**`qemu-guest-agent`** que corre dentro. `nodo/pve-fsinfo-collector.py` hace esa pregunta a
cada VM encendida cada 5 minutos y publica el resultado por el *textfile collector* de
node-exporter:

```bash
cd nodo && ./install-colectores.sh              # rollback: ./desinstalar-colectores.sh
```

No instala **nada dentro de las máquinas**: un script en el nodo, un timer, y el fichero
`/var/lib/prometheus/node-exporter/pve_fsinfo.prom`. Sin puertos, sin usuarios, sin
credenciales — usa `qm` en local.

Métricas que publica: `pve_guest_fs_size_bytes`, `pve_guest_fs_used_bytes`
(con `mountpoint` y `fstype`), `pve_guest_agent_up`, `pve_fsinfo_guests_total`,
`pve_fsinfo_last_run_timestamp_seconds`.

**La columna `AGENTE` de la tabla distingue tres casos**, que no son lo mismo:

| Valor | Qué significa |
|---|---|
| `Sí` | el agente contesta; `DISCO (%)` es uso real medido dentro |
| `NO` | la VM está encendida pero su agente no responde — **no hay dato**, la celda queda en blanco |
| `no aplica` | es un contenedor: nunca necesita agente, el nodo ya sabe su disco |
| *(en blanco)* | la máquina está apagada |

Una VM de **RouterOS/CHR saldrá siempre como `NO`**: no existe `qemu-guest-agent` para CHR.
Eso es correcto y es el motivo de que una celda vacía **no** se rellene con un cero.

**El valor que devuelve el agente lo escribe el sistema operativo del invitado, no el nodo.**
Por eso el recolector escapa todo valor de etiqueta antes de escribirlo: un `mountpoint` con
comillas o saltos de línea podría inyectar líneas enteras de métricas en el fichero que
node-exporter sirve. Ver la cabecera del script.

### Qué sabe el host de cada máquina, sin entrar en ella

`nodo/pve-cgroup-collector.py` lee `/sys/fs/cgroup` cada minuto. Ni API, ni agente, ni
subprocesos: unas pocas lecturas de fichero por máquina. Con 4 máquinas tarda **0,002 s**, así
que escala a 134 invitados sin despeinarse. Sirve igual para VM y para contenedores.

| Métrica | Qué es | Por qué no la da la API |
|---|---|---|
| `pve_guest_pressure_waiting_seconds_total` / `_stalled_` | **PSI por máquina**, por recurso (`cpu`, `io`, `memory`) | `/cluster/resources` no tiene nada parecido |
| `pve_guest_memory_host_bytes` | RAM que la máquina ocupa **de verdad** en el host (`memory.current`) | la API da el valor del **balloon**, que es lo que el hypervisor *cree* |
| `pve_guest_cpu_seconds_total` | CPU consumida, contador exacto del cgroup | la API da una media recalculada |

La diferencia no es teórica: la VM 101 marcaba **1,80 GiB reales** en el cgroup y 1,68 GB por
el balloon. El cgroup es lo que el nodo está pagando, overhead de QEMU incluido.

**PSI por máquina es lo que distingue "esta VM consume poco" de "esta VM no avanza porque el
nodo va saturado".** Con sobrecompromiso de vCPU, es la métrica que dice *a quién* le está
tocando esperar.

Una máquina apagada no tiene cgroup, así que no aparece: eso es *no hay dato*, no un cero.

### Los tres dashboards

| Dashboard | uid | Para qué |
|---|---|---|
| **Proxmox — visión general** | `proxmox-overview` | el nodo y la tabla de todas las máquinas |
| **Proxmox — todas las máquinas** | `proxmox-maquinas` | **comparar**: una serie por máquina en cada panel |
| **Proxmox — una máquina** | `proxmox-maquina` | una sola, a fondo |

Están enlazados entre sí por arriba (conservando nodo y rango de tiempo), y desde la tabla de
Máquinas se llega al detalle **pinchando el nombre**.

### Todas las máquinas, comparadas

Los mismos paneles que el de una máquina, pero con todas a la vez: CPU en núcleos, memoria
real en el host, red de entrada y salida, lectura y escritura de disco, los **tres paneles de
presión** y una barra de disco usado real por máquina.

Dos decisiones que importan con 134 invitados:

- **El selector de máquinas es múltiple y trae "Todas"**. Con una flota grande, pintar 134
  series por panel no se lee; se filtra a las que interesan.
- **Solo salen las encendidas.** Una máquina apagada pintaría una línea plana en cero, que no
  es un dato. Los paneles de cgroup ya se filtran solos (una máquina apagada no tiene cgroup);
  a los que vienen de la API se les añade `and on(id) (pve_up == 1)`.

La leyenda va en tabla a la derecha, **ordenada por máximo**: lo que se busca en un panel con
muchas máquinas es cuáles son las de arriba.

### Segundo dashboard: una máquina a fondo

`dashboards/proxmox-maquina.json` (uid `proxmox-maquina`). Se elige nodo y máquina arriba, o
se llega **pinchando el nombre en la tabla de Máquinas** del dashboard general, que conserva
el rango de tiempo.

Trae: estado, uptime, vCPU, RAM y disco asignados, disco real usado, CPU contra su techo de
vCPU, memoria (real en el host / balloon / asignada), red, lectura y escritura de disco, y
**los tres paneles de presión de esa máquina**.

### PSI: lo que el porcentaje de uso no cuenta

El kernel publica en `/proc/pressure` cuánto tiempo hubo tareas **paradas** esperando un
recurso. Es distinto del % de uso y distinto del load:

- **`Esperando`** (*some*): % del tiempo en que **al menos una** tarea estuvo bloqueada.
- **`Todas paradas`** (*full*): % del tiempo en que **ninguna** pudo avanzar — el nodo entero
  parado.

Un hypervisor puede estar al 40% de CPU y con presión alta: lo que duele no es cuánto se usa,
es cuánto se espera. **La presión de E/S es la mejor señal de que el almacenamiento es el
cuello de botella**, y la de memoria avisa antes de que aparezca un OOM.

Como referencia: sostenido por encima del 10% ya se nota; por encima del 40% el nodo va a
tirones.

### Dos columnas que no son consumo

- **`ARRANQUE AUTO`**: si pone `NO`, esa máquina **no vuelve sola** cuando reinicies el nodo.
  El exportador **no emite la métrica** para las que no lo tienen puesto, así que hay que
  rellenar las que faltan con cero o desaparecen de la columna sin avisar.
- **`vCPU` / `RAM TECHO` / `DISCO TECHO`**: lo reservado, no lo gastado. Están para comparar:
  8 vCPU asignadas con 0,03 núcleos en uso son ocho vCPU que no hacen nada.

El **uptime del servidor se pone naranja a los 90 días**: en un hypervisor un uptime largo
casi siempre significa que hay un kernel instalado que nunca se arrancó.

El **sobrecompromiso** divide lo comprometido por las máquinas encendidas entre lo que el
host tiene de verdad. `2,50` en vCPU significa dos vCPU y media por cada núcleo real.

### Nada de ceros falsos

Si la variable **Nodo** llega vacía, un filtro `instance=""` no casa con nada. Un
`count(...) or vector(0)` pintaría entonces un **`0` perfectamente creíble** — y ver
`0 encendidas` y `0 apagadas` a la vez es mentira, no un dato. Por eso el relleno de cero se
hace con `or (count(pve_guest_info{instance="$nodo"}) * 0)`: solo aparece si el nodo existe
de verdad, y si no, el panel dice **"No data"**, que es la verdad.

## Alertas

Sin esto, el montaje es una pantalla que hay que mirar. Las reglas están en
`stack/alertas.yaml` y se provisionan en Grafana; no se crean a mano.

| Regla | Salta cuando | Espera |
|---|---|---|
| **Objetivo caído** | `up == 0` en cualquier job | 5 min |
| **Storage > 85%** | un storage del nodo se llena | 10 min |
| **Filesystem del nodo > 90%** | se llena el disco del **hypervisor** | 10 min |
| **RAM del nodo > 90%** | antes de que llegue el OOM killer | 10 min |
| **Máquina con arranque automático apagada** | tiene `onboot` y no está encendida | 10 min |
| **Presión de CPU sostenida** | PSI de CPU > 40% | 15 min |

**"Objetivo caído" cubre también al propio stack.** Si `pve-exporter` se cae, el dashboard no
da error: se queda quieto con el último dato bueno. Esa es la alerta que avisa de que el
monitoreo se quedó ciego.

**La de presión usa PSI y no porcentaje de CPU a propósito.** Un nodo al 100% puede ir fino y
uno al 40% estar ahogando a todo el mundo. Lo que duele es la espera, no el uso.

### Por dónde te avisa: eso lo configuras tú, una vez

`alertas.yaml` define **cuándo** salta cada alerta, no **por dónde** te llega. El canal lleva
un token de Telegram o unas credenciales de SMTP, y eso **no puede vivir en un repositorio
público**. Se configura una sola vez en **Grafana → Alerting → Contact points**.

Hasta que lo hagas, las alertas se ven y cambian de color en *Alerting → Alert rules*, pero no
te escriben a ningún sitio.

### El detalle que convierte las alertas en ruido

Una regla cuya consulta **se queda vacía cuando todo va bien** —como `up == 0`— necesita
`noDataState: OK`. Con el valor por omisión, Grafana levanta una alerta `DatasourceNoData`
**permanente**, y una alerta que está siempre encendida es la forma más segura de que nadie
vuelva a mirar ninguna.

Las que siempre devuelven un número (storage, filesystem, RAM, presión) se quedan con
`NoData`, porque ahí *sin datos* sí significa que el objetivo dejó de responder.

## Actualizar paneles y alertas sin reinstalar

```bash
curl -fsSL https://raw.githubusercontent.com/mtandazo35/proxmox-monitoring/main/instalar.sh | bash -s -- dashboards
```

Copia los `.json` y `alertas.yaml`, valida antes de copiar, reinicia Grafana y **comprueba
contra su base de datos qué reglas ha ingerido de verdad** — que el fichero esté no significa
que Grafana lo acepte.

**No toca paquetes, ni configuración, ni la contraseña.** Esto existe porque antes la única
forma de llevar un panel nuevo era reejecutar `install-stack.sh`, que **regenera la contraseña
de admin**: actualizar un gráfico te echaba de tu propio panel.

## Lo que se tira a propósito

De 4300 series, **1320 no se usaban en ningún panel**:

| Métrica | Series | Qué es |
|---|---|---|
| `node_systemd_unit_state` | ~1000 por nodo | una serie por unidad de systemd **y por estado** |
| `pve_ha_state` + `pve_lock_state` | ~285 | una por cada estado posible de cada máquina, a cero casi siempre |

Se descartan con `metric_relabel_configs` **al guardar, no al scrapear**: así no hay que tocar
el hypervisor. Si algún día hacen falta, se quita la regla y vuelven.

La retención tiene **techo de tiempo y de tamaño**. Solo por tiempo no protege de nada: si la
flota crece, 15 días pueden no caber, y **con el disco lleno Prometheus se para**. El techo se
calcula como la mitad del disco libre, con un mínimo de 1 GB, porque Prometheus necesita sitio
para compactar.

## Deshacer

| Qué | Cómo |
|---|---|
| Los paquetes del stack, conservando la máquina | `stack/desinstalar-stack.sh`, en la máquina del stack |
| La máquina entera, si era un LXC | `pct stop <VMID> && pct destroy <VMID>`, desde el nodo |
| La máquina entera, si era una VM | `qm stop <VMID> && qm destroy <VMID>`, desde el nodo |
| La máquina entera, si era un servidor tuyo | lo que hagas normalmente; el stack no deja nada fuera de sus paquetes |
| Los recolectores del nodo | `nodo/desinstalar-colectores.sh`, en el nodo |
| El nodo: exporter, usuario y token | `nodo/desinstalar-nodo.sh`, en el nodo |

Dentro de las máquinas vigiladas **nunca se instaló nada**, así que ahí no hay nada que
deshacer.

## Trampas que ya están resueltas en los scripts

- **`--privsep 0` en el token.** Con el valor por omisión (`1`) el token no hereda los permisos
  del usuario: el exporter responde, pero **todas las métricas salen a cero**. Es el fallo más
  difícil de diagnosticar porque nada da error.
- **`--no-install-recommends` en cada `apt install`.** Sin él, `apt install prometheus` arrastra
  `prometheus-node-exporter`; en la flota DNS eso dejó una unidad en `failed` peleando por el
  `:9100`.
- **El `relabel` del job `pve`.** El objetivo viaja como parámetro `?target=` y la petición se
  manda siempre al exporter local. Sin ese relabel, Prometheus intentaría scrapear un `:9221`
  en el propio Proxmox, que no existe.
- **Retención de 15 días, no 30.** En la flota DNS 30 días llenaron el disco.
- **Zona horaria.** La máquina del stack se queda en UTC y es Grafana quien muestra hora de Ecuador
  (`default_timezone = America/Guayaquil`): así las series no se mueven si cambia el huso.
- **`promtool check config` antes de arrancar**, y si no valida se restaura el original.
- **El repo de Grafana se escribe en un `.list`.** apt solo lee `*.list` y `*.sources`; un
  `.bak` es inerte y lleva a diagnósticos falsos.

## Lo que este montaje NO puede darte

**El disco de una VM sin `qemu-guest-agent`.** Con agente sí se ve, y es lo que hace
`pve-fsinfo-collector.py`. Sin él no hay forma: para el hypervisor ese disco es un bloque y el
filesystem de dentro solo lo entiende el sistema del invitado. Una VM de **RouterOS/CHR no lo
tendrá nunca**, porque no existe `qemu-guest-agent` para CHR. En la tabla aparece como
`AGENTE: NO` y la celda de disco queda **en blanco**, que es la verdad: *no se sabe*, no *cero*.

**Lo que pasa dentro de una máquina.** Procesos, servicios, logs, qué proceso concreto se está
comiendo la CPU. Esto mide desde fuera. Para eso haría falta un `node-exporter` dentro de cada
invitado, que es otro proyecto y otra decisión.

**La presión que siente el invitado por dentro.** `pve_guest_pressure_*` es la presión del
**cgroup de esa máquina en el host**: cuánto la está haciendo esperar el nodo. No es el PSI que
mide el kernel de dentro de la VM, que puede ser otro.

**Logs.** No hay Loki ni nada parecido. Esto son métricas.

**Avisos.** No hay alertas configuradas: es una pantalla que hay que mirar. Grafana trae
alerting propio y no haría falta Alertmanager, pero **todavía no está montado**.

**Retención con techo de tamaño.** Hay límite por tiempo (15 días) pero no por tamaño, así que
en un disco pequeño conviene añadir `--storage.tsdb.retention.size` antes de llevarlo a una
flota grande.

## Añadir más nodos

Correr `nodo/install-nodo.sh` y `nodo/install-colectores.sh` en el nodo nuevo, y añadir dos
entradas a `/etc/prometheus/prometheus.yml` **de la máquina del stack**: su `:9100` en el job
`node` y su IP en el job `pve`. Los tres dashboards tienen un selector de nodo arriba, así que
no hay que duplicar ni un panel.

Con varios nodos, lo razonable es que **el stack no viva dentro de ninguno de ellos**: ver
*Dónde poner el stack*.
