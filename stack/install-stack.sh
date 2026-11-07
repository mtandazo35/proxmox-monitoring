#!/bin/bash
# install-stack.sh - instala el stack de monitoreo DENTRO del LXC.
#
#   prometheus              :9090   guarda las series (15 dias)
#   prometheus-pve-exporter :9221   traduce la API de Proxmox a metricas
#   grafana                 :3000   los paneles
#
# Uso:  ./install-stack.sh <IP_NODO> <USUARIO!TOKEN> <VALOR_DEL_TOKEN>
# Ej.:  ./install-stack.sh 192.0.2.10 'prometheus@pve!monitoring' 'a1b2c3d4-...'
#
# Los tres datos los imprime ../nodo/install-nodo.sh al terminar.
#
# El contenedor se queda en UTC y es Grafana quien muestra hora de Ecuador:
# asi las series no se mueven si algun dia cambia el huso.
set -u

if [ $# -lt 3 ]; then
    echo "Uso: $0 <IP_NODO> <USUARIO!TOKEN> <VALOR_DEL_TOKEN>" >&2
    exit 1
fi

IP_NODO="$1"
USR_TOKEN="$2"
TOKEN_VALUE="$3"
PVE_USER="${USR_TOKEN%%!*}"
TOKEN_NAME="${USR_TOKEN##*!}"
TS=$(date +%Y%m%d-%H%M%S)
LOG=/root/install-stack-${TS}.log
RETENCION=${RETENCION:-15d}
TZ_VISTA=${TZ_VISTA:-America/Guayaquil}

exec > >(tee -a "$LOG") 2>&1

echo "=============================================================="
echo " install-stack.sh en $(hostname)  ($(date '+%Y-%m-%d %H:%M:%S %Z'))"
echo "=============================================================="
echo "   nodo Proxmox : $IP_NODO"
echo "   usuario      : $PVE_USER"
echo "   token        : $TOKEN_NAME"
echo "   retencion    : $RETENCION"
echo

if [ "$(id -u)" -ne 0 ]; then
    echo "ABORTO: hay que correrlo como root." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

# -------------------------------------------------------- 1. paquetes base
echo "--- 1. Paquetes base"
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl gnupg apt-transport-https
if [ $? -ne 0 ]; then
    echo "ABORTO: fallo apt-get install de los paquetes base." >&2
    exit 1
fi
echo "    OK"

# -------------------------------------------------------- 2. prometheus
echo
echo "--- 2. Prometheus"
# --no-install-recommends a proposito: sin el, apt arrastra
# prometheus-node-exporter y en la flota DNS eso deja una unidad en 'failed'
# peleando por el :9100.
apt-get install -y --no-install-recommends prometheus
if [ $? -ne 0 ]; then
    echo "ABORTO: fallo la instalacion de prometheus." >&2
    exit 1
fi
echo "    instalado $(dpkg-query -W -f='${Version}' prometheus)"

# -------------------------------------------------------- 3. pve-exporter
echo
echo "--- 3. prometheus-pve-exporter"
if apt-get install -y --no-install-recommends prometheus-pve-exporter 2>/dev/null; then
    echo "    instalado desde apt: $(dpkg-query -W -f='${Version}' prometheus-pve-exporter)"
    PVE_EXP_UNIT=prometheus-pve-exporter
else
    # Fallback: en Debian 13 el paquete puede no estar. Se instala en un venv
    # propio, sin tocar los paquetes de Python del sistema.
    echo "    no esta en apt; instalando en un venv en /opt/pve-exporter"
    apt-get install -y --no-install-recommends python3-venv python3-pip
    python3 -m venv /opt/pve-exporter
    /opt/pve-exporter/bin/pip -q install --upgrade pip
    /opt/pve-exporter/bin/pip -q install prometheus-pve-exporter
    if [ ! -x /opt/pve-exporter/bin/pve_exporter ]; then
        echo "ABORTO: no se pudo instalar pve-exporter ni por apt ni por pip." >&2
        exit 1
    fi
    id prometheus >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin prometheus
    cat > /etc/systemd/system/prometheus-pve-exporter.service <<'UNIT'
[Unit]
Description=Prometheus Proxmox VE Exporter
After=network-online.target
Wants=network-online.target

[Service]
User=prometheus
Group=prometheus
ExecStart=/opt/pve-exporter/bin/pve_exporter --config.file=/etc/prometheus/pve.yml --web.listen-address=127.0.0.1:9221
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/log

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    PVE_EXP_UNIT=prometheus-pve-exporter
    echo "    OK  venv + unidad systemd propia"
fi

# -------------------------------------------------------- 4. credencial
echo
echo "--- 4. Credencial del exporter"
mkdir -p /etc/prometheus
# El token va en claro porque es una credencial de uso, no de verificacion:
# se presenta a la API de Proxmox. Lo que si se controla son los permisos.
cat > /etc/prometheus/pve.yml <<EOF
default:
    user: ${PVE_USER}
    token_name: ${TOKEN_NAME}
    token_value: ${TOKEN_VALUE}
    verify_ssl: false
EOF
chown root:prometheus /etc/prometheus/pve.yml 2>/dev/null || chown root:root /etc/prometheus/pve.yml
chmod 0640 /etc/prometheus/pve.yml
echo "    /etc/prometheus/pve.yml  $(stat -c '%U:%G %a' /etc/prometheus/pve.yml)"
echo "    (verify_ssl false: el certificado de Proxmox es autofirmado)"

# -------------------------------------------------------- 5. prometheus.yml
echo
echo "--- 5. Configuracion de Prometheus"
[ -f /etc/prometheus/prometheus.yml ] && cp -a /etc/prometheus/prometheus.yml "/etc/prometheus/prometheus.yml.orig-${TS}"

cat > /etc/prometheus/prometheus.yml <<EOF
# Generado por install-stack.sh el ${TS}
global:
  scrape_interval: 30s
  evaluation_interval: 30s

scrape_configs:

  # El propio Prometheus, para saber si se esta quedando sin sitio.
  - job_name: prometheus
    static_configs:
      - targets: ['127.0.0.1:9090']

  # Sistema operativo del hypervisor: CPU, RAM, cada filesystem,
  # presion de E/S, ZFS ARC si hay ZFS, OOM kills.
  - job_name: node
    static_configs:
      - targets: ['${IP_NODO}:9100']
        labels:
          nodo: '${IP_NODO}'

  # Detalle por invitado desde la API de Proxmox.
  # El truco del relabel: el 'target' viaja como parametro ?target= y la
  # peticion se manda siempre al exporter local. Sin esto, Prometheus
  # intentaria scrapear el :9221 del propio Proxmox, que no existe.
  - job_name: pve
    metrics_path: /pve
    params:
      module: [default]
      cluster: ['1']
      node: ['1']
    static_configs:
      - targets: ['${IP_NODO}']
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: 127.0.0.1:9221
EOF

promtool check config /etc/prometheus/prometheus.yml
if [ $? -ne 0 ]; then
    echo "ABORTO: prometheus.yml no valida. Se restaura el original." >&2
    [ -f "/etc/prometheus/prometheus.yml.orig-${TS}" ] && \
        cp -a "/etc/prometheus/prometheus.yml.orig-${TS}" /etc/prometheus/prometheus.yml
    exit 1
fi
echo "    OK  promtool valida la configuracion"

# Retencion: 15 dias. En la flota DNS 30 dias llenaron el disco.
if [ -f /etc/default/prometheus ]; then
    cp -a /etc/default/prometheus "/etc/default/prometheus.orig-${TS}"
    sed -i "s|^ARGS=.*|ARGS=\"--storage.tsdb.retention.time=${RETENCION}\"|" /etc/default/prometheus
    grep -q '^ARGS=' /etc/default/prometheus || echo "ARGS=\"--storage.tsdb.retention.time=${RETENCION}\"" >> /etc/default/prometheus
    echo "    retencion: $(grep '^ARGS=' /etc/default/prometheus)"
fi

# -------------------------------------------------------- 6. grafana
echo
echo "--- 6. Grafana"
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
if [ ! -s /etc/apt/keyrings/grafana.gpg ]; then
    echo "ABORTO: no se pudo bajar la clave de apt.grafana.com." >&2
    exit 1
fi
chmod 0644 /etc/apt/keyrings/grafana.gpg
# Un .list, no un .bak: apt solo lee *.list y *.sources.
cat > /etc/apt/sources.list.d/grafana.list <<'EOF'
deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main
EOF
apt-get update -qq
apt-get install -y --no-install-recommends grafana
if [ $? -ne 0 ]; then
    echo "ABORTO: fallo la instalacion de grafana." >&2
    exit 1
fi
echo "    instalado $(dpkg-query -W -f='${Version}' grafana)"

# -------------------------------------------------------- 7. provisionar
echo
echo "--- 7. Datasource y dashboard"
install -d -m 0755 /etc/grafana/provisioning/datasources \
                   /etc/grafana/provisioning/dashboards \
                   /var/lib/grafana/dashboards

cat > /etc/grafana/provisioning/datasources/prometheus.yml <<'EOF'
apiVersion: 1
datasources:
  - name: Prometheus
    uid: pmx-prometheus
    type: prometheus
    access: proxy
    url: http://127.0.0.1:9090
    isDefault: true
    jsonData:
      timeInterval: 30s
EOF

cat > /etc/grafana/provisioning/dashboards/proxmox.yml <<'EOF'
apiVersion: 1
providers:
  - name: proxmox
    orgId: 1
    folder: Proxmox
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
EOF

if [ -f /root/proxmox-overview.json ]; then
    cp /root/proxmox-overview.json /var/lib/grafana/dashboards/proxmox-overview.json
    echo "    dashboard copiado"
else
    echo "    AVISO: no encontre /root/proxmox-overview.json."
    echo "           Empujalo con: pct push <VMID> ../dashboards/proxmox-overview.json /root/proxmox-overview.json"
fi
chown -R grafana:grafana /var/lib/grafana/dashboards

# El contenedor en UTC, la vista en hora de Ecuador.
if ! grep -q '^default_timezone' /etc/grafana/grafana.ini; then
    sed -i "s|^;default_timezone =.*|default_timezone = ${TZ_VISTA}|" /etc/grafana/grafana.ini
fi
echo "    zona horaria de la vista: ${TZ_VISTA} (el contenedor sigue en UTC)"

# -------------------------------------------------------- 8. arrancar
echo
echo "--- 8. Arrancando servicios"
systemctl daemon-reload
# OJO: 'enable --now' NO reinicia una unidad que ya esta activa, y el paquete
# de Debian arranca Prometheus al instalarse. Con 'enable --now' se quedaba
# corriendo con el prometheus.yml por omision del paquete (targets
# localhost:9100 y localhost:9090, sin job 'pve') y el config escrito aqui
# no se leia nunca. Hay que reiniciar de verdad.
for s in prometheus "$PVE_EXP_UNIT" grafana-server; do
    systemctl enable "$s" >/dev/null 2>&1
    systemctl restart "$s" >/dev/null 2>&1
done
sleep 8

# -------------------------------------------------------- 9. contrasena de grafana
echo
echo "--- 9. Contrasena de admin de Grafana"
# Con `tr ... </dev/urandom | head -c 20`, head cierra la tuberia y tr grita
# "write error: Broken pipe". cut lee toda la entrada y no da el aviso.
GF_PASS=$(head -c 256 /dev/urandom | tr -dc 'A-Za-z0-9' | cut -c1-20)
if grafana cli --homepath /usr/share/grafana admin reset-admin-password "$GF_PASS" >/dev/null 2>&1 \
   || grafana-cli --homepath /usr/share/grafana admin reset-admin-password "$GF_PASS" >/dev/null 2>&1; then
    echo "    contrasena generada al azar (20 caracteres), se imprime al final"
else
    echo "    AVISO: no se pudo fijar la contrasena. Entra con admin/admin y cambiala."
    GF_PASS="admin/admin (CAMBIALA)"
fi

# -------------------------------------------------------- 10. verificacion
echo
echo "=============================================================="
echo " VERIFICACION"
echo "=============================================================="
FALLOS=0

for s in prometheus "$PVE_EXP_UNIT" grafana-server; do
    E=$(systemctl is-active "$s" 2>/dev/null)
    printf "   %-28s %s\n" "$s" "$E"
    [ "$E" = "active" ] || FALLOS=$((FALLOS + 1))
done

echo
echo "   Puertos escuchando:"
ss -lntp 2>/dev/null | grep -E ':(9090|9221|3000)\b' | awk '{printf "     %s %s\n", $4, $6}'

echo
echo "   node-exporter del nodo ${IP_NODO}:9100 ..."
if curl -fsS --max-time 8 "http://${IP_NODO}:9100/metrics" >/dev/null 2>&1; then
    echo "     OK  alcanzable"
else
    echo "     FALLO: no se alcanza. Corriste ../nodo/install-nodo.sh en el nodo?"
    echo "            Si el nodo tiene UFW activo: ./install-nodo.sh $(hostname -I | awk '{print $1}')"
    FALLOS=$((FALLOS + 1))
fi

echo
echo "   pve-exporter (la prueba de fuego: lee la API?) ..."
PVE_OUT=$(curl -fsS --max-time 20 "http://127.0.0.1:9221/pve?target=${IP_NODO}&module=default&cluster=1&node=1" 2>&1)
N_GUESTS=$(echo "$PVE_OUT" | grep -c '^pve_guest_info')
if [ "$N_GUESTS" -gt 0 ]; then
    echo "     OK  ve $N_GUESTS invitados y $(echo "$PVE_OUT" | grep -c '^pve_') metricas pve_*"
else
    echo "     FALLO: el exporter no devuelve invitados." >&2
    echo "$PVE_OUT" | head -15 >&2
    echo "     Causa tipica: el token se creo con --privsep 1 y no ve nada." >&2
    FALLOS=$((FALLOS + 1))
fi

echo
echo "   Targets de Prometheus (hasta 40 s para el primer scrape):"
# Esto NO es decorativo: si un target esta 'down' el stack no sirve, asi que
# cuenta como fallo. Antes solo se imprimia y el script decia "todo verificado"
# con el job 'node' caido.
TARGETS_OK=0
for intento in 1 2 3 4 5 6 7 8; do
    SALIDA=$(curl -fsS --max-time 10 'http://127.0.0.1:9090/api/v1/targets' 2>/dev/null \
        | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)["data"]["activeTargets"]
except Exception as e:
    print("ERROR no se pudo leer la API de targets: %s" % e); sys.exit(0)
if not d:
    print("ERROR Prometheus no tiene ningun target activo"); sys.exit(0)
malos = 0
for t in sorted(d, key=lambda x: x["labels"].get("job", "")):
    job = t["labels"].get("job", "?")
    print("   %-12s %-28s %s" % (job, t["scrapeUrl"], t["health"]))
    if t["health"] != "up":
        malos += 1
        if t["lastError"]:
            print("                error: %s" % t["lastError"])
jobs = {t["labels"].get("job") for t in d}
for esperado in ("prometheus", "node", "pve"):
    if esperado not in jobs:
        print("   ERROR falta el job '"'"'%s'"'"': Prometheus no leyo el prometheus.yml de este script" % esperado)
        malos += 1
print("RESULTADO %d" % malos)
' 2>/dev/null)
    echo "$SALIDA" | grep -v '^RESULTADO'
    MALOS=$(echo "$SALIDA" | sed -n 's/^RESULTADO //p')
    if [ "${MALOS:-9}" = "0" ]; then
        TARGETS_OK=1
        break
    fi
    [ "$intento" -lt 8 ] && { echo "   (reintentando en 5 s...)"; sleep 5; }
done
if [ "$TARGETS_OK" -ne 1 ]; then
    FALLOS=$((FALLOS + 1))
fi

IP_LXC=$(hostname -I | awk '{print $1}')
echo
echo "=============================================================="
if [ "$FALLOS" -eq 0 ]; then
    echo " LISTO - todo verificado"
else
    echo " TERMINADO CON $FALLOS FALLO(S) - mira arriba"
fi
echo "=============================================================="
echo "   Grafana     : http://${IP_LXC}:3000   admin / ${GF_PASS}"
echo "   Prometheus  : http://${IP_LXC}:9090"
echo "   Registro    : $LOG"
echo "   Deshacer    : ./desinstalar-stack.sh"
echo
echo " Guarda la contrasena en tu gestor: no se vuelve a mostrar."
echo "=============================================================="
