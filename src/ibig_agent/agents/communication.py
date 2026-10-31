"""Agent Communication : calendrier éditorial des pôles, textes, déclinaison par réseau.

Chaque lundi, il propose les publications de la semaine pour chaque pôle dont la fiche
est complète. Chaque publication part en validation en un clic (niveau 2) ; les comptes
sans publication automatique (groupes Facebook, TikTok, chaînes WhatsApp) deviennent des
« publications à faire à la main », préparées par l'agent.

Un pôle dont la fiche n'est pas complétée est ignoré : sans source de vérité, pas de
contenu (section 7).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from ..config import OrgConfig, SocialAccount
from ..governance import ActionRequest, Governor
from ..knowledge import KnowledgeBase
from ..llm import LLM, LLMError

AGENT = "communication"

NETWORK_CHANNEL = {
    "facebook_page": "facebook",
    "facebook_groupe": "facebook",
    "instagram": "instagram",
    "threads": "threads",
    "linkedin": "linkedin",
    "tiktok": "tiktok",
    "x": "x",
    "whatsapp_chaine": "whatsapp",
}

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def calendar_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "publications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "jour": {"type": "string", "enum": JOURS},
                        "compte_index": {"type": "integer"},
                        "sujet": {"type": "string"},
                        "texte": {"type": "string"},
                        "brief_visuel": {"type": "string"},
                    },
                    "required": ["jour", "compte_index", "sujet", "texte", "brief_visuel"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["publications"],
        "additionalProperties": False,
    }


def post_schema() -> dict:
    return {
        "type": "object",
        "properties": {"texte": {"type": "string"}, "brief_visuel": {"type": "string"}},
        "required": ["texte", "brief_visuel"],
        "additionalProperties": False,
    }


@dataclass
class CalendarResult:
    poles_traites: list[str]
    poles_ignores: dict[str, str]
    publications: int


class CommunicationAgent:
    def __init__(self, org: OrgConfig, kb: KnowledgeBase, llm: LLM, governor: Governor,
                 posts_per_pole: int = 3,
                 directives: Callable[[str], str] | None = None) -> None:
        self.directives = directives or (lambda pole: "")
        self.org = org
        self.kb = kb
        self.llm = llm
        self.gov = governor
        self.posts_per_pole = posts_per_pole

    def pole_ready(self, pole: str) -> bool:
        fiches = [d for d in self.kb.documents if d.type == "fiche_pole" and d.pole == pole]
        return bool(fiches) and all(d.meta.get("statut") != "a_completer" for d in fiches)

    def accounts_for(self, pole: str) -> list[SocialAccount]:
        return [a for a in self.org.social_accounts if a.pole == pole]

    def weekly_calendar(self, week_start: date) -> CalendarResult:
        result = CalendarResult([], {}, 0)
        for pole in self.org.poles:
            if pole.code == "GROUPE":
                continue
            accounts = self.accounts_for(pole.code) or self.accounts_for("GROUPE")
            if not self.pole_ready(pole.code):
                result.poles_ignores[pole.code] = "fiche pôle à compléter"
                continue
            if not accounts:
                result.poles_ignores[pole.code] = "aucun compte rattaché"
                continue
            try:
                result.publications += self._plan_pole(pole.code, accounts, week_start)
                result.poles_traites.append(pole.code)
            except LLMError as exc:
                result.poles_ignores[pole.code] = f"génération impossible : {exc}"
        return result

    def _plan_pole(self, pole_code: str, accounts: list[SocialAccount], week_start: date) -> int:
        pole = self.org.pole(pole_code)
        comptes = "\n".join(
            f"{i} : {a.reseau} « {a.compte} » — {self.org.declinaison.get(a.reseau, '')}"
            for i, a in enumerate(accounts)
        )
        system = (
            f"Tu es l'agent Communication d'IBIG SARL, pôle {pole.nom}.\n"
            "Tu prépares le calendrier éditorial de la semaine.\n"
            "Règles :\n"
            "- N'utilise QUE les informations de la base de connaissances. Aucun prix, "
            "contact, lien, date ou promesse qui n'y figure pas.\n"
            "- Un même sujet est réécrit pour chaque réseau selon sa règle de déclinaison.\n"
            "- Varie les sujets (offre, conseil pratique, témoignage, coulisses, question).\n"
            "- Respecte la liste des sujets interdits et des formulations à éviter.\n\n"
            f"{self.directives(pole_code)}\n\n"
            f"{self.kb.style_examples(pole_code)}\n\n"
            f"Base de connaissances :\n{self.kb.context_for(pole_code)}"
        )
        user = (
            f"Semaine du {week_start:%d/%m/%Y}. Propose {self.posts_per_pole} sujets, chacun "
            f"décliné sur les comptes pertinents parmi :\n{comptes}\n"
            "compte_index désigne le numéro du compte dans cette liste."
        )
        data = self.llm.structured("social.calendar", "writing", system, user,
                                   calendar_schema(), max_tokens=12000)
        count = 0
        for post in data["publications"]:
            idx = post["compte_index"]
            if not 0 <= idx < len(accounts):
                continue
            account = accounts[idx]
            day = week_start + timedelta(days=JOURS.index(post["jour"]))
            alerts = [str(i) for i in self.kb.verify_facts(post["texte"])]
            self.gov.submit(ActionRequest(
                agent=AGENT,
                action_type="social.post" if account.publication_auto else "social.manual_post",
                channel=NETWORK_CHANNEL.get(account.reseau, account.reseau),
                account=account.compte,
                pole=pole_code,
                title=f"{post['jour'].capitalize()} {day:%d/%m} · {account.reseau} · "
                      f"{post['sujet'][:100]}",
                payload={
                    "reseau": account.reseau, "compte": account.compte,
                    "date": day.isoformat(), "texte": post["texte"],
                    "brief_visuel": post["brief_visuel"], "alertes": alerts,
                    "publication_auto": account.publication_auto,
                },
            ))
            count += 1
        return count

    def write_post(self, pole_code: str, account: SocialAccount, sujet: str,
                   day: date) -> int | None:
        """Une publication à la demande (tableau de bord), soumise à validation.

        Contrairement au calendrier, elle est permise même si la fiche du pôle est
        incomplète : l'information manquante est marquée « À COMPLÉTER », ce qui bloque
        la validation tant qu'un humain ne l'a pas remplacée.
        """
        pole = self.org.pole(pole_code)
        system = (
            f"Tu es l'agent Communication d'IBIG SARL, pôle {pole.nom if pole else pole_code}.\n"
            f"Tu rédiges UNE publication pour {account.reseau} « {account.compte} ».\n"
            f"Règle de ce réseau : {self.org.declinaison.get(account.reseau, '')}\n"
            "Règles :\n"
            "- N'utilise QUE les informations de la base de connaissances. Aucun prix, "
            "contact, lien, date ou promesse qui n'y figure pas.\n"
            "- Si une information utile manque, écris [À COMPLÉTER : ...] à sa place.\n"
            "- Respecte la liste des sujets interdits et des formulations à éviter.\n"
            "- brief_visuel : description de l'image ou de la vidéo à produire.\n\n"
            f"{self.directives(pole_code)}\n\n"
            f"{self.kb.style_examples(pole_code, account.reseau)}\n\n"
            f"Base de connaissances :\n{self.kb.context_for(pole_code, sujet)}"
        )
        data = self.llm.structured("social.post", "writing", system,
                                   f"Sujet demandé : {sujet}", post_schema(), max_tokens=4000)
        alerts = [str(i) for i in self.kb.verify_facts(data["texte"])]
        out = self.gov.submit(ActionRequest(
            agent=AGENT,
            action_type="social.post" if account.publication_auto else "social.manual_post",
            channel=NETWORK_CHANNEL.get(account.reseau, account.reseau),
            account=account.compte, pole=pole_code,
            title=f"{JOURS[day.weekday()].capitalize()} {day:%d/%m} · {account.reseau} · "
                  f"{sujet[:100]}",
            payload={"reseau": account.reseau, "compte": account.compte,
                     "date": day.isoformat(), "texte": data["texte"],
                     "brief_visuel": data["brief_visuel"], "alertes": alerts,
                     "publication_auto": account.publication_auto},
        ))
        return out.pending_id
