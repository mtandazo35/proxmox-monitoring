#!/bin/bash
# install-nodo.sh - prepara UN nodo Proxmox para ser monitoreado.
#
# Hace dos cosas y nada mas:
#   1. Instala prometheus-node-exporter (:9100) -> CPU, RAM, cada filesystem,
#      presion de E/S, ZFS ARC si hay ZFS, OOM kills.
#   2. Crea el usuario PVE "prometheus@pve" con rol PVEAuditor (SOLO LECTURA)
#      y un token de API, que es lo que usara pve-exporter para el detalle
#      por invitado (CPU, RAM, disco, encendida/apagada).
#
# NO instala Prometheus ni Grafana en el nodo: eso va en el LXC del stack
# (ver ../stack/crear-lxc.sh). El hypervisor queda con un solo paquete extra.
#
# Uso:   ./install-nodo.sh [IP_DEL_STACK]
#        IP_DEL_STACK solo se usa para abrir :9100 en UFW si UFW esta activo.
#
# Rollback: ./desinstalar-nodo.sh
#
# No se usa `set -e`: aborta a mitad y deja el nodo en un estado intermedio
# sin avisar. Cada paso comprueba su propio resultado.
set -u

IP_STACK="${1:-}"
TS=$(date +%Y%m%d-%H%M%S)
BACKUPS=/root/backups
LOG=/root/install-nodo-${TS}.log
TOKEN_ID=monitoring
PVE_USER=prometheus@pve

exec > >(tee -a "$LOG") 2>&1

echo "=============================================================="
echo " install-nodo.sh  en $(hostname)  ($(date '+%Y-%m-%d %H:%M:%S %Z'))"
echo "=============================================================="

# ---------------------------------------------------------------- comprobaciones
if ! command -v pveum >/dev/null 2>&1; then
    echo "ABORTO: esto no parece un nodo Proxmox (no hay pveum)." >&2
    exit 1
fi
echo "Proxmox: $(pveversion)"

# ---------------------------------------------------------------- 0. RESPALDO
# Regla: respaldo ANTES de cambiar nada.
mkdir -p "$BACKUPS"
echo
echo "--- 0. Respaldo previo -> $BACKUPS/pre-monitoreo-${TS}.tar.gz"
tar czf "$BACKUPS/pre-monitoreo-${TS}.tar.gz" \
    /etc/pve/user.cfg \
    /etc/pve/priv/token.cfg \
    /etc/default/prometheus-node-exporter \
    /etc/ufw 2>/dev/null
if [ -s "$BACKUPS/pre-monitoreo-${TS}.tar.gz" ]; then
    echo "    OK  $(du -h "$BACKUPS/pre-monitoreo-${TS}.tar.gz" | cut -f1)"
else
    echo "ABORTO: el respaldo salio vacio. No se toca nada." >&2
    exit 1
fi

# ---------------------------------------------------------------- 1. node-exporter
echo
echo "--- 1. node-exporter"
if dpkg -s prometheus-node-exporter >/dev/null 2>&1; then
    echo "    ya instalado: $(dpkg-query -W -f='${Version}' prometheus-node-exporter)"
else
    # Refrescar indices primero: si estan viejos, el install falla con un
    # "404 Not Found" que no dice nada. Solo lee, no actualiza ningun paquete.
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    # --no-install-recommends a proposito: sin el, apt arrastra media colección
    # de paquetes de monitoreo que no queremos en un hypervisor.
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        prometheus-node-exporter
    if [ $? -ne 0 ]; then
        echo "ABORTO: fallo la instalacion de prometheus-node-exporter." >&2
        exit 1
    fi
fi

systemctl enable --now prometheus-node-exporter >/dev/null 2>&1
sleep 2
if curl -fsS --max-time 5 http://127.0.0.1:9100/metrics >/dev/null 2>&1; then
    N=$(curl -fsS http://127.0.0.1:9100/metrics | grep -c '^[a-z]')
    echo "    OK  :9100 responde ($N lineas de metricas)"
else
    echo "    FALLO: :9100 no responde. Estado del servicio:" >&2
    systemctl status prometheus-node-exporter --no-pager -l | head -20 >&2
    exit 1
fi

# Aviso de la trampa vista en la flota DNS: dos unidades peleando por :9100.
OTRAS=$(systemctl list-units --all --no-legend 'node*exporter*' 2>/dev/null \
        | awk '{print $1}' | grep -v '^prometheus-node-exporter' | tr '\n' ' ')
if [ -n "$OTRAS" ]; then
    echo "    AVISO: hay otras unidades de node-exporter ($OTRAS)."
    echo "           En la flota DNS esto deja una en 'failed' peleando por :9100."
    echo "           Revisa y enmascara la que no uses."
fi

# ---------------------------------------------------------------- 2. usuario + token
echo
echo "--- 2. Usuario PVE de solo lectura y token de API"

if pveum user list --output-format json 2>/dev/null | grep -q "\"$PVE_USER\""; then
    echo "    el usuario $PVE_USER ya existe"
else
    pveum user add "$PVE_USER" --comment "Solo lectura para prometheus-pve-exporter"
    if [ $? -ne 0 ]; then
        echo "ABORTO: no se pudo crear $PVE_USER." >&2
        exit 1
    fi
    echo "    creado $PVE_USER (sin contrasena: solo se usa por token)"
fi

# PVEAuditor sobre / = ver todo, cambiar nada.
pveum acl modify / --users "$PVE_USER" --roles PVEAuditor
if [ $? -ne 0 ]; then
    echo "ABORTO: no se pudo dar PVEAuditor a $PVE_USER." >&2
    exit 1
fi
echo "    ACL: PVEAuditor sobre / (solo lectura)"

# El valor del token se muestra UNA sola vez. Si ya existe no hay forma de
# releerlo, asi que se borra y se crea de nuevo.
if pveum user token list "$PVE_USER" --output-format json 2>/dev/null | grep -q "\"$TOKEN_ID\""; then
    echo "    el token '$TOKEN_ID' ya existe; se recrea para poder mostrar su valor"
    pveum user token remove "$PVE_USER" "$TOKEN_ID" >/dev/null 2>&1
fi

# --privsep 0: el token hereda los permisos del usuario. Con --privsep 1
# (el defecto) el token no ve NADA y el exporter devuelve todo a cero.
TOKEN_JSON=$(pveum user token add "$PVE_USER" "$TOKEN_ID" --privsep 0 --output-format json 2>&1)
TOKEN_VALUE=$(echo "$TOKEN_JSON" | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

if [ -z "$TOKEN_VALUE" ]; then
    echo "ABORTO: no se pudo leer el valor del token. Salida de pveum:" >&2
    echo "$TOKEN_JSON" >&2
    exit 1
fi
echo "    token '$TOKEN_ID' creado"

# ---------------------------------------------------------------- 3. verificar el token
echo
echo "--- 3. Comprobar que el token de verdad lee la API"
IP_NODO=$(hostname -I | awk '{print $1}')
RESP=$(curl -fsS --max-time 10 -k \
    -H "Authorization: PVEAPIToken=${PVE_USER}!${TOKEN_ID}=${TOKEN_VALUE}" \
    "https://127.0.0.1:8006/api2/json/nodes" 2>&1)
if echo "$RESP" | grep -q '"node"'; then
    # Contar '"node":"<algo>"', NO '"node"' a secas: la respuesta trae tambien
    # "type":"node" por cada nodo y el conteo sale al doble.
    N_NODOS=$(echo "$RESP" | grep -o '"node":"[^"]*"' | sort -u | wc -l)
    echo "    OK  la API responde con el token (ve $N_NODOS nodo/s: $(echo "$RESP" | grep -o '"node":"[^"]*"' | sed 's/.*:"//; s/"//' | sort -u | tr '\n' ' '))"
else
    echo "    FALLO: el token no lee /nodes. Respuesta:" >&2
    echo "    $RESP" >&2
    exit 1
fi

# Comprobar que NO puede escribir (que PVEAuditor es de verdad solo lectura).
ESCRIBE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -k -X POST \
    -H "Authorization: PVEAPIToken=${PVE_USER}!${TOKEN_ID}=${TOKEN_VALUE}" \
    "https://127.0.0.1:8006/api2/json/nodes/$(hostname)/stopall" 2>/dev/null)
if [ "$ESCRIBE" = "403" ]; then
    echo "    OK  el token NO puede escribir (403 en un POST de prueba)"
else
    echo "    AVISO: el POST de prueba devolvio $ESCRIBE en vez de 403. Revisa la ACL."
fi

# ---------------------------------------------------------------- 4. firewall
echo
echo "--- 4. Firewall"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    if [ -n "$IP_STACK" ]; then
        ufw allow from "$IP_STACK" to any port 9100 proto tcp comment 'node-exporter -> stack monitoreo'
        echo "    UFW activo: abierto :9100 solo desde $IP_STACK"
    else
        echo "    AVISO: UFW esta ACTIVO y no se paso IP_DEL_STACK."
        echo "           El stack no podra scrapear :9100. Vuelve a correr:"
        echo "           ./install-nodo.sh <IP_DEL_STACK>"
    fi
else
    echo "    UFW no activo. :9100 queda accesible desde la red del nodo."
    echo "    AVISO: node-exporter no tiene autenticacion. Si este nodo tiene"
    echo "           IP publica, restringe :9100 al stack."
fi

# ---------------------------------------------------------------- resumen
echo
echo "=============================================================="
echo " LISTO. Pega estos tres datos en ../stack/install-stack.sh:"
echo "=============================================================="
echo "   IP del nodo   : $IP_NODO"
echo "   Usuario/token : ${PVE_USER}!${TOKEN_ID}"
echo "   Valor         : $TOKEN_VALUE"
echo
echo " El valor NO se puede volver a leer. Si lo pierdes, vuelve a correr"
echo " este script: borra el token y crea otro."
echo
echo " Respaldo previo : $BACKUPS/pre-monitoreo-${TS}.tar.gz"
echo " Registro        : $LOG"
echo " Deshacer        : ./desinstalar-nodo.sh"
echo "=============================================================="
