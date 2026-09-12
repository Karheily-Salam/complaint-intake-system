🇬🇧 **English** | [🇷🇺 Русский](ml.ru.md)

# The ML layer

Everything statistical in this system is an *assistant*. It proposes a
complaint type, points at the text supporting a value, says which tickets look
alike, and flags an unusual burst. It never changes a ticket's status, merges
tickets, or sends a customer anything. The deterministic engine, the YAML
complaint schemas and support staff remain the decision makers — see
[ADR 009](adr/009-ml-assists-the-deterministic-engine.md).

It also has to run on the real host: one vCPU, 1.9 GB of RAM, a 384 MB backend
container. That rules out serving an LLM and rules in small models that are
honest about what they do not know.

## What runs

| Capability | Model | Where it runs | Cost |
|---|---|---|---|
| Complaint classification | Calibrated multinomial logistic regression on hashed character n-grams | In the reply path | ~1 ms |
| Evidence-backed extraction | Deterministic matching (exact, normalised, date, overlap) | In the reply path | <1 ms |
| Embeddings | `multilingual-e5-small` int8 ONNX, or hashed char n-grams | Background worker | ~13 ms / complaint (e5) |
| Similar & duplicate tickets | Cosine similarity + identity + field overlap | On request | ~ms |
| Incident detection | Agglomerative clustering + Poisson burst test | On request, cached 60 s | ~ms |

No paid APIs, no external services, no vector database. The only runtime
dependency added for all of this is numpy; the ONNX runtime is optional.

## Classification: rules first, model second

The decision order in `app/ai/providers/hybrid.py`:

1. **Keyword rules.** An explicit withdrawal/deposit keyword is deterministic
   and auditable, and always wins. The model may never overrule it.
2. **The classifier**, only where the rules found nothing, and only when its
   calibrated probability clears the abstention threshold.
3. **The provider's own fallback** otherwise — which is exactly the pre-ML
   behaviour, including asking the customer what the problem is about.

Three things make the model's output safe to act on:

- **Calibration.** Raw softmax scores of a regularised linear model are not
  probabilities. A temperature fitted on out-of-fold predictions rescales them,
  so "0.8" means about 80% right on held-out data.
- **An explicit `unclear` class.** "Hello" and "I have a problem" are a class of
  their own in training, so the model learns to say *too vague* instead of
  guessing one of the three real types.
- **Abstention.** Below the threshold chosen from the risk–coverage curve, the
  prediction is withheld and the engine asks the customer, exactly as before.

`ML_CLASSIFIER_MODE=shadow` computes and records the model's opinion without
letting it decide anything — the safe way to evaluate a new model on live
traffic. `off` removes it entirely.

## Extraction: no value without evidence

Every extractor promises never to invent a value. `app/domain/evidence.py`
checks that promise instead of trusting it: for each proposed value it finds the
span of the customer's message that supports it, or the engine refuses the
value. Matching, strictest first:

| Method | Example |
|---|---|
| `exact` | `U-482913` appears in the message (case and whitespace insensitive) |
| `date` | `2026-09-08` is supported by "8 сентября", "08/09/2026" or "أمس" |
| `normalized` | `U482913` is supported by `U-482913` |
| `overlap` | a free-text description that paraphrases the message |

The evidence is stored with the field and shown in the dashboard, so an agent
can see why the system believes a value. This guard applies to every provider,
including a future language model — measured hallucination rate on the gold set
is **0.000**.

## Embeddings, similarity and incidents

One embedding per complaint, computed in a background worker (never on the email
path), stored as bytes in SQLite keyed by model version. Identifiers are masked
before embedding, so similarity is driven by what a complaint is *about* and the
vectors carry as little personal data as possible.

**Similar tickets** combine three signals: semantic similarity, customer
identity, and extracted fields with the same value. "Possible duplicate"
requires strong evidence — the same customer and near-identical text, or the same
transaction reference. Text alone is only ever "similar", because in a complaint
inbox every withdrawal complaint sounds like every other one.

**Incidents** cluster the recent window (average-linkage agglomerative, no preset
number of clusters), compare each cluster with its *own* history, and report only
bursts whose Poisson tail probability is below 0.01. A topic that is always busy
is therefore not an incident; a quiet topic that suddenly is, is. A separate
per-type volume check catches incidents too varied to cluster.

The default embedder is dependency-free character-n-gram hashing, which finds
near-duplicate wording in one language. The real multilingual model is one
command away:

```bash
cd backend
pip install -e ".[ml-onnx]"
python scripts/download_embedding_model.py     # ~120 MB, pinned by SHA-256
EMBEDDING_PROVIDER=onnx                        # and raise the container limit
```

With it, a Russian and an Arabic description of the same problem land together:
cross-lingual nearest-neighbour accuracy is 100% on the test set, against 35% for
hashing.

It is not free. Measured resident size of the whole backend process:

| Configuration | Resident |
|---|---:|
| Default (hashing) | ~97 MB |
| ONNX session loaded | ~497 MB |
| ONNX, embedding a 256-complaint batch | ~503 MB |

Almost all of it is the session itself, and it is paid the moment the model
loads. The embedder therefore caps its own forward pass at 8 texts
(`MAX_BATCH`) and runs ONNX Runtime with the CPU memory arena disabled;
without that cap a single 64-text batch measured over 1 GB. **Before enabling
it in production, raise the backend container's memory limit in `compose.yml`
from 384 MB to at least 768 MB** (the host has 1.9 GB, of which the frontend
takes 128 MB).

## Learning from staff

A correction in the dashboard (complaint type or one field) is applied as a staff
decision — validated against the schema, `source=employee`, never touching the
ticket's status — and recorded in `ml_feedback` with everything a future model
needs: the original value, which layer produced it (rules, classifier,
extractor), its model version and confidence, the evidence it cited, and the
inbound message it came from.

Nothing retrains automatically. `scripts/export_training_feedback.py` writes a
masked JSONL dataset for review; excluded tickets (`ML_DATASET_EXCLUDED_TICKETS`)
never appear in it, and demo corrections are excluded by default because anyone
can create demo tickets.

## Evaluation

`backend/scripts/evaluate_ml.py` runs the held-out sets through the real code
paths — the same providers and the same conversation engine — and writes
[`evaluation.md`](ml/evaluation.md) and `evaluation.json`. It is fully
reproducible: fixed files, hashes recorded, no sampling.

Current headline numbers (96 held-out messages, 24 gold extraction cases):

| System | Accuracy | Macro-F1 | Coverage | Selective accuracy |
|---|---:|---:|---:|---:|
| Keyword rules (baseline) | 0.417 | 0.383 | 0.198 | 0.842 |
| Classifier alone | 0.750 | 0.761 | 0.510 | 0.980 |
| **Hybrid (what runs)** | **0.833** | **0.841** | **0.594** | **0.983** |

Extraction: precision **1.000**, recall **0.868**, F1 **0.930**, hallucination
rate **0.000**.

A test fails if the datasets change without the model being retrained, if the
committed report goes stale, if the hybrid falls below a macro-F1 floor, or if
extraction precision ever drops below 1.0.

## Monitoring

`GET /api/v1/ml/monitoring` (staff) and the dashboard's "Model health" panel
report what the models are doing on live traffic: volumes by model version,
which layer decided, abstention rate, classifier latency, extraction rejection
rate, PSI drift between the recent window and the one before it, and the staff
correction rate per layer.

One number there is worth singling out: **shadow agreement**. On every message a
keyword rule decided, the classifier's own opinion was recorded anyway, so the
two can be compared continuously on real traffic at no risk to any customer.

## Privacy

- Identifiers are masked before text reaches an embedding, an incident label or
  an exported dataset (`app/ml/pii.py`).
- `ml_predictions` stores labels, scores and timings — never message text,
  addresses or values.
- Demo and real data are analysed separately everywhere, as in the rest of the
  system.
- Real customer mail is never training data by default; it takes an explicit,
  reviewed export.

## Honest limits

- The datasets are small (366 training and 96 test messages, 24 extraction
  cases) and written by one author. They measure the pipeline and catch
  regressions; they are not a claim about accuracy on real customer mail.
- Production has seen very few real complaints, so the live monitoring numbers
  are thin, and drift detection needs two populated windows before it says
  anything.
- Only the classifier's confidences are calibrated. The hybrid's ECE is higher
  than the model's alone because the rules' confidences are heuristic constants.
- The default hashing embedder is not cross-lingual. Similar-ticket and incident
  quality improves substantially with `EMBEDDING_PROVIDER=onnx`.
- ONNX embeddings are not bit-reproducible: int8 kernels and padding make a
  vector depend slightly on the batch it was computed in (~0.995 cosine
  agreement). It does not move decisions at the thresholds in use, but two
  runs over the same complaint can differ in the last decimal. The hashing
  embedder is exactly reproducible.
- Nothing here reads attachments, and there is no reply drafting or priority
  prediction yet.
