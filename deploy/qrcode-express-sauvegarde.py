#!/usr/bin/env python3
"""Rapatrie les fichiers de QR CODE EXPRESS depuis l'ANCIEN projet Supabase (lecture seule).

La base et la connexion des utilisateurs sont déjà sur le serveur (/opt/qrcode-supabase). Restent les fichiers
envoyés avant la migration (images et PDF), qui ne sont lisibles chez Supabase qu'une fois le quota remis à zéro.
Tant que Supabase répond 402, le script réessaie sans rien faire d'autre. Dès que la lecture revient :
  1. copie les fichiers des buckets qr-images et qr-pdfs dans /var/www/qrcode-express-uploads/<bucket>/<chemin>
     (nginx les sert sur https://qrcode-express.com/files/<bucket>/<chemin>) ;
  2. remplace, dans la base du serveur, les anciennes adresses Supabase Storage par les adresses /files/ ;
  3. écrit le fichier-témoin /root/.qrcode-express-sauvegarde-ok.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

ENV = "/root/qr-env.avant-selfhost"      # identifiants de l'ANCIEN projet, sauvegardés avant la migration
DEST = "/var/www/qrcode-express-uploads"
DONE = "/root/.qrcode-express-sauvegarde-ok"
BUCKETS = ("qr-images", "qr-pdfs")


def env() -> dict:
    out = {}
    with open(ENV, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"([A-Z_]+)=(.*)", line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


class Blocked(Exception):
    pass


def call(base: str, key: str, path: str, body: dict | None = None, raw: bool = False):
    req = urllib.request.Request(
        base + path, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        data = urllib.request.urlopen(req, timeout=120).read()
    except urllib.error.HTTPError as exc:
        if exc.code in (402, 429, 503):
            raise Blocked(exc.code) from exc
        raise
    return data if raw else json.loads(data)


def list_objects(base: str, key: str, bucket: str, prefix: str = "") -> list[str]:
    found, offset = [], 0
    while True:
        page = call(base, key, f"/storage/v1/object/list/{bucket}",
                    {"prefix": prefix, "limit": 100, "offset": offset})
        for item in page:
            path = f"{prefix}{item['name']}"
            if item.get("id") is None:          # dossier
                found += list_objects(base, key, bucket, path + "/")
            else:
                found.append(path)
        if len(page) < 100:
            return found
        offset += 100


def rewrite_urls(old: str, new: str) -> int:
    """Remplace l'ancienne adresse du stockage par la nouvelle dans toutes les colonnes texte/json de la base."""
    sql_cols = ("select table_name, column_name, data_type from information_schema.columns where table_schema = 'public' "
                "and data_type in ('text', 'character varying', 'jsonb', 'json')")
    def psql(sql: str) -> str:
        return subprocess.run(["docker", "exec", "-i", "qrcode-supabase-db-1", "psql", "-U", "postgres", "-d", "postgres",
                               "-v", "ON_ERROR_STOP=1", "-tA", "-F", "|"], input=sql.encode(), capture_output=True,
                              check=True).stdout.decode()
    total = 0
    for line in psql(sql_cols).splitlines():
        table, col, kind = line.split("|")
        if not (re.match(r"^[a-z_0-9]+$", table) and re.match(r"^[a-z_0-9]+$", col)):
            continue
        cast = "::jsonb" if kind == "jsonb" else ("::json" if kind == "json" else "")
        like = old.replace("'", "''")
        out = psql(f"WITH u AS (UPDATE public.{table} SET {col} = replace({col}::text, '{like}', '{new}'){cast} "
                   f"WHERE {col}::text LIKE '%{like}%' RETURNING 1) SELECT count(*) FROM u;")
        total += int(out.strip() or 0)
    return total


def main() -> int:
    if os.path.exists(DONE):
        return 0
    e = env()
    base, key = e["NEXT_PUBLIC_SUPABASE_URL"].rstrip("/"), e["SUPABASE_SERVICE_ROLE_KEY"]
    try:
        copied = 0
        for bucket in BUCKETS:
            for path in list_objects(base, key, bucket):
                if not re.match(r"^[A-Za-z0-9._/-]+$", path) or ".." in path:
                    continue
                target = os.path.join(DEST, bucket, path)
                if os.path.exists(target) and os.path.getsize(target) > 0:
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as fh:
                    fh.write(call(base, key, f"/storage/v1/object/{bucket}/{path}", raw=True))
                copied += 1
        old = base + "/storage/v1/object/public/"
        rewritten = rewrite_urls(old, "https://qrcode-express.com/files/")
        stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M")
        with open(DONE, "w", encoding="utf-8") as fh:
            fh.write(stamp)
        print(f"{copied} fichiers copiés, {rewritten} champ(s) de la base mis à jour")
        return 0
    except Blocked as exc:
        print(f"Supabase répond encore {exc}: nouvel essai demain")
        return 0


if __name__ == "__main__":
    sys.exit(main())
