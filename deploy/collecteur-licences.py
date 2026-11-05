#!/usr/bin/env python3
"""Collecteur de licences : lit, EN LECTURE SEULE, les licences/abonnements de tous les logiciels du VPS.

Lancé chaque nuit par cron (3 h 40). N'écrit que le fichier /opt/ibig-ia/state/licences-collectees.json.
Seules des requêtes SELECT sont émises, sur quelques colonnes utiles : client, contact, produit, formule,
montant, dates, état. Aucune clé de licence, aucun mot de passe, aucune donnée métier (élèves, patients…).
L'agent IA importe ensuite ce fichier dans « Licences et abonnements ».
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime

OUT = os.environ.get("IBIG_LICENCES_OUT", "/opt/ibig-ia/state/licences-collectees.json")
MYSQL = ["mysql", "--defaults-extra-file=/root/.ibig-mysql.cnf", "-N", "-B", "-r", "-e"]


def pg(container: str, db: str, sql: str) -> list[dict]:
    cmd = ["docker", "exec", "-i", container, "sh", "-c", f"psql -U $POSTGRES_USER -d {db} -tA -v ON_ERROR_STOP=1"]
    out = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=60, check=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def my(sql: str) -> list[dict]:
    out = subprocess.run(MYSQL + [sql], capture_output=True, text=True, timeout=60, check=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


SOURCES = {
    "SANTAREX": lambda: pg("santarex_postgres", "santarex_db", """
        select json_build_object('external_id', l.id::text, 'client', coalesce(t.nom, l."tenantSlug"),
          'contact', coalesce(t.email, ''), 'product', 'SANTAREX', 'raw_plan', l."offreCode",
          'raw_status', l.statut, 'amount', coalesce(l."montantPaye", 0), 'starts', l."dateDebut"::date,
          'ends', l."dateExpiration"::date, 'site', l."tenantSlug")
        from licences l left join tenants t on t.slug = l."tenantSlug";"""),
    "SCOLABY": lambda: pg("s-db-1", "scolaby", """
        select json_build_object('external_id', l.id::text, 'client', e.name, 'contact', coalesce(e.email, ''),
          'product', 'SCOLABY', 'raw_plan', l.plan, 'raw_status', l.status, 'billing', l."billingPeriod",
          'amount', coalesce((select p.amount from "LicensePayment" p where p."establishmentId" = l."establishmentId"
                              and not p.demo and not p.refunded order by p."paidAt" desc limit 1), 0),
          'starts', l."startsAt"::date, 'ends', l."endsAt"::date, 'site', e.slug)
        from "License" l join "Establishment" e on e.id = l."establishmentId";"""),
    "ANOUANZE": lambda: pg("anouanze_postgres", "anouanze_erp", """
        select json_build_object('external_id', a.id::text, 'client', o.nom, 'contact', coalesce(o.email, ''),
          'product', 'ANOUANZÊ', 'raw_plan', p.code, 'raw_status', a.statut, 'billing', a.periodicite,
          'amount', coalesce(a.montant, 0), 'starts', a."dateDebut"::date, 'ends', a."dateFin"::date, 'site', o.slug)
        from abonnements a join organisations o on o.id = a."organisationId"
        left join plans p on p.id = a."planId";"""),
    "GESTMONEY": lambda: pg("gestmoney_postgres", "gestmoney_prod", """
        select json_build_object('external_id', t.id::text, 'client', t.name, 'contact', '',
          'product', 'GESTMONEY', 'raw_plan', t.plan, 'raw_status', t.status,
          'amount', coalesce((select e.montant from licence_events e where e."tenantId" = t.id
                              order by e."createdAt" desc limit 1), 0),
          'starts', t."createdAt"::date, 'ends', coalesce(t."subscriptionEndsAt", t."trialEndsAt")::date,
          'site', coalesce(t.domain, t.slug))
        from tenants t;"""),
    "CONSTRUIRO": lambda: my("""
        select json_object('external_id', s.id, 'client', c.name, 'contact', coalesce(c.email, ''),
          'product', 'CONSTRUIRO', 'raw_plan', p.slug, 'raw_status', s.status, 'billing', s.billing_cycle,
          'amount', coalesce(case s.billing_cycle when 'yearly' then p.price_yearly else p.price_monthly end, 0),
          'starts', date(s.starts_at), 'ends', date(coalesce(s.ends_at, s.trial_ends_at)), 'site', c.slug)
        from construiro.subscriptions s join construiro.companies c on c.id = s.company_id
        left join construiro.subscription_plans p on p.id = s.plan_id where coalesce(c.is_demo, 0) = 0"""),
    "FACTPRO": lambda: my("""
        select json_object('external_id', l.id, 'client', u.name, 'contact', coalesce(u.email, ''),
          'product', 'FACTPRO', 'raw_plan', p.code, 'raw_status', l.status, 'kind', l.type,
          'amount', coalesce(p.price_monthly, 0), 'starts', date(l.starts_at), 'ends', date(l.ends_at),
          'site', '')
        from factpro.licenses l join factpro.users u on u.id = l.user_id
        left join factpro.plans p on p.id = l.plan_id where l.deleted_at is null"""),
    "FLEET360": lambda: my("""
        select json_object('external_id', c.id, 'client', c.name, 'contact', coalesce(c.email, ''),
          'product', 'FLEET 360', 'raw_plan', c.plan, 'raw_status', if(c.is_active = 1, 'active', 'inactive'),
          'etat', c.etat, 'is_trial', c.is_trial, 'amount', coalesce(p.price, 0),
          'billing', p.billing_cycle, 'starts', date(c.created_at), 'ends', date(c.license_expires_at), 'site', c.slug)
        from ibig_fleet360.companies c left join ibig_fleet360.plans p on p.slug = c.plan"""),
}


def main() -> int:
    result = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "sources": {}, "errors": {}}
    for name, fetch in SOURCES.items():
        try:
            result["sources"][name] = fetch()
        except Exception as exc:  # noqa: BLE001 - une base en panne ne doit pas bloquer les autres
            result["errors"][name] = f"{type(exc).__name__}: {str(exc)[:200]}"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False)
    os.chmod(tmp, 0o640)
    os.replace(tmp, OUT)
    print({k: len(v) for k, v in result["sources"].items()}, "erreurs:", list(result["errors"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
