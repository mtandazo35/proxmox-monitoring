#!/bin/bash
# desinstalar-nodo.sh - deshace exactamente lo que hizo install-nodo.sh.
#
# Quita node-exporter, el token y el usuario prometheus@pve, y la regla de UFW.
# NO toca nada mas del nodo.
set -u

TS=$(date +%Y%m%d-%H%M%S)
PVE_USER=prometheus@pve
TOKEN_ID=monitoring

echo "=== desinstalar-nodo.sh en $(hostname) ($(date '+%Y-%m-%d %H:%M:%S %Z')) ==="

# No borrar a ciegas: ver primero que es lo que hay.
echo
echo "--- Esto es lo que se va a quitar:"
dpkg -s prometheus-node-exporter >/dev/null 2>&1 \
    && echo "    paquete prometheus-node-exporter $(dpkg-query -W -f='${Version}' prometheus-node-exporter)" \
    || echo "    (node-exporter no esta instalado)"
pveum user token list "$PVE_USER" 2>/dev/null | grep -q "$TOKEN_ID" \
    && echo "    token ${PVE_USER}!${TOKEN_ID}" \
    || echo "    (el token no existe)"
pveum user list --output-format json 2>/dev/null | grep -q "\"$PVE_USER\"" \
    && echo "    usuario $PVE_USER" \
    || echo "    (el usuario no existe)"
ufw status 2>/dev/null | grep -q '9100' && echo "    regla UFW de :9100"

echo
read -r -p "Continuar? [s/N] " OK
case "$OK" in
    s|S|si|SI|Si) ;;
    *) echo "Cancelado. Nada tocado."; exit 0 ;;
esac

echo
echo "--- 1. node-exporter"
systemctl disable --now prometheus-node-exporter >/dev/null 2>&1
DEBIAN_FRONTEND=noninteractive apt-get purge -y prometheus-node-exporter >/dev/null 2>&1
if ! ss -lntu 2>/dev/null | grep -q ':9100'; then
    echo "    OK  :9100 ya no escucha"
else
    echo "    AVISO: algo sigue en :9100 (otra unidad?)"
fi

echo
echo "--- 2. Token y usuario PVE"
pveum user token remove "$PVE_USER" "$TOKEN_ID" >/dev/null 2>&1 \
    && echo "    token borrado" || echo "    (sin token que borrar)"
pveum acl delete / --users "$PVE_USER" --roles PVEAuditor >/dev/null 2>&1 \
    && echo "    ACL retirada" || echo "    (sin ACL que retirar)"
pveum user delete "$PVE_USER" >/dev/null 2>&1 \
    && echo "    usuario borrado" || echo "    (sin usuario que borrar)"

echo
echo "--- 3. UFW"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    # Se borran por numero, de mayor a menor, para que no se desplacen.
    for n in $(ufw status numbered 2>/dev/null | grep '9100' \
               | sed -n 's/^\[[[:space:]]*\([0-9]*\)\].*/\1/p' | sort -rn); do
        yes | ufw delete "$n" >/dev/null 2>&1
    done
    ufw status 2>/dev/null | grep -q '9100' \
        && echo "    AVISO: queda alguna regla de 9100" \
        || echo "    OK  sin reglas de 9100"
else
    echo "    UFW no activo, nada que hacer"
fi

echo
echo "=== Nodo limpio. Verifica con: pveum user list; ss -lntu | grep 9100 ==="
echo "Los respaldos de /root/backups/pre-monitoreo-*.tar.gz NO se borran."
