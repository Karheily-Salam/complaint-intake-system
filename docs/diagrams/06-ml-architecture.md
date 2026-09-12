# ML architecture and data flow

**Scope.** Every statistical component, split by when it runs: in the reply
path, in the background, on request from the dashboard, or offline. Also shows
what each one writes.

**Read it** when changing anything under `app/ml`, and to confirm that no ML
path mutates a ticket on its own. Related decision record:
[ADR-009](../adr/009-ml-assists-the-deterministic-engine.md). Narrative:
[docs/ml.md](../ml.md).

Sources: `app/ml/*`, `app/ai/providers/hybrid.py`, `app/services/ml_worker.py`,
`app/services/correction_service.py`, `app/api/routes/ml.py`,
`app/api/routes/tickets.py`, `backend/scripts/*`.

```mermaid
flowchart TD
    subgraph replypath["In the reply path, synchronous"]
        MSG["Customer message"]
        HYB["HybridAIProvider.classify"]
        RULES["keyword_classification<br/>deterministic, always wins"]
        CLF["LinearTextClassifier.predict<br/>hashed char n-grams, temperature scaled"]
        ART["artifacts/complaint_classifier.npz and .json"]
        SIGNAL["MLSignal<br/>label, confidence, abstained"]
        EXT["Engine extraction plus locate_evidence"]
        LOG["record_classification<br/>record_extraction"]
    end

    subgraph background["Background, ml_worker every ML_WORKER_INTERVAL_SECONDS"]
        WORKER["ml_worker.run_once"]
        EMBPEND["embed_pending<br/>new or stale complaints only"]
        EMB["Embedder<br/>HashingEmbedder default,<br/>OnnxE5Embedder when configured"]
        PII["pii.mask_identifiers<br/>before any text is embedded"]
    end

    subgraph onrequest["On request, staff routes"]
        SIM["find_similar<br/>cosine plus customer identity<br/>plus shared strong fields"]
        INC["detect_incidents<br/>average linkage clusters,<br/>Poisson burst test, c-TF-IDF labels,<br/>per type volume spikes"]
        MON["monitoring_snapshot<br/>decided_by mix, abstention,<br/>shadow agreement, latency, PSI drift"]
        REG["model_registry"]
        FB["feedback_summary and recent_feedback"]
    end

    subgraph correction["Staff correction loop"]
        CORR["CorrectionService<br/>validated against the schema,<br/>source becomes employee"]
    end

    subgraph offline["Offline, run by hand"]
        TRAIN["scripts/train_classifier.py<br/>datasets/complaints/train.jsonl"]
        EVAL["scripts/evaluate_ml.py<br/>test.jsonl and extraction_test.jsonl"]
        EXPORT["scripts/export_training_feedback.py<br/>masked export"]
        DL["scripts/download_embedding_model.py"]
    end

    T_PRED[("ml_predictions")]
    T_EMB[("complaint_embeddings")]
    T_FB[("ml_feedback")]
    T_FIELDS[("complaint_fields<br/>evidence_* columns")]
    T_TICKET[("tickets and complaints")]
    REPORT["docs/ml/evaluation.md and .json"]

    MSG --> HYB
    HYB --> RULES
    HYB --> CLF
    CLF --> ART
    RULES --> SIGNAL
    CLF --> SIGNAL
    SIGNAL --> LOG
    MSG --> EXT
    EXT --> LOG
    EXT --> T_FIELDS
    LOG --> T_PRED

    WORKER --> EMBPEND --> PII --> EMB --> T_EMB

    T_EMB --> SIM
    T_EMB --> INC
    T_PRED --> MON
    T_FB --> FB
    ART --> REG

    CORR --> T_FB
    CORR --> T_TICKET
    T_PRED -.->|"the prediction being corrected"| CORR

    TRAIN --> ART
    EVAL --> REPORT
    EXPORT --> T_FB
    DL -.->|"optional model files"| EMB
```

Boundaries the code actually enforces:

- Keyword rules outrank the classifier. The model is consulted only where the
  rules found nothing, and only above its calibrated threshold.
- `ML_CLASSIFIER_MODE=shadow` records the model's opinion and returns the base
  provider's answer, which is how shadow agreement is measured on live traffic.
  `off` removes the model entirely.
- Similarity and incidents produce suggestions. Nothing under `app/ml` merges,
  closes, reprioritises, or sends mail.
- `ml_predictions` stores labels, scores and timings only, never message text
  or addresses.
- A correction is applied as a staff decision and recorded. No model retrains
  itself; `train_classifier.py` is a deliberate, committed step.
