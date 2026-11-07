#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pve-cgroup-collector.py - CPU, RAM y PRESION (PSI) de cada maquina.

Corre EN EL NODO PROXMOX. Lee ficheros de /sys/fs/cgroup y nada mas: ni API,
ni agente dentro del invitado, ni subprocesos. Son unas pocas lecturas de
fichero por maquina, asi que es barato aunque haya 134.

QUE APORTA SOBRE LA API DE PROXMOX
----------------------------------
1. **PSI por maquina.** /cluster/resources no tiene nada parecido. El cgroup de
   cada invitado dice cuanto tiempo estuvo PARADO esperando CPU, disco o
   memoria. Es lo que distingue "esta VM consume poco" de "esta VM no avanza
   porque el nodo va saturado".
2. **RAM real en el host** (memory.current). La API da el valor del balloon, que
   es lo que el hypervisor CREE que usa el invitado. memory.current es lo que
   el host esta pagando de verdad, overhead de QEMU incluido.
3. **CPU exacta** (cpu.stat usage_usec), un contador acumulado, en vez de una
   media que la API recalcula.

Sirve igual para VM y para contenedores; solo cambia la ruta del cgroup. Una
maquina apagada no tiene cgroup y por tanto no aparece: eso es correcto, es
"no hay dato", no un cero.

Rollback: ./desinstalar-colectores.sh
"""
import os
import sys
import time

SALIDA = os.environ.get("SALIDA", "/var/lib/prometheus/node-exporter/pve_cgroup.prom")
CG_QEMU = "/sys/fs/cgroup/qemu.slice"
CG_LXC = "/sys/fs/cgroup/lxc"
CONF_QEMU = "/etc/pve/qemu-server"
CONF_LXC = "/etc/pve/lxc"
MAX_ETIQUETA = 128


def etiqueta(valor):
    """Escapa un valor de etiqueta segun el formato de exposicion de Prometheus."""
    s = str(valor)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\n")
    s = "".join(c for c in s if c == " " or c.isprintable())
    return s[:MAX_ETIQUETA]


def lee(ruta):
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, UnicodeError):
        return None


def nombre_de(vmid, tipo):
    """El nombre configurado, leido del .conf. Si no esta, el vmid."""
    if tipo == "qemu":
        txt, clave = lee("%s/%s.conf" % (CONF_QEMU, vmid)), "name:"
    else:
        txt, clave = lee("%s/%s.conf" % (CONF_LXC, vmid)), "hostname:"
    if not txt:
        return vmid
    for linea in txt.splitlines():
        if linea.startswith(clave):
            v = linea[len(clave):].strip()
            if v:
                return v
        if linea.startswith("["):      # empiezan las snapshots: parar
            break
    return vmid


def psi(ruta):
    """{'some': total_us, 'full': total_us} o {} si el fichero no esta."""
    txt = lee(ruta)
    if not txt:
        return {}
    r = {}
    for linea in txt.splitlines():
        campos = linea.split()
        if not campos or campos[0] not in ("some", "full"):
            continue
        for c in campos[1:]:
            if c.startswith("total="):
                try:
                    r[campos[0]] = int(c[6:])
                except ValueError:
                    pass
    return r


def cpu_usado_us(directorio):
    txt = lee(directorio + "/cpu.stat")
    if not txt:
        return None
    for linea in txt.splitlines():
        if linea.startswith("usage_usec"):
            try:
                return int(linea.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def entero(ruta):
    txt = lee(ruta)
    if txt is None:
        return None
    txt = txt.strip()
    if not txt or txt == "max":
        return None
    try:
        return int(txt)
    except ValueError:
        return None


def maquinas():
    """[(vmid, tipo, directorio_cgroup)] de las maquinas ENCENDIDAS."""
    r = []
    try:
        for e in os.listdir(CG_QEMU):
            if e.endswith(".scope"):
                vmid = e[:-6]
                if vmid.isdigit():
                    r.append((vmid, "qemu", os.path.join(CG_QEMU, e)))
    except OSError:
        pass
    try:
        for e in os.listdir(CG_LXC):
            if e.isdigit() and os.path.isdir(os.path.join(CG_LXC, e)):
                r.append((e, "lxc", os.path.join(CG_LXC, e)))
    except OSError:
        pass
    return sorted(r, key=lambda x: int(x[0]))


RECURSOS = (("cpu", "cpu.pressure"), ("io", "io.pressure"), ("memory", "memory.pressure"))


def main():
    t0 = time.time()
    nodo = etiqueta(os.uname().nodename)

    cab = [
        "# HELP pve_guest_pressure_waiting_seconds_total Tiempo con AL MENOS una tarea "
        "de la maquina parada esperando el recurso (PSI some).",
        "# TYPE pve_guest_pressure_waiting_seconds_total counter",
        "# HELP pve_guest_pressure_stalled_seconds_total Tiempo con TODAS las tareas de "
        "la maquina paradas esperando el recurso (PSI full).",
        "# TYPE pve_guest_pressure_stalled_seconds_total counter",
        "# HELP pve_guest_cpu_seconds_total CPU consumida por la maquina, del cgroup.",
        "# TYPE pve_guest_cpu_seconds_total counter",
        "# HELP pve_guest_memory_host_bytes RAM que la maquina ocupa de VERDAD en el host "
        "(memory.current del cgroup), no el valor del balloon.",
        "# TYPE pve_guest_memory_host_bytes gauge",
    ]
    espera, parado, cpu, mem = [], [], [], []
    n = 0

    for vmid, tipo, d in maquinas():
        etqs = 'id="%s/%s",vmid="%s",name="%s",type="%s",nodo="%s"' % (
            tipo, etiqueta(vmid), etiqueta(vmid), etiqueta(nombre_de(vmid, tipo)),
            tipo, nodo)
        hubo = False

        for recurso, fichero in RECURSOS:
            p = psi(os.path.join(d, fichero))
            if "some" in p:
                espera.append('pve_guest_pressure_waiting_seconds_total{%s,recurso="%s"} %.6f'
                              % (etqs, recurso, p["some"] / 1e6))
                hubo = True
            if "full" in p:
                parado.append('pve_guest_pressure_stalled_seconds_total{%s,recurso="%s"} %.6f'
                              % (etqs, recurso, p["full"] / 1e6))

        us = cpu_usado_us(d)
        if us is not None:
            cpu.append("pve_guest_cpu_seconds_total{%s} %.6f" % (etqs, us / 1e6))
            hubo = True

        m = entero(os.path.join(d, "memory.current"))
        if m is not None:
            mem.append("pve_guest_memory_host_bytes{%s} %d" % (etqs, m))
            hubo = True

        if hubo:
            n += 1

    lineas = cab + espera + parado + cpu + mem + [
        "# HELP pve_cgroup_last_run_timestamp_seconds Cuando termino la ultima pasada.",
        "# TYPE pve_cgroup_last_run_timestamp_seconds gauge",
        'pve_cgroup_last_run_timestamp_seconds{nodo="%s"} %d' % (nodo, int(time.time())),
        "# HELP pve_cgroup_guests_total Maquinas encendidas con cgroup legible.",
        "# TYPE pve_cgroup_guests_total gauge",
        'pve_cgroup_guests_total{nodo="%s"} %d' % (nodo, n),
        "# HELP pve_cgroup_duration_seconds Lo que tardo la ultima pasada.",
        "# TYPE pve_cgroup_duration_seconds gauge",
        'pve_cgroup_duration_seconds{nodo="%s"} %.4f' % (nodo, time.time() - t0),
    ]

    # Atomica: node-exporter lee este directorio en cada scrape.
    tmp = SALIDA + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, SALIDA)
    except OSError as e:
        sys.stderr.write("no se pudo escribir %s: %s\n" % (SALIDA, e))
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return 1

    sys.stderr.write("pve-cgroup: %d maquinas, %d series de presion, %.3fs\n"
                     % (n, len(espera) + len(parado), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
