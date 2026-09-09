"""Builds the configured :class:`AIProvider`. The rest of the app depends on the
abstraction and never imports a concrete provider directly.
"""

from __future__ import annotations

from functools import lru_cache

from app.ai.base import AIProvider
from app.core.config import settings


@lru_cache
def get_ai_provider() -> AIProvider:
    provider = settings.ai_provider.lower()

    if provider == "rule_based":
        from app.ai.providers.rule_based import RuleBasedAIProvider

        return RuleBasedAIProvider()

    if provider == "ollama":
        from app.ai.providers.ollama import OllamaAIProvider
        from app.ai.providers.rule_based import RuleBasedAIProvider

        fallback = RuleBasedAIProvider() if settings.ollama_fallback_to_rule_based else None
        return OllamaAIProvider(fallback=fallback)

    raise ValueError(
        f"Unknown AI_PROVIDER '{settings.ai_provider}'. Expected 'rule_based' or 'ollama'."
    )
