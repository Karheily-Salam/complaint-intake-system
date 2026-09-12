🇬🇧 **English** | [🇷🇺 Русский](README.ru.md)

# Architecture decision records

Short notes on the decisions that shaped this system: what the situation was,
what was chosen, what else was considered, and what it cost.

They exist because the interesting parts of this project are the trade-offs,
and a diff cannot show why an alternative was rejected. Each is a page at
most.

| # | Decision |
|---|---|
| [001](001-email-only-intake.md) | Email-only intake instead of a web form |
| [002](002-deterministic-workflow-engine.md) | A deterministic engine owns workflow, not the model |
| [003](003-ai-provider-abstraction.md) | AI behind a provider interface |
| [004](004-imap-polling.md) | IMAP polling instead of webhooks or a mail API |
| [005](005-commit-before-acknowledge.md) | Commit state before acknowledging inbound mail |
| [006](006-sqlite-first.md) | SQLite for the first deployment |
| [007](007-threading-and-idempotency.md) | Email threading and idempotency strategy |
| [008](008-demo-isolation.md) | Public demo isolated from real customer data |
| [009](009-ml-assists-the-deterministic-engine.md) | Statistical models assist; the engine still decides |
