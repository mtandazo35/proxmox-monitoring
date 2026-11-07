#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verificar-dashboard.py - comprueba que CADA consulta de CADA panel devuelve datos.

Se corre DENTRO del contenedor. Solo lee: no cambia nada.

    pct push <VMID> verificar-dashboard.py /root/verificar-dashboard.py
    pct exec <VMID> -- python3 /root/verificar-dashboard.py [ruta-del-json]

Que un dashboard cargue no significa nada: un panel con una consulta mal escrita
sale vacio y no da error en ninguna parte. Este script saca las expresiones del
JSON provisionado, sustituye las variables y las lanza contra Prometheus.

Sale con codigo != 0 si algun panel se quedaria vacio.
"""
import glob
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

PROM = "http://127.0.0.1:9090/api/v1"
JSON = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/grafana/dashboards/proxmox-overview.json"
BUILD = "/usr/share/grafana/public/build"

# Paneles donde un resultado vacio es legitimo: vacio es la buena noticia.
VACIO_ACEPTABLE = {
    "Hay que encenderlas a mano",
}


def consulta(expr):
    url = PROM + "/query?" + urllib.parse.urlencode({"query": expr})
    try:
        r = json.load(urllib.request.urlopen(url, timeout=20))
    except Exception as e:                                    # noqa: BLE001
        return None, str(e)
    if r.get("status") != "success":
        return None, r.get("error", "respuesta no exitosa")
    return r["data"]["result"], None


def valor_variable(nombre, definicion, ya_resueltas):
    """Resuelve una variable de tipo query (label_values(metrica, etiqueta)).

    La definicion puede usar variables anteriores ($nodo dentro del selector de
    $maquina), asi que primero se sustituyen las ya resueltas.

    Se puede forzar un valor desde el entorno con VAR_<NOMBRE>, util cuando la
    primera serie que devuelve Prometheus no es la que interesa (p. ej. una
    maquina apagada, que no tiene ni cgroup ni agente):

        VAR_MAQUINA=<maquina-encendida> python3 verificar-dashboard.py <json>
    """
    forzado = os.environ.get("VAR_" + nombre.upper())
    if forzado:
        return forzado
    definicion = definicion.strip()
    for k, v in ya_resueltas.items():
        definicion = definicion.replace("$" + k, v)
    m = re.match(r"label_values\(\s*(.+)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)$", definicion)
    if not m:
        return None
    metrica, etiqueta = m.group(1).strip(), m.group(2).strip()
    res, err = consulta(metrica)
    if err or not res:
        return None
    for s in res:
        if etiqueta in s["metric"]:
            return s["metric"][etiqueta]
    return None


def comprobar_unidades(texto_json):
    """Una unidad inventada no da error: Grafana imprime su nombre junto al numero.

    Paso por aqui de verdad: puse "dtdurationhms", que no existe, y la columna
    UPTIME mostraba "634483 dtdurationhms" en vez de "7d 08:14:43".
    """
    unidades = sorted(set(re.findall(r'"unit":\s*"([^"]+)"', texto_json)))
    if not unidades:
        return 0, []
    ficheros = glob.glob(BUILD + "/*.js")
    if not ficheros:
        print("   AVISO: no se encuentra el build de Grafana en %s; no se comprueban." % BUILD)
        return 0, unidades
    malas = []
    for u in unidades:
        r = subprocess.call(["grep", "-qF", 'id:"%s"' % u] + ficheros)
        if r != 0:
            malas.append(u)
    for u in unidades:
        print("   %-10s %s" % (u, "NO EXISTE en Grafana" if u in malas else "ok"))
    return len(malas), unidades


def paneles_de(d):
    for p in d.get("panels", []):
        yield p
        for h in p.get("panels", []):      # filas plegadas
            yield h


def main():
    with open(JSON, encoding="utf-8") as f:
        crudo = f.read()
    dash = json.loads(crudo)

    print("=" * 70)
    print(" verificar-dashboard.py  ->  %s" % JSON)
    print(" dashboard: %s (uid %s)" % (dash.get("title"), dash.get("uid")))
    print("=" * 70)

    print("\n--- Unidades")
    unidades_malas, _ = comprobar_unidades(crudo)

    # --- variables
    variables = {}
    for v in dash.get("templating", {}).get("list", []):
        if v.get("type") != "query":
            continue
        val = valor_variable(v["name"], v.get("query", ""), variables)
        if val is None:
            print("\nFALLO: la variable $%s no resuelve a ningun valor." % v["name"])
            return 1
        variables[v["name"]] = val
    print("\nVariables: %s" % (", ".join("$%s = %s" % kv for kv in variables.items()) or "(ninguna)"))

    def sustituir(expr):
        for k, val in variables.items():
            expr = expr.replace("$" + k, val)
        return expr

    fallos = 0
    total = 0
    for p in paneles_de(dash):
        if p.get("type") in ("row", "text"):
            continue
        titulo = p.get("title") or "(sin titulo)"
        print("\n--- [%s] %s" % (p.get("type"), titulo))
        for t in p.get("targets", []):
            expr = t.get("expr")
            if not expr:
                continue
            total += 1
            res, err = consulta(sustituir(expr))
            ref = t.get("refId", "?")
            if err:
                print("    %s ERROR  %s" % (ref, err))
                print("       expr: %s" % expr)
                fallos += 1
                continue
            if not res:
                marca = "(vacio, aceptado)" if titulo in VACIO_ACEPTABLE else "SIN DATOS  <- panel vacio"
                print("    %s %s" % (ref, marca))
                if titulo not in VACIO_ACEPTABLE:
                    print("       expr: %s" % expr)
                    fallos += 1
                continue
            muestra = []
            for s in res[:3]:
                etiqueta = (s["metric"].get("name") or s["metric"].get("id")
                            or s["metric"].get("device") or s["metric"].get("mountpoint")
                            or s["metric"].get("almacen") or "")
                try:
                    v = round(float(s["value"][1]), 2)
                except (TypeError, ValueError):
                    v = s["value"][1]
                muestra.append(("%s=%s" % (etiqueta, v)).lstrip("="))
            print("    %s %2d serie(s)  %s" % (ref, len(res), ", ".join(muestra)[:90]))

    print("\n" + "=" * 70)
    if fallos:
        print(" %d de %d consultas NO devuelven datos" % (fallos, total))
    else:
        print(" Las %d consultas del dashboard devuelven datos" % total)
    if unidades_malas:
        print(" %d unidad(es) no existen en Grafana: saldria el nombre en crudo" % unidades_malas)
    print("=" * 70)
    return 1 if (fallos or unidades_malas) else 0


if __name__ == "__main__":
    sys.exit(main())
