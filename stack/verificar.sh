#!/bin/bash
# verificar.sh - comprueba que el stack no solo este "activo", sino que de datos.
#
# Se corre DENTRO del contenedor. Solo lee: no cambia nada.
#
#   pct push <VMID> verificar.sh /root/verificar.sh
#   pct exec <VMID> -- bash /root/verificar.sh
#
# Un servicio "active" no significa nada: Prometheus puede estar corriendo con
# el config por omision del paquete y sin un solo target bueno.
set -u

PROM=${PROM:-http://127.0.0.1:9090}
FALLOS=0

echo "=============================================================="
echo " verificar.sh en $(hostname)  ($(date '+%Y-%m-%d %H:%M:%S %Z'))"
echo "=============================================================="

# ---------------------------------------------------------------- servicios
echo
echo "--- Servicios"
for s in prometheus prometheus-pve-exporter grafana-server; do
    E=$(systemctl is-active "$s" 2>/dev/null)
    printf "   %-26s %s\n" "$s" "$E"
    [ "$E" = "active" ] || FALLOS=$((FALLOS + 1))
done

# ---------------------------------------------------------------- flags
echo
echo "--- Con que config corre Prometheus"
curl -fsS --max-time 10 "$PROM/api/v1/status/flags" 2>/dev/null \
  | tr ',' '\n' | grep -E 'config\.file|storage\.tsdb\.path|retention\.time' \
  | sed 's/^/   /'

# ---------------------------------------------------------------- targets
echo
echo "--- Targets"
python3 - "$PROM" <<'PY'
import json, sys, urllib.request
prom = sys.argv[1]
try:
    d = json.load(urllib.request.urlopen(prom + "/api/v1/targets", timeout=10))
    d = d["data"]["activeTargets"]
except Exception as e:
    print("   ERROR no se pudo leer la API de targets: %s" % e)
    sys.exit(1)
if not d:
    print("   ERROR Prometheus no tiene ningun target activo")
    sys.exit(1)
malos = 0
for t in sorted(d, key=lambda x: x["labels"].get("job", "")):
    job = t["labels"].get("job", "?")
    print("   %-11s %-46s %s" % (job, t["scrapeUrl"][:46], t["health"]))
    if t["health"] != "up":
        malos += 1
        if t.get("lastError"):
            print("               error: %s" % t["lastError"])
jobs = {t["labels"].get("job") for t in d}
for esperado in ("prometheus", "node", "pve"):
    if esperado not in jobs:
        print("   ERROR falta el job '%s': Prometheus no leyo el prometheus.yml correcto" % esperado)
        malos += 1
sys.exit(1 if malos else 0)
PY
[ $? -ne 0 ] && FALLOS=$((FALLOS + 1))

# ---------------------------------------------------------------- datos
echo
echo "--- Datos que ya hay dentro (no solo 'el target responde')"
python3 - "$PROM" <<'PY'
import json, sys, urllib.parse, urllib.request
prom = sys.argv[1]

def q(expr):
    url = prom + "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    try:
        r = json.load(urllib.request.urlopen(url, timeout=15))
    except Exception as e:
        return None, str(e)
    if r.get("status") != "success":
        return None, r.get("error", "respuesta no exitosa")
    return r["data"]["result"], None

PRUEBAS = [
    ("CPU del host (%)",
     '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)'),
    ("RAM del host (%)",
     '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)'),
    ("Invitados encendidos",
     'count(pve_up{id=~"(qemu|lxc)/.*"} == 1)'),
    ("Invitados apagados",
     'count(pve_up{id=~"(qemu|lxc)/.*"} == 0)'),
    ("Nucleos en uso, top 5",
     'topk(5, pve_cpu_usage_ratio{id=~"(qemu|lxc)/.*"} '
     '* on(id) group_left(name) pve_guest_info '
     '* on(id) group_left() pve_cpu_usage_limit)'),
    ("RAM por invitado, top 5 (GB)",
     'topk(5, pve_memory_usage_bytes{id=~"(qemu|lxc)/.*"} '
     '* on(id) group_left(name) pve_guest_info) / 1024 / 1024 / 1024'),
    ("Filesystems del host (% usado)",
     '100 * (1 - node_filesystem_avail_bytes{fstype!~"tmpfs|devtmpfs|overlay|squashfs"} '
     '/ node_filesystem_size_bytes{fstype!~"tmpfs|devtmpfs|overlay|squashfs"})'),
    ("Storages del nodo (% usado)",
     '100 * (pve_disk_usage_bytes{id=~"storage/.*"} / pve_disk_size_bytes{id=~"storage/.*"})'),
]

fallos = 0
for titulo, expr in PRUEBAS:
    print("\n   %s:" % titulo)
    res, err = q(expr)
    if err:
        print("      ERROR %s" % err)
        fallos += 1
        continue
    if not res:
        print("      (sin datos) <- el dashboard saldria vacio aqui")
        fallos += 1
        continue
    for s in res[:6]:
        m = s["metric"]
        etiqueta = (m.get("name") or m.get("mountpoint") or m.get("id")
                    or m.get("device") or m.get("instance") or "")
        try:
            valor = round(float(s["value"][1]), 2)
        except (TypeError, ValueError):
            valor = s["value"][1]
        print("      %-30s %s" % (str(etiqueta)[:30], valor))
sys.exit(1 if fallos else 0)
PY
[ $? -ne 0 ] && FALLOS=$((FALLOS + 1))

# ---------------------------------------------------------------- grafana
echo
echo "--- Grafana"
COD=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3000/api/health 2>/dev/null)
echo "   /api/health -> HTTP $COD"
[ "$COD" = "200" ] || FALLOS=$((FALLOS + 1))
curl -fsS --max-time 10 http://127.0.0.1:3000/api/health 2>/dev/null | sed 's/^/   /'

echo
echo "   Dashboards provisionados en disco:"
ls -1 /var/lib/grafana/dashboards/ 2>/dev/null | sed 's/^/     /' || echo "     (ninguno)"
echo "   Datasource provisionado:"
grep -E 'name:|url:|uid:' /etc/grafana/provisioning/datasources/prometheus.yml 2>/dev/null | sed 's/^/     /'

# ---------------------------------------------------------------- resumen
echo
echo "=============================================================="
if [ "$FALLOS" -eq 0 ]; then
    echo " TODO BIEN - hay datos, no solo servicios arrancados"
else
    echo " $FALLOS BLOQUE(S) CON PROBLEMAS - mira arriba"
fi
echo "=============================================================="
exit "$FALLOS"
