# zelador — Specification

Repo: `zotero-driver`. Package and CLI: `zelador` / `zel`.

## What it is

A deterministic Python CLI over the Zotero Web API for reading, auditing, and reorganizing a personal Zotero library (~2,000 items). Claude Code is the brain; the CLI is the hands. Classification and proposal-making happen in interactive Claude Code sessions; the CLI provides the safe, deterministic read/validate/apply machinery. The chat is the approval UI.

Explicitly out of scope: building another citation manager, writing to Zotero's SQLite database, a standalone scheduled pipeline (may be revisited later by reusing the same CLI), realtime processing, duplicate detection UI (Zotero's own is adequate for now).

## Core principle

The agent proposes; deterministic code validates; the user approves; every applied change is logged and reversible. Writes either succeed verifiably or fail loudly.

## Architecture decisions

| Area        | Decision                                                                                                                                                                                                        |
|-------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Read path   | Zotero Web API (v3) is canonical for reads and writes. The local SQLite replica is a secondary, explicitly-named path (`zel local ...`) for bulk analytics and direct PDF file access only. Always read from a snapshot copy, never the live file. The Zotero data directory is auto-discovered per platform (`~/Zotero` on Linux/macOS, `%USERPROFILE%\Zotero` on Windows, mounted Windows profile under WSL — here `/mnt/c/Users/noah_/Zotero`), overridable in config |
| Write path  | Web API only, with `If-Unmodified-Since-Version` / per-object `version` concurrency safety. Never write to SQLite                                                                                                    |
| HTTP client | Hand-rolled thin client on `httpx` (~350 lines), not pyzotero. Rationale: pyzotero's `_batch_update` discards per-object failure maps and its 429 handling returns success without retrying — both sit on our critical path. Crib pyzotero's `_client.py` version-header conventions and test suite as reference. Revisit adoption if scope grows to attachments/file uploads |
| Client musts | Paginated full-library dump following `Link: next` headers; batch writes chunked at 50 parsing the per-object `success`/`unchanged`/`failed` response maps; honor `Backoff`/`Retry-After` including on 200s, with real retry of 429'd requests; surface 412 version conflicts per item key |
| Auth        | `.env` at repo root: `ZOTERO_API_KEY`, `ZOTERO_USER_ID=11868292`. Never read into agent context; consumed by the CLI process only                                                                                     |
| Stack       | Python via `uv`; TDD against a mocked httpx transport                                                                                                                                                               |

## Taxonomy

Flat tags, controlled by a registry file (`taxonomy.yaml`) — not namespaced tags. The registry declares canonical tags, their meaning, and known aliases; the validator only permits tags listed there. The tags themselves stay human.

- Existing status vocabulary is kept as-is: `lido` (read), `ler` (to read), `fav`, `_tablet`.
- The ~1,200 auto-imported subject tags (arXiv categories, newspaper sections, publisher keywords) are slated for rewrite against the registry, in approved batches. The registry itself is designed from real audit data (M2), not invented up front.
- User should disable Zotero's "Automatically tag items with keywords and subject headings" setting so the mess stops growing.

## Safety model (three layers)

1. **Pre-session snapshot** — `zel backup` dumps every item's full JSON to `<data dir>/backups/<timestamp>.jsonl`. `zel apply` refuses to run without a snapshot from the current day. This is the undo source: Zotero's server keeps no history — item deletions go to a restorable trash, but tag removals and field overwrites are unrecoverable server-side.
2. **Append-only change log** — every mutation appends `(item key, field, old, new, item version)` to a session JSONL. `zel undo <session>` replays it backwards.
3. **Validator hard rules** — no item deletions ever (the agent may only propose trashing, never purge); changesets touching >200 items refuse to apply without an explicit `--big` flag; writes only to fields the changeset schema allows.

Free fourth layer: the local Windows SQLite replica is itself a full copy sync could restore from.

## Approval flow

```
agent reads library → proposes changeset (JSON in <data dir>/changesets/)
→ zel validate (against taxonomy.yaml + hard rules)
→ agent renders compact summary in chat, user approves per group
→ zel apply --dry-run (always) → zel apply
→ change log written
```

Risk tiers: filling an empty field is low-risk (auto-appliable once trusted); overwriting a non-empty field, tag deletion, and major moves always require approval.

## Data layout

The repo is public. Personal library data never lives inside the working tree — it lives in the platform-native user data directory, resolved via `platformdirs` (`~/.local/share/zelador/` on Linux, `~/Library/Application Support/zelador/` on macOS, `%LOCALAPPDATA%\zelador\` on Windows), overridable with `ZELADOR_DATA_DIR`:

```
<data dir>/
  backups/     pre-session full-library JSONL snapshots
  audit/       audit output JSON + generated report
  changesets/  proposed changeset JSON files
  log/         append-only per-session change logs
```

Committed: code, `SPEC.md`, `taxonomy.yaml` (the registry is public — acceptable for a reference-manager vocabulary). Never committed: `.env`, any library dumps. A defensive `.gitignore` for `.env` and `data/` exists as a second net regardless.

## Audit (`zel audit`)

Emits one JSON file per check under `<data dir>/audit/` (machine-readable, diffable between runs) plus a generated `audit-report.md` summary.

1. **Metadata completeness** — per item, missing DOI / date / creators / publication, scored by item type (a webpage legitimately lacks a DOI; a journal article doesn't). Judged against citation needs, not completionism. Includes standalone attachments: PDFs with no parent item, invisible to bibliographies.
2. **Tag mess** — cluster near-duplicates and case-duplicates (`Artificial Intelligence` / `artificial intelligence` / `AI`, four casings of `machine learning`), separate automatic from manual tags (the API marks tag type).
3. **Collection hygiene** — items in no collection, empty collections, duplicate sibling names (two `Clickbait` under Detection), orphaned subtrees.
4. **Duplicate items** — same DOI or near-identical title+year.

## Enrichment (missing metadata)

Sources, composable, all proposal-only through the standard changeset flow:

- **Crossref API** (free, keyless) — fill journal/date/pages by DOI; fuzzy title+author match to find missing DOIs.
- **arXiv API** — abstracts and canonical versions for the arXiv-heavy portion.
- **PDF extraction** — first-page text of local PDFs (2 GB storage folder reachable from WSL) for orphan attachments and worst items.

Crossref disagreements with existing metadata (wrong year, mangled authors) are flagged in the audit report only — never auto-fixed.

## Skills (shipped with the repo)

The repo is public and Claude Code is the intended driver, so the workflows ship as project skills in `.claude/skills/`, written for any user, not just the author:

| Skill              | Purpose                                                                                                                          |
|--------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `zelador-setup`    | First-run onboarding: walk the user through creating a Zotero API key, finding their user ID, writing `.env` (without the agent reading it), verifying the connection, and recommending the auto-tagging setting be disabled |
| `zelador-taxonomy` | Design or revise `taxonomy.yaml` interactively from real audit data — cluster existing tags, propose canonical vocabulary + aliases, record decisions |
| `zelador-review`   | The recurring session: backup → audit → interpret findings → propose changesets → validate → in-chat approval → dry-run → apply → log |

Skills encode the safety flow so it is followed by construction, not memory.

## Milestones

| #   | Milestone                                                                                                     |
|-----|-----------------------------------------------------------------------------------------------------------------|
| M1  | Read client + `zel audit` — tags, metadata, collections, duplicates → JSON + report                              |
| M2  | Taxonomy registry (`taxonomy.yaml`) designed together from M1 audit data                                          |
| M3a | Shakedown write: merge obvious case-duplicate tags only — smallest reversible job, exercises changeset/apply/undo |
| M3b | Metadata enrichment (the stated pain: bibliographies)                                                             |
| M4  | Full tag rewrite against the registry (~1,500 tags → curated vocabulary)                                          |
| M5  | Collections restructure — last, because the tree is load-bearing for CAPSTONE and this is a taxonomy conversation |

## Process

git init first (not yet a repo), then branch → TDD → `/review` → merge per user workflow. `uv` for everything. Semantic commits.

## Library facts (as of 2026-07-19 audit snapshot)

2,006 real items; 1,510 tags; sync live to zotero.org (user `noah-art3mis`, ID 11868292); 1,142 PDF annotations (Dec 2024 – May 2025) exist only locally and never synced — investigate separately; Better BibTeX installed; two group libraries present but out of scope.
