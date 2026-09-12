🇬🇧 **English** | [🇷🇺 Русский](009-ml-assists-the-deterministic-engine.ru.md)

# ADR-009: Statistical models assist; the deterministic engine still decides

**Status:** accepted

## Context

[ADR-002](002-deterministic-workflow-engine.md) put every workflow decision in
a deterministic engine, and [ADR-003](003-ai-provider-abstraction.md) put the
AI behind an interface. Both were written when the AI layer was keyword rules
or an optional local LLM.

Adding real models (a trained classifier, embeddings, clustering) reopens the
question those ADRs answered. A model that decides is a model that can be wrong
in a plausible-sounding way, on a ticket someone acts on. Meanwhile the rules
were measurably weak outside English, because their "other" signal is
English-only.

The host is also small: one vCPU, 1.9 GB of RAM, a 384 MB container, and a
reply-latency budget of about a second end to end.

## Decision

Models produce scored signals; the engine, the YAML schemas and support staff
make the decisions.

1. Keyword rules outrank the classifier. The model is consulted only where the
   rules found nothing, and only above a calibrated abstention threshold.
2. Abstention is a first-class outcome. "Too vague" is a trained class, and a
   low-confidence prediction is withheld instead of rounded to a guess.
3. No extracted value without evidence. Every value must be supported by a span
   of the customer's own message, or it is refused. The guard applies to any
   provider, including a future LLM.
4. Nothing statistical mutates a ticket. Similarity suggests and incident
   detection alerts; neither merges, closes, reprioritises nor sends mail.
5. Small models, in-process: linear models and int8 ONNX embeddings on the CPU,
   no paid API, no vector database, no extra service.
6. Heavy work is asynchronous. Only classification and extraction sit in the
   reply path; embeddings and clustering run in a background worker or on
   request.
7. Staff corrections are recorded, not applied to a model. Retraining is a
   deliberate, reviewed, committed step.

## Alternatives considered

- **Let the classifier decide outright.** Rejected: it would overrule an
  auditable keyword match with a probability, and the failure mode (a confident
  wrong type) is the expensive one.
- **Fine-tune or serve an LLM for classification.** Rejected: it does not fit
  the host, it would spend the whole latency budget, and there is not enough
  data to fine-tune honestly.
- **A vector database.** Rejected at this scale: an exact cosine scan over a
  few thousand stored vectors takes milliseconds, and a separate service would
  be one more thing to run, back up and secure.
- **Automatic retraining from staff corrections.** Rejected: a feedback loop
  that ships itself has no review step, and a handful of corrections can move a
  small model a long way.

## Consequences

- The hybrid classifies substantially better than the keyword baseline on the
  held-out set, and gives a wrong answer rarely because it abstains instead.
  Numbers, including per-language results, are in
  [the evaluation report](../ml/evaluation.md).
- Every stored field value can show the words it came from, and a value nothing
  supports is refused.
- Two thresholds now need periodic review (classifier abstention, similarity),
  and they are model-specific: switching the embedder switches its thresholds.
- The default embedder is not cross-lingual, so similar-ticket quality on the
  default install is lower than the system can reach with the ONNX model.
- More state to operate: embeddings, predictions and feedback tables, and a
  background worker.
