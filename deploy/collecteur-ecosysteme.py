#!/usr/bin/env python3
"""Collecteur d'état du serveur pour le centre de contrôle de l'écosystème IBIG (LECTURE SEULE).

À lancer en root par cron sur le VPS, toutes les 2 minutes :
  */2 * * * * /usr/bin/python3 /opt/ibig-ia/deploy/collecteur-ecosysteme.py >> /var/log/ibig-collecteur.log 2>&1

Il relève l'état de la machine (charge, mémoire, disque, services, conteneurs Docker, applications
pm2, erreurs du serveur web par site, tentatives d'intrusion, mises à jour, certificats, âge des
sauvegardes) et l'écrit dans state/serveur.json, que l'agent lit. L'agent n'a ainsi besoin d'aucun
accès au serveur. Ce script ne modifie rien : il ne lance que des commandes de consultation.
Les éléments lents (mises à jour, certificats) ne sont relus qu'une fois par heure.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

STATE = Path(os.environ.get("IBIG_STATE_DIR", "/opt/ibig-ia/state"))
OUT = STATE / "serveur.json"
CACHE = STATE / ".collecteur-cache.json"
SERVICES = ("nginx", "docker", "cron", "fail2ban", "mysql", "postgresql", "redis-server")
BACKUPS = (  # nom, dossier, motif
    ("Agent IA", "/var/backups/ibig-agent", "*.gpg"),
    ("Bases des plateformes", "/var/backups/ibig-plateformes", "*.gpg"),
    ("Lokativo", "/var/backups/lokativo", "*.sql.gz"),
    ("Scolaby", "/opt/backups", "scolaby-*.sql.gz"),
    ("Fleet 360", "/var/www/ibig/storage/app/backups", "*.sql.gz"),
    ("Santarex", "/opt/backups", "santarex-*.dump"),
)
WINDOW = timedelta(minutes=15)


def run(cmd: str, timeout: int = 25) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def system() -> dict:
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        mem[k] = int(v.split()[0]) // 1024 if v.split() else 0
    disk = shutil.disk_usage("/")
    return {
        "uptime_s": int(float(Path("/proc/uptime").read_text().split()[0])),
        "load": [round(x, 2) for x in os.getloadavg()], "cores": os.cpu_count() or 1,
        "mem": {"total_mb": mem.get("MemTotal", 0), "available_mb": mem.get("MemAvailable", 0),
                "swap_total_mb": mem.get("SwapTotal", 0),
                "swap_used_mb": mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)},
        "disks": [{"mount": "/", "used_pct": round(100 * disk.used / disk.total, 1),
                   "free_gb": round(disk.free / 1024**3, 1)}],
        "reboot_required": Path("/var/run/reboot-required").exists(),
    }


def containers() -> list[dict]:
    ids = run("docker ps -aq").split()
    if not ids:
        return []
    try:
        data = json.loads(run("docker inspect " + " ".join(ids)))
    except ValueError:
        return []
    out = []
    for c in data:
        st = c.get("State", {})
        out.append({"name": c.get("Name", "").lstrip("/"), "image": c.get("Config", {}).get("Image", "")[:60],
                    "state": st.get("Status", ""), "health": (st.get("Health") or {}).get("Status", ""),
                    "restarts": c.get("RestartCount", 0), "started": st.get("StartedAt", "")[:19]})
    return sorted(out, key=lambda x: x["name"])


def pm2() -> list[dict]:
    try:
        apps = json.loads(run("pm2 jlist 2>/dev/null"))
    except ValueError:
        return []
    return [{"name": a["name"], "status": a["pm2_env"].get("status", ""),
             "restarts": a["pm2_env"].get("restart_time", 0),
             "mem_mb": round(a.get("monit", {}).get("memory", 0) / 1024 / 1024)} for a in apps]


def nginx_errors(now: datetime) -> dict:
    """Erreurs du serveur web des 15 dernières minutes, par site (le journal d'erreurs nomme le site)."""
    by_site: dict[str, int] = {}
    total = 0
    for line in run("tail -n 6000 /var/log/nginx/error.log").splitlines():
        m = re.match(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(error|crit|alert|emerg)\]", line)
        if not m:
            continue
        try:
            when = datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S")
        except ValueError:
            continue
        if now - when > WINDOW:
            continue
        total += 1
        site = re.search(r"server: ([^,\s]+)", line)
        key = site.group(1) if site else "(non précisé)"
        by_site[key] = by_site.get(key, 0) + 1
    requests = errors5 = 0
    for line in run("tail -n 20000 /var/log/nginx/access.log").splitlines():
        m = re.search(r'\[(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2}) [+-]\d{4}\] "[^"]*" (\d{3}) ', line)
        if not m:
            continue
        try:
            when = datetime.strptime(m.group(1), "%d/%b/%Y:%H:%M:%S")
        except ValueError:
            continue
        if now - when > WINDOW:
            continue
        requests += 1
        errors5 += m.group(2).startswith("5")
    return {"errors_15m_by_site": by_site, "errors_15m_total": total,
            "requests_15m": requests, "http5xx_15m": errors5}


def security() -> dict:
    failed = run("journalctl -u ssh --since '24 hours ago' --no-pager 2>/dev/null | grep -c 'Failed password'")
    banned = re.search(r"Currently banned:\s+(\d+)", run("fail2ban-client status sshd 2>/dev/null"))
    return {"ssh_failed_24h": int((failed.strip() or "0")), "banned_now": int(banned.group(1)) if banned else 0}


def slow_items(cache: dict, now: float) -> dict:
    """Mises à jour et certificats : relus au plus une fois par heure."""
    if now - cache.get("slow_at", 0) < 3600 and "updates" in cache:
        return {"updates": cache["updates"], "certs": cache["certs"]}
    lines = [ln for ln in run("apt list --upgradable 2>/dev/null").splitlines() if "/" in ln]
    updates = {"pending": len(lines), "security": sum("-security" in ln for ln in lines)}
    certs = []
    for m in re.finditer(r"Certificate Name: (\S+).*?VALID: (\d+) days",
                         run("certbot certificates 2>/dev/null", 60), re.S):
        certs.append({"name": m.group(1), "days": int(m.group(2))})
    cache.update(slow_at=now, updates=updates, certs=certs)
    return {"updates": updates, "certs": certs}


def backups(now: float) -> list[dict]:
    out = []
    for name, folder, pattern in BACKUPS:
        files = list(Path(folder).glob(pattern)) if Path(folder).is_dir() else []
        newest = max((f.stat().st_mtime for f in files), default=None)
        out.append({"name": name, "count": len(files),
                    "age_h": round((now - newest) / 3600, 1) if newest else None})
    return out


def offsite() -> dict | None:
    """État de la copie chiffrée vers Google Drive (écrit par deploy/copie-google.sh), None si absent."""
    try:
        data = json.loads((STATE / "copie-google.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        when = datetime.fromisoformat(data["date"])
        data["age_h"] = round((time.time() - when.timestamp()) / 3600, 1)
    except (KeyError, ValueError):
        data["age_h"] = None
    return data


def main() -> None:
    now_ts = time.time()
    now = datetime.now()
    cache = load_cache()
    slow = slow_items(cache, now_ts)
    failed = run("systemctl list-units --state=failed --no-legend --plain").splitlines()
    data = {
        "version": 1, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hostname": os.uname().nodename, **system(),
        "containers": containers(), "pm2": pm2(),
        "failed_units": [ln.split()[0] for ln in failed if ln.strip()],
        "nginx": nginx_errors(now), "security": security(), **slow, "backups": backups(now_ts), "offsite": offsite(),
        "services": {s: run(f"systemctl is-active {s}").strip() or "inconnu" for s in SERVICES},
    }
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(OUT)
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    print(f"{data['at']} collecte ok : {len(data['containers'])} conteneurs, {len(data['pm2'])} apps pm2")


if __name__ == "__main__":
    main()
