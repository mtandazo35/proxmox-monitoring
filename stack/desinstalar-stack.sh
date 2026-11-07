#!/bin/bash
# desinstalar-stack.sh - limpia el stack DENTRO del LXC.
#
# Si lo que quieres es borrarlo todo de golpe, es mas rapido desde el nodo:
#     pct stop <VMID> && pct destroy <VMID>
# Este script sirve para cuando quieres conservar el contenedor y reinstalar.
set -u

echo "=== desinstalar-stack.sh en $(hostname) ($(date '+%Y-%m-%d %H:%M:%S %Z')) ==="
echo
echo "Se van a purgar prometheus, prometheus-pve-exporter y grafana,"
echo "y a borrar /etc/prometheus, /var/lib/prometheus y /var/lib/grafana."
echo "Eso incluye TODAS las series guardadas y los paneles no provisionados."
echo
read -r -p "Continuar? [s/N] " OK
case "$OK" in
    s|S|si|SI|Si) ;;
    *) echo "Cancelado. Nada tocado."; exit 0 ;;
esac

export DEBIAN_FRONTEND=noninteractive

for s in grafana-server prometheus-pve-exporter prometheus; do
    systemctl disable --now "$s" >/dev/null 2>&1
done

apt-get purge -y prometheus prometheus-pve-exporter grafana >/dev/null 2>&1

rm -rf /etc/prometheus /var/lib/prometheus /var/lib/grafana /etc/grafana
rm -f /etc/apt/sources.list.d/grafana.list /etc/apt/keyrings/grafana.gpg
rm -rf /opt/pve-exporter /etc/systemd/system/prometheus-pve-exporter.service
systemctl daemon-reload

echo
echo "--- Comprobacion"
for p in 9090 9221 3000; do
    ss -lntu 2>/dev/null | grep -q ":${p}\b" \
        && echo "    AVISO: algo sigue escuchando en :${p}" \
        || echo "    OK  :${p} libre"
done
echo
echo "=== Stack limpio. El token sigue existiendo en el nodo:"
echo "    para quitarlo tambien, corre nodo/desinstalar-nodo.sh alla. ==="
