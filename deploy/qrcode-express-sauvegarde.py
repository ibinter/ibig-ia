#!/usr/bin/env python3
"""Sauvegarde de QR CODE EXPRESS hors de Supabase (lecture seule chez Supabase).

Tant que Supabase bloque la lecture (quota dépassé), le script réessaie sans rien faire d'autre.
Dès que la lecture revient :
  1. copie tous les fichiers des buckets qr-images et qr-pdfs dans /var/www/qrcode-express-uploads/<bucket>/<chemin>
     (même chemin qu'avant : nginx les sert sur /files/<bucket>/<chemin>) ;
  2. exporte chaque table de la base en JSON dans /opt/backups/qrcode-express-<date>/ ;
  3. écrit le fichier-témoin /root/.qrcode-express-sauvegarde-ok.
Les adresses enregistrées dans la base ne sont PAS modifiées ici : on le fait ensuite, à la main, une fois
la copie vérifiée.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

ENV = "/var/www/qrcode-express/.env"
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
        stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M")
        out_dir = f"/opt/backups/qrcode-express-{stamp}"
        os.makedirs(out_dir, exist_ok=True)
        spec = call(base, key, "/rest/v1/")
        tables = [t for t in spec.get("definitions", {}) if re.match(r"^[a-z0-9_]+$", t)]
        for table in tables:
            rows, start = [], 0
            while True:
                chunk = call(base, key, f"/rest/v1/{table}?select=*&offset={start}&limit=1000")
                rows += chunk
                if len(chunk) < 1000:
                    break
                start += 1000
            with open(os.path.join(out_dir, table + ".json"), "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False)
            os.chmod(os.path.join(out_dir, table + ".json"), 0o600)
        open(DONE, "w", encoding="utf-8").write(stamp)
        print(f"{copied} fichiers copiés, {len(tables)} tables exportées dans {out_dir}")
        return 0
    except Blocked as exc:
        print(f"Supabase répond encore {exc}: nouvel essai demain")
        return 0


if __name__ == "__main__":
    sys.exit(main())
