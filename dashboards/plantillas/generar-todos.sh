#!/bin/bash
# generar-todos.sh - regenera los tres dashboards desde sus plantillas.
#
# Los .json de dashboards/ son GENERADOS: no se editan a mano. Lo que se toca
# es la plantilla .py de al lado, y luego se corre esto.
#
#   cd dashboards/plantillas && ./generar-todos.sh
#
# Cada plantilla es independiente a proposito: se puede leer y ejecutar una
# sola sin arrastrar las otras dos.
#
# Despues de regenerar, SIEMPRE comprobar que los paneles dan datos (que el
# JSON sea valido no significa nada; una consulta mal escrita sale en blanco
# sin dar un error en ninguna parte):
#
#   pct push <VMID> ../../stack/verificar-dashboard.py /root/verificar-dashboard.py
#   pct exec <VMID> -- python3 /root/verificar-dashboard.py \
#       /var/lib/grafana/dashboards/proxmox-overview.json
set -u

AQUI="$(cd "$(dirname "$0")" && pwd)"
DESTINO="$(cd "$AQUI/.." && pwd)"
PY=${PY:-python3}

command -v "$PY" >/dev/null 2>&1 || { echo "ABORTO: no encuentro $PY" >&2; exit 1; }

FALLOS=0
generar() {
    local plantilla="$1" salida="$2"
    # -I: aisla el interprete, para que no cargue modulos del directorio actual.
    if "$PY" -I "$AQUI/$plantilla" "$DESTINO/$salida"; then
        "$PY" -c "import json,io,sys; d=json.load(io.open(sys.argv[1],encoding='utf-8')); \
print('    %-26s %2d paneles  uid=%s' % (sys.argv[1].split('/')[-1], len(d['panels']), d['uid']))" \
            "$DESTINO/$salida"
    else
        echo "    FALLO generando $salida" >&2
        FALLOS=$((FALLOS + 1))
    fi
}

echo "Regenerando en $DESTINO"
generar generar-overview.py  proxmox-overview.json
generar generar-maquina.py   proxmox-maquina.json
generar generar-maquinas.py  proxmox-maquinas.json

echo
if [ "$FALLOS" -eq 0 ]; then
    echo "Listo. Acuerdate de verificar que los paneles dan datos (ver cabecera)."
else
    echo "$FALLOS plantilla(s) fallaron."
fi
exit "$FALLOS"
