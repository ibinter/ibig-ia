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
    status: Mapped[str] = mapped_column(String(30), default="nouveau")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
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
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
