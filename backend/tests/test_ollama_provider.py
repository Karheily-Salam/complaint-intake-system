"""OllamaAIProvider: safe fallback / clear error when Ollama is unavailable.

No Ollama server runs in CI, so these tests point the provider at a dead port.
"""

from __future__ import annotations

import pytest

from app.ai.base import AIProviderError, TypeOption
from app.ai.providers.ollama import OllamaAIProvider
from app.ai.providers.rule_based import RuleBasedAIProvider

pytestmark = pytest.mark.asyncio

DEAD_URL = "http://127.0.0.1:9"  # discard port - never accepts connections

_OPTIONS = [
    TypeOption(type="withdrawal", label="Withdrawal", description="withdrawal problem"),
    TypeOption(type="other", label="Other", description="anything else"),
]


async def test_available_is_false_when_ollama_is_down():
    provider = OllamaAIProvider(base_url=DEAD_URL, timeout=1)
    assert await provider.available() is False


async def test_falls_back_to_rule_based_when_configured():
    provider = OllamaAIProvider(
        base_url=DEAD_URL, timeout=1, fallback=RuleBasedAIProvider()
    )
    result = await provider.classify("I want to withdraw my money but it fails", _OPTIONS)
    assert result.type == "withdrawal"  # answered by the fallback


async def test_raises_clear_error_without_fallback():
    provider = OllamaAIProvider(base_url=DEAD_URL, timeout=1, fallback=None)
    with pytest.raises(AIProviderError) as exc:
        await provider.classify("hello", _OPTIONS)
    assert "Ollama" in str(exc.value)
