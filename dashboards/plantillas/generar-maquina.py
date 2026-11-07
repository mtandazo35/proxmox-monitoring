# -*- coding: utf-8 -*-
"""Genera dashboards/proxmox-maquina.json: una sola maquina, a fondo."""
import json, io, sys

DS = {"type": "prometheus", "uid": "pmx-prometheus"}
VERDE, NARANJA, ROJO = "green", "orange", "red"
_id = [0]


def pid():
    _id[0] += 1
    return _id[0]


def tgt(expr, ref="A", legend=None):
    t = {"datasource": DS, "editorMode": "code", "expr": expr, "refId": ref, "range": True}
    if legend is not None:
        t["legendFormat"] = legend
    return t


def pasos(*pares):
    s = [{"color": pares[0][1], "value": None}]
    for v, c in pares[1:]:
        s.append({"color": c, "value": v})
    return {"mode": "absolute", "steps": s}


def gp(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def stat(titulo, expr, x, y, w, h, unit=None, decimals=None, mapeos=None, thr=None,
         colorMode="value", desc=None):
    d = {"color": {"mode": "thresholds"}, "mappings": mapeos or [],
         "thresholds": thr or pasos((None, VERDE))}
    if unit:
        d["unit"] = unit
    if decimals is not None:
        d["decimals"] = decimals
    p = {"id": pid(), "type": "stat", "title": titulo, "datasource": DS, "gridPos": gp(x, y, w, h),
         "fieldConfig": {"defaults": d, "overrides": []},
         "options": {"colorMode": colorMode, "graphMode": "none", "justifyMode": "auto",
                     "orientation": "auto", "textMode": "auto",
                     "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
         "targets": [tgt(expr)]}
    if desc:
        p["description"] = desc
    return p


def gauge(titulo, expr, x, y, w, h, thr, unit="percent", mx=100, desc=None):
    p = {"id": pid(), "type": "gauge", "title": titulo, "datasource": DS, "gridPos": gp(x, y, w, h),
         "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "mappings": [],
                                      "max": mx, "min": 0, "unit": unit, "decimals": 1,
                                      "thresholds": thr}, "overrides": []},
         "options": {"minVizHeight": 60, "minVizWidth": 60, "showThresholdLabels": False,
                     "showThresholdMarkers": True, "sizing": "auto",
                     "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
         "targets": [tgt(expr)]}
    if desc:
        p["description"] = desc
    return p


def ts(titulo, targets, x, y, w, h, unit=None, mx=None, desc=None):
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


def fila(titulo, y):
    return {"id": pid(), "type": "row", "title": titulo, "collapsed": False,
            "gridPos": gp(0, y, 24, 1), "panels": []}


# ---------------------------------------------------------------- selectores
# Las metricas de pve-exporter solo llevan `id`; el nombre esta en
# pve_guest_info. Multiplicar por el (que vale 1) filtra a la maquina elegida y
# ademas arrastra su nombre.
JOIN = ' * on(id) group_left(name) pve_guest_info{instance="$nodo",name="$maquina"}'
JOIN0 = ' * on(id) group_left() pve_guest_info{instance="$nodo",name="$maquina"}'
# Las de los recolectores del nodo ya traen `name`: se filtran directo.
M = 'nodo="$nodo",name="$maquina"'

UMBRAL = pasos((None, VERDE), (70, NARANJA), (85, ROJO))
UMBRAL_DISCO = pasos((None, VERDE), (80, NARANJA), (90, ROJO))
PSI_DESC = ("'Esperando' = % del tiempo en que al menos una tarea de ESTA maquina estuvo "
            "parada por el recurso. 'Todo parado' = % del tiempo en que la maquina entera no "
            "pudo avanzar. Viene del cgroup del invitado, lo mide el kernel del host.")

paneles = []

# ================================================================= identificacion
paneles.append(stat("Estado", 'pve_up{instance="$nodo"}' + JOIN0, 0, 0, 3, 4,
                    colorMode="background",
                    mapeos=[{"type": "value", "options": {
                        "0": {"text": "APAGADA", "color": ROJO, "index": 0},
                        "1": {"text": "ENCENDIDA", "color": VERDE, "index": 1}}}]))

paneles.append(stat("Uptime", 'pve_uptime_seconds{instance="$nodo"}' + JOIN0, 3, 0, 4, 4,
                    unit="dtdhms"))

paneles.append(stat("vCPU asignadas", 'pve_cpu_usage_limit{instance="$nodo"}' + JOIN0,
                    7, 0, 3, 4, decimals=0))

paneles.append(stat("RAM asignada", 'pve_memory_size_bytes{instance="$nodo"}' + JOIN0,
                    10, 0, 4, 4, unit="bytes", decimals=1))

paneles.append(stat("Disco asignado", 'pve_disk_size_bytes{instance="$nodo"}' + JOIN0,
                    14, 0, 4, 4, unit="bytes", decimals=1,
                    desc="Lo que tiene asignado, no lo que ocupa."))

paneles.append(gauge("Disco usado (real)",
                     "(100 * sum by (id) (pve_guest_fs_used_bytes{" + M + "}) "
                     "/ sum by (id) (pve_guest_fs_size_bytes{" + M + "})) "
                     'or (100 * sum by (id) (pve_disk_usage_bytes{id=~"lxc/.*",instance="$nodo"}'
                     + JOIN0 + ") "
                     '/ sum by (id) (pve_disk_size_bytes{id=~"lxc/.*",instance="$nodo"}'
                     + JOIN0 + "))",
                     18, 0, 6, 4, UMBRAL_DISCO,
                     desc="Uso real de DENTRO. En una VM lo contesta su qemu-guest-agent; en un "
                          "LXC lo sabe el nodo. Vacio = la maquina esta apagada o su agente no "
                          "contesta (p. ej. RouterOS/CHR, que no tiene agente)."))

# ================================================================= consumo
paneles.append(fila("Consumo", 4))

paneles.append(ts("CPU",
                  [tgt("rate(pve_guest_cpu_seconds_total{" + M + "}[5m])", "A", "Nucleos en uso"),
                   tgt('pve_cpu_usage_limit{instance="$nodo"}' + JOIN0, "B", "vCPU asignadas")],
                  0, 5, 12, 8, unit="short",
                  desc="Nucleos consumidos de verdad, del cgroup de la maquina. Si la linea se "
                       "pega al techo de vCPU asignadas, le falta CPU."))

paneles.append(ts("Memoria",
                  [tgt("pve_guest_memory_host_bytes{" + M + "}", "A", "Real en el host"),
                   tgt('pve_memory_usage_bytes{instance="$nodo"}' + JOIN0, "B", "Segun el balloon"),
                   tgt('pve_memory_size_bytes{instance="$nodo"}' + JOIN0, "C", "Asignada")],
                  12, 5, 12, 8, unit="bytes",
                  desc="'Real en el host' es memory.current de su cgroup: lo que el nodo paga de "
                       "verdad, con el overhead de QEMU incluido. 'Segun el balloon' es lo que el "
                       "hypervisor cree que usa el invitado. No tienen por que coincidir."))

paneles.append(ts("Red",
                  [tgt('rate(pve_network_receive_bytes_total{instance="$nodo"}[5m]) * 8' + JOIN0,
                       "A", "Entrada"),
                   tgt('-rate(pve_network_transmit_bytes_total{instance="$nodo"}[5m]) * 8' + JOIN0,
                       "B", "Salida")],
                  0, 13, 12, 8, unit="bps"))

paneles.append(ts("Disco: lectura y escritura",
                  [tgt('rate(pve_disk_read_bytes_total{instance="$nodo"}[5m])' + JOIN0,
                       "A", "Lectura"),
                   tgt('-rate(pve_disk_written_bytes_total{instance="$nodo"}[5m])' + JOIN0,
                       "B", "Escritura")],
                  12, 13, 12, 8, unit="Bps"))

# ================================================================= presion
paneles.append(fila("Presion de esta maquina (PSI)", 21))

for i, (recurso, titulo, extra) in enumerate((
        ("cpu", "Presion de CPU",
         "Sube cuando esta maquina pide CPU y el nodo no se la da. Es la senal de que el "
         "sobrecompromiso le esta pasando factura A ELLA."),
        ("io", "Presion de E/S",
         "Sube cuando esta maquina espera al disco. Con varias maquinas peleando por el mismo "
         "almacenamiento, aqui se ve a cual le toca esperar."),
        ("memory", "Presion de memoria",
         "Sube cuando el kernel pelea por recuperar memoria de esta maquina. Avisa antes del OOM."))):
    paneles.append(ts(titulo,
                      [tgt("rate(pve_guest_pressure_waiting_seconds_total{" + M +
                           ',recurso="' + recurso + '"}[5m]) * 100', "A", "Esperando"),
                       tgt("rate(pve_guest_pressure_stalled_seconds_total{" + M +
                           ',recurso="' + recurso + '"}[5m]) * 100', "B", "Todo parado")],
                      i * 8, 22, 8, 8, unit="percent", mx=100,
                      desc=extra + " " + PSI_DESC))

# ================================================================= dashboard
dash = {
    "annotations": {"list": []},
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 1,
    "links": [{"asDropdown": False, "icon": "external link", "includeVars": True,
               "keepTime": True, "tags": [], "title": "Vision general",
               "type": "link", "url": "/d/proxmox-overview"},
              {"asDropdown": False, "icon": "external link", "includeVars": True, "keepTime": True, "tags": [], "title": "Todas las maquinas", "type": "link", "url": "/d/proxmox-maquinas"}],
    "panels": paneles,
    "refresh": "1m",
    "schemaVersion": 39,
    "tags": ["proxmox"],
    "templating": {"list": [
        {"name": "nodo", "label": "Nodo", "type": "query", "datasource": DS,
         "query": "label_values(pve_node_info, instance)",
         "refresh": 1, "includeAll": False, "multi": False,
         "current": {}, "options": [], "sort": 1},
        {"name": "maquina", "label": "Maquina", "type": "query", "datasource": DS,
         "query": 'label_values(pve_guest_info{instance="$nodo"}, name)',
         "refresh": 1, "includeAll": False, "multi": False,
         "current": {}, "options": [], "sort": 1},
    ]},
    "time": {"from": "now-6h", "to": "now"},
    "timepicker": {},
    "timezone": "America/Guayaquil",
    "title": "Proxmox - una maquina",
    "uid": "proxmox-maquina",
    "version": 1,
    "weekStart": "",
}

destino = sys.argv[1]
with io.open(destino, "w", encoding="utf-8", newline="\n") as f:
    json.dump(dash, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("escrito:", destino, "-", len(paneles), "paneles")
