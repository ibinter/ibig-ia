"""Diagnostic de mise en service : tout vérifier SANS rien envoyer ni publier.

`ibig-agent diagnostic` affiche un tableau OK / ATTENTION / ÉCHEC et renvoie un code de
sortie non nul en cas d'échec (utilisable avant chaque déploiement).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select

from . import migrate
from .db import User
from .runtime import Runtime

OK, WARN, FAIL = "OK", "ATTENTION", "ÉCHEC"


@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: str = ""


class Diagnostic:
    def __init__(self, rt: Runtime, online: bool = True) -> None:
        self.rt = rt
        self.online = online  # False : pas de connexion aux services extérieurs
        self.checks: list[Check] = []

    def add(self, area: str, name: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(area, name, status, detail))

    def probe(self, area: str, name: str, fn: Callable[[], str]) -> None:
        try:
            self.add(area, name, OK, fn())
        except Exception as exc:  # noqa: BLE001 — chaque échec est rapporté, pas levé
            self.add(area, name, FAIL, f"{type(exc).__name__} : {exc}"[:300])

    # ------------------------------------------------------------------ ensemble
    def run(self) -> list[Check]:
        self.settings()
        self.database()
        self.governance()
        self.knowledge()
        if self.online:
            self.ai()
            self.mailboxes()
            self.sites()
        return self.checks

    @property
    def failed(self) -> bool:
        return any(c.status == FAIL for c in self.checks)

    def as_text(self) -> str:
        width = max((len(c.area) + len(c.name) for c in self.checks), default=10) + 3
        lines = []
        for c in self.checks:
            label = f"{c.area} · {c.name}"
            lines.append(f"{c.status:10} {label:{width}} {c.detail}")
        n = {s: sum(c.status == s for c in self.checks) for s in (OK, WARN, FAIL)}
        lines.append(f"\n{n[OK]} OK, {n[WARN]} à surveiller, {n[FAIL]} en échec")
        return "\n".join(lines)

    # ------------------------------------------------------------------ contrôles
    def settings(self) -> None:
        s = self.rt.settings
        self.add("Réglages", "clé de session", OK if len(s.secret_key) >= 32 else FAIL,
                 "" if len(s.secret_key) >= 32 else "IBIG_SECRET_KEY : 32 caractères minimum")
        https = s.dashboard_url.startswith("https://")
        self.add("Réglages", "adresse du tableau de bord", OK if https else WARN,
                 s.dashboard_url + ("" if https else " (HTTPS obligatoire en production)"))
        mb = s.notification_mailbox
        if not mb:
            self.add("Réglages", "boîte des alertes", WARN,
                     "IBIG_NOTIFICATION_MAILBOX vide : aucune alerte par mail")
        elif mb not in self.rt.connectors:
            self.add("Réglages", "boîte des alertes", FAIL,
                     f"{mb} n'est pas déclarée dans config/mailboxes.yaml")
        else:
            self.add("Réglages", "boîte des alertes", OK, mb)
        key = s.sara_api_key
        self.add("Réglages", "API SARA", OK if len(key) >= 32 else WARN,
                 "activée" if len(key) >= 32 else "désactivée (IBIG_SARA_API_KEY < 32 car.)")
        self.add("Réglages", "plafond IA mensuel", OK if s.monthly_ai_budget_usd > 0 else FAIL,
                 f"{s.monthly_ai_budget_usd:.0f} USD, alerte à {s.budget_alert_ratio:.0%}")

    def database(self) -> None:
        engine = self.rt.sessions.kw["bind"]
        current, head = migrate.current_revision(engine), migrate.head_revision()
        self.add("Base", "version du schéma", OK if current == head else FAIL,
                 f"{current} (attendue : {head})")

    def governance(self) -> None:
        stopped = [c for c, st in self.rt.governor.channel_states().items() if st.stopped]
        self.add("Gouvernance", "bouton d'arrêt", WARN if stopped else OK,
                 f"canaux suspendus : {', '.join(stopped)}" if stopped else "aucun canal suspendu")
        with self.rt.sessions() as s:
            users = {u.email: u for u in s.scalars(select(User)).all()}
        if not any(u.role in ("admin", "direction") and u.active for u in users.values()):
            self.add("Gouvernance", "direction", FAIL,
                     "aucun compte admin ou direction : ibig-agent utilisateur ajouter")
        for p in self.rt.org.poles:
            if p.code == "GROUPE":
                continue
            if not p.valideur:
                self.add("Gouvernance", f"valideur {p.code}", WARN,
                         "non renseigné dans poles.yaml : alertes à la direction")
            elif p.valideur.lower() not in users:
                self.add("Gouvernance", f"valideur {p.code}", FAIL,
                         f"{p.valideur} n'a pas de compte pour valider")
            else:
                backup = "" if p.suppleant else " (pas de suppléant)"
                self.add("Gouvernance", f"valideur {p.code}", OK if p.suppleant else WARN,
                         p.valideur + backup)

    def knowledge(self) -> None:
        kb = self.rt.kb
        todo = [d.pole for d in kb.documents if d.meta.get("statut") == "a_completer"]
        self.add("Base de connaissances", "fiches pôles", WARN if todo else OK,
                 f"à compléter : {', '.join(sorted(todo))}" if todo else "complètes")
        self.add("Base de connaissances", "FAQ", OK if kb.faq else WARN,
                 f"{len(kb.faq)} entrée(s)" if kb.faq else "aucune : pas de réponse FAQ")
        guides = [d for d in kb.documents if d.type == "guide"]
        auto = [d for d in guides if d.meta.get("reponses_auto") is True]
        self.add("Base de connaissances", "guides du support", OK if guides else WARN,
                 f"{len(guides)} guide(s), dont {len(auto)} validé(s) pour les réponses "
                 "automatiques" if guides else "aucun : le Support ne répond jamais seul")

    def ai(self) -> None:
        llm = self.rt.llm
        client = getattr(llm, "client", None)
        if client is None:
            self.add("IA", "API Anthropic", WARN, "client non configuré")
            return
        for label, model in (("modèle de tri", self.rt.settings.triage_model),
                             ("modèle de rédaction", self.rt.settings.writing_model)):
            self.probe("IA", label, lambda m=model: f"{client.models.retrieve(m).id} accessible")

    def mailboxes(self) -> None:
        if not self.rt.connectors:
            self.add("Mails", "boîtes", FAIL, "aucune boîte dans config/mailboxes.yaml")
        for addr, conn in self.rt.connectors.items():
            self.probe("Mails", addr, conn.check)

    def sites(self) -> None:
        for site in self.rt.org.sites:
            conn = self.rt.web_connectors.get(site.url)
            if not site.actif:
                self.add("Sites", site.nom, OK, "inactif (ignoré)")
            elif conn is None:
                self.add("Sites", site.nom, FAIL, f"technologie « {site.technologie} » à préciser")
            else:
                self.probe("Sites", site.nom, conn.check)
