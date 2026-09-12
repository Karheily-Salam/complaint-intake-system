🇬🇧 **English** | [🇷🇺 Русский](003-ai-provider-abstraction.ru.md)

# ADR-003: AI behind a provider interface

**Status:** accepted

## Context

The system needs language understanding, but a portfolio project that requires
a paid API key to run is a project nobody runs, and a test suite that calls a
model is slow and flaky.

## Decision

`AIProvider` is an interface with five narrow operations: classify, extract,
summarise, detect language, compose reply. Two implementations exist:

- `RuleBasedAIProvider`: deterministic, offline, the default.
- `OllamaAIProvider`: a local LLM, opt-in via `AI_PROVIDER=ollama`, with an
  automatic fallback to rule-based when Ollama is unreachable.

## Alternatives considered

- **A hosted API (OpenAI, Anthropic) as the default.** Rejected: costs money,
  needs a key to run the demo, and makes tests non-deterministic. The interface
  means adding one later is a new class, not a redesign.
- **No abstraction, call the model directly.** Rejected: the engine would depend
  on a vendor SDK, and the deterministic boundary in ADR-002 would be much
  harder to keep honest.

## Consequences

- `git clone && docker compose up` works with no credentials.
- The suite runs in ~30 seconds and is fully deterministic.
- The rule-based provider is genuinely limited: it does not detect corrections
  to already-valid fields, because that needs real language understanding. The
  README says so rather than implying otherwise.
- Swapping providers changes no engine or service code.
