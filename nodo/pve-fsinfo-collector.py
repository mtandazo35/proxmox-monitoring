#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pve-fsinfo-collector.py - el disco REAL de dentro de cada VM.

Corre EN EL NODO PROXMOX, como root, cada 5 minutos por un timer de systemd.
Escribe un fichero .prom en el directorio textfile de node-exporter; no abre
ningun puerto, no usa la API por red y no necesita credenciales.

POR QUE EXISTE
--------------
`/cluster/resources` devuelve **0** en el campo `disk` de una maquina virtual:
para el hypervisor el disco de una VM es un bloque y lo que hay dentro (ext4,
LVM, NTFS...) solo lo entiende el sistema de dentro. pve-exporter solo lee ese
endpoint, asi que ese 0 es lo unico que llega a Prometheus.

El dato si existe: `qm agent <vmid> get-fsinfo` se lo pregunta al
qemu-guest-agent que corre DENTRO del invitado. Este script hace esa pregunta
a cada VM encendida y publica la respuesta como metricas.

SEGURIDAD: la respuesta del agente es DATO NO CONFIABLE
-------------------------------------------------------
Lo que devuelve el agente lo escribe el sistema operativo del invitado, no el
nodo. Si una VM esta comprometida puede contestar un punto de montaje con
comillas o saltos de linea y **inyectar lineas enteras** en el fichero .prom,
que node-exporter sirve tal cual. Por eso TODO valor de etiqueta pasa por
`etiqueta()`: se escapa segun el formato de exposicion de Prometheus y se
recorta. Sin eso, un invitado podria falsear las metricas de cualquier otro.

Rollback: ./desinstalar-fsinfo-collector.sh
"""
import json
import os
import subprocess
import sys
import time

SALIDA = os.environ.get("SALIDA", "/var/lib/prometheus/node-exporter/pve_fsinfo.prom")
ESPERA = int(os.environ.get("ESPERA", "8"))        # segundos por invocacion de qm
MAX_ETIQUETA = 128                                  # recorte de valores de etiqueta

# Pseudo-filesystems: no son disco de nadie y solo ensucian.
FSTYPE_FUERA = {
    "tmpfs", "devtmpfs", "devfs", "overlay", "squashfs", "iso9660", "ramfs",
    "autofs", "proc", "sysfs", "cgroup", "cgroup2", "efivarfs", "fuse.gvfsd-fuse",
    "fuse.portal", "nsfs", "tracefs", "debugfs", "securityfs", "pstore", "bpf",
    "configfs", "hugetlbfs", "mqueue", "binfmt_misc", "rpc_pipefs",
}


def etiqueta(valor):
    """Escapa un valor de etiqueta segun el formato de exposicion de Prometheus.

    Backslash, comilla doble y salto de linea. Es lo unico que separa un
    mountpoint raro -o malicioso- de una linea de metrica inventada.
    """
    s = str(valor)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\n")
    # Un \t o un \0 no rompen el formato, pero no pintan nada en una etiqueta.
    s = "".join(c for c in s if c == " " or c.isprintable())
    return s[:MAX_ETIQUETA]


def corre(cmd, espera):
    """Ejecuta un comando y devuelve (ok, stdout). Nunca lanza excepcion."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=espera)
    except subprocess.TimeoutExpired:
        return False, ""
    except (OSError, ValueError):
        return False, ""
    return p.returncode == 0, p.stdout


def vms_encendidas():
    """[(vmid, nombre)] de las VM en estado running, via `qm list`."""
    ok, salida = corre(["/usr/sbin/qm", "list"], 20)
    if not ok:
        return []
    r = []
    for linea in salida.splitlines()[1:]:
        campos = linea.split()
        if len(campos) < 3 or not campos[0].isdigit():
            continue
        if campos[2] == "running":
            r.append((campos[0], campos[1]))
    return r


def fsinfo(vmid):
    """None si el agente no contesta; si contesta, la lista de filesystems."""
    ok, salida = corre(["/usr/sbin/qm", "agent", str(vmid), "get-fsinfo"], ESPERA)
    if not ok or not salida.strip():
        return None
    try:
        d = json.loads(salida)
    except ValueError:
        return None
    return d if isinstance(d, list) else None


def main():
    t0 = time.time()
    nodo = etiqueta(os.uname().nodename)
    lineas = [
        "# HELP pve_guest_agent_up El qemu-guest-agent de la VM contesta (1) o no (0).",
        "# TYPE pve_guest_agent_up gauge",
    ]
    fs_size, fs_used = [], []
    con_agente = sin_agente = 0

    for vmid, nombre in vms_encendidas():
        comun = 'id="qemu/%s",vmid="%s",name="%s",nodo="%s"' % (
            etiqueta(vmid), etiqueta(vmid), etiqueta(nombre), nodo)
        datos = fsinfo(vmid)
        if datos is None:
            # Sin agente NO es 0% de disco: es "no se sabe". Por eso aqui no se
            # emite ninguna metrica de tamano, solo el 0 del agente.
            lineas.append("pve_guest_agent_up{%s} 0" % comun)
            sin_agente += 1
            continue
        lineas.append("pve_guest_agent_up{%s} 1" % comun)
        con_agente += 1
        for fs in datos:
            if not isinstance(fs, dict):
                continue
            total = fs.get("total-bytes")
            usado = fs.get("used-bytes")
            tipo = str(fs.get("type", ""))
            punto = fs.get("mountpoint", "")
            if total in (None, 0) or usado is None:
                continue                      # sin cifras utiles (p.ej. un CD)
            if tipo in FSTYPE_FUERA:
                continue
            try:
                total, usado = int(total), int(usado)
            except (TypeError, ValueError):
                continue
            if total <= 0 or usado < 0:
                continue
            etqs = '%s,mountpoint="%s",fstype="%s"' % (comun, etiqueta(punto), etiqueta(tipo))
            fs_size.append("pve_guest_fs_size_bytes{%s} %d" % (etqs, total))
            fs_used.append("pve_guest_fs_used_bytes{%s} %d" % (etqs, usado))

    if fs_size:
        lineas += ["# HELP pve_guest_fs_size_bytes Tamano del filesystem DENTRO de la VM.",
                   "# TYPE pve_guest_fs_size_bytes gauge"] + fs_size
        lineas += ["# HELP pve_guest_fs_used_bytes Usado DENTRO de la VM, segun su agente.",
                   "# TYPE pve_guest_fs_used_bytes gauge"] + fs_used

    lineas += [
        "# HELP pve_fsinfo_last_run_timestamp_seconds Cuando termino la ultima pasada.",
        "# TYPE pve_fsinfo_last_run_timestamp_seconds gauge",
        'pve_fsinfo_last_run_timestamp_seconds{nodo="%s"} %d' % (nodo, int(time.time())),
        "# HELP pve_fsinfo_duration_seconds Lo que tardo la ultima pasada.",
        "# TYPE pve_fsinfo_duration_seconds gauge",
        'pve_fsinfo_duration_seconds{nodo="%s"} %.3f' % (nodo, time.time() - t0),
        "# HELP pve_fsinfo_guests_total VMs encendidas, por si su agente contesta o no.",
        "# TYPE pve_fsinfo_guests_total gauge",
        'pve_fsinfo_guests_total{nodo="%s",agente="si"} %d' % (nodo, con_agente),
        'pve_fsinfo_guests_total{nodo="%s",agente="no"} %d' % (nodo, sin_agente),
    ]

    # Escritura atomica: node-exporter lee este directorio cada vez que le
    # scrapean. Si escribieramos en el sitio, leeria ficheros a medias.
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

    sys.stderr.write("pve-fsinfo: %d con agente, %d sin agente, %d filesystems, %.2fs\n"
                     % (con_agente, sin_agente, len(fs_size), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
