"""Configuration saisie dans le tableau de bord, appliquée à chaud au runtime.

- Base de connaissances : les documents modifiés remplacent les fichiers livrés.
- Boîtes mail : ajoutées, testées et retirées sans toucher au serveur ; mots de passe
  chiffrés dans le coffre (vault).
- Consignes de la direction : lues par les agents qui rédigent.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Mailbox, Settings
from .db import Directive, KnowledgeEdit, MailboxAccount, ServiceSetting, utcnow
from .knowledge import KnowledgeBase
from .vault import VaultError, decrypt, encrypt

# Hébergeur LWS : serveurs par défaut (modifiables boîte par boîte)
LWS_DEFAULT_HOST = "mail.lws-hosting.com"


def knowledge_overrides(sessions: sessionmaker[Session]) -> dict[str, str]:
    with sessions() as s:
        return {e.path: e.content for e in s.scalars(select(KnowledgeEdit)).all()}


def reload_knowledge(rt) -> None:
    rt.kb = KnowledgeBase(rt.settings.knowledge_dir, knowledge_overrides(rt.sessions))


def to_mailbox(row: MailboxAccount, settings: Settings) -> Mailbox:
    secret = ""
    if row.secret_enc:
        try:
            secret = decrypt(settings.secret_key, row.secret_enc)
        except VaultError:
            secret = ""
    return Mailbox(adresse=row.adresse, hebergeur=row.hebergeur, pole=row.pole,
                   responsable=row.responsable, signature=row.signature,
                   imap_host=row.imap_host, imap_port=row.imap_port,
                   smtp_host=row.smtp_host, smtp_port=row.smtp_port, secret_value=secret)


def dashboard_mailboxes(sessions: sessionmaker[Session], settings: Settings) -> list[Mailbox]:
    with sessions() as s:
        rows = s.scalars(select(MailboxAccount).where(MailboxAccount.active.is_(True))).all()
    return [to_mailbox(r, settings) for r in rows]


def sync_mailboxes(rt) -> None:
    """Remplace les boîtes du tableau de bord par leur état en base (en place : les
    exécuteurs d'envoi partagent le même dictionnaire de connecteurs)."""
    for addr in list(rt.dashboard_boxes):
        rt.connectors.pop(addr, None)
    rt.org.mailboxes[:] = [m for m in rt.org.mailboxes if m.adresse not in rt.dashboard_boxes]
    rt.dashboard_boxes.clear()
    yaml_addresses = {m.adresse for m in rt.org.mailboxes}
    for box in dashboard_mailboxes(rt.sessions, rt.settings):
        if box.adresse in yaml_addresses:
            continue  # la configuration du serveur (mailboxes.yaml) garde la priorité
        rt.org.mailboxes.append(box)
        rt.connectors[box.adresse] = rt.connector_factory(box)
        rt.dashboard_boxes.add(box.adresse)


def with_lws_defaults(box: Mailbox) -> Mailbox:
    if box.hebergeur != "lws":
        return box
    return replace(box, imap_host=box.imap_host or LWS_DEFAULT_HOST,
                   smtp_host=box.smtp_host or LWS_DEFAULT_HOST)


def active_directives(sessions: sessionmaker[Session], pole: str = "",
                      now: datetime | None = None) -> list[Directive]:
    now = now or utcnow()
    q = select(Directive).where(Directive.active.is_(True),
                                or_(Directive.until.is_(None), Directive.until >= now))
    if pole:
        q = q.where(Directive.pole.in_(("", pole)))
    with sessions() as s:
        return list(s.scalars(q.order_by(Directive.created_at)).all())


def directives_text(sessions: sessionmaker[Session], pole: str = "") -> str:
    """Consignes en cours, à insérer dans les instructions des agents qui rédigent."""
    items = active_directives(sessions, pole)
    if not items:
        return ""
    lines = [f"- {'[' + d.pole + '] ' if d.pole else ''}{d.text}" for d in items]
    return ("Consignes de la direction en cours (à suivre, dans le respect des règles "
            "ci-dessus et de la base de connaissances) :\n" + "\n".join(lines))


# ------------------------------------------------------------------ services extérieurs
def service_value(sessions: sessionmaker[Session], settings: Settings, name: str) -> str:
    """Valeur d'un réglage de service (déchiffrée si c'est un secret), vide si absent."""
    with sessions() as s:
        row = s.get(ServiceSetting, name)
    if row is None or not row.value:
        return ""
    if not row.secret:
        return row.value
    try:
        return decrypt(settings.secret_key, row.value)
    except VaultError:
        return ""


def set_service_value(sessions: sessionmaker[Session], settings: Settings, name: str,
                      value: str, by: str, secret: bool = False) -> None:
    stored = encrypt(settings.secret_key, value) if secret and value else value
    with sessions() as s:
        s.merge(ServiceSetting(name=name, value=stored, secret=secret, updated_by=by,
                               updated_at=utcnow()))
        s.commit()
