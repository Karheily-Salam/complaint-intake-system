# Data model

**Scope.** Every table, its foreign keys, and the columns that carry a rule.
Only columns worth explaining are listed; the models are the full reference.

**Read it** before writing a query or a migration. The schema is authored only
by Alembic, never by `create_all()`.

Sources: `app/db/models/*.py`, `alembic/versions/*.py`.

```mermaid
erDiagram
    customers ||--o{ conversations : "opens"
    customers ||--o{ tickets : "owns"
    conversations ||--o{ messages : "contains"
    conversations ||--o| complaints : "has at most one"
    conversations ||--o{ email_logs : "records"
    complaints ||--o{ complaint_fields : "collects"
    complaints ||--o| tickets : "becomes"
    complaints ||--o{ complaint_embeddings : "is vectorised as"
    messages ||--o{ complaint_fields : "is the source of"
    conversations ||--o{ ml_predictions : "produces"
    messages ||--o{ ml_predictions : "produces"
    tickets ||--o{ ml_feedback : "is corrected by"
    ml_predictions ||--o{ ml_feedback : "is judged by"

    customers {
        int id PK
        string email "unique together with is_demo"
        bool is_demo "separates demo from real identity"
        string name
    }

    conversations {
        int id PK
        int customer_id FK
        bool is_demo "every public read filters on this"
        string subject
        string thread_token "unique, the [Ref:token] fallback"
        string status "ConversationStatus"
        string language_code "reply language, kept when a message is ambiguous"
        string pending_field "the field a bare answer is read against"
    }

    messages {
        int id PK
        int conversation_id FK
        string direction "inbound or outbound"
        string external_message_id "unique index, carries idempotency and threading"
        string body "stored raw, quoted history is stripped downstream"
        json raw_meta "in_reply_to, references, received_at"
    }

    complaints {
        int id PK
        int conversation_id FK "unique"
        string type "withdrawal, deposit, other"
        string method_key "denormalised deposit method, never branched on"
        string concise_description
        string status "ComplaintStatus"
    }

    complaint_fields {
        int id PK
        int complaint_id FK "unique together with key"
        string key
        string value
        string status "FieldStatus"
        string source "customer_message, ai_inference, employee"
        int source_message_id FK
        float confidence
        string evidence_text "the customer words supporting the value"
        int evidence_start
        int evidence_end
        string evidence_method "exact, date, normalized, overlap"
    }

    tickets {
        int id PK
        string reference "unique, formatted from the ticket id"
        int complaint_id FK "unique"
        int conversation_id FK
        int customer_id FK
        string type
        string status "TicketStatus, closed is terminal for threading"
        string priority
        json structured_data "denormalised snapshot of the fields"
    }

    email_logs {
        int id PK
        int conversation_id FK
        string direction
        string provider "the transport that actually handled it"
        string subject
        string body
    }

    ml_predictions {
        int id PK
        string task "classification or extraction"
        string model_name
        string model_version
        int conversation_id FK
        int message_id FK
        bool is_demo
        string predicted_label
        float confidence
        bool abstained
        string final_label "what the system acted on"
        string decided_by "rules, ml or provider"
        float latency_ms
        json details "no message text, addresses or values"
    }

    complaint_embeddings {
        int id PK
        int complaint_id FK "unique together with model_version"
        string model_name
        string model_version "vectors from different models never mix"
        int dim
        bytes vector "float32 bytes"
        string text_sha256 "detects whether the text really changed"
    }

    ml_feedback {
        int id PK
        string kind "classification or field"
        int ticket_id FK
        int complaint_id FK
        int conversation_id FK
        int prediction_id FK "the prediction this corrects, when known"
        string field_key
        string original_value
        string corrected_value
        string original_source
        float original_confidence
        string model_version
        bool exported
    }
```

Constraints that carry behaviour rather than tidiness:

- `messages.external_message_id` is uniquely indexed. That single index is what
  makes redelivery harmless, since a duplicate cannot create a second
  conversation, question, or ticket.
- `customers` is unique on `(email, is_demo)`, so a demo visitor claiming a real
  customer's address gets a separate row.
- `conversations.thread_token` is unique and is the fallback when a provider
  rewrites Message-IDs in transit.
- `complaint_fields` is unique on `(complaint_id, key)`, so a field has exactly
  one current value with its evidence.
- `complaint_embeddings` is unique on `(complaint_id, model_version)`, so
  switching embedder adds vectors rather than corrupting the space.
- Ten migrations build this, ending at `8883faf476b6`. Every one uses batch
  mode, which is what makes an ALTER safe on SQLite.
