"""Ligne de commande : ibig-agent <commande>."""

from __future__ import annotations

import argparse
import getpass
import logging
import os

from .runtime import build_runtime


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ibig-agent", description="Agent IA IBIG")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="Créer ou mettre à jour la base (alias de migrer)")
    mig = sub.add_parser("migrer", help="Appliquer les migrations de la base")
    mig.add_argument("--nouvelle", metavar="MESSAGE",
                     help="Générer une migration à partir des changements de db.py")
    mig.add_argument("--marquer", action="store_true",
                     help="Marquer une base existante comme à jour, sans rien modifier")
    serve = sub.add_parser("serve", help="Tableau de bord + tâches planifiées")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--sans-planificateur", action="store_true")
    sub.add_parser("poll", help="Relever les boîtes mail une fois")
    sub.add_parser("rapport", help="Produire le rapport quotidien maintenant")
    cal = sub.add_parser("calendrier", help="Préparer le calendrier éditorial")
    cal.add_argument("--semaine", help="Lundi de la semaine (AAAA-MM-JJ)")
    sub.add_parser("verifier-base", help="Contrôler la base de connaissances")
    sub.add_parser("alertes", help="Alerter les valideurs maintenant")
    ind = sub.add_parser("indicateurs", help="Indicateurs de réussite (section 3)")
    ind.add_argument("--jours", type=int, default=7)
    sub.add_parser("veille", help="Lancer les alertes de veille maintenant")
    sub.add_parser("commercial", help="Qualifier et relancer les prospects maintenant")
    imp = sub.add_parser("prospects", help="Importer une liste de prospects (CSV)")
    imp.add_argument("fichier", help="CSV : email, nom, pole, solution, besoin, fin_essai, "
                                     "demo, boite")
    art = sub.add_parser("article", help="Préparer un article (brouillon à valider)")
    art.add_argument("--site", help="URL du site (défaut : tous les sites actifs)")
    art.add_argument("--sujet", default="", help="Sujet imposé (facultatif)")
    user = sub.add_parser("utilisateur", help="Gérer les comptes du tableau de bord")
    user_sub = user.add_subparsers(dest="user_cmd", required=True)
    add = user_sub.add_parser("ajouter", help="Créer un compte")
    add.add_argument("--email", required=True)
    add.add_argument("--nom", required=True)
    add.add_argument("--role", required=True, choices=["admin", "direction", "valideur"])
    pwd = user_sub.add_parser("mot-de-passe", help="Changer un mot de passe")
    pwd.add_argument("--email", required=True)
    for name in ("desactiver", "reactiver"):
        p = user_sub.add_parser(name, help=f"{name.capitalize()} un compte")
        p.add_argument("--email", required=True)
    user_sub.add_parser("lister", help="Lister les comptes")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)

    if args.cmd == "verifier-base":
        rt = build_runtime(with_llm=False, connectors={})
        incomplete = [d.path for d in rt.kb.documents if d.meta.get("statut") == "a_completer"]
        print(f"{len(rt.kb.documents)} documents, {len(rt.kb.faq)} entrées de FAQ")
        print("Fiches à compléter :", ", ".join(incomplete) or "aucune")
        return

    if args.cmd == "utilisateur":
        _users(args)
        return
    if args.cmd in ("migrer", "init-db"):
        _migrate(args)
        return

    rt = build_runtime()
    if args.cmd == "poll":
        print(rt.messagerie.poll())
    elif args.cmd == "article":
        if args.site:
            site = rt.org.site(args.site)
            if site is None:
                raise SystemExit(f"Site inconnu : {args.site} (voir config/sites.yaml)")
            reason = rt.contenus_web.why_skipped(site)
            if reason:
                raise SystemExit(f"{site.nom} ignoré : {reason}")
            print("Brouillon à valider :", rt.contenus_web.write_article(site, args.sujet))
        else:
            print(rt.contenus_web.run())
    elif args.cmd == "indicateurs":
        print(rt.veille.indicators(args.jours).as_text())
    elif args.cmd == "commercial":
        print(rt.commercial.qualify_new())
        print(rt.commercial.run())
    elif args.cmd == "prospects":
        result = rt.commercial.import_csv(args.fichier)
        print(f"{result.crees} créé(s), {result.mis_a_jour} mis à jour")
        for err in result.erreurs:
            print("  -", err)
    elif args.cmd == "veille":
        print(rt.veille.check_alerts())
    elif args.cmd == "alertes":
        rt.governor.flag_stale()
        print(rt.notifier.run())
    elif args.cmd == "rapport":
        print(rt.chef.run_daily().as_text())
    elif args.cmd == "calendrier":
        from datetime import date

        from .scheduler import next_monday

        week = date.fromisoformat(args.semaine) if args.semaine else next_monday(tz=rt.settings.timezone)
        print(rt.communication.weekly_calendar(week))
    elif args.cmd == "serve":
        import uvicorn

        from .dashboard.app import create_app
        from .scheduler import build_scheduler

        if len(rt.settings.secret_key) < 32:
            raise SystemExit("Définir IBIG_SECRET_KEY (32 caractères minimum) avant de lancer "
                             "le tableau de bord")
        if not args.sans_planificateur:
            build_scheduler(rt).start()
        uvicorn.run(create_app(rt), host=args.host, port=args.port)


def _migrate(args: argparse.Namespace) -> None:
    from sqlalchemy.engine import make_url

    from . import migrate
    from .config import get_settings
    from .db import make_engine

    url = get_settings().database_url
    engine = make_engine(url)
    shown = make_url(url).render_as_string(hide_password=True)
    if getattr(args, "nouvelle", None):
        migrate.upgrade(engine)  # la comparaison se fait contre une base à jour
        migrate.new_revision(engine, args.nouvelle)
        print("Migration générée dans src/ibig_agent/migrations/versions : à relire")
    elif getattr(args, "marquer", False):
        migrate.stamp(engine)
        print(f"Base marquée à la version {migrate.head_revision()} : {shown}")
    else:
        migrate.upgrade(engine)
        print(f"Base à jour (version {migrate.current_revision(engine)}) : {shown}")


def _read_password() -> str:
    # IBIG_NEW_PASSWORD permet une création non interactive (script de déploiement).
    password = os.environ.get("IBIG_NEW_PASSWORD") or getpass.getpass("Mot de passe : ")
    if not os.environ.get("IBIG_NEW_PASSWORD") and password != getpass.getpass("Confirmer : "):
        raise SystemExit("Les mots de passe ne correspondent pas")
    return password


def _users(args: argparse.Namespace) -> None:
    from .auth import UserStore

    rt = build_runtime(with_llm=False, connectors={})
    store = UserStore(rt.sessions, rt.org)
    try:
        if args.user_cmd == "ajouter":
            store.create(args.email, args.nom, args.role, _read_password())
            poles = rt.org.poles_of(args.email)
            print(f"Compte créé : {args.email} ({args.role})")
            if args.role == "valideur" and not poles:
                print("Attention : cette adresse n'est valideur ou suppléant d'aucun pôle "
                      "dans config/poles.yaml ; elle ne pourra rien valider.")
        elif args.user_cmd == "mot-de-passe":
            store.set_password(args.email, _read_password())
            print("Mot de passe changé")
        elif args.user_cmd in ("desactiver", "reactiver"):
            store.set_active(args.email, args.user_cmd == "reactiver")
            print(f"Compte {'réactivé' if args.user_cmd == 'reactiver' else 'désactivé'}")
        else:
            for u in store.all():
                poles = ", ".join(rt.org.poles_of(u.email)) or "—"
                print(f"{u.email:40} {u.role:10} {'actif' if u.active else 'inactif':8} {poles}")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
