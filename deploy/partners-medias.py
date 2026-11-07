#!/usr/bin/env python3
"""Rapatrie les médias de mission d'IBIG PARTNERS de Supabase vers le disque du serveur.

Lancé chaque jour par cron tant que Supabase bloque la lecture (quota dépassé) : il ne fait rien d'autre que
réessayer. Dès que les 40 fichiers sont lus, il :
  1. les copie dans /var/www/ibig-partners-uploads/mission-media/ ;
  2. sauvegarde la table "MissionMedia" ;
  3. remplace leurs adresses par /uploads/mission-media/<nom> ;
  4. écrit le fichier-témoin qui arrête les essais suivants.
Idempotent : sans danger si relancé.
"""

import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

DEST = "/var/www/ibig-partners-uploads/mission-media"
DONE = "/root/.partners-medias-ok"
ENV = "/var/www/ibig-partners/.env"
NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def psql(sql: str) -> str:
    cmd = ["runuser", "-u", "postgres", "--", "psql", "-d", "ibig_partners", "-tA", "-F", "|", "-v", "ON_ERROR_STOP=1"]
    return subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=60, check=True).stdout


def supabase_base() -> str:
    with open(ENV, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"(?:NEXT_PUBLIC_)?SUPABASE_URL=(.*)", line.strip())
            if m:
                return m.group(1).strip().strip('"').strip("'").rstrip("/")
    raise SystemExit("adresse Supabase introuvable")


def main() -> int:
    if os.path.exists(DONE):
        return 0
    base = supabase_base()
    rows = [r.split("|", 1) for r in psql(
        'select id, url from "MissionMedia" where url ilike \'%supabase%\' or url ilike \'/storage/%\' '
        "or url !~ '^(https?:|/uploads/)';").splitlines() if "|" in r]
    if not rows:
        open(DONE, "w", encoding="utf-8").write(datetime.now(UTC).isoformat())
        print("rien à rapatrier")
        return 0
    os.makedirs(DEST, exist_ok=True)
    plan = []
    for rid, url in rows:
        name = url.rsplit("/", 1)[-1].split("?")[0]
        if not NAME.match(name):
            print("nom ignoré (inattendu) pour la ligne", rid)
            return 1
        plan.append((rid, name))
    for _rid, name in plan:
        target = os.path.join(DEST, name)
        if os.path.exists(target) and os.path.getsize(target) > 0:
            continue
        try:
            data = urllib.request.urlopen(f"{base}/storage/v1/object/public/mission-media/{name}", timeout=60).read()  # noqa: S310
        except urllib.error.HTTPError as exc:
            print(f"Supabase répond encore {exc.code} : nouvel essai demain")
            return 0
        with open(target, "wb") as fh:
            fh.write(data)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M")
    dump = subprocess.run(["runuser", "-u", "postgres", "--", "pg_dump", "-d", "ibig_partners", "-t", '"MissionMedia"'],
                          capture_output=True, text=True, timeout=120, check=True).stdout
    with open(f"/opt/backups/partners-missionmedia-{stamp}.sql", "w", encoding="utf-8") as fh:
        fh.write(dump)
    for rid, name in plan:
        if not re.match(r"^[A-Za-z0-9-]+$", rid):
            print("identifiant inattendu", rid)
            return 1
        psql(f'update "MissionMedia" set url = \'/uploads/mission-media/{name}\' where id = \'{rid}\';')
    open(DONE, "w", encoding="utf-8").write(stamp)
    print(f"{len(plan)} médias rapatriés et adresses mises à jour")
    return 0


if __name__ == "__main__":
    sys.exit(main())
