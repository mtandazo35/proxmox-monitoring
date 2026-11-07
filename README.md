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
   NODO PROXMOX                          LXC "pmx-monitor"
  ┌────────────────────┐                ┌──────────────────────────────────┐
  │ node-exporter      │◄───── scrape ──┤ prometheus          :9090        │
  │   :9100            │                │   retención 15 días              │
  │                    │                │                                 │
  │ API :8006 ◄────────┼─── lectura ────┤ prometheus-pve-exporter  :9221   │
  │   token PVEAuditor │                │   (token de solo lectura)        │
  │   (SOLO LECTURA)   │                │                                 │
  └────────────────────┘                │ grafana             :3000        │
                                        └──────────────────────────────────┘
```

**En el hypervisor va un solo paquete** (`prometheus-node-exporter`) más un usuario de API de
solo lectura. Todo lo demás vive en el contenedor: si el stack se cae, o se borra, el nodo no
se enteró.

**Por qué LXC y no VM:** un LXC no reserva la RAM, es un límite de cgroup. En un host con poca
memoria libre eso importa — lo que el stack no use se queda disponible para el host. Una VM sin
`balloon` se la queda fija.

**Dos exporters porque miden cosas distintas:**

| | node-exporter | pve-exporter |
|---|---|---|
| Qué ve | el sistema operativo del host | la API de Proxmox |
| CPU/RAM | del nodo, real | de cada invitado |
| Disco | cada filesystem del host, real | storages del nodo, real |
| Extra | ZFS ARC, presión de E/S, OOM kills | encendidas/apagadas, plantillas |

## Instalación

### 1. En el nodo Proxmox

```bash
scp nodo/install-nodo.sh root@<IP_NODO>:/root/     # los instaladores van a /root
ssh root@<IP_NODO> 'chmod +x /root/install-nodo.sh && /root/install-nodo.sh'
```

Instala node-exporter, crea `prometheus@pve` con rol `PVEAuditor`, saca un token, **comprueba
que el token lee la API y que NO puede escribir**, e imprime los tres datos que necesita el
paso 3. Respalda `user.cfg` y `token.cfg` en `/root/backups/` antes de tocar nada.

Si el nodo tiene UFW activo, hay que pasarle la IP del stack para que abra el `:9100`:
`./install-nodo.sh 192.0.2.20`.

### 2. Crear el contenedor (también en el nodo)

```bash
scp stack/crear-lxc.sh stack/install-stack.sh dashboards/proxmox-overview.json root@<IP_NODO>:/root/
ssh root@<IP_NODO> 'cd /root && chmod +x crear-lxc.sh && IP=192.0.2.20/24 GW=192.0.2.1 VLAN=78 ./crear-lxc.sh'
```

Antes de crear nada comprueba tres cosas: que el VMID esté libre, que **la IP no responda a
ping**, y que al host le queden al menos 512 MB de margen después de dar la RAM al contenedor.

Valores por defecto: VMID `111`, 2 núcleos, 1536 MB, 8 GB en `local-lvm`. Se cambian por
variable de entorno (`RAM=1024 ./crear-lxc.sh`).

### 3. El stack dentro del contenedor

```bash
pct push 111 /root/install-stack.sh /root/install-stack.sh
pct push 111 /root/proxmox-overview.json /root/proxmox-overview.json
pct exec 111 -- chmod +x /root/install-stack.sh
pct exec 111 -- /root/install-stack.sh <IP_NODO> 'prometheus@pve!monitoring' '<VALOR_DEL_TOKEN>'
```

Al terminar imprime la URL de Grafana y una contraseña de admin generada al azar, que **no se
vuelve a mostrar**: al gestor de contraseñas.

### 4. Verificar (solo lectura)

```bash
pct push 111 /root/verificar.sh /root/verificar.sh
pct exec 111 -- bash /root/verificar.sh

pct push 111 /root/verificar-dashboard.py /root/verificar-dashboard.py
pct exec 111 -- python3 /root/verificar-dashboard.py
```

`verificar.sh` comprueba el stack: servicios, con qué config corre Prometheus de verdad,
targets y ocho consultas con datos.

`verificar-dashboard.py` comprueba **el dashboard**: saca las expresiones de todos sus
paneles del JSON provisionado, resuelve la variable `$nodo` y lanza cada consulta contra
Prometheus. Sale con código distinto de cero si algún panel se quedaría vacío. Que un
dashboard **cargue** no significa que **muestre algo**: un panel con una consulta mal escrita
sale en blanco y no deja un solo error en ninguna parte.

Se le puede pasar otro dashboard y **forzar el valor de una variable**, útil cuando la
primera serie que devuelve Prometheus no es la que interesa (una máquina apagada no tiene ni
cgroup ni agente, así que medio dashboard saldría vacío con razón):

```bash
pct exec 111 -- env VAR_MAQUINA=<maquina-encendida> python3 /root/verificar-dashboard.py     /var/lib/grafana/dashboards/proxmox-maquina.json
```

También valida **cada id de unidad** contra el build de Grafana. Si una unidad no existe,
Grafana no da error: escribe su nombre en crudo detrás del número (`634483 dtdurationhms`
en vez de `7d 08:14:43`).

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

## Deshacer

| Qué | Cómo |
|---|---|
| Todo el stack | `pct stop 111 && pct destroy 111` (desde el nodo) |
| Solo los paquetes, conservando el LXC | `stack/desinstalar-stack.sh` dentro del contenedor |
| El nodo: exporter, usuario y token | `nodo/desinstalar-nodo.sh` en el nodo |

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
- **Zona horaria.** El contenedor se queda en UTC y es Grafana quien muestra hora de Ecuador
  (`default_timezone = America/Guayaquil`): así las series no se mueven si cambia el huso.
- **`promtool check config` antes de arrancar**, y si no valida se restaura el original.
- **El repo de Grafana se escribe en un `.list`.** apt solo lee `*.list` y `*.sources`; un
  `.bak` es inerte y lleva a diagnósticos falsos.

## Lo que este montaje NO puede darte

**El uso de disco dentro de una VM.** Para una VM, la API de Proxmox reporta el **tamaño del
disco asignado**, no lo ocupado por el filesystem de dentro: la columna sale casi siempre al
100% y no significa nada. Para un LXC sí es uso real, porque el filesystem lo gestiona el nodo.

Para ver el disco real dentro de una VM hacen falta `qemu-guest-agent` en el invitado (ya lo
instala `deploy-vm.sh` en las VMs nuevas) o un node-exporter dentro. El dashboard lleva un
panel de aviso para que esa columna no se lea mal, y el consumo real de almacenamiento sí se ve
en el panel de **storages**.

## Añadir más nodos

Correr `nodo/install-nodo.sh` en el nodo nuevo y añadir dos entradas a
`/etc/prometheus/prometheus.yml` del contenedor: su `:9100` en el job `node` y su IP en el job
`pve`. El dashboard tiene un selector de nodo arriba, así que no hay que duplicar paneles.
