"""Persistance : journal, file de validation, bouton d'arrêt, prospects, usage IA."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
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
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    model: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


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
