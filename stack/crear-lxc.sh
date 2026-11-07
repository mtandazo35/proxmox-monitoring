#!/bin/bash
# crear-lxc.sh - crea el contenedor donde vive el stack de monitoreo.
#
# Se corre EN EL NODO PROXMOX. Crea un LXC Debian 13 sin privilegios y lo deja
# arrancado con red, listo para install-stack.sh.
#
# Por que LXC y no VM: un LXC no reserva la RAM, es un limite de cgroup. En un
# host con poca memoria libre eso importa: lo que el stack
# no use se queda en el host. Una VM sin balloon se la queda fija.
#
# Variables (se pueden sobrescribir al invocar):
#   IP y GW son OBLIGATORIAS: no hay valor por omision a proposito. Una IP
#   por defecto acaba creando el contenedor en la red de otro.
#
#   IP=192.0.2.20/24 GW=192.0.2.1 VLAN=78 ./crear-lxc.sh
#   VMID=112 NOMBRE=pmx-monitor IP=192.0.2.20/24 GW=192.0.2.1 ./crear-lxc.sh
#
# Rollback: pct stop <VMID> && pct destroy <VMID>
set -u

VMID=${VMID:-111}
# OJO: la variable NO se puede llamar HOSTNAME. Bash la rellena solo con el
# nombre del host, asi que ${HOSTNAME:-...} nunca toma el valor por omision y
# el contenedor acabaria llamandose como el nodo. Con sh esta vacia y funciona:
# el fallo depende de quien ejecute el script.
NOMBRE=${NOMBRE:-pmx-monitor}
IP=${IP:-}
GW=${GW:-}
VLAN=${VLAN:-}
BRIDGE=${BRIDGE:-vmbr0}
CORES=${CORES:-2}
RAM=${RAM:-1536}
SWAP=${SWAP:-512}
DISCO=${DISCO:-8}
ALM_DISCO=${ALM_DISCO:-local-lvm}
ALM_TPL=${ALM_TPL:-local}
DNS=${DNS:-$GW}

echo "=============================================================="
echo " crear-lxc.sh en $(hostname)  ($(date '+%Y-%m-%d %H:%M:%S %Z'))"
echo "=============================================================="
echo "   VMID $VMID  hostname $NOMBRE"
echo "   red  $IP  gw $GW  vlan $VLAN  bridge $BRIDGE"
echo "   $CORES nucleos, $RAM MB RAM, $SWAP MB swap, $DISCO GB en $ALM_DISCO"
echo

# ------------------------------------------------- 1. comprobaciones previas
if [ -z "$IP" ] || [ -z "$GW" ]; then
    echo "ABORTO: faltan IP y/o GW. No hay valor por omision a proposito:" >&2
    echo "        una IP por defecto acaba creando el contenedor en la red de otro." >&2
    echo "        Ej.: IP=192.0.2.20/24 GW=192.0.2.1 VLAN=78 ./crear-lxc.sh" >&2
    exit 1
fi

if ! command -v pct >/dev/null 2>&1; then
    echo "ABORTO: esto no es un nodo Proxmox (no hay pct)." >&2
    exit 1
fi

if pct status "$VMID" >/dev/null 2>&1 || qm status "$VMID" >/dev/null 2>&1; then
    echo "ABORTO: el VMID $VMID ya esta en uso. Pasa otro: VMID=112 ./crear-lxc.sh" >&2
    exit 1
fi

# La IP libre se comprueba de verdad, no se asume.
IP_SOLA=${IP%%/*}
if ping -c1 -W2 "$IP_SOLA" >/dev/null 2>&1; then
    echo "ABORTO: $IP_SOLA ya responde a ping. Elige otra: IP=192.0.2.21/24 ./crear-lxc.sh" >&2
    exit 1
fi
echo "--- 1. $IP_SOLA esta libre (sin respuesta a ping)"

# Margen de RAM: este host va justo y no quiero que el lab mate una VM.
DISP=$(free -m | awk '/^Mem:/{print $7}')
echo "    RAM disponible en el host: ${DISP} MB (el LXC pide ${RAM} MB)"
if [ "$DISP" -lt $((RAM + 512)) ]; then
    echo "ABORTO: quedarian menos de 512 MB de margen. Baja el LXC:" >&2
    echo "        RAM=1024 ./crear-lxc.sh" >&2
    exit 1
fi

# ------------------------------------------------- 2. plantilla
echo
echo "--- 2. Plantilla Debian 13"
# La arquitectura se filtra SIEMPRE. El indice de pveam lista amd64 y arm64, y
# un `tail -1` a secas se queda con arm64: el contenedor se crea pero no
# arranca ("Detected container architecture: arm64" y sync_wait falla).
ARCH=$(dpkg --print-architecture)
echo "    arquitectura del host: $ARCH"

TPL=$(pveam list "$ALM_TPL" 2>/dev/null | awk -v a="$ARCH" '$1 ~ /debian-13-standard/ && $1 ~ a {print $1}' | tail -1)
if [ -z "$TPL" ]; then
    echo "    no esta descargada; actualizando el indice..."
    pveam update >/dev/null 2>&1
    # TPL_NOMBRE, no NOMBRE: NOMBRE es el hostname del contenedor y aqui lo
    # pisariamos con el nombre del fichero de la plantilla.
    TPL_NOMBRE=$(pveam available --section system 2>/dev/null \
             | awk -v a="$ARCH" '$2 ~ /debian-13-standard/ && $2 ~ a {print $2}' | tail -1)
    if [ -z "$TPL_NOMBRE" ]; then
        echo "ABORTO: no hay plantilla debian-13-standard para $ARCH en el indice." >&2
        echo "        Mira que ofrece: pveam available --section system | grep debian-13" >&2
        exit 1
    fi
    echo "    descargando $TPL_NOMBRE ..."
    pveam download "$ALM_TPL" "$TPL_NOMBRE"
    if [ $? -ne 0 ]; then
        echo "ABORTO: fallo la descarga de la plantilla." >&2
        exit 1
    fi
    TPL=$(pveam list "$ALM_TPL" 2>/dev/null | awk -v a="$ARCH" '$1 ~ /debian-13-standard/ && $1 ~ a {print $1}' | tail -1)
fi

# Red de seguridad: si por lo que sea la plantilla elegida no es de la
# arquitectura del host, parar ANTES de crear el contenedor.
case "$TPL" in
    *"$ARCH"*) ;;
    *) echo "ABORTO: la plantilla '$TPL' no es $ARCH. No se crea nada." >&2; exit 1 ;;
esac
echo "    OK  $TPL"

# ------------------------------------------------- 3. crear
echo
echo "--- 3. Creando el contenedor"
# nesting=1 no es por capricho: el systemd 257 de Debian 13 lo necesita
# ("WARN: Systemd 257 detected. You may need to enable nesting"). Sin el,
# el contenedor arranca a medias o no arranca.
pct create "$VMID" "$TPL" \
    --hostname "$NOMBRE" \
    --cores "$CORES" \
    --memory "$RAM" \
    --swap "$SWAP" \
    --rootfs "${ALM_DISCO}:${DISCO}" \
    --net0 "name=eth0,bridge=${BRIDGE},tag=${VLAN},ip=${IP},gw=${GW}" \
    --nameserver "$DNS" \
    --ostype debian \
    --unprivileged 1 \
    --features nesting=1 \
    --onboot 1 \
    --description "Stack de monitoreo Proxmox (prometheus + pve-exporter + grafana). Repo: proxmox-monitoring"
if [ $? -ne 0 ]; then
    echo "ABORTO: pct create fallo. No hay nada que limpiar." >&2
    exit 1
fi
echo "    OK  contenedor $VMID creado"

# ------------------------------------------------- 4. arrancar y verificar red
echo
echo "--- 4. Arrancando"
pct start "$VMID"
if [ $? -ne 0 ]; then
    echo "FALLO: no arranco. Para deshacer: pct destroy $VMID" >&2
    exit 1
fi

echo -n "    esperando red"
LISTO=0
for i in $(seq 1 30); do
    if pct exec "$VMID" -- getent hosts deb.debian.org >/dev/null 2>&1; then
        LISTO=1; break
    fi
    echo -n "."
    sleep 2
done
echo
if [ "$LISTO" -eq 1 ]; then
    echo "    OK  el contenedor resuelve DNS y tiene salida"
else
    echo "    FALLO: sin salida a internet desde el contenedor." >&2
    echo "    Comprueba la VLAN $VLAN y el gateway $GW:" >&2
    pct exec "$VMID" -- ip -4 a show eth0 2>/dev/null >&2
    pct exec "$VMID" -- ip route 2>/dev/null >&2
    echo "    Para deshacer: pct stop $VMID && pct destroy $VMID" >&2
    exit 1
fi

# ------------------------------------------------- resumen
echo
echo "=============================================================="
echo " Contenedor $VMID ($NOMBRE) listo en $IP_SOLA"
echo "=============================================================="
echo " Siguiente paso - instalar el stack dentro:"
echo
echo "   pct push $VMID install-stack.sh /root/install-stack.sh"
echo "   pct push $VMID ../dashboards/proxmox-overview.json /root/proxmox-overview.json"
echo "   pct exec $VMID -- chmod +x /root/install-stack.sh"
echo "   pct exec $VMID -- /root/install-stack.sh <IP_NODO> <USUARIO!TOKEN> <VALOR>"
echo
echo " Los tres datos los imprimio ../nodo/install-nodo.sh"
echo
echo " Deshacer todo esto: pct stop $VMID && pct destroy $VMID"
echo "=============================================================="
