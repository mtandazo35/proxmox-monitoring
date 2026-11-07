# -*- coding: utf-8 -*-
"""Genera dashboards/proxmox-maquinas.json: lo mismo que el de una maquina,
pero con TODAS a la vez, una serie por maquina y selector multiple."""
import json, io, sys

DS = {"type": "prometheus", "uid": "pmx-prometheus"}
VERDE, NARANJA, ROJO = "green", "orange", "red"
_id = [0]


def pid():
    _id[0] += 1
    return _id[0]


def tgt(expr, ref="A", legend=None):
    return {"datasource": DS, "editorMode": "code", "expr": expr, "refId": ref,
            "range": True, "legendFormat": legend if legend is not None else "{{name}}"}


def pasos(*pares):
    s = [{"color": pares[0][1], "value": None}]
    for v, c in pares[1:]:
        s.append({"color": c, "value": v})
    return {"mode": "absolute", "steps": s}


def gp(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def ts(titulo, targets, x, y, w, h, unit=None, desc=None, apiladas=False):
    d = {"color": {"mode": "palette-classic"},
         "custom": {"axisBorderShow": False, "axisCenteredZero": False, "axisLabel": "",
                    "axisPlacement": "auto", "barAlignment": 0, "drawStyle": "bars",
                    "fillOpacity": 47, "gradientMode": "none",
                    "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                    "insertNulls": False, "lineInterpolation": "linear", "lineWidth": 1,
                    "pointSize": 5, "scaleDistribution": {"type": "linear"},
                    "showPoints": "never", "spanNulls": False,
                    "stacking": {"group": "A", "mode": "normal" if apiladas else "none"},
                    "thresholdsStyle": {"mode": "off"}},
         "mappings": [], "thresholds": pasos((None, VERDE))}
    if unit:
        d["unit"] = unit
    p = {"id": pid(), "type": "timeseries", "title": titulo, "datasource": DS,
         "gridPos": gp(x, y, w, h),
         "fieldConfig": {"defaults": d, "overrides": []},
         # Leyenda en tabla ordenada por maximo: con muchas maquinas, lo util es
         # saber cuales son las de arriba sin tener que mirar la grafica.
         "options": {"legend": {"calcs": ["mean", "max"], "displayMode": "table",
                                "placement": "right", "showLegend": True,
                                "sortBy": "Max", "sortDesc": True, "width": 260},
                     "tooltip": {"mode": "multi", "sort": "desc", "maxHeight": 450}},
         "targets": targets}
    if desc:
        p["description"] = desc
    return p


def bargauge(titulo, targets, x, y, w, h, thr, unit="percent", mx=100, desc=None):
    p = {"id": pid(), "type": "bargauge", "title": titulo, "datasource": DS,
         "gridPos": gp(x, y, w, h),
         "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "mappings": [],
                                      "max": mx, "min": 0, "unit": unit, "decimals": 1,
                                      "thresholds": thr}, "overrides": []},
         "options": {"displayMode": "gradient", "orientation": "horizontal",
                     "showUnfilled": True, "valueMode": "color", "minVizHeight": 16,
                     "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
         "targets": targets}
    if desc:
        p["description"] = desc
    return p


def fila(titulo, y):
    return {"id": pid(), "type": "row", "title": titulo, "collapsed": False,
            "gridPos": gp(0, y, 24, 1), "panels": []}


# ---------------------------------------------------------------- selectores
# Metricas de los recolectores del nodo: ya traen `name`.
MM = 'nodo="$nodo",name=~"$maquina"'
# Metricas de pve-exporter: solo traen `id`, el nombre sale de pve_guest_info.
JOINM = (' * on(id) group_left(name) pve_guest_info{instance="$nodo",name=~"$maquina"}'
         # Solo las ENCENDIDAS. Sin esto, una maquina apagada pinta una linea
         # plana a cero: con 134 invitados eso es un panel ilegible lleno de
         # series que no dicen nada. Los paneles de cgroup ya salen filtrados
         # solos, porque una maquina apagada no tiene cgroup.
         ' and on(id) (pve_up{instance="$nodo"} == 1)')

UMBRAL_DISCO = pasos((None, VERDE), (80, NARANJA), (90, ROJO))
PSI_PIE = ("Una linea por maquina: % del tiempo que CADA UNA estuvo parada esperando el "
           "recurso. Es lo que dice a quien le toca esperar cuando el nodo va justo.")

paneles = []

# ================================================================= consumo
paneles.append(fila("Consumo de cada maquina", 0))

paneles.append(ts("CPU - nucleos en uso",
                  [tgt("rate(pve_guest_cpu_seconds_total{" + MM + "}[5m])")],
                  0, 1, 12, 9, unit="short",
                  desc="Nucleos consumidos de verdad por cada maquina, del cgroup. No es "
                       "porcentaje: 2 = dos nucleos enteros."))

paneles.append(ts("Memoria real en el host",
                  [tgt("pve_guest_memory_host_bytes{" + MM + "}")],
                  12, 1, 12, 9, unit="bytes",
                  desc="memory.current del cgroup de cada maquina: lo que el nodo paga de "
                       "verdad, overhead de QEMU incluido. No es el valor del balloon."))

paneles.append(ts("Red - entrada",
                  [tgt('rate(pve_network_receive_bytes_total{instance="$nodo"}[5m]) * 8' + JOINM)],
                  0, 10, 12, 8, unit="bps"))

paneles.append(ts("Red - salida",
                  [tgt('rate(pve_network_transmit_bytes_total{instance="$nodo"}[5m]) * 8' + JOINM)],
                  12, 10, 12, 8, unit="bps"))

paneles.append(ts("Disco - lectura",
                  [tgt('rate(pve_disk_read_bytes_total{instance="$nodo"}[5m])' + JOINM)],
                  0, 18, 12, 8, unit="Bps"))

paneles.append(ts("Disco - escritura",
                  [tgt('rate(pve_disk_written_bytes_total{instance="$nodo"}[5m])' + JOINM)],
                  12, 18, 12, 8, unit="Bps"))

# ================================================================= presion
paneles.append(fila("Presion (PSI) de cada maquina", 26))

for i, (recurso, titulo, extra) in enumerate((
        ("cpu", "Presion de CPU",
         "Sube en las maquinas que piden CPU y no la reciben. Con el nodo sobrecomprometido, "
         "aqui se ve cual lo esta sufriendo."),
        ("io", "Presion de E/S",
         "Sube en las maquinas que esperan al disco. Si varias comparten almacenamiento, esto "
         "dice a cual le toca esperar."),
        ("memory", "Presion de memoria",
         "Sube cuando el kernel pelea por recuperar memoria de esa maquina. Avisa antes del "
         "OOM."))):
    paneles.append(ts(titulo,
                      [tgt("rate(pve_guest_pressure_waiting_seconds_total{" + MM +
                           ',recurso="' + recurso + '"}[5m]) * 100')],
                      i * 8, 27, 8, 9, unit="percent",
                      desc=extra + " " + PSI_PIE))

# ================================================================= disco
paneles.append(fila("Disco usado de cada maquina", 36))

paneles.append(bargauge("Disco usado real (%)",
                        [tgt("(100 * sum by (name) (pve_guest_fs_used_bytes{" + MM + "}) "
                             "/ sum by (name) (pve_guest_fs_size_bytes{" + MM + "})) "
                             'or (100 * sum by (name) (pve_disk_usage_bytes{id=~"lxc/.*",'
                             'instance="$nodo"}' + JOINM + ") "
                             '/ sum by (name) (pve_disk_size_bytes{id=~"lxc/.*",'
                             'instance="$nodo"}' + JOINM + "))")],
                        0, 37, 24, 10, UMBRAL_DISCO,
                        desc="Uso real de DENTRO de cada maquina. En una VM lo contesta su "
                             "qemu-guest-agent; en un LXC lo sabe el nodo. Las que no aparecen "
                             "estan apagadas o no tienen agente (RouterOS/CHR nunca lo tendra)."))

# ================================================================= dashboard
dash = {
    "annotations": {"list": []},
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 1,
    "links": [
        {"asDropdown": False, "icon": "external link", "includeVars": True, "keepTime": True,
         "tags": [], "title": "Vision general", "type": "link", "url": "/d/proxmox-overview"},
        {"asDropdown": False, "icon": "external link", "includeVars": True, "keepTime": True,
         "tags": [], "title": "Una sola maquina", "type": "link", "url": "/d/proxmox-maquina"},
    ],
    "panels": paneles,
    "refresh": "1m",
    "schemaVersion": 39,
    "tags": ["proxmox"],
    "templating": {"list": [
        {"name": "nodo", "label": "Nodo", "type": "query", "datasource": DS,
         "query": "label_values(pve_node_info, instance)",
         "refresh": 1, "includeAll": False, "multi": False,
         "current": {}, "options": [], "sort": 1},
        # Multiple y con "Todas": con 134 invitados conviene poder quedarse con
        # un punado en vez de pintar 134 series por panel.
        {"name": "maquina", "label": "Maquinas", "type": "query", "datasource": DS,
         "query": 'label_values(pve_guest_info{instance="$nodo"}, name)',
         "refresh": 1, "includeAll": True, "allValue": ".*", "multi": True,
         "current": {}, "options": [], "sort": 1},
    ]},
    "time": {"from": "now-6h", "to": "now"},
    "timepicker": {},
    "timezone": "America/Guayaquil",
    "title": "Proxmox - todas las maquinas",
    "uid": "proxmox-maquinas",
    "version": 1,
    "weekStart": "",
}

destino = sys.argv[1]
with io.open(destino, "w", encoding="utf-8", newline="\n") as f:
    json.dump(dash, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("escrito:", destino, "-", len(paneles), "paneles")
