#!/bin/bash
# actualizar-dashboards.sh - lleva los dashboards y las alertas a Grafana.
#
# Se corre EN LA MAQUINA DEL STACK. Copia los .json y el alertas.yaml del repo
# a donde Grafana los lee, y nada mas.
#
# POR QUE EXISTE
# --------------
# Hasta ahora, la unica forma de llevar un panel nuevo era volver a ejecutar
# install-stack.sh, y eso REGENERA LA CONTRASENA DE ADMIN de Grafana. O sea
# que actualizar un grafico te echaba de tu propio panel. Este script no toca
# ni paquetes, ni configuracion, ni contrasenas.
#
# Uso:
#   ./actualizar-dashboards.sh [carpeta-del-repo]
#
# Por el one-liner, que ya trae el repo:
#   curl -fsSL .../instalar.sh | bash -s -- dashboards
set -u

AQUI="$(cd "$(dirname "$0")" && pwd)"
REPO=${1:-$(cd "$AQUI/.." && pwd)}
DIR_DASH=/var/lib/grafana/dashboards
DIR_ALERTAS=/etc/grafana/provisioning/alerting

echo "=============================================================="
echo " actualizar-dashboards.sh en $(hostname)"
echo "=============================================================="

if [ ! -d "$DIR_DASH" ]; then
    echo "ABORTO: no existe $DIR_DASH. Aqui no hay un stack instalado." >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "ABORTO: hace falta python3 para validar los JSON." >&2
    exit 1
fi

FALLOS=0

# ---------------------------------------------------------------- dashboards
echo
echo "--- Dashboards"
N=0
for d in "$REPO"/dashboards/proxmox-*.json; do
    [ -f "$d" ] || continue
    # Validar ANTES de copiar: un JSON roto deja a Grafana sin ese panel y el
    # unico rastro es una linea en su log que nadie mira.
    TITULO=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print(d['title'])
" "$d" 2>/dev/null)
    if [ -z "$TITULO" ]; then
        echo "    FALLO: $(basename "$d") no es un JSON valido o no tiene titulo. No se copia." >&2
        FALLOS=$((FALLOS + 1))
        continue
    fi
    cp "$d" "$DIR_DASH/$(basename "$d")"
    printf '    OK  %-26s %s\n' "$(basename "$d")" "$TITULO"
    N=$((N + 1))
done
if [ "$N" -eq 0 ]; then
    echo "    FALLO: no encontre ningun $REPO/dashboards/proxmox-*.json" >&2
    FALLOS=$((FALLOS + 1))
fi
chown -R grafana:grafana "$DIR_DASH" 2>/dev/null
chmod 0644 "$DIR_DASH"/*.json 2>/dev/null

# ---------------------------------------------------------------- alertas
echo
echo "--- Alertas"
if [ -f "$REPO/stack/alertas.yaml" ]; then
    install -d -m 0755 "$DIR_ALERTAS"
    if python3 -c "
import sys
try:
    import yaml
except ImportError:
    sys.exit(2)
yaml.safe_load(open(sys.argv[1], encoding='utf-8'))
" "$REPO/stack/alertas.yaml" 2>/dev/null; then
        echo "    YAML valido"
    elif [ $? -eq 2 ]; then
        echo "    (sin PyYAML aqui: no se puede validar antes de copiar)"
    else
        echo "    FALLO: alertas.yaml no es YAML valido. No se copia." >&2
        FALLOS=$((FALLOS + 1))
    fi
    if [ "$FALLOS" -eq 0 ] || [ ! -f "$DIR_ALERTAS/alertas.yaml" ]; then
        cp "$REPO/stack/alertas.yaml" "$DIR_ALERTAS/alertas.yaml"
        chown root:grafana "$DIR_ALERTAS/alertas.yaml" 2>/dev/null
        chmod 0640 "$DIR_ALERTAS/alertas.yaml" 2>/dev/null
        REGLAS=$(grep -c '^      - uid:' "$REPO/stack/alertas.yaml")
        echo "    OK  $REGLAS reglas copiadas"
    fi
else
    echo "    AVISO: no hay $REPO/stack/alertas.yaml; las alertas se quedan como esten."
fi

# ---------------------------------------------------------------- recargar
# Los dashboards los recoge Grafana solo (el provisionador de ficheros los
# relee cada pocos segundos). Las ALERTAS no: esas se leen al arrancar.
echo
echo "--- Recargando Grafana"
systemctl restart grafana-server
COD=$(curl -s --retry 20 --retry-delay 2 --retry-connrefused \
      -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/api/health 2>/dev/null)
if [ "$COD" = "200" ]; then
    echo "    OK  Grafana responde ($COD)"
else
    echo "    FALLO: Grafana no responde tras reiniciar (HTTP $COD)" >&2
    FALLOS=$((FALLOS + 1))
fi

# ---------------------------------------------------------------- comprobar
echo
echo "--- Lo que ha quedado cargado"
python3 - <<'PY' || FALLOS=$((FALLOS + 1))
import glob, json, os, sqlite3, sys

print("   Dashboards en disco:")
for f in sorted(glob.glob("/var/lib/grafana/dashboards/*.json")):
    try:
        t = json.load(open(f, encoding="utf-8"))["title"]
    except Exception as e:                                    # noqa: BLE001
        print("     %-26s ILEGIBLE: %s" % (os.path.basename(f), e))
        sys.exit(1)
    print("     %-26s %s" % (os.path.basename(f), t))

# La base de Grafana dice que reglas ha ingerido de verdad. Es la unica
# comprobacion honesta: que el fichero este no significa que Grafana lo acepte.
db = "/var/lib/grafana/grafana.db"
if not os.path.exists(db):
    print("\n   (no hay grafana.db; no puedo comprobar las alertas)")
    sys.exit(0)
try:
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    filas = con.execute("select title from alert_rule order by title").fetchall()
    con.close()
except Exception as e:                                        # noqa: BLE001
    print("\n   (no pude leer grafana.db: %s)" % e)
    sys.exit(0)
print("\n   Reglas de alerta que Grafana ha cargado: %d" % len(filas))
for (t,) in filas:
    print("     - %s" % t)
if not filas:
    print("     NINGUNA. Mira: journalctl -u grafana-server | grep -i provision")
    sys.exit(1)
PY

echo
echo "=============================================================="
if [ "$FALLOS" -eq 0 ]; then
    echo " LISTO. Ni paquetes ni contrasenas tocados."
else
    echo " $FALLOS PROBLEMA(S) - mira arriba"
fi
echo "=============================================================="
exit "$FALLOS"
