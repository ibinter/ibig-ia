"""Persistance : journal, file de validation, bouton d'arrêt, prospects, usage IA."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class JournalEntry(Base):
    """Journal complet : quoi, quand, sur quel compte, validé par qui (section 12)."""

    __tablename__ = "journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    agent: Mapped[str] = mapped_column(String(40))
    action_type: Mapped[str] = mapped_column(String(60))
    level: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(40))
    account: Mapped[str] = mapped_column(String(200), default="")
    pole: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    decided_by: Mapped[str] = mapped_column(String(120), default="")


class PendingAction(Base):
    """Action en attente de décision humaine (niveau 2) ou préparée pour un humain (niveau 3)."""

    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    agent: Mapped[str] = mapped_column(String(40))
    action_type: Mapped[str] = mapped_column(String(60))
    level: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(40))
    account: Mapped[str] = mapped_column(String(200), default="")
    pole: Mapped[str] = mapped_column(String(40), default="")
    title: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # pending | approved | rejected | executed | failed | prepared (niveau 3, pour un humain)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by: Mapped[str] = mapped_column(String(120), default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    flagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")


class ChannelState(Base):
    """Bouton d'arrêt : un canal (ou « * » pour tout le périmètre) peut être suspendu."""

    __tablename__ = "channel_state"

    channel: Mapped[str] = mapped_column(String(40), primary_key=True)
    stopped: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessedMessage(Base):
    """Mail ou message déjà traité (garantit R-01 : aucun mail manqué, aucun doublon)."""

    __tablename__ = "processed_messages"
    __table_args__ = (UniqueConstraint("mailbox", "message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mailbox: Mapped[str] = mapped_column(String(200), index=True)
    message_id: Mapped[str] = mapped_column(String(500))
    sender: Mapped[str] = mapped_column(String(300), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    pole: Mapped[str] = mapped_column(String(40), default="")
    category: Mapped[str] = mapped_column(String(40), default="")
    urgency: Mapped[str] = mapped_column(String(20), default="")
    sentiment: Mapped[str] = mapped_column(String(20), default="")
    decision: Mapped[str] = mapped_column(String(40), default="")
    # Résumé du tri, sans nom ni coordonnées (sert aux « questions réelles » des articles)
    summary: Mapped[str] = mapped_column(Text, default="")
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Prospect(Base):
    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    pole: Mapped[str] = mapped_column(String(40), default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    need: Mapped[str] = mapped_column(Text, default="")
    # nouveau | qualifie | en_discussion | relance | sans_suite | gagne | perdu
    status: Mapped[str] = mapped_column(String(30), default="nouveau")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Qualification (agent Commercial)
    score: Mapped[int] = mapped_column(Integer, default=0)
    temperature: Mapped[str] = mapped_column(String(10), default="")  # chaud | tiede | froid
    solution: Mapped[str] = mapped_column(String(200), default="")
    next_step: Mapped[str] = mapped_column(Text, default="")
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Suivi des échanges et des relances
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                              nullable=True)
    followups_sent: Mapped[int] = mapped_column(Integer, default=0)
    stop_followups: Mapped[bool] = mapped_column(Boolean, default=False)
    # Essais et démonstrations
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                           nullable=True)
    trial_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                               nullable=True)
    demo_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    demo_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                              nullable=True)


class Ticket(Base):
    """Question que l'agent Support n'a pas pu traiter seul : un humain prend le relais."""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 index=True)
    channel: Mapped[str] = mapped_column(String(20))  # mail | sara | whatsapp
    contact: Mapped[str] = mapped_column(String(300), default="")
    pole: Mapped[str] = mapped_column(String(40), default="")
    question: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    draft: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list] = mapped_column(JSON, default=list)
    ref: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="ouvert", index=True)
    resolved_by: Mapped[str] = mapped_column(String(300), default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WhatsAppContact(Base):
    """Contact WhatsApp : fenêtre de 24 h et désinscription (section 10)."""

    __tablename__ = "whatsapp_contacts"

    wa_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # numéro international
    name: Mapped[str] = mapped_column(String(200), default="")
    phone_number_id: Mapped[str] = mapped_column(String(40), default="")
    pole: Mapped[str] = mapped_column(String(40), default="")
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    last_ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    # Consentement explicite aux messages promotionnels (jamais déduit d'un simple message)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    """Compte nominatif du tableau de bord.

    Rôles : admin (tout, dont les comptes), direction (niveau 3 et tous les pôles),
    valideur (niveau 2 des pôles dont il est valideur ou suppléant dans poles.yaml).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(300))
    # Numéro WhatsApp (format international sans +) pour valider par WhatsApp
    phone: Mapped[str] = mapped_column(String(32), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Sessions ouvertes avant cette date refusées (mot de passe changé, « déconnecter partout »)
    sessions_valid_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                                 nullable=True)


class AIUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    model: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class KnowledgeEdit(Base):
    """Document de la base de connaissances modifié depuis le tableau de bord.

    Il remplace le fichier du même chemin (ou crée un document) sans toucher au dépôt :
    les mises à jour du logiciel n'écrasent jamais le travail d'IBIG.
    """

    __tablename__ = "knowledge_edits"

    path: Mapped[str] = mapped_column(String(300), primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MailboxAccount(Base):
    """Boîte mail raccordée depuis le tableau de bord ; secret chiffré (coffre)."""

    __tablename__ = "mailbox_accounts"

    adresse: Mapped[str] = mapped_column(String(300), primary_key=True)
    hebergeur: Mapped[str] = mapped_column(String(20))
    pole: Mapped[str] = mapped_column(String(40))
    responsable: Mapped[str] = mapped_column(String(300), default="")
    signature: Mapped[str] = mapped_column(Text, default="")
    imap_host: Mapped[str] = mapped_column(String(200), default="")
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    smtp_host: Mapped[str] = mapped_column(String(200), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=465)
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Directive(Base):
    """Objectif ou consigne donné par la direction à l'agent chef (section 5)."""

    __tablename__ = "directives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    text: Mapped[str] = mapped_column(Text)
    pole: Mapped[str] = mapped_column(String(40), default="")  # vide : tout le groupe
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(300), default="")


class ServiceSetting(Base):
    """Réglage d'un service extérieur saisi au tableau de bord (clé chiffrée ou valeur)."""

    __tablename__ = "service_settings"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScheduledPost(Base):
    """Publication validée, programmée pour publication automatique à sa date."""

    __tablename__ = "scheduled_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pending_id: Mapped[int] = mapped_column(Integer, index=True)
    reseau: Mapped[str] = mapped_column(String(40))
    compte: Mapped[str] = mapped_column(String(200))
    pole: Mapped[str] = mapped_column(String(40), default="")
    texte: Mapped[str] = mapped_column(Text)
    titre: Mapped[str] = mapped_column(String(300), default="")
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="programme")  # publie | echec
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SocialComment(Base):
    """Commentaire relevé sur une page Facebook (veille e-réputation, section 6)."""

    __tablename__ = "social_comments"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)  # identifiant Meta
    reseau: Mapped[str] = mapped_column(String(40))
    compte: Mapped[str] = mapped_column(String(200))
    pole: Mapped[str] = mapped_column(String(40), default="")
    post_id: Mapped[str] = mapped_column(String(120), default="")
    author: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[str] = mapped_column(String(20), default="neutre")  # positif|neutre|negatif
    summary: Mapped[str] = mapped_column(String(300), default="")
    posted_at: Mapped[str] = mapped_column(String(40), default="")
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                              index=True)


class KbEmbedding(Base):
    """Vecteur d'un passage de la base de connaissances (recherche par le sens).

    Sur PostgreSQL avec pgvector, la migration ajoute la colonne `vec vector(1024)` utilisée
    pour la recherche ; `embedding` (JSON) sert partout ailleurs."""

    __tablename__ = "kb_embeddings"

    id: Mapped[str] = mapped_column(String(300), primary_key=True)  # identifiant du passage
    content_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(40))
    embedding: Mapped[list] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MediaAsset(Base):
    """Photo générée par IA pour une publication (fond du visuel aux couleurs IBIG)."""

    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pending_id: Mapped[int] = mapped_column(Integer, index=True)
    mime: Mapped[str] = mapped_column(String(40), default="image/jpeg")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    prompt: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WhatsAppAccount(Base):
    """Numéro WhatsApp Business raccordé depuis le tableau de bord ; jeton chiffré."""

    __tablename__ = "whatsapp_accounts"

    phone_number_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nom: Mapped[str] = mapped_column(String(200))
    numero: Mapped[str] = mapped_column(String(32))
    pole: Mapped[str] = mapped_column(String(40))
    token_enc: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SocialAccountRow(Base):
    """Compte de réseau social ou chaîne WhatsApp ajouté depuis le tableau de bord."""

    __tablename__ = "social_account_rows"
    __table_args__ = (UniqueConstraint("reseau", "compte"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reseau: Mapped[str] = mapped_column(String(40))
    compte: Mapped[str] = mapped_column(String(200))
    pole: Mapped[str] = mapped_column(String(40))
    publication_auto: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobStatus(Base):
    """Santé d'une tâche planifiée : dernière exécution, dernier échec, échecs de suite."""

    __tablename__ = "job_status"

    job_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    last_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ok: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                           nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_result: Mapped[str] = mapped_column(String(500), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    running: Mapped[bool] = mapped_column(Boolean, default=False)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)        # échecs de suite
    total_failures: Mapped[int] = mapped_column(Integer, default=0)
    alerted: Mapped[bool] = mapped_column(Boolean, default=False)


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Autogénération Alembic : la colonne pgvector (hors modèle) n'est pas une différence."""
    return not (type_ == "column" and reflected and compare_to is None
                and name == "vec" and obj.table.name == "kb_embeddings")


def make_engine(url: str) -> Engine:
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, **kwargs)


def init_db(engine: Engine) -> sessionmaker[Session]:
    """Crée le schéma directement : réservé aux tests unitaires isolés."""
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def open_db(engine: Engine) -> sessionmaker[Session]:
    """Applique les migrations en attente puis ouvre la base (usage normal)."""
    from .migrate import upgrade

    upgrade(engine)
    return sessionmaker(engine, expire_on_commit=False)
