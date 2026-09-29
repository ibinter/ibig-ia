"""Configuration de l'agent : variables d'environnement + fichiers YAML de config/."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IBIG_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./ibig_agent.db"
    triage_model: str = "claude-haiku-4-5"
    writing_model: str = "claude-opus-5-5"
    writing_effort: str = "medium"  # low | medium | high | xhigh | max
    monthly_ai_budget_usd: float = 150.0
    budget_alert_ratio: float = 0.8
    # Clé de signature des sessions du tableau de bord (longue et aléatoire).
    secret_key: str = ""
    dashboard_url: str = "http://localhost:8000"
    # Boîte (déclarée dans mailboxes.yaml) qui envoie les notifications aux valideurs.
    notification_mailbox: str = ""
    timezone: str = "Africa/Abidjan"
    config_dir: Path = Path("./config")
    knowledge_dir: Path = Path("./knowledge")
    mail_poll_minutes: int = 5
    daily_report_hour: int = 8
    approval_timeout_hours: int = 24
    # Veille : heures ouvrées (lundi–vendredi), cible de réponse, seuils de pic
    business_open_hour: int = 8
    business_close_hour: int = 18
    response_target_hours: float = 2.0
    spike_min_messages: int = 10
    spike_factor: float = 3.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


@dataclass(frozen=True)
class Pole:
    code: str
    nom: str
    activite: str = ""
    cibles: str = ""
    valideur: str = ""
    suppleant: str = ""


@dataclass(frozen=True)
class Mailbox:
    adresse: str
    hebergeur: str  # "lws" ou "gmail"
    pole: str
    responsable: str = ""
    signature: str = ""
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 465
    password_env: str = ""
    gmail_token_env: str = ""

    def secret(self) -> str:
        """Lit le secret de la boîte dans l'environnement (alimenté par le coffre)."""
        name = self.password_env or self.gmail_token_env
        value = os.environ.get(name, "") if name else ""
        if not value:
            raise RuntimeError(f"Secret manquant pour {self.adresse} (variable {name or '?'})")
        return value


@dataclass(frozen=True)
class Site:
    nom: str
    url: str
    pole: str
    technologie: str  # wordpress | php | statique | a_preciser
    articles_par_mois: int = 2
    actif: bool = False
    wp_user: str = ""
    wp_password_env: str = ""
    endpoint_url: str = ""
    secret_env: str = ""
    export_dir: str = "./exports/articles"

    def secret(self) -> str:
        name = self.wp_password_env or self.secret_env
        value = os.environ.get(name, "") if name else ""
        if not value:
            raise RuntimeError(f"Secret manquant pour {self.url} (variable {name or '?'})")
        return value


@dataclass(frozen=True)
class SocialAccount:
    reseau: str
    compte: str
    pole: str
    publication_auto: bool


@dataclass
class OrgConfig:
    poles: list[Pole] = field(default_factory=list)
    mailboxes: list[Mailbox] = field(default_factory=list)
    social_accounts: list[SocialAccount] = field(default_factory=list)
    declinaison: dict[str, str] = field(default_factory=dict)
    sites: list[Site] = field(default_factory=list)

    @property
    def pole_codes(self) -> list[str]:
        return [p.code for p in self.poles]

    def pole(self, code: str) -> Pole | None:
        return next((p for p in self.poles if p.code == code), None)

    def poles_of(self, email: str) -> list[str]:
        """Pôles dont cette personne est valideur ou suppléant."""
        email = email.lower()
        return [p.code for p in self.poles
                if email in (p.valideur.lower(), p.suppleant.lower()) and email]

    def site(self, url: str) -> Site | None:
        return next((s for s in self.sites if s.url == url), None)

    def mailbox(self, adresse: str) -> Mailbox | None:
        return next((m for m in self.mailboxes if m.adresse == adresse), None)


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_org_config(config_dir: Path | None = None) -> OrgConfig:
    config_dir = config_dir or get_settings().config_dir
    poles = [Pole(**p) for p in _read_yaml(config_dir / "poles.yaml").get("poles", [])]
    mailboxes = [Mailbox(**m) for m in _read_yaml(config_dir / "mailboxes.yaml").get("mailboxes", [])]
    canaux = _read_yaml(config_dir / "canaux.yaml")
    accounts = [SocialAccount(**a) for a in canaux.get("comptes_sociaux", [])]
    return OrgConfig(
        poles=poles,
        mailboxes=mailboxes,
        social_accounts=accounts,
        declinaison=canaux.get("declinaison", {}),
        sites=[Site(**s) for s in _read_yaml(config_dir / "sites.yaml").get("sites", [])],
    )
