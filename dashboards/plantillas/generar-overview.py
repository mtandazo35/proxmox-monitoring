# -*- coding: utf-8 -*-
"""Genera dashboards/proxmox-overview.json.

Zona de invitados separada en dos apartados: VM (qemu) y contenedores (lxc).
Se separan porque las mismas metricas NO significan lo mismo en cada uno:
  - disco usado: en una VM la API devuelve 0 (Proxmox no ve dentro del
    filesystem del invitado); en un LXC es uso real.
  - RAM: en una VM es lo que el hypervisor cree via balloon; en un LXC es real.
"""
import json, io, sys

DS = {"type": "prometheus", "uid": "pmx-prometheus"}
VERDE, NARANJA, ROJO, AZUL = "green", "orange", "red", "blue"
TEAL = "#173c48"          # fondo de celda de la tabla
_id = [0]


def pid():
    _id[0] += 1
    return _id[0]


def tgt(expr, ref="A", legend=None, instant=False, fmt=None):
    t = {"datasource": DS, "editorMode": "code", "expr": expr, "refId": ref, "range": not instant}
    if legend is not None:
        t["legendFormat"] = legend
    if instant:
        t["instant"] = True
        t["range"] = False
    if fmt:
        t["format"] = fmt
    return t


def pasos(*pares):
    s = [{"color": pares[0][1] if pares else VERDE, "value": None}]
    for v, c in pares[1:]:
        s.append({"color": c, "value": v})
    return {"mode": "absolute", "steps": s}


def gp(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def stat(titulo, expr, x, y, w, h, unit=None, decimals=None, mapeos=None,
         thr=None, colorMode="value", grafico=True, texto_modo=None, desc=None):
    d = {"color": {"mode": "thresholds"},
         "mappings": mapeos or [],
         "thresholds": thr or pasos((None, VERDE))}
    if unit:
        d["unit"] = unit
    if decimals is not None:
        d["decimals"] = decimals
    p = {
        "id": pid(), "type": "stat", "title": titulo, "datasource": DS, "gridPos": gp(x, y, w, h),
        "fieldConfig": {"defaults": d, "overrides": []},
        "options": {"colorMode": colorMode, "graphMode": "area" if grafico else "none",
                    "justifyMode": "auto", "orientation": "auto",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "textMode": texto_modo or "auto"},
        "targets": [tgt(expr)],
    }
    if desc:
        p["description"] = desc
    return p


def gauge(titulo, expr, x, y, w, h, thr, unit="percent", mx=100, decimals=1):
    return {
        "id": pid(), "type": "gauge", "title": titulo, "datasource": DS, "gridPos": gp(x, y, w, h),
        "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "mappings": [],
                                     "max": mx, "min": 0, "unit": unit, "decimals": decimals,
                                     "thresholds": thr}, "overrides": []},
        "options": {"minVizHeight": 60, "minVizWidth": 60, "showThresholdLabels": False,
                    "showThresholdMarkers": True, "sizing": "auto",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
        "targets": [tgt(expr)],
    }


def bargauge(titulo, targets, x, y, w, h, thr, unit="percent", mx=100):
    return {
        "id": pid(), "type": "bargauge", "title": titulo, "datasource": DS, "gridPos": gp(x, y, w, h),
        "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "mappings": [],
                                     "max": mx, "min": 0, "unit": unit, "decimals": 1,
                                     "thresholds": thr}, "overrides": []},
        "options": {"displayMode": "gradient", "orientation": "horizontal",
                    "showUnfilled": True, "valueMode": "color", "minVizHeight": 16,
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
        "targets": targets,
    }


def ts(titulo, targets, x, y, w, h, unit=None, mx=None, desc=None):
    # Estilo de Manuel: barras (no lineas), fillOpacity 47.
    d = {"color": {"mode": "palette-classic"},
         "custom": {"axisBorderShow": False, "axisCenteredZero": False, "axisLabel": "",
                    "axisPlacement": "auto", "barAlignment": 0, "drawStyle": "bars",
                    "fillOpacity": 47, "gradientMode": "none",
                    "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                    "insertNulls": False, "lineInterpolation": "linear", "lineWidth": 1,
                    "pointSize": 5, "scaleDistribution": {"type": "linear"},
                    "showPoints": "never", "spanNulls": False,
                    "stacking": {"group": "A", "mode": "none"},
                    "thresholdsStyle": {"mode": "off"}},
         "mappings": [], "thresholds": pasos((None, VERDE))}
    if unit:
        d["unit"] = unit
    if mx is not None:
        d["max"] = mx
        d["min"] = 0
    p = {"id": pid(), "type": "timeseries", "title": titulo, "datasource": DS,
         "gridPos": gp(x, y, w, h),
         "fieldConfig": {"defaults": d, "overrides": []},
         "options": {"legend": {"calcs": ["mean", "max"], "displayMode": "table",
                                "placement": "bottom", "showLegend": True},
                     "tooltip": {"mode": "multi", "sort": "desc"}},
         "targets": targets}
    if desc:
        p["description"] = desc
    return p


def texto(titulo, md, x, y, w, h):
    return {"id": pid(), "type": "text", "title": titulo, "gridPos": gp(x, y, w, h),
            "options": {"mode": "markdown", "content": md}, "transparent": titulo == ""}


def fila(titulo, y, colapsada=False, hijos=None):
    return {"id": pid(), "type": "row", "title": titulo, "collapsed": colapsada,
            "gridPos": gp(0, y, 24, 1), "panels": hijos or []}


def ov(nombre, props):
    return {"matcher": {"id": "byName", "options": nombre}, "properties": props}


def celda_gauge(thr_pasos, ancho=130):
    return [{"id": "unit", "value": "percent"},
            {"id": "max", "value": 100}, {"id": "min", "value": 0},
            {"id": "decimals", "value": 1},
            {"id": "thresholds", "value": thr_pasos},
            {"id": "custom.cellOptions",
             "value": {"type": "gauge", "mode": "gradient", "valueDisplayMode": "color"}},
            {"id": "custom.width", "value": ancho}]


# ---------------------------------------------------------------- consultas base
N = '{job="node",nodo="$nodo"}'
NFS = 'fstype!~"tmpfs|devtmpfs|overlay|squashfs|fuse.*"'
G = 'id=~"(qemu|lxc)/.*",instance="$nodo"'
NUCLEOS = 'scalar(count(count by (cpu) (node_cpu_seconds_total{job="node",nodo="$nodo"})))'
CPU_PCT = '100 - (avg(rate(node_cpu_seconds_total{job="node",mode="idle",nodo="$nodo"}[5m])) * 100)'
MEM_PCT = "100 * (1 - node_memory_MemAvailable_bytes" + N + " / node_memory_MemTotal_bytes" + N + ")"

UMBRAL_USO = pasos((None, VERDE), (70, NARANJA), (85, ROJO))
UMBRAL_DISCO = pasos((None, VERDE), (80, NARANJA), (90, ROJO))

paneles = []

# ================================================================= FILA 1: el host de un vistazo
paneles.append(texto("", '<div style="text-align:center;padding-top:14px">'
                         '<div style="font-size:30px;font-weight:700;letter-spacing:2px;color:#e8713a">'
                         'PROXMOX</div>'
                         '<div style="font-size:15px;color:#9aa4ad;margin-top:6px">$nodo</div>'
                         '</div>', 0, 0, 4, 5))

paneles.append(stat("Procesos en ejecucion", "node_procs_running" + N, 4, 0, 3, 5, decimals=0))

paneles.append(stat("Procesos bloqueados por E/S", "node_procs_blocked" + N, 7, 0, 3, 5,
                    decimals=0, thr=pasos((None, VERDE), (1, NARANJA), (5, ROJO)),
                    desc="Procesos en estado D: esperando al disco. Si esto sube y se queda arriba, "
                         "el cuello de botella es el almacenamiento, no la CPU."))

paneles.append(gauge("Uso de disco (%)",
                     '100 * (1 - node_filesystem_avail_bytes{job="node",nodo="$nodo",mountpoint="/"} '
                     '/ node_filesystem_size_bytes{job="node",nodo="$nodo",mountpoint="/"})',
                     10, 0, 3, 5, UMBRAL_DISCO))

paneles.append(gauge("Uso de memoria (%)", MEM_PCT, 13, 0, 3, 5, UMBRAL_USO))
paneles.append(gauge("Uso de CPU (%)", CPU_PCT, 16, 0, 3, 5, UMBRAL_USO))

paneles.append(stat("Uptime del servidor",
                    "node_time_seconds" + N + " - node_boot_time_seconds" + N,
                    19, 0, 5, 5, unit="dtdhms", grafico=False,
                    thr=pasos((None, VERDE), (7776000, NARANJA)),
                    desc="Se pone naranja a los 90 dias. Un uptime largo en un hypervisor suele "
                         "significar que hay un kernel instalado que nunca se arranco."))

# ================================================================= FILA 2: nodo y almacenamiento
paneles.append(stat("Estado del nodo", 'pve_up{id=~"node/.*",instance="$nodo"}', 0, 5, 4, 4,
                    colorMode="background", grafico=False, texto_modo="value",
                    mapeos=[{"type": "value", "options": {
                        "0": {"text": "CAIDO", "color": ROJO, "index": 0},
                        "1": {"text": "EN LINEA", "color": VERDE, "index": 1}}}]))

# OJO con el 'or vector(0)': si la variable $nodo llega vacia, el filtro
# instance="" no casa con nada y el panel pintaria un 0 perfectamente creible.
# Un cero falso es peor que un "sin datos". Por eso el relleno de cero se hace
# con CERO (count de maquinas) * 0: solo aparece si el nodo existe de verdad.
CERO = '(count(pve_guest_info{instance="$nodo"}) * 0)'

paneles.append(stat("Maquinas", 'count(pve_guest_info{instance="$nodo"})',
                    4, 5, 3, 4, decimals=0, grafico=False))
paneles.append(stat("Encendidas", "count(pve_up{" + G + "} == 1) or " + CERO,
                    7, 5, 3, 4, decimals=0, grafico=False, colorMode="background"))
paneles.append(stat("Apagadas", "count(pve_up{" + G + "} == 0) or " + CERO,
                    10, 5, 3, 4, decimals=0, grafico=False, colorMode="background",
                    thr=pasos((None, "text"), (1, NARANJA))))

paneles.append(stat("Disco total (storages)",
                    'sum(pve_disk_size_bytes{id=~"storage/.*",instance="$nodo"})',
                    13, 5, 4, 4, unit="bytes", grafico=False, decimals=2,
                    desc="Suma de la capacidad de todos los storages del nodo. Dos storages sobre el "
                         "mismo disco fisico se cuentan dos veces: es capacidad declarada, no "
                         "tamano del hardware."))

paneles.append(bargauge("Uso de disco (storages)",
                        [tgt('100 * (label_replace(pve_disk_usage_bytes{id=~"storage/.*",instance="$nodo"}, '
                             '"almacen", "$1", "id", "storage/[^/]+/(.*)") '
                             '/ on(id, instance) pve_disk_size_bytes{id=~"storage/.*",instance="$nodo"})',
                             legend="{{almacen}}")],
                        17, 5, 7, 4, UMBRAL_USO))

# ================================================================= tabla de invitados (generica)
def tabla_invitados(titulo, tipo, y, alto, columnas, descripcion, ancho=24, x=0):
    """Construye una tabla con una fila por maquina.

    Cada consulta se reduce a (id, valor) con `sum by (id)` para que el join
    sobre 'id' no arrastre etiquetas repetidas. La consulta A es la unica que
    aporta nombre, nodo y tipo.
    """
    sel = 'id=~"%s",instance="$nodo"' % tipo
    # El 'id' crudo es "qemu/101". Se saca el numero a una etiqueta aparte con
    # label_replace y se muestra esa: el prefijo ya lo dice la columna TIPO.
    # El 'id' entero se conserva porque es la clave del join, pero se oculta.
    targets = [tgt('label_replace(pve_guest_info{%s}, "vmid", "$1", "id", ".*/(.*)")' % sel,
                   "A", instant=True, fmt="table")]
    renombrar = {"node": "NODO", "name": "MAQUINA", "vmid": "ID", "type": "TIPO"}
    orden = {"node": 0, "name": 1, "vmid": 2, "type": 3}
    for i, (ref, nombre, expr, _props) in enumerate(columnas):
        targets.append(tgt(expr % {"sel": sel}, ref, instant=True, fmt="table"))
        renombrar["Value #" + ref] = nombre
        orden["Value #" + ref] = 4 + i

    overrides = [
        ov("NODO", [{"id": "custom.cellOptions", "value": {"type": "auto"}},
                    {"id": "custom.width", "value": 85}]),
        # Pinchar el nombre abre el dashboard de esa maquina sola, con el mismo
        # rango de tiempo. __value.raw es el texto de la celda, o sea el nombre.
        ov("MAQUINA", [{"id": "custom.cellOptions", "value": {"type": "auto"}},
                       {"id": "custom.width", "value": 180},
                       {"id": "links", "value": [{
                           "title": "Ver esta maquina a fondo",
                           "url": "/d/proxmox-maquina/?var-nodo=${nodo}"
                                  "&var-maquina=${__value.raw}&${__url_time_range}"}]}]),
        ov("ID", [{"id": "custom.cellOptions", "value": {"type": "auto"}},
                  {"id": "custom.width", "value": 70}]),
        # TIPO: qemu = maquina virtual, lxc = contenedor. El embudo de la
        # cabecera deja filtrar uno u otro sin partir la tabla en dos.
        ov("TIPO", [{"id": "custom.cellOptions", "value": {"type": "color-background",
                                                           "mode": "basic"}},
                    {"id": "custom.width", "value": 85},
                    {"id": "mappings", "value": [{"type": "value", "options": {
                        "qemu": {"text": "VM", "color": AZUL, "index": 0},
                        "lxc": {"text": "LXC", "color": "purple", "index": 1}}}]}]),
    ]
    for _ref, nombre, _expr, props in columnas:
        if props:
            overrides.append(ov(nombre, props))

    return {
        "id": pid(), "type": "table", "title": titulo, "description": descripcion,
        "datasource": DS, "gridPos": gp(x, y, ancho, alto),
        "fieldConfig": {
            "defaults": {"color": {"mode": "thresholds"},
                         "custom": {"align": "auto",
                                    "cellOptions": {"type": "color-background", "mode": "basic"},
                                    "filterable": True, "inspect": False, "minWidth": 80},
                         "mappings": [],
                         "thresholds": {"mode": "absolute", "steps": [{"color": TEAL, "value": None}]}},
            "overrides": overrides},
        "options": {"cellHeight": "sm",
                    "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
                    "showHeader": True,
                    "sortBy": [{"desc": True, "displayName": "CPU (%)"}]},
        "targets": targets,
        "transformations": [
            {"id": "filterFieldsByName",
             "options": {"include": {"pattern": "^(id|vmid|name|node|type|Value #.*)$"}}},
            {"id": "joinByField", "options": {"byField": "id", "mode": "outer"}},
            {"id": "organize", "options": {"excludeByName": {"Value #A": True, "id": True},
                                           "includeByName": {},
                                           "indexByName": orden,
                                           "renameByName": renombrar}},
        ],
    }


MAPEO_ESTADO = [{"id": "mappings", "value": [{"type": "value", "options": {
    "0": {"text": "Apagada", "color": ROJO, "index": 0},
    "1": {"text": "Encendida", "color": VERDE, "index": 1}}}]},
    {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
    {"id": "custom.width", "value": 105}]

# onboot: el exporter NO emite la serie para los invitados que no lo tienen puesto.
# Por eso hay que rellenar con cero los que faltan, o desaparecen de la columna.
ONBOOT = ('sum by (id) (pve_onboot_status{%(sel)s}) '
          'or sum by (id) (pve_guest_info{%(sel)s} * 0)')
MAPEO_ONBOOT = [{"id": "mappings", "value": [{"type": "value", "options": {
    "0": {"text": "NO", "color": NARANJA, "index": 0},
    "1": {"text": "Si", "color": VERDE, "index": 1}}}]},
    {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
    {"id": "custom.width", "value": 115}]

COL_COMUNES_1 = [
    ("B", "ESTADO", 'sum by (id) (pve_up{%(sel)s})', MAPEO_ESTADO),
    ("C", "ARRANQUE AUTO", ONBOOT, MAPEO_ONBOOT),
    ("D", "CPU (%)", 'sum by (id) (pve_cpu_usage_ratio{%(sel)s}) * 100',
     celda_gauge(UMBRAL_USO)),
    ("E", "NUCLEOS EN USO",
     'sum by (id) (pve_cpu_usage_ratio{%(sel)s} * on(id, instance) group_left() '
     'pve_cpu_usage_limit{instance="$nodo"})',
     [{"id": "decimals", "value": 2}, {"id": "unit", "value": "short"},
      {"id": "custom.width", "value": 120}]),
    ("F", "vCPU", 'sum by (id) (pve_cpu_usage_limit{%(sel)s})',
     [{"id": "unit", "value": "short"}, {"id": "decimals", "value": 0},
      {"id": "custom.width", "value": 75}]),
    ("G", "RAM (%)",
     '100 * sum by (id) (pve_memory_usage_bytes{%(sel)s}) '
     '/ sum by (id) (pve_memory_size_bytes{%(sel)s})', celda_gauge(UMBRAL_USO)),
    ("H", "RAM USADA", 'sum by (id) (pve_memory_usage_bytes{%(sel)s})',
     [{"id": "unit", "value": "bytes"}, {"id": "decimals", "value": 1}]),
    ("I", "RAM TECHO", 'sum by (id) (pve_memory_size_bytes{%(sel)s})',
     [{"id": "unit", "value": "bytes"}, {"id": "decimals", "value": 0}]),
]

COL_COMUNES_2 = [
    ("M", "BITS (ENTRADA)",
     'sum by (id) (rate(pve_network_receive_bytes_total{%(sel)s}[5m])) * 8',
     [{"id": "unit", "value": "bps"}]),
    ("N", "BITS (SALIDA)",
     'sum by (id) (rate(pve_network_transmit_bytes_total{%(sel)s}[5m])) * 8',
     [{"id": "unit", "value": "bps"}]),
    ("O", "DISCO (LECTURA)",
     'sum by (id) (rate(pve_disk_read_bytes_total{%(sel)s}[5m]))',
     [{"id": "unit", "value": "Bps"}]),
    ("P", "DISCO (ESCRITURA)",
     'sum by (id) (rate(pve_disk_written_bytes_total{%(sel)s}[5m]))',
     [{"id": "unit", "value": "Bps"}]),
    # dtdhms = "7d 08:14:43". "dtdurationhms" NO existe: Grafana escribiria el
    # nombre de la unidad en crudo detras del numero, sin dar ningun error.
    ("Q", "UPTIME", 'sum by (id) (pve_uptime_seconds{%(sel)s})',
     [{"id": "unit", "value": "dtdhms"}, {"id": "custom.width", "value": 140}]),
]

# ================================================================= APARTADO: MAQUINAS
paneles.append(fila("Maquinas", 9))

TODAS = 'id=~"(qemu|lxc)/.*",instance="$nodo"'
VCPU_ENC = ('sum(pve_cpu_usage_limit{' + TODAS + '} '
            'and on(id, instance) pve_up{instance="$nodo"} == 1)')
RAM_ENC = ('sum(pve_memory_size_bytes{' + TODAS + '} '
           'and on(id, instance) pve_up{instance="$nodo"} == 1)')

paneles.append(stat("Maquinas virtuales",
                    'count(pve_guest_info{id=~"qemu/.*",instance="$nodo"}) or ' + CERO,
                    0, 10, 3, 4, decimals=0, grafico=False))
paneles.append(stat("Contenedores",
                    'count(pve_guest_info{id=~"lxc/.*",instance="$nodo"}) or ' + CERO,
                    3, 10, 3, 4, decimals=0, grafico=False))
paneles.append(stat("vCPU comprometidas", VCPU_ENC, 6, 10, 4, 4, decimals=0, grafico=False,
                    desc="Suma de las vCPU de las maquinas ENCENDIDAS. No es consumo: es lo que "
                         "tienen derecho a pedir todas a la vez."))
paneles.append(stat("Sobrecompromiso de vCPU", "(" + VCPU_ENC + ") / " + NUCLEOS,
                    10, 10, 4, 4, decimals=2, unit="none", grafico=False,
                    thr=pasos((None, VERDE), (2, NARANJA), (4, ROJO)),
                    desc="vCPU comprometidas dividido entre los nucleos reales del host. 1 = cada "
                         "vCPU tiene su nucleo. 6 = seis maquinas se pelean por cada nucleo si "
                         "todas piden a la vez."))
paneles.append(stat("RAM comprometida", RAM_ENC, 14, 10, 5, 4, unit="bytes", decimals=1,
                    grafico=False,
                    desc="Suma de la RAM asignada a las maquinas encendidas. Si supera la RAM del "
                         "host, el nodo depende de que nadie la use entera."))
paneles.append(stat("Sobrecompromiso de RAM",
                    "(" + RAM_ENC + ") / scalar(node_memory_MemTotal_bytes" + N + ")",
                    19, 10, 5, 4, decimals=2, unit="none", grafico=False,
                    thr=pasos((None, VERDE), (1, NARANJA), (1.5, ROJO)),
                    desc="RAM comprometida dividida entre la RAM fisica del host. Por encima de 1 "
                         "hay mas RAM prometida que instalada."))

# Disco REAL. Dos fuentes distintas unidas con `or`, que funciona porque las dos
# quedan reducidas a la etiqueta `id`:
#   - VM : pve_guest_fs_* lo publica pve-fsinfo-collector.py preguntandole al
#          qemu-guest-agent de dentro del invitado (job node, etiqueta `nodo`).
#   - LXC: pve_disk_usage_bytes ya es real (job pve, etiqueta `instance`).
# Una VM apagada o sin agente no aparece en ninguna de las dos: la celda queda
# EN BLANCO, que es la verdad. Un 0 ahi seria mentira.
FS_USADO = 'sum by (id) (pve_guest_fs_used_bytes{nodo="$nodo"})'
FS_TOTAL = 'sum by (id) (pve_guest_fs_size_bytes{nodo="$nodo"})'
LXC_USADO = 'sum by (id) (pve_disk_usage_bytes{id=~"lxc/.*",instance="$nodo"})'
LXC_TOTAL = 'sum by (id) (pve_disk_size_bytes{id=~"lxc/.*",instance="$nodo"})'

DISCO_USADO_REAL = FS_USADO + " or " + LXC_USADO
DISCO_PCT_REAL = ("(100 * " + FS_USADO + " / " + FS_TOTAL + ")"
                  " or (100 * " + LXC_USADO + " / " + LXC_TOTAL + ")")

# 0 = el agente no contesta, 1 = contesta, 2 = es un LXC y no le hace falta.
AGENTE = ('sum by (id) (pve_guest_agent_up{nodo="$nodo"}) '
          'or (sum by (id) (pve_guest_info{id=~"lxc/.*",instance="$nodo"}) * 0 + 2)')
MAPEO_AGENTE = [{"id": "mappings", "value": [{"type": "value", "options": {
    "0": {"text": "NO", "color": NARANJA, "index": 0},
    "1": {"text": "Si", "color": VERDE, "index": 1},
    "2": {"text": "no aplica", "color": "text", "index": 2}}}]},
    {"id": "custom.cellOptions", "value": {"type": "color-text"}},
    {"id": "custom.width", "value": 100}]

paneles.append(tabla_invitados(
    "Maquinas", "(qemu|lxc)/.*", 14, 12,
    COL_COMUNES_1 + [
        ("S", "AGENTE", AGENTE, MAPEO_AGENTE),
        ("J", "DISCO (%)", DISCO_PCT_REAL, celda_gauge(UMBRAL_DISCO)),
        ("T", "DISCO USADO", DISCO_USADO_REAL,
         [{"id": "unit", "value": "bytes"}, {"id": "decimals", "value": 1}]),
        ("K", "DISCO TECHO", 'sum by (id) (pve_disk_size_bytes{%(sel)s})',
         [{"id": "unit", "value": "bytes"}, {"id": "decimals", "value": 0}]),
    ] + COL_COMUNES_2,
    "Una fila por maquina. Filtra por TIPO para ver solo VM o solo contenedores. "
    "DISCO (%) y DISCO USADO son uso REAL de dentro: en una VM lo contesta su qemu-guest-agent, "
    "en un LXC lo sabe el nodo. Si la celda esta en blanco es que no se sabe (apagada, o sin "
    "agente: mira la columna AGENTE), no que este a cero. DISCO TECHO es lo asignado. "
    "RAM (%) de una VM viene del balloon, es lo que el hypervisor cree que usa."))

# ================================================================= APARTADO: ARRANQUE AUTOMATICO
paneles.append(fila("Arranque automatico", 26))

SIN_ONBOOT = ('pve_guest_info{instance="$nodo"} unless on(id) '
              '(sum by (id) (pve_onboot_status{instance="$nodo"}) == 1)')

paneles.append(stat("No arrancan solas", "count(" + SIN_ONBOOT + ") or " + CERO,
                    0, 27, 4, 6, decimals=0, grafico=False, colorMode="background",
                    thr=pasos((None, VERDE), (1, NARANJA)),
                    desc="Maquinas sin 'arranque al inicio'. Si reinicias el nodo, estas NO "
                         "vuelven: hay que encenderlas a mano."))

paneles.append(stat("Arrancan solas",
                    'count(sum by (id) (pve_onboot_status{instance="$nodo"}) == 1) or ' + CERO,
                    4, 27, 4, 6, decimals=0, grafico=False, colorMode="background",
                    thr=pasos((None, VERDE))))

paneles.append({
    "id": pid(), "type": "table", "title": "Hay que encenderlas a mano",
    "description": ("Maquinas sin 'arranque al inicio'. Vacio = todas vuelven solas tras un "
                    "reinicio del nodo. La columna NODO no se muestra: la tabla ya esta "
                    "filtrada por el nodo que elijas arriba."),
    "datasource": DS, "gridPos": gp(8, 27, 16, 6),
    "fieldConfig": {
        # color-text en vez de color-background: con 5 filas a ancho completo, el
        # fondo macizo convertia el panel en una pared naranja.
        "defaults": {"color": {"mode": "thresholds"},
                     "custom": {"align": "auto",
                                "cellOptions": {"type": "color-text"},
                                "filterable": True, "inspect": False},
                     "mappings": [],
                     "thresholds": {"mode": "absolute", "steps": [{"color": NARANJA, "value": None}]}},
        "overrides": [
            ov("ID", [{"id": "custom.width", "value": 80}]),
            ov("TIPO", [{"id": "custom.width", "value": 90},
                        {"id": "mappings", "value": [{"type": "value", "options": {
                            "qemu": {"text": "VM", "index": 0},
                            "lxc": {"text": "LXC", "index": 1}}}]}]),
        ]},
    "options": {"cellHeight": "sm",
                "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
                "showHeader": True,
                "sortBy": [{"desc": False, "displayName": "ID"}]},
    "targets": [tgt('label_replace(%s, "vmid", "$1", "id", ".*/(.*)")' % SIN_ONBOOT,
                    "A", instant=True, fmt="table")],
    "transformations": [
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Value": True, "__name__": True, "instance": True,
                              "job": True, "template": True, "node": True, "id": True},
            "includeByName": {},
            "indexByName": {"vmid": 0, "name": 1, "type": 2},
            "renameByName": {"vmid": "ID", "name": "MAQUINA", "type": "TIPO"}}},
    ],
})

# ================================================================= APARTADO: EL HOST
paneles.append(fila("El host", 34))

IF_FISICAS = 'device=~"(nic|eno|enp|eth|ens|bond)[0-9a-z]*|vmbr[0-9]+"'

paneles.append(ts("Trafico de las interfaces del host",
                  [tgt('sum by (device) (rate(node_network_receive_bytes_total{job="node",nodo="$nodo",'
                       + IF_FISICAS + "}[5m])) * 8", "A", "{{device}} entrada"),
                   tgt('-sum by (device) (rate(node_network_transmit_bytes_total{job="node",nodo="$nodo",'
                       + IF_FISICAS + "}[5m])) * 8", "B", "{{device}} salida")],
                  0, 35, 10, 8, unit="bps",
                  desc="Solo fisicas y bridges. Se dejan fuera lo, tap*, veth*, fwbr* y las "
                       "subinterfaces de VLAN, que contarian el mismo trafico dos veces. La salida "
                       "va en negativo."))

paneles.append(ts("CPU y memoria del host",
                  [tgt(CPU_PCT, "A", "CPU %"), tgt(MEM_PCT, "B", "Memoria %")],
                  10, 35, 9, 8, unit="percent", mx=100))

carga = gauge("Carga del sistema", "", 19, 35, 5, 8,
              pasos((None, VERDE), (70, NARANJA), (100, ROJO)), mx=150)
carga["targets"] = [
    tgt("100 * node_load1" + N + " / " + NUCLEOS, "A", "1 min"),
    tgt("100 * node_load5" + N + " / " + NUCLEOS, "B", "5 min"),
    tgt("100 * node_load15" + N + " / " + NUCLEOS, "C", "15 min"),
]
carga["description"] = ("Carga en % de los nucleos del nodo. 100% = hay tantos procesos esperando "
                        "como nucleos hay. Por encima de 100% la CPU es el cuello de botella.")
paneles.append(carga)

paneles.append(bargauge("Disco del host por filesystem",
                        [tgt('100 * (1 - node_filesystem_avail_bytes{job="node",nodo="$nodo",' + NFS + "} "
                             '/ node_filesystem_size_bytes{job="node",nodo="$nodo",' + NFS + "})",
                             legend="{{mountpoint}}")],
                        0, 43, 8, 8, UMBRAL_DISCO))

paneles.append(ts("E/S de disco del host",
                  [tgt('sum by (device) (rate(node_disk_read_bytes_total{job="node",nodo="$nodo",'
                       'device!~"dm-.*|loop.*"}[5m]))', "A", "{{device}} lectura"),
                   tgt('-sum by (device) (rate(node_disk_written_bytes_total{job="node",nodo="$nodo",'
                       'device!~"dm-.*|loop.*"}[5m]))', "B", "{{device}} escritura")],
                  8, 43, 8, 8, unit="Bps"))

paneles.append(ts("Espera de disco",
                  [tgt('sum by (device) (rate(node_disk_io_time_weighted_seconds_total{job="node",'
                       'nodo="$nodo",device!~"dm-.*|loop.*"}[5m]))', "A", "{{device}}")],
                  16, 43, 8, 8, unit="s",
                  desc="Tiempo que los procesos pasan esperando a cada disco, por segundo. Dice si "
                       "el disco es el cuello de botella, cosa que el porcentaje de uso no dice."))

# ================================================================= APARTADO: PRESION (PSI)
# PSI viene del kernel (/proc/pressure). Mide algo que ni el % de CPU ni el load
# dicen: cuanto tiempo hubo tareas PARADAS esperando un recurso.
#   some = % del tiempo en que AL MENOS una tarea esperaba.
#   full = % del tiempo en que TODAS esperaban, o sea el host no avanzo nada.
# Un nodo puede estar al 40% de CPU y con presion alta: lo que duele no es
# cuanto se usa, es cuanto se espera.
paneles.append(fila("Presion del sistema (PSI)", 51))

PSI_DESC = ("'Esperando' = % del tiempo en que al menos una tarea estuvo parada por este "
            "recurso. 'Todas paradas' = % del tiempo en que ninguna pudo avanzar. "
            "Sostenido por encima del 10% ya duele; por encima del 40% el nodo va a tirones.")

paneles.append(ts("Presion de CPU",
                  [tgt("rate(node_pressure_cpu_waiting_seconds_total" + N + "[5m]) * 100",
                       "A", "Esperando CPU")],
                  0, 52, 8, 8, unit="percent", mx=100,
                  desc="Porcentaje de tiempo con alguna tarea esperando turno de CPU. A diferencia "
                       "del % de uso, esto sube cuando hay MAS trabajo que nucleos. " + PSI_DESC))

paneles.append(ts("Presion de E/S",
                  [tgt("rate(node_pressure_io_waiting_seconds_total" + N + "[5m]) * 100",
                       "A", "Esperando disco"),
                   tgt("rate(node_pressure_io_stalled_seconds_total" + N + "[5m]) * 100",
                       "B", "Todas paradas")],
                  8, 52, 8, 8, unit="percent", mx=100,
                  desc="La mejor senal de que el almacenamiento es el cuello de botella. "
                       "'Todas paradas' alto = el nodo entero se queda esperando al disco. "
                       + PSI_DESC))

paneles.append(ts("Presion de memoria",
                  [tgt("rate(node_pressure_memory_waiting_seconds_total" + N + "[5m]) * 100",
                       "A", "Esperando memoria"),
                   tgt("rate(node_pressure_memory_stalled_seconds_total" + N + "[5m]) * 100",
                       "B", "Todas paradas")],
                  16, 52, 8, 8, unit="percent", mx=100,
                  desc="Sube cuando el kernel pelea por recuperar memoria: swap, cache que se "
                       "tira, reclamo directo. Avisa del problema ANTES de que llegue el OOM. "
                       + PSI_DESC))

# ================================================================= plegado
hijos = [
    ts("CPU - los 10 que mas consumen",
       [tgt("topk(10, pve_cpu_usage_ratio{" + G + "} * on(id, instance) group_left(name) "
            'pve_guest_info{instance="$nodo"} * on(id, instance) group_left() '
            'pve_cpu_usage_limit{instance="$nodo"})', "A", "{{name}}")],
       0, 61, 12, 9, unit="short",
       desc="Nucleos consumidos de verdad, no porcentaje: 2 = esta usando dos nucleos enteros."),
    ts("RAM - los 10 que mas consumen",
       [tgt("topk(10, pve_memory_usage_bytes{" + G + "} * on(id, instance) group_left(name) "
            'pve_guest_info{instance="$nodo"})', "A", "{{name}}")],
       12, 61, 12, 9, unit="bytes"),
    ts("Red por maquina - los 10 que mas mueven",
       [tgt("topk(10, (rate(pve_network_receive_bytes_total{" + G + "}[5m]) + "
            "rate(pve_network_transmit_bytes_total{" + G + "}[5m])) * 8 "
            '* on(id, instance) group_left(name) pve_guest_info{instance="$nodo"})', "A", "{{name}}")],
       0, 70, 12, 9, unit="bps"),
    ts("E/S por maquina - los 10 que mas mueven",
       [tgt("topk(10, (rate(pve_disk_read_bytes_total{" + G + "}[5m]) + "
            "rate(pve_disk_written_bytes_total{" + G + "}[5m])) "
            '* on(id, instance) group_left(name) pve_guest_info{instance="$nodo"})', "A", "{{name}}")],
       12, 70, 12, 9, unit="Bps"),
]
paneles.append(fila("Historico por maquina", 60, colapsada=True, hijos=hijos))

# ================================================================= dashboard
dash = {
    "annotations": {"list": []},
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 1,
    "links": [
        {"asDropdown": False, "icon": "external link", "includeVars": True, "keepTime": True, "tags": [], "title": "Todas las maquinas", "type": "link", "url": "/d/proxmox-maquinas"},
        {"asDropdown": False, "icon": "external link", "includeVars": True, "keepTime": True, "tags": [], "title": "Una sola maquina", "type": "link", "url": "/d/proxmox-maquina"},
    ],
    "panels": paneles,
    "refresh": "1m",
    "schemaVersion": 39,
    "tags": ["proxmox"],
    "templating": {"list": [{
        "name": "nodo", "label": "Nodo", "type": "query", "datasource": DS,
        "query": "label_values(pve_node_info, instance)",
        "refresh": 1, "includeAll": False, "multi": False,
        "current": {}, "options": [], "sort": 1}]},
    "time": {"from": "now-1h", "to": "now"},
    "timepicker": {},
    "timezone": "America/Guayaquil",
    "title": "Proxmox - vision general",
    "uid": "proxmox-overview",
    "version": 3,
    "weekStart": "",
}

destino = sys.argv[1]
with io.open(destino, "w", encoding="utf-8", newline="\n") as f:
    json.dump(dash, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("escrito:", destino, "-", len(paneles), "paneles de primer nivel")
