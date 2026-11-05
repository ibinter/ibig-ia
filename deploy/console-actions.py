#!/usr/bin/env python3
"""Exécuteur de la console de maintenance : lancé chaque minute par cron, sur le serveur, en root.

Il ne traite QUE les demandes que la direction a confirmées dans l'agent (état « confirmee »), et il n'exécute
QUE la commande fixée dans la liste fermée ibig_agent/maintenance_catalog.py pour (action, cible). Une
action ou une cible inconnue est refusée, même si la base avait été modifiée à la main. Aucune chaîne
venant de la base n'est jamais passée à un shell. Le résultat (fin de sortie) est écrit dans la demande.
"""

import fcntl
import os
import subprocess
import sys

sys.path.insert(0, "/opt/ibig-ia/src")
from ibig_agent.maintenance_catalog import command_for

DB_CONTAINER = "ibig-ia-db-1"
LOCK = "/var/run/ibig-console-actions.lock"
TIMEOUT = {"sauvegarder": 1800}      # secondes ; 5 minutes pour le reste
ENV = {**os.environ, "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}


def psql(sql: str, **variables) -> str:
    args = ["docker", "exec", "-i", DB_CONTAINER, "sh", "-c",
            'psql -U $POSTGRES_USER -d $POSTGRES_DB -tA -F "|" -v ON_ERROR_STOP=1 "$@"', "sh"]
    for k, v in variables.items():
        args += ["-v", f"{k}={v}"]
    return subprocess.run(args, input=sql, capture_output=True, text=True, timeout=60, check=True, env=ENV).stdout


def finish(action_id: int, status: str, output: str) -> None:
    psql("update maintenance_actions set status = :'st', output = :'out', done_at = now() where id = :id;",
         st=status, out=output[-800:], id=str(action_id))


def main() -> int:
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0                       # une exécution précédente tourne encore
    rows = psql("select id, action, target from maintenance_actions where status = 'confirmee' "
                "order by id limit 3;").splitlines()
    for line in rows:
        try:
            raw_id, action, target = line.split("|", 2)
            action_id = int(raw_id)
        except ValueError:
            continue
        taken = psql("update maintenance_actions set status = 'en_cours' where id = :id and status = 'confirmee' "
                     "returning id;", id=str(action_id)).strip()
        if not taken:
            continue
        cmd = command_for(action, target)
        if cmd is None:
            finish(action_id, "refusee", "Hors de la liste autorisée : rien n'a été exécuté")
            continue
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT.get(action, 300), env=ENV, check=False)
            tail = (res.stdout + res.stderr).strip() or "(aucun message)"
            finish(action_id, "ok" if res.returncode == 0 else "erreur", f"code {res.returncode} : {tail}")
        except subprocess.TimeoutExpired:
            finish(action_id, "erreur", "Délai dépassé")
        except OSError as exc:
            finish(action_id, "erreur", f"Impossible de lancer la commande : {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
