🇬🇧 **English** | [🇷🇺 Русский](006-sqlite-first.ru.md)

# ADR-006: SQLite for the first deployment

**Status:** accepted

## Context

One application process, one 2 GB VPS, a handful of writes per complaint, and a
single operator. The realistic peak is a few emails per minute.

## Decision

SQLite, accessed through SQLAlchemy, with schema managed by Alembic. WAL
journalling, a 30-second busy timeout, and `foreign_keys=ON` enabled per
connection.

## Alternatives considered

- **PostgreSQL now.** A second container, more memory on a 2 GB host, backups to
  design, and no capability this workload needs. Chosen against deliberately,
  not from unfamiliarity: the migration path is understood and mostly prepared.
- **A managed database.** Cost and latency for no benefit at this size.
- **Files or JSON.** No transactions, which ADR-005 depends on entirely.

## Consequences

- Backups are a file copy, taken with SQLite's online-backup API instead of
  `cp`, so a live database is never captured mid-write.
- `foreign_keys=ON` matters more than it sounds: SQLite ignores foreign keys by
  default, which would make the migrations' constraints decorative. Turning it
  on immediately caught a real ordering bug in the demo-reset script.
- The ceiling is one writer. This is the main reason the system is single-node,
  and the honest limit stated in the README.
- Migrating to PostgreSQL means a URL change, a migration run, and re-testing
  concurrency assumptions. SQLAlchemy and Alembic are already the seam.
