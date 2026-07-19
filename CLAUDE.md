# zelador — agent guide

Deterministic CLI over the Zotero Web API; full design in `SPEC.md`. The agent proposes, deterministic code validates, the user approves, every applied change is logged and reversible. Run everything with `uv run zel ...`; add `--json` when you are the consumer (NDJSON, one object per line). Diagnostics arrive on stderr; exit 0 success, 1 operational failure, 2 bad input.

## Situation → command

- Starting any session → `uv run zel status --json` (mandatory opener: live version, backup marker, pending logs, audit stamp, config presence)
- Need the whole library → `uv run zel items --json`
- Need specific items → `uv run zel items KEY1 KEY2 --json`
- What changed since the last backup → `uv run zel items --since <backup version> --json`
- How would an entry look in a bibliography → `uv run zel items KEY --bib`
- Survey the tag vocabulary → `uv run zel tags --json`
- See the collection tree → `uv run zel collections`
- Find problems (mess, gaps, duplicates) → `uv run zel audit` then read `<data dir>/audit/*.json` and `audit-report.md`
- Triage only recent arrivals → `uv run zel audit --since <backup version>`
- Bulk analytics or unsynced annotations → `uv run zel local "<SQL>" --json` (read-only snapshot of the desktop's SQLite)
- Before proposing any change → `uv run zel backup` (verified no-op when nothing changed)
- Check credentials/identity → `uv run zel debug whoami`
- Where do files live → `uv run zel debug paths`
- Inspect a raw API response → `uv run zel debug probe "items?limit=1"`

## Rules

- Never read `.env`; the CLI consumes it itself.
- Reads are cheap but not free: a full dump is ~40 requests. Prefer `--since` scoping and the latest backup/audit files in `<data dir>` (see `zel debug paths`) over re-dumping.
- Writes (`validate`/`apply`/`undo`) land in M3a — nothing mutates the library yet.

## Development

- Branch → TDD → `/review` → merge; `uv run pytest -q`, `uv run ruff check zelador tests`.
- Tests run against `tests/conftest.py`'s `FakeZotero` over `httpx.MockTransport` — no network.
