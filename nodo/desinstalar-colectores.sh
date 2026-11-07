#!/bin/bash
# desinstalar-colectores.sh - deshace install-colectores.sh.
#
# Quita los dos timers, sus unidades, los scripts y los ficheros de metricas.
# No toca node-exporter, ni Proxmox, ni las maquinas: dentro de los invitados
# no se instalo nada, asi que no hay nada que limpiar ahi.
set -u

BIN=/usr/local/bin
TEXTFILE_DIR=/var/lib/prometheus/node-exporter

echo "=============================================================="
echo " desinstalar-colectores.sh en $(hostname)"
echo "=============================================================="
echo " Se va a quitar:"
echo "   - timers y unidades pve-cgroup y pve-fsinfo"
echo "   - $BIN/pve-cgroup-collector.py"
echo "   - $BIN/pve-fsinfo-collector.py"
echo "   - $TEXTFILE_DIR/pve_cgroup.prom y pve_fsinfo.prom"
echo
read -r -p " Continuar? (escribe SI): " R
[ "$R" = "SI" ] || { echo "Cancelado."; exit 0; }

for u in pve-cgroup pve-fsinfo; do
    systemctl disable --now "${u}.timer" 2>/dev/null
    systemctl stop "${u}.service" 2>/dev/null
    rm -f "/etc/systemd/system/${u}.timer" "/etc/systemd/system/${u}.service"
done
systemctl daemon-reload

rm -f "$BIN/pve-cgroup-collector.py" "$BIN/pve-fsinfo-collector.py"
rm -f "$TEXTFILE_DIR"/pve_cgroup.prom "$TEXTFILE_DIR"/pve_cgroup.prom.tmp
rm -f "$TEXTFILE_DIR"/pve_fsinfo.prom "$TEXTFILE_DIR"/pve_fsinfo.prom.tmp

echo
echo "--- Comprobacion"
if curl -fsS --max-time 10 http://127.0.0.1:9100/metrics 2>/dev/null | grep -q '^pve_guest_'; then
    echo "   AVISO: node-exporter todavia sirve metricas pve_guest_*."
    echo "          Puede ser el scrape anterior; vuelve a mirar en un minuto."
else
    echo "   OK  node-exporter ya no sirve pve_guest_*"
fi
echo "   Las maquinas no se tocaron: nada que deshacer dentro de ellas."
echo "=============================================================="
