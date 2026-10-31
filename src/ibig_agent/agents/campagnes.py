"""Campagnes mail (sections 9 et 12) : l'agent Communication rédige, un humain valide
(niveau 2), puis le service d'emailing (Brevo) envoie à une liste gérée dans Brevo.

Même règle que partout : uniquement ce qui figure dans la base de connaissances ; un
passage manquant est marqué « À COMPLÉTER », ce qui bloque la validation.
"""

from __future__ import annotations

from collections.abc import Callable

from ..channels.web import sanitize_html
from ..config import OrgConfig
from ..governance import ActionRequest, Governor
from ..knowledge import KnowledgeBase
from ..llm import LLM

AGENT = "communication"
UNSUBSCRIBE_FOOTER = (
    '<hr><p style="font-size:12px;color:#667085">Vous recevez ce message car vous êtes '
    "inscrit à la lettre d'information d'IBIG SARL. "
    '<a href="{{ unsubscribe }}">Se désinscrire</a></p>'
)


def campaign_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "objet": {"type": "string"},
            "pre_entete": {"type": "string"},
            "contenu_html": {"type": "string"},
        },
        "required": ["objet", "pre_entete", "contenu_html"],
        "additionalProperties": False,
    }


class CampaignWriter:
    def __init__(self, org: OrgConfig, kb: KnowledgeBase, llm: LLM, governor: Governor,
                 directives: Callable[[str], str] | None = None) -> None:
        self.org, self.kb, self.llm, self.gov = org, kb, llm, governor
        self.directives = directives or (lambda pole: "")

    def draft(self, pole_code: str, sujet: str, list_id: int, list_name: str,
              sender_name: str, sender_email: str) -> int | None:
        pole = self.org.pole(pole_code)
        system = (
            f"Tu es l'agent Communication d'IBIG SARL, pôle {pole.nom if pole else pole_code}. "
            "Tu rédiges une campagne mail (newsletter) en français.\n"
            "Règles :\n"
            "- N'utilise QUE les informations de la base de connaissances : aucun prix, "
            "contact, lien, date ou promesse absent de la base. Information manquante : "
            "[À COMPLÉTER : ...].\n"
            "- objet : 60 caractères maximum, sans majuscules criardes ni points "
            "d'exclamation multiples (filtres anti-spam).\n"
            "- pre_entete : 90 caractères maximum.\n"
            "- contenu_html : 150 à 350 mots, uniquement <h2>, <h3>, <p>, <ul>, <li>, "
            "<strong>, <em>, <a href> ; un seul appel à l'action clair. Pas de lien de "
            "désinscription (il est ajouté automatiquement).\n\n"
            f"{self.directives(pole_code)}\n\n"
            f"{self.kb.style_examples(pole_code)}\n\n"
            f"Base de connaissances :\n{self.kb.context_for(pole_code, sujet)}"
        )
        data = self.llm.structured("campaign.mail", "writing", system,
                                   f"Sujet de la campagne : {sujet}", campaign_schema(),
                                   max_tokens=4000)
        html = sanitize_html(data["contenu_html"])
        alerts = [str(i) for i in self.kb.verify_facts(f"{data['objet']}\n{html}")]
        out = self.gov.submit(ActionRequest(
            agent=AGENT, action_type="campaign.mail", channel="emailing",
            account=f"Brevo · {list_name}", pole=pole_code,
            title=f"Campagne mail · {data['objet'][:120]}",
            payload={"titre": data["objet"][:250], "subject": data["objet"][:250],
                     "pre_entete": data["pre_entete"][:150], "contenu_html": html,
                     "liste_id": list_id, "liste": list_name,
                     "expediteur": sender_name, "expediteur_mail": sender_email,
                     "alertes": alerts},
        ))
        return out.pending_id
