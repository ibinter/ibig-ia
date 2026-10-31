"""Page « Agents IA » : les agents du cahier des charges (section 6), leur mission, leur
état et les actions qu'on peut leur demander tout de suite depuis le tableau de bord."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..db import JournalEntry, utcnow
from ..governance import AGENT_AUTONOMY
from ..runtime import Runtime
from ..scheduler import next_monday, this_monday


@dataclass
class AgentAction:
    key: str
    label: str
    icon: str
    enabled: bool = True
    reason: str = ""


@dataclass
class AgentCard:
    key: str
    name: str
    icon: str
    color: str
    mission: str
    does: list[str]
    when: str
    ready: bool
    status: str
    actions: list[AgentAction] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    last: datetime | None = None
    count_30d: int = 0

    @property
    def autonomy(self) -> str:
        return {"aucune": "N'agit jamais seul : il rend compte et alerte",
                "lecture": "Lecture seule : il observe et alerte",
                "validation": "Propose, un humain valide",
                "automatique": "Agit seul sur les cas simples, propose le reste"}.get(
            AGENT_AUTONOMY.get({"whatsapp": "messagerie"}.get(self.key, self.key), "aucune"), "")


# Actions lancées en arrière-plan : (fonction, message affiché après le lancement)
def runners(rt: Runtime) -> dict[str, tuple]:
    tz = rt.settings.timezone
    return {
        "plan": (lambda: rt.planner.run(this_monday(tz)),
                 "Plan de la semaine en préparation : il apparaîtra dans Rapports."),
        "rapport": (rt.chef.run_daily, "Rapport en préparation : il apparaîtra dans Rapports."),
        "revue": (rt.revue.run, "Revue mensuelle en préparation : elle apparaîtra dans Rapports."),
        "releve": (rt.messagerie.poll, ("Relève des boîtes lancée : les mails traités "
                   "apparaîtront dans le Journal et les réponses dans Validations.")),
        "calendrier": (lambda: rt.communication.weekly_calendar(next_monday(tz=tz)),
                       ("Calendrier de la semaine en préparation : les publications "
                        "arriveront dans Validations d'ici quelques minutes.")),
        "articles": (rt.contenus_web.run, ("Articles en rédaction : ils arriveront dans "
                     "Validations d'ici quelques minutes.")),
        "qualifier": (rt.commercial.qualify_new, ("Qualification des nouveaux prospects "
                      "lancée : scores visibles dans Prospects.")),
        "relances": (rt.commercial.run, ("Relances du jour en préparation : elles "
                     "arriveront dans Validations.")),
        "veille": (rt.veille.check_alerts, ("Vérification lancée : les alertes éventuelles "
                   "apparaîtront dans Indicateurs.")),
        "indicateurs": (rt.veille.weekly_report, ("Bilan des indicateurs en préparation : "
                        "il apparaîtra dans Rapports.")),
    }


def agent_cards(rt: Runtime, now: datetime | None = None) -> list[AgentCard]:
    now = now or utcnow()
    org, kb = rt.org, rt.kb
    mails = bool(rt.connectors)
    ready_poles = [p.code for p in org.poles if p.code != "GROUPE"
                   and rt.communication.pole_ready(p.code)]
    sites_ready = [s for s in org.sites if rt.contenus_web.why_skipped(s) is None]
    guides = [d for d in kb.documents if d.type == "guide"]
    sara = len(rt.settings.sara_api_key) >= 32
    no_mail = "aucune boîte mail raccordée"

    cards = [
        AgentCard(
            "chef", "Agent chef", "bot", "blue",
            "Coordonne les agents, suit les validations en retard et rend compte à la direction.",
            ["Plan de la semaine chaque lundi, d'après vos objectifs",
             "Rapport quotidien à 8 h (tableau de bord et mail)",
             "Alerte les valideurs quand une proposition attend",
             "Revue mensuelle : bilan, erreurs, pistes d'amélioration"],
            "Lundi 7 h 30 (plan) · chaque jour à 8 h · revue le 1er du mois",
            True, "actif",
            [AgentAction("plan", "Plan de la semaine", "calendar"),
             AgentAction("rapport", "Rapport du jour", "file"),
             AgentAction("revue", "Lancer la revue mensuelle", "chart")],
            [("/objectifs", "Donner des objectifs"), ("/rapports", "Rapports")]),
        AgentCard(
            "messagerie", "Agent Messagerie", "mail", "violet",
            "Lit toutes les boîtes mail, trie chaque message et prépare les réponses.",
            ["Classe par pôle, catégorie et urgence",
             "Répond seul aux questions de la FAQ, envoie les accusés de réception",
             "Prépare les autres réponses pour validation",
             "Signale les messages piégés, transfère les plaintes à la direction"],
            f"Toutes les {rt.settings.mail_poll_minutes} minutes",
            mails, f"{len(rt.connectors)} boîte(s) raccordée(s)" if mails else no_mail,
            [AgentAction("releve", "Relever les mails maintenant", "inbox", mails,
                         "" if mails else no_mail)],
            [("/essai", "Essayer sur un message"), ("/validations", "Validations")]),
        AgentCard(
            "communication", "Agent Communication", "share", "amber",
            "Prépare le calendrier éditorial et les publications des réseaux sociaux.",
            ["Chaque lundi : 3 sujets par pôle pour la semaine suivante",
             "Chaque sujet réécrit pour chaque réseau (Facebook, LinkedIn, Instagram, TikTok…)",
             "Brief visuel pour chaque publication",
             "Publication après validation ; à la main pour les groupes et TikTok",
             "Campagnes mail (newsletters) envoyées par Brevo après validation"],
            "Chaque lundi à 7 h",
            True, (f"calendrier prêt pour : {', '.join(ready_poles)}" if ready_poles
                   else "publications à la demande ; calendrier dès qu'une fiche pôle "
                        "sera complète"),
            [AgentAction("calendrier", "Préparer le calendrier de la semaine", "calendar",
                         bool(ready_poles), "" if ready_poles else "fiches pôles à compléter")],
            [("#publication", "Rédiger une publication maintenant"),
             ("/campagnes", "Campagnes mail")]),
        AgentCard(
            "contenus_web", "Agent Contenus web", "globe", "green",
            "Écrit des articles de blog et des pages produits, pensés pour le référencement.",
            ["2 articles par mois et par site",
             "Pages produits des solutions et formations du catalogue, à la demande",
             "Un mot-clé, une question réelle, des liens vers vos offres",
             "Déposé en brouillon sur le site après validation (WordPress, PHP ou fichier)"],
            "Le 1er et le 15 du mois",
            bool(sites_ready),
            (f"sites prêts : {', '.join(s.nom for s in sites_ready)}" if sites_ready
             else "aucun site prêt (site inactif ou fiche pôle à compléter)"),
            [AgentAction("articles", "Rédiger les articles maintenant", "edit",
                         bool(sites_ready), "" if sites_ready else "aucun site prêt")],
            [("#contenu-web", "Article ou page produit sur un sujet choisi")]),
        AgentCard(
            "commercial", "Agent Commercial", "target", "red",
            "Qualifie les prospects et prépare les relances pour ne perdre aucune affaire.",
            ["Note chaque prospect de 0 à 100 (froid, tiède, chaud)",
             "Alerte le commercial pour un prospect chaud",
             "Relances à J+3, J+7 et J+14 sans réponse (à valider)",
             "Suivi des essais gratuits et des démonstrations"],
            "Toutes les 15 min · relances en semaine aux heures ouvrées",
            True, "actif" if mails else f"actif, en attente de prospects ({no_mail})",
            [AgentAction("qualifier", "Qualifier les nouveaux prospects", "target"),
             AgentAction("relances", "Préparer les relances du jour", "mail")],
            [("/prospects", "Prospects")]),
        AgentCard(
            "support", "Agent Support et SARA", "life", "blue",
            "Répond aux questions d'utilisation des solutions, par mail et sur le chat SARA.",
            ["Réponse construite uniquement à partir des guides utilisateurs",
             "Chaque citation vérifiée mot pour mot",
             "Transmet un ticket à un humain quand il ne sait pas"],
            "À chaque question reçue",
            bool(guides),
            (f"{len(guides)} guide(s)" if guides else "aucun guide : il transmet tout "
             "à un humain") + (" · API SARA prête" if sara else " · API SARA sans clé"),
            [], [("/tickets", "Tickets du support")]),
        AgentCard(
            "veille", "Agent Veille", "chart", "violet",
            "Surveille les indicateurs et prévient en cas de problème.",
            ["Pic inhabituel de messages",
             "Clients mécontents",
             "Mails sans réponse au-delà de 2 heures ouvrées",
             "Bilan des indicateurs chaque lundi"],
            "Toutes les heures · bilan le lundi à 8 h 15",
            True, "actif",
            [AgentAction("veille", "Vérifier maintenant", "shield"),
             AgentAction("indicateurs", "Bilan des indicateurs", "chart")],
            [("/indicateurs", "Indicateurs")]),
        AgentCard(
            "whatsapp", "WhatsApp Business", "message", "green",
            "Les messages WhatsApp traités comme les mails, avec l'API officielle de Meta.",
            ["Même tri et mêmes règles de validation que les mails",
             "Fenêtre de 24 h et désinscription « STOP » respectées"],
            "À chaque message reçu",
            bool(org.whatsapp),
            f"{len(org.whatsapp)} numéro(s)" if org.whatsapp
            else "aucun numéro raccordé (migration vers l'API de Meta)"),
    ]

    names = {"chef": ["chef"], "messagerie": ["messagerie"], "whatsapp": ["messagerie"],
             "communication": ["communication"], "contenus_web": ["contenus_web"],
             "commercial": ["commercial"], "support": ["support"], "veille": ["veille"]}
    since = now - timedelta(days=30)
    with rt.sessions() as s:
        for c in cards:
            q = select(func.max(JournalEntry.created_at), func.count()).where(
                JournalEntry.agent.in_(names[c.key]))
            if c.key == "whatsapp":
                q = q.where(JournalEntry.channel == "whatsapp")
            elif c.key == "messagerie":
                q = q.where(JournalEntry.channel != "whatsapp")
            c.last = s.execute(q).one()[0]
            c.count_30d = s.scalar(q.with_only_columns(func.count()).where(
                JournalEntry.created_at >= since)) or 0
    return cards
