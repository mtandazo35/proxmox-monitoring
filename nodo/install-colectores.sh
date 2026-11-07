#!/bin/bash
# install-colectores.sh - instala los dos recolectores por maquina del nodo.
#
# Se corre EN EL NODO PROXMOX. Deja dos scripts en /usr/local/bin y dos timers
# de systemd. Lo que publican lo recoge el *textfile collector* del
# node-exporter que ya estaba instalado: no abre puertos, no crea usuarios,
# no usa credenciales y NO instala nada dentro de las maquinas.
#
#   pve-cgroup-collector.py  cada 1 min  - CPU, RAM real y PRESION (PSI) por
#                                          maquina, leyendo /sys/fs/cgroup.
#                                          Barato: solo lee ficheros.
#   pve-fsinfo-collector.py  cada 5 min  - disco REAL de dentro de cada VM,
#                                          preguntando a su qemu-guest-agent.
#                                          Cuesta una llamada por VM encendida.
#
# Requisito: prometheus-node-exporter ya instalado (lo hace install-nodo.sh).
#
# Rollback: ./desinstalar-colectores.sh
set -u

TS=$(date +%Y%m%d-%H%M%S)
BACKUPS=/root/backups
BIN=/usr/local/bin
TEXTFILE_DIR=/var/lib/prometheus/node-exporter
AQUI="$(cd "$(dirname "$0")" && pwd)"

INTERVALO_CGROUP=${INTERVALO_CGROUP:-1min}
INTERVALO_FSINFO=${INTERVALO_FSINFO:-5min}

echo "=============================================================="
echo " install-colectores.sh en $(hostname)  ($(date '+%Y-%m-%d %H:%M:%S %Z'))"
echo "=============================================================="

# ---------------------------------------------------------------- comprobaciones
command -v qm >/dev/null 2>&1 || { echo "ABORTO: no es un nodo Proxmox (no hay qm)." >&2; exit 1; }
dpkg -s prometheus-node-exporter >/dev/null 2>&1 || {
    echo "ABORTO: falta prometheus-node-exporter. Corre antes ./install-nodo.sh" >&2; exit 1; }
[ -d "$TEXTFILE_DIR" ] || {
    echo "ABORTO: no existe $TEXTFILE_DIR (directorio textfile de node-exporter)." >&2; exit 1; }

for f in pve-cgroup-collector.py pve-fsinfo-collector.py; do
    [ -f "$AQUI/$f" ] || { echo "ABORTO: no encuentro $AQUI/$f" >&2; exit 1; }
done

# cgroup v2: sin el, no hay *.pressure ni memory.current.
CGV=$(stat -fc %T /sys/fs/cgroup 2>/dev/null)
if [ "$CGV" != "cgroup2fs" ]; then
    echo "AVISO: /sys/fs/cgroup no es cgroup v2 (dice '$CGV')."
    echo "       El recolector de cgroup publicara poco o nada."
fi
[ -r /proc/pressure/cpu ] || echo "AVISO: el kernel no expone /proc/pressure: no habra PSI."
echo "--- node-exporter lee $TEXTFILE_DIR   (cgroup: $CGV)"

# ---------------------------------------------------------------- 0. respaldo
mkdir -p "$BACKUPS"
if ls /etc/systemd/system/pve-cgroup.* /etc/systemd/system/pve-fsinfo.* >/dev/null 2>&1; then
    echo
    echo "--- 0. Ya habia instalacion: respaldo -> $BACKUPS/pre-colectores-${TS}.tar.gz"
    tar czf "$BACKUPS/pre-colectores-${TS}.tar.gz" \
        /etc/systemd/system/pve-cgroup.service /etc/systemd/system/pve-cgroup.timer \
        /etc/systemd/system/pve-fsinfo.service /etc/systemd/system/pve-fsinfo.timer \
        "$BIN/pve-cgroup-collector.py" "$BIN/pve-fsinfo-collector.py" 2>/dev/null
    echo "    OK  $(du -h "$BACKUPS/pre-colectores-${TS}.tar.gz" 2>/dev/null | cut -f1)"
fi

# ---------------------------------------------------------------- 1. unidades
unidad() {
    local nombre="$1" script="$2" intervalo="$3" descripcion="$4" extra="$5"
    install -m 0755 -o root -g root "$AQUI/$script" "$BIN/$script"
    cat > "/etc/systemd/system/${nombre}.service" <<EOF
[Unit]
Description=$descripcion
After=pve-cluster.service

[Service]
Type=oneshot
ExecStart=$BIN/$script
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=180
ProtectSystem=strict
ReadWritePaths=$TEXTFILE_DIR
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes
$extra
EOF
    cat > "/etc/systemd/system/${nombre}.timer" <<EOF
[Unit]
Description=$descripcion (cada $intervalo)

[Timer]
OnBootSec=1min
OnUnitActiveSec=$intervalo
AccuracySec=15s
Unit=${nombre}.service

[Install]
WantedBy=timers.target
EOF
    echo "    OK  $nombre cada $intervalo"
}

echo
echo "--- 1. Instalando recolectores y timers"
# El de cgroup solo lee /sys y /etc/pve: se le puede atar mas corto.
unidad pve-cgroup pve-cgroup-collector.py "$INTERVALO_CGROUP" \
    "CPU, RAM real y presion (PSI) por maquina, desde los cgroups" \
    "PrivateNetwork=yes"
unidad pve-fsinfo pve-fsinfo-collector.py "$INTERVALO_FSINFO" \
    "Disco real dentro de cada VM (qemu-guest-agent)" \
    ""

systemctl daemon-reload
systemctl enable pve-cgroup.timer pve-fsinfo.timer >/dev/null 2>&1
systemctl restart pve-cgroup.timer pve-fsinfo.timer

# ---------------------------------------------------------------- 2. primera pasada
echo
echo "--- 2. Primera pasada de cada uno"
FALLOS=0
for u in pve-cgroup pve-fsinfo; do
    systemctl start "${u}.service"
    if [ $? -ne 0 ]; then
        echo "    FALLO: ${u}.service no termino bien:" >&2
        journalctl -u "${u}.service" -n 15 --no-pager >&2
        FALLOS=$((FALLOS + 1))
    else
        journalctl -u "${u}.service" -n 10 --no-pager | grep -E 'pve-(cgroup|fsinfo):' | tail -1 | sed 's/^/    /'
    fi
done

# ---------------------------------------------------------------- 3. verificar
echo
echo "--- 3. Que el dato llegue de verdad a node-exporter"
METRICAS=$(curl -fsS --max-time 15 http://127.0.0.1:9100/metrics 2>/dev/null)

comprobar() {
    local metrica="$1" minimo="$2"
    local n
    n=$(echo "$METRICAS" | grep -c "^${metrica}{")
    if [ "${n:-0}" -ge "$minimo" ]; then
        echo "    OK  $metrica -> $n series"
    else
        echo "    FALLO: $metrica -> $n series (se esperaban >= $minimo)" >&2
        FALLOS=$((FALLOS + 1))
    fi
}
comprobar pve_guest_pressure_waiting_seconds_total 3
comprobar pve_guest_cpu_seconds_total 1
comprobar pve_guest_memory_host_bytes 1
comprobar pve_guest_agent_up 1

ERR=$(echo "$METRICAS" | awk '/^node_textfile_scrape_error/{print $2}')
if [ "${ERR:-1}" = "0" ]; then
    echo "    OK  node_textfile_scrape_error = 0"
else
    echo "    FALLO: node_textfile_scrape_error = $ERR (algun .prom mal formado)" >&2
    FALLOS=$((FALLOS + 1))
fi

SIN=$(echo "$METRICAS" | awk '/^pve_guest_agent_up\{/ && $2 == 0' | wc -l)
if [ "${SIN:-0}" -gt 0 ]; then
    echo
    echo "    AVISO: $SIN VM encendida(s) sin que su agente conteste:"
    echo "$METRICAS" | awk '/^pve_guest_agent_up\{/ && $2 == 0' \
        | sed -n 's/.*name="\([^"]*\)".*/           - \1/p'
    echo "           Normal en RouterOS/CHR: no existe qemu-guest-agent para CHR."
    echo "           En Debian/Ubuntu, DENTRO de la VM:"
    echo "             apt install qemu-guest-agent && systemctl enable --now qemu-guest-agent"
fi

echo
echo "=============================================================="
if [ "$FALLOS" -eq 0 ]; then
    echo " LISTO. Los dos recolectores publican datos."
else
    echo " $FALLOS PROBLEMA(S). Deshacer: ./desinstalar-colectores.sh"
fi
echo "=============================================================="
exit "$FALLOS"
