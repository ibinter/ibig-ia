"""Accès au modèle d'IA (Claude, API Anthropic) avec maîtrise des coûts (section 16).

* modèle léger pour le tri des mails, modèle avancé pour la rédaction seulement ;
* plafond de dépense mensuel, alerte à 80 %, blocage au-delà ;
* chaque appel est comptabilisé dans la table ai_usage.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, Protocol

import anthropic
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db import AIUsage, JournalEntry, ServiceSetting, utcnow

# Prix publics en USD par million de jetons (entrée, sortie). À tenir à jour.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-fable-5-1": (10.0, 50.0),
}

# Modèles qui acceptent le repli serveur automatique en cas de refus (`fallbacks`).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
# Modèles qui acceptent le paramètre `effort`.
EFFORT_MODELS = FALLBACK_MODELS | {"claude-opus-4-8"}

ModelKind = Literal["triage", "writing"]


class LLMError(Exception):
    pass


class LLMRefusal(LLMError):
    pass


class BudgetExceeded(LLMError):
    pass


class LLMCreditExhausted(LLMError):
    """Crédit du compte Anthropic épuisé : rien ne sert de réessayer avant un rechargement."""


class LLM(Protocol):
    """Interface utilisée par les agents (permet de les tester sans appel réseau)."""

    def structured(self, purpose: str, kind: ModelKind, system: str, user: str,
                   schema: dict, max_tokens: int = 2000) -> dict: ...

    def write(self, purpose: str, system: str, user: str, max_tokens: int = 4000) -> str: ...


def cost_usd(model: str, usage: Any) -> float:
    price_in, price_out = PRICES.get(model, PRICES["claude-opus-5-5"])
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    tokens_in = usage.input_tokens + 1.25 * cache_write + 0.1 * cache_read
    return (tokens_in * price_in + usage.output_tokens * price_out) / 1_000_000


class ClaudeClient:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session],
                 client: anthropic.Anthropic | None = None) -> None:
        self.settings = settings
        self._sessions = session_factory
        # Identifiants lus dans l'environnement (ANTHROPIC_API_KEY), jamais dans le code.
        self.client = client or anthropic.Anthropic()

    # ---------------------------------------------------------------- budget
    def month_spend(self, now: datetime | None = None) -> float:
        now = now or utcnow()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self._sessions() as s:
            total = s.scalar(select(func.sum(AIUsage.cost_usd)).where(AIUsage.created_at >= start))
        return float(total or 0.0)

    def budget(self) -> float:
        """Plafond mensuel : celui de la page Services s'il existe, sinon l'environnement."""
        try:
            value = float(self._override("ia_budget").replace(",", "."))
        except ValueError:
            value = 0.0
        return value if value > 0 else self.settings.monthly_ai_budget_usd

    def _check_budget(self) -> None:
        budget = self.budget()
        spent = self.month_spend()
        if spent >= budget:
            raise BudgetExceeded(f"Plafond mensuel atteint ({spent:.2f} / {budget:.2f} USD)")
        if spent >= budget * self.settings.budget_alert_ratio:
            self._alert_once(spent, budget)

    def _alert_once(self, spent: float, budget: float) -> None:
        now = utcnow()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self._sessions() as s:
            already = s.scalar(
                select(func.count()).select_from(JournalEntry).where(
                    JournalEntry.action_type == "budget.alert", JournalEntry.created_at >= start
                )
            )
            if not already:
                s.add(JournalEntry(
                    agent="chef", action_type="budget.alert", level=1, channel="ia",
                    status="flagged",
                    summary=f"Dépense IA à {spent / budget:.0%} du plafond mensuel",
                    details={"spent_usd": round(spent, 2), "budget_usd": budget},
                ))
                s.commit()

    def _record(self, model: str, purpose: str, usage: Any) -> None:
        with self._sessions() as s:
            s.add(AIUsage(
                model=model, purpose=purpose, input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens, cost_usd=cost_usd(model, usage),
            ))
            s.commit()

    # ---------------------------------------------------------------- appels
    def _override(self, name: str) -> str:
        """Réglage choisi dans l'application (page Services), prioritaire sur l'environnement."""
        with self._sessions() as s:
            row = s.get(ServiceSetting, name)
        return (row.value or "").strip() if row else ""

    def _model(self, kind: ModelKind) -> str:
        chosen = self._override(f"ia_model_{kind}")
        if chosen in PRICES:
            return chosen
        return self.settings.triage_model if kind == "triage" else self.settings.writing_model

    def _call(self, purpose: str, model: str, system: str, user: str, max_tokens: int,
              output_config: dict) -> Any:
        self._check_budget()
        if model in EFFORT_MODELS:
            output_config = {**output_config, "effort": self.settings.writing_effort}
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            # Contexte stable (charte, fiche pôle…) en tête et mis en cache.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        if output_config:
            kwargs["output_config"] = output_config
        try:
            # Toujours en streaming : les longues réponses (import des sites, articles)
            # dépasseraient sinon la limite de 10 minutes d'une requête simple.
            if model in FALLBACK_MODELS:
                stream = self.client.beta.messages.stream(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                stream = self.client.messages.stream(**kwargs)
            with stream as s:
                response = s.get_final_message()
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Connexion à l'API impossible : {exc}") from exc
        except anthropic.APIStatusError as exc:
            if "credit balance" in str(exc.message).lower():
                raise LLMCreditExhausted("Crédit Anthropic épuisé : rechargez le compte") from exc
            raise LLMError(f"Erreur API {exc.status_code} : {exc.message}") from exc

        self._record(getattr(response, "model", model) or model, purpose, response.usage)
        if response.stop_reason == "refusal":
            raise LLMRefusal(f"Requête refusée par le modèle ({purpose})")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"Réponse tronquée ({purpose}) : augmenter max_tokens")
        return response

    @staticmethod
    def _text(response: Any) -> str:
        return "".join(b.text for b in response.content if b.type == "text")

    def structured(self, purpose: str, kind: ModelKind, system: str, user: str,
                   schema: dict, max_tokens: int = 2000) -> dict:
        response = self._call(
            purpose, self._model(kind), system, user, max_tokens,
            {"format": {"type": "json_schema", "schema": schema}},
        )
        try:
            return json.loads(self._text(response))
        except json.JSONDecodeError as exc:
            raise LLMError(f"JSON invalide ({purpose})") from exc

    def write(self, purpose: str, system: str, user: str, max_tokens: int = 4000) -> str:
        response = self._call(purpose, self._model("writing"), system, user, max_tokens, {})
        return self._text(response).strip()
