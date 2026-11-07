#!/bin/bash
# instalar.sh - punto de entrada unico. Trae el repo y lanza la pieza que pidas.
#
#   curl -fsSL https://raw.githubusercontent.com/mtandazo35/proxmox-monitoring/main/instalar.sh \
#     | bash -s -- <pieza> [argumentos...]
#
# PIEZAS
#   nodo        TODO el lado del hypervisor y nada mas:      -> EN EL NODO PROXMOX
#               node-exporter, usuario PVEAuditor, token y
#               los dos recolectores. Al terminar te explica
#               que hace falta una maquina para el stack.
#   colectores  solo los recolectores, para reinstalarlos    -> EN EL NODO PROXMOX
#               sueltos. 'nodo' ya los incluye.
#   lxc         crea un LXC para el stack (OPCIONAL)         -> EN EL NODO PROXMOX
#   stack       Prometheus + pve-exporter + Grafana          -> EN LA MAQUINA DEL STACK
#   dashboards  solo los paneles y las alertas, sin tocar     -> EN LA MAQUINA DEL STACK
#               paquetes ni la contrasena de Grafana
#   verificar   comprueba que hay datos, no solo servicios   -> EN LA MAQUINA DEL STACK
#
# El orden normal es: 'nodo' en el hypervisor -> creas una maquina (LXC, VM o
# un servidor que ya tengas) -> 'stack' dentro de ella -> 'verificar'.
#
# El repo se deja en /root/proxmox-monitoring, nunca en /tmp. Si ya estaba, se
# actualiza.
#
# OJO con `curl | bash`: la entrada estandar es el propio script, asi que las
# piezas que preguntan algo (los desinstaladores) NO se lanzan desde aqui. Para
# esas, clona primero y ejecutalas a mano.
#
# OJO con el token del paso `stack`: va en la linea de comandos, o sea que es
# visible en `ps` mientras corre y queda en el historial del shell. Es el mismo
# caso que copiarlo por scp; si eso no te vale, clona el repo y lanza
# install-stack.sh leyendo el token de un fichero.
set -u

REPO_URL=${REPO_URL:-https://github.com/mtandazo35/proxmox-monitoring}
RAMA=${RAMA:-main}
DESTINO=${DESTINO:-/root/proxmox-monitoring}
PROBAR=${PROBAR:-0}

PIEZA=${1:-}
[ $# -gt 0 ] && shift

uso() {
    cat >&2 <<EOF

 instalar.sh - trae el repo y lanza una pieza

 Uso:  ... | bash -s -- <pieza> [argumentos]

   nodo        [IP_DEL_STACK]                 en el NODO Proxmox
               hace TODO el lado del hypervisor, recolectores incluidos
   colectores                                 en el NODO Proxmox
               solo los recolectores; 'nodo' ya los instala
   lxc                                        en el NODO Proxmox (opcional)
               requiere IP y GW:
               ... | IP=192.0.2.20/24 GW=192.0.2.1 bash -s -- lxc
   stack       <IP_NODO> <USUARIO!TOKEN> <VALOR>   en la MAQUINA DEL STACK
   dashboards                                 en la MAQUINA DEL STACK
               actualiza paneles y alertas sin tocar nada mas
   verificar                                  en la MAQUINA DEL STACK

 Variables: DESTINO (por omision /root/proxmox-monitoring), RAMA, PROBAR=1.
 PROBAR=1 trae el repo y dice que ejecutaria, sin ejecutarlo.

EOF
    exit 1
}

case "$PIEZA" in
    nodo|colectores|lxc|stack|dashboards|verificar) ;;
    ""|-h|--help|ayuda) uso ;;
    *) echo "ABORTO: pieza desconocida: '$PIEZA'" >&2; uso ;;
esac

if [ "$(id -u)" != "0" ]; then
    echo "ABORTO: esto tiene que correr como root." >&2
    exit 1
fi

# --------------------------------------------------- la pieza va donde va
EN_NODO=0
command -v pct >/dev/null 2>&1 && command -v qm >/dev/null 2>&1 && EN_NODO=1

case "$PIEZA" in
    nodo|colectores|lxc)
        if [ "$EN_NODO" -ne 1 ]; then
            echo "ABORTO: '$PIEZA' va EN EL NODO PROXMOX y aqui no hay pct/qm." >&2
            exit 1
        fi ;;
    stack|dashboards|verificar)
        if [ "$EN_NODO" -eq 1 ]; then
            echo "AVISO: '$PIEZA' va en la MAQUINA DEL STACK, y esto parece un nodo Proxmox."
            echo "       Si de verdad quieres el stack sobre el propio hypervisor, sigue;"
            echo "       pero si ese nodo cae, te quedas sin el historico que explicaria"
            echo "       por que cayo. Ctrl-C para parar."
            sleep 5
        fi ;;
esac

echo "=============================================================="
echo " instalar.sh  pieza: $PIEZA   en $(hostname)"
echo "=============================================================="

# --------------------------------------------------- traer el repo
echo
echo "--- Trayendo el repo a $DESTINO"
if command -v git >/dev/null 2>&1; then
    if [ -d "$DESTINO/.git" ]; then
        git -C "$DESTINO" fetch --quiet origin "$RAMA" \
          && git -C "$DESTINO" reset --hard --quiet "origin/$RAMA" \
          && echo "    actualizado ($(git -C "$DESTINO" rev-parse --short HEAD))"
    else
        rm -rf "$DESTINO"
        git clone --quiet --depth 1 --branch "$RAMA" "$REPO_URL" "$DESTINO" \
          && echo "    clonado ($(git -C "$DESTINO" rev-parse --short HEAD))"
    fi
else
    # Sin git: el tarball de la rama. Se baja entero a proposito, porque
    # install-stack.sh necesita tambien los dashboards, no solo su script.
    echo "    (no hay git; bajando el tarball)"
    TMP=$(mktemp -d /root/.pm-XXXXXX) || exit 1
    if ! curl -fsSL "$REPO_URL/archive/refs/heads/$RAMA.tar.gz" | tar xz -C "$TMP"; then
        echo "ABORTO: no se pudo bajar el repo." >&2
        rm -rf "$TMP"; exit 1
    fi
    rm -rf "$DESTINO"
    mv "$TMP"/*/ "$DESTINO"
    rm -rf "$TMP"
    echo "    descargado"
fi

if [ ! -d "$DESTINO/nodo" ] || [ ! -d "$DESTINO/stack" ]; then
    echo "ABORTO: $DESTINO no tiene la pinta del repo (faltan nodo/ o stack/)." >&2
    exit 1
fi
chmod +x "$DESTINO"/nodo/*.sh "$DESTINO"/stack/*.sh "$DESTINO"/dashboards/plantillas/*.sh 2>/dev/null

# --------------------------------------------------- lanzar
case "$PIEZA" in
    nodo)       CARPETA="$DESTINO/nodo";  GUION=./install-nodo.sh ;;
    colectores) CARPETA="$DESTINO/nodo";  GUION=./install-colectores.sh ;;
    lxc)        CARPETA="$DESTINO/stack"; GUION=./crear-lxc.sh ;;
    stack)      CARPETA="$DESTINO/stack"; GUION=./install-stack.sh ;;
    dashboards) CARPETA="$DESTINO/stack"; GUION=./actualizar-dashboards.sh ;;
    verificar)  CARPETA="$DESTINO/stack"; GUION=./verificar.sh ;;
esac

# El stack necesita los dashboards en /root para provisionarlos.
if [ "$PIEZA" = "stack" ]; then
    cp "$DESTINO"/dashboards/proxmox-*.json /root/ 2>/dev/null \
      && echo "    dashboards copiados a /root/ para que los recoja el instalador"
fi

echo
echo "--- Lanzando $GUION $*"
if [ "$PROBAR" = "1" ]; then
    echo "    PROBAR=1: no se ejecuta nada. El repo esta en $DESTINO."
    exit 0
fi

cd "$CARPETA" || exit 1

# </dev/null: la entrada estandar es el propio instalar.sh cuando se viene por
# `curl | bash`, y un guion que leyera de ahi se tragaria el resto del script.
if [ "$PIEZA" = "verificar" ]; then
    # Las dos comprobaciones: el stack y los paneles. Cuentan los fallos de las
    # dos, porque una sola no basta para decir que esto funciona.
    FALLOS=0
    ./verificar.sh </dev/null || FALLOS=$((FALLOS + 1))
    echo
    if [ -f /var/lib/grafana/dashboards/proxmox-overview.json ]; then
        python3 ./verificar-dashboard.py </dev/null || FALLOS=$((FALLOS + 1))
    else
        echo "AVISO: no hay dashboards provisionados; me salto verificar-dashboard.py"
    fi
    exit "$FALLOS"
fi

exec "$GUION" "$@" </dev/null
