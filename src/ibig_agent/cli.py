"""Ligne de commande : ibig-agent <commande>."""

from __future__ import annotations

import argparse
import logging

from .runtime import build_runtime


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ibig-agent", description="Agent IA IBIG")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="Créer les tables de la base")
    serve = sub.add_parser("serve", help="Tableau de bord + tâches planifiées")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--sans-planificateur", action="store_true")
    sub.add_parser("poll", help="Relever les boîtes mail une fois")
    sub.add_parser("rapport", help="Produire le rapport quotidien maintenant")
    cal = sub.add_parser("calendrier", help="Préparer le calendrier éditorial")
    cal.add_argument("--semaine", help="Lundi de la semaine (AAAA-MM-JJ)")
    sub.add_parser("verifier-base", help="Contrôler la base de connaissances")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.cmd == "verifier-base":
        rt = build_runtime(with_llm=False, connectors={})
        incomplete = [d.path for d in rt.kb.documents if d.meta.get("statut") == "a_completer"]
        print(f"{len(rt.kb.documents)} documents, {len(rt.kb.faq)} entrées de FAQ")
        print("Fiches à compléter :", ", ".join(incomplete) or "aucune")
        return

    rt = build_runtime()
    if args.cmd == "init-db":
        print("Base initialisée :", rt.settings.database_url)
    elif args.cmd == "poll":
        print(rt.messagerie.poll())
    elif args.cmd == "rapport":
        print(rt.chef.run_daily().as_text())
    elif args.cmd == "calendrier":
        from datetime import date

        from .scheduler import next_monday

        week = date.fromisoformat(args.semaine) if args.semaine else next_monday()
        print(rt.communication.weekly_calendar(week))
    elif args.cmd == "serve":
        import uvicorn

        from .dashboard.app import create_app
        from .scheduler import build_scheduler

        if rt.settings.dashboard_token in ("", "change-moi"):
            raise SystemExit("Définir IBIG_DASHBOARD_TOKEN avant de lancer le tableau de bord")
        if not args.sans_planificateur:
            build_scheduler(rt).start()
        uvicorn.run(create_app(rt), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
