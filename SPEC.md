# zelador — Specification

Repo: `zotero-driver`. Package and CLI: `zelador` / `zel`.

## What it is

A deterministic Python CLI over the Zotero Web API for reading, auditing, and reorganizing a personal Zotero library (~2,000 items). Claude Code is the brain; the CLI is the hands. Classification and proposal-making happen in interactive Claude Code sessions; the CLI provides the safe, deterministic read/validate/apply machinery. The chat is the approval UI.

Explicitly out of scope: building another citation manager, writing to Zotero's SQLite database, a standalone scheduled pipeline (may be revisited later by reusing the same CLI), realtime processing, duplicate detection UI (Zotero's own is adequate for now).

## Core principle

The agent proposes; deterministic code validates; the user approves; every applied change is logged and reversible. Writes either succeed verifiably or fail loudly.

## Prototype posture

This is a prototype, not a product. Optimize for conciseness, understandability, clean architecture, and simplicity — in that spirit, not for bulletproofness. Concretely: no defensive guards against states the design already makes impossible, no retry/fallback layers beyond what the spec names (429 retry, version pins), no configurability beyond what a real session needs, no premature abstraction. Small files, pure functions, obvious names. The safety model above is the one place rigor is non-negotiable — everything else should be the simplest code that reads well. When bulletproofing and simplicity conflict outside the safety model, choose simplicity.

## Architecture decisions

| Area        | Decision                                                                                                                                                                                                        |
|-------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Read path   | Zotero Web API (v3) is canonical for reads and writes. The local SQLite replica is a secondary, explicitly-named path (`zel local ...`) for bulk analytics and direct PDF file access only. Always read from a snapshot copy, never the live file. The Zotero data directory is auto-discovered per platform (`~/Zotero` on Linux/macOS, `%USERPROFILE%\Zotero` on Windows, mounted Windows profile under WSL — here `/mnt/c/Users/noah_/Zotero`), overridable in config |
| Write path  | Web API only; never write to SQLite. Batch item writes carry each item's `data.version` (per-object conflicts arrive in the response's `failed` map) and no library-level `If-Unmodified-Since-Version` — a library-wide header would let any unrelated change 412 the whole batch. The library-level header is used only on endpoints that require it (e.g. tag deletion), which fail whole-operation |
| HTTP client | Hand-rolled thin client on `httpx` (~350 lines), not pyzotero. Rationale: pyzotero's `_batch_update` discards per-object failure maps and its 429 handling returns success without retrying — both sit on our critical path. Crib pyzotero's `_client.py` version-header conventions and test suite as reference. Revisit adoption if scope grows to attachments/file uploads |
| Client musts | Paginated full-library dump following `Link: next` headers; batch reads of specific items via `itemKey` in 50-key chunks (post-apply verification and undo state-checks fetch exactly the touched items); batch writes chunked at 50 parsing the per-object `success`/`unchanged`/`failed` response maps; honor `Backoff`/`Retry-After` including on 200s, with real retry of 429'd requests; surface version conflicts per item key; endpoint builder constructs `/users/<ZOTERO_USER_ID>/` paths only — group libraries are unreachable by construction, and setup verifies the key's identity and write access via `/keys/current` |
| Auth        | `.env` at repo root: `ZOTERO_API_KEY`, `ZOTERO_USER_ID=11868292`. Never read into agent context; consumed by the CLI process only                                                                                     |
| Stack       | Python via `uv`; TDD against a mocked httpx transport                                                                                                                                                               |

## Taxonomy

Namespaced tags of the form `family:value` (lowercase, hyphens for compound values), controlled by a registry file (`taxonomy.yaml`). The registry declares the allowed families, each family's canonical tags, their meaning, known aliases, and optional colours; the validator only permits tags listed there.

- Initial families: `status:` (workflow state), `topic:` (subject vocabulary), `device:` (workflow markers). Further families (`method:`, `use:`, ...) are added only if the M2 audit shows a real need – the family list lives in the registry, not in code.
- Existing status vocabulary migrates into the scheme keeping its Portuguese values: `ler` → `status:ler`, `lido` → `status:lido`, `fav` → `status:fav`, `_tablet` → `device:tablet`.
- Colours: a registry entry may declare a colour (hex) and position. `zel apply` syncs declared colours to Zotero's coloured-tags library setting (`tagColors`, via the Web API settings endpoint, same version-safety rules as item writes). The validator enforces Zotero's hard cap of 9 coloured tags; colours are reserved for the `status:`/`device:` families so they stay pinned atop the tag selector.
- The ~1,200 auto-imported subject tags (arXiv categories, newspaper sections, publisher keywords) are slated for rewrite against the registry as `topic:` tags, in approved batches. The registry itself is designed from real audit data (M2), not invented up front.
- User should disable Zotero's "Automatically tag items with keywords and subject headings" setting so the mess stops growing.

Registry shape (`taxonomy.yaml`), kept minimal:

```yaml
families:
  status: {description: workflow state, coloured: true}
  topic:  {description: subject vocabulary}
  device: {description: workflow markers, coloured: true}
tags:
  - tag: status:lido
    description: read
    aliases: [lido]
    colour: "#2DA608"    # optional; position = order within coloured tags
```

Aliases are valid as selectors in changeset intents (so `merge_tag` can name what it replaces) but never as proposed output — the validator only ever writes canonical tags.

## Safety model (three layers)

1. **Pre-session snapshot** — `zel backup` dumps every item's full JSON, including trashed items (`includeTrashed=1`), to `<data dir>/backups/<timestamp>.jsonl`. A conditional GET (`If-Modified-Since-Version` with the last backup's library version) makes it a verified no-op when nothing changed. Each expanded plan records the backup it was validated against plus the library version at validation; `zel apply` requires that exact backup, not merely a recent one. This is the undo source: Zotero's server keeps no history — item deletions go to a restorable trash, but tag removals and field overwrites are unrecoverable server-side.
2. **Write-ahead change log** — before each write request, the session log gets a `pending` entry per operation carrying the old state; after the response, each entry is marked `applied`, `unchanged`, or `failed` from the per-object result maps, with the new version. A crash mid-apply therefore never loses the undo record. `zel apply` refuses to start while a session has unresolved `pending` entries. `zel undo <session>` replays only `applied` entries backwards, and before reversing each one verifies the item's current state still equals the logged new state — anything else is reported as an undo conflict and left untouched.
3. **Validator hard rules** — no item deletions ever (the agent may only propose trashing, never purge); changesets touching >200 items refuse to apply without an explicit `--big` flag; writes only to fields the changeset schema allows, and `fill_field` targets are checked against the item type's actual fields via the API schema endpoints (`/itemTypeFields`, cached per session) rather than hardcoded lists.

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

1. **Metadata completeness** — per item, missing DOI / date / creators / publication, scored by item type (a webpage legitimately lacks a DOI; a journal article doesn't). Judged against citation needs, not completionism — literally: the audit renders each item's actual bibliography entry server-side (`include=bib` with a CSL `style=`, ABNT by default) and a visibly broken entry is the finding, alongside the field-level rules. Those live as one data table in code, keyed by Zotero `itemType`: which fields are required, and which field counts as the "publication" (`publicationTitle`, `bookTitle`, `conferenceName`, `publisher`, ...). Includes standalone attachments: PDFs with no parent item, invisible to bibliographies.
2. **Tag mess** — cluster near-duplicates and case-duplicates (`Artificial Intelligence` / `artificial intelligence` / `AI`, four casings of `machine learning`), separate automatic from manual tags (the API marks tag type).
3. **Collection hygiene** — items in no collection, empty collections, duplicate sibling names (two `Clickbait` under Detection), orphaned subtrees.
4. **Duplicate items** — same DOI or near-identical title+year.

## Enrichment (missing metadata)

Sources, composable, all proposal-only through the standard changeset flow:

- **Crossref API** (free, keyless) — fill journal/date/pages by DOI; fuzzy title+author match to find missing DOIs.
- **arXiv API** — abstracts and canonical versions for the arXiv-heavy portion.
- **Server fulltext first** — Zotero has already indexed synced PDFs; `GET /items/<key>/fulltext` returns the extracted text in one request. Try this before any local parsing.
- **PDF extraction** — first-page text of local PDFs (2 GB storage folder reachable from WSL), fallback for orphan attachments and unsynced files only.

Crossref disagreements with existing metadata (wrong year, mangled authors) are flagged in the audit report only — never auto-fixed.

## CLI design

House style distilled from `judex-mini` and `adapta` (the reference designs): humans and agents share the same interface; `--json` is the machine contract.

- **Framework**: Typer, single console script `zel = "zelador.cli:app"`. Everyday verbs top-level, grouped with `rich_help_panel`; utilities under one nested `zel debug` sub-app. Modern `Annotated[T, typer.Option(...)]` signatures. English commands, kebab-case options, `--flag/--no-flag` booleans, positional argument for the primary subject.
- **Output**: human output on stdout; diagnostics and defaulting notices on stderr (`err=True`). Every read command takes `--json` emitting newline-delimited JSON, one object per line (jq-friendly; the form agents consume). Color respects `NO_COLOR` and disables when piped. Exit codes: 0 success, 1 operational failure, 2 bad input / user abort.
- **Safety idioms**: `--dry-run` on every mutating command printing exactly what would happen; `typer.confirm` gate unless `--yes`; forecast banner (item/request counts) before anything expensive or rate-limited.
- **Docs**: README with aligned command table and copy-pasteable `uv run zel ...` blocks; every command docstring has an Examples block; `CLAUDE.md` carries a "situation X → run command Y" routing table for agent use.
- **Layout**: flat package (`zelador/` at repo root), hatchling, `.env` + committed `.env.example`, thin Typer bodies over pure functions, `CliRunner` tests asserting exit codes and output. Deliberate deviation from house style: data lives in `platformdirs` user data dir, not in-repo, because this repo is public.

### Command surface

| Command                                     | Purpose                                                                                       |
|---------------------------------------------|---------------------------------------------------------------------------------------------------|
| `zel items` / `zel item <key>`              | Read items (full dump paginated, or one), `--json` NDJSON                                          |
| `zel tags` / `zel collections`              | Read tag list (with counts and type) / collection tree, `--json`                                   |
| `zel audit [check]`                         | Run all audit checks or one; writes JSON per check + `audit-report.md` to the data dir             |
| `zel backup`                                | Full-library JSONL snapshot to the data dir                                                        |
| `zel validate <changeset>`                  | Check symbolic intents against `taxonomy.yaml` + hard rules; expand into a version-pinned per-item plan |
| `zel apply <plan>`                          | Execute an expanded plan; `--dry-run` first-class; refuses unless the plan's pinned backup exists; `--big` for >200 items |
| `zel undo <session>`                        | Replay a session's change log backwards                                                            |
| `zel bib [keys]`                            | Render items' bibliography entries server-side (`include=bib`, `--style`, ABNT default) — before/after checks for enrichment |
| `zel lookup crossref\|arxiv`                | Deterministic enrichment lookups by DOI/id/fuzzy title, cached, candidates with scores             |
| `zel pdf-meta <key>`                        | First-page text extraction from the local PDF for metadata recovery                                |
| `zel local <query>`                         | Read-only analytics against a snapshot copy of the local SQLite                                    |
| `zel debug ...`                             | Inspection utilities (config paths, cache state, raw API probe)                                    |

## Changesets

Changesets are **symbolic intents**, not expanded edits: a closed operation vocabulary — `merge_tag`, `add_tag`, `remove_tag`, `fill_field`, `add_to_collection`, `remove_from_collection`, `create_note`, `trash_item` (propose-only) — defined by a schema in the repo and grown only by editing that schema. `zel validate` expands intents against the live library into an exact per-item plan pinned to item versions; the expanded plan is what the user approves and what `zel apply` executes. Version pins make stale plans fail loudly per item instead of drifting silently.

**Expansion semantics.** Zotero treats `tags` and `collections` as complete arrays — a partial write silently removes whatever it omits. So every tag/collection operation expands by read-modify-write of the full array, and the plan records both old and new arrays per item. `merge_tag` rewrites each carrying item's tag array (add canonical, drop aliases, preserve everything else); the alias tag may only be deleted globally after validation confirms no item still carries it. `create_note` operations carry a precomputed client-generated object key, so a retried request cannot create a duplicate and undo always knows the key. `trash_item` is a PATCH of `deleted: true` — its undo is exactly `deleted: false`, and trashed items remain listed under `/items/trash`.

**Contracts.** Three versioned JSON shapes, defined once as dataclasses + schema in the repo: `changeset.v1` (the intents), `plan.v1` (per-item operations, each with an operation id, item key, pinned item version, old state, new state, risk tier, and the backup id it binds to), and `log.v1` (operation id → pending/applied/unchanged/failed + resulting version). Approval in chat is per intent group; the plan file is what apply executes, byte-for-byte.

Enrichment logic is CLI-side (deterministic, cached, fixture-tested) — the agent's role is judgment: deciding whether a lookup candidate truly matches an item and emitting `fill_field` intents.

## Skills (shipped with the repo)

The repo is public and Claude Code is the intended driver, so the workflows ship as project skills in `.claude/skills/`, written for any user, not just the author:

| Skill              | Purpose                                                                                                                          |
|--------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `zelador-setup`    | First-run onboarding: walk the user through creating a Zotero API key, finding their user ID, writing `.env` (without the agent reading it), verifying the connection, and recommending the auto-tagging setting be disabled |
| `zelador-taxonomy` | Design or revise `taxonomy.yaml` interactively from real audit data — cluster existing tags, propose canonical vocabulary + aliases, record decisions |
| `zelador-review`   | The recurring session: backup → audit → interpret findings → propose changesets → validate → in-chat approval → dry-run → apply → log |

Skills encode the safety flow so it is followed by construction, not memory.

## Milestones

| #   | Milestone                                                                                                        |
|-----|---------------------------------------------------------------------------------------------------------------------|
| M1  | Read client + `zel backup` (incl. trash) + `zel audit` — tags, metadata, collections, duplicates → JSON + report     |
| M2  | Taxonomy registry (`taxonomy.yaml`) designed together from M1 audit data                                             |
| M3a | Write machinery — contracts, `zel validate` expansion, `zel apply`, `zel undo` — TDD against mocked transport and golden plan fixtures; no live writes |
| M3b | Live shakedown: merge obvious case-duplicate tags on a handful of items, then an undo drill — smallest reversible job |
| M3c | Metadata enrichment (the stated pain: bibliographies)                                                                |
| M4  | Full tag rewrite against the registry (~1,500 tags → curated vocabulary)                                             |
| M5  | Collections restructure — last, because the tree is load-bearing for CAPSTONE and this is a taxonomy conversation    |

## Discarded ideas

Considered and rejected, with the condition that would revive each:

| Idea                                | Why discarded                                                                                                   | Revive if                                             |
|-------------------------------------|---------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------|
| pyzotero adoption                   | Its batch updates swallow per-object failures and 429s return success without retrying — both on our critical path; 3 of 4 deps dead weight for our subset | Scope grows to attachments/file uploads                   |
| Zotero 7 local API (`localhost:23119`) | Read-only mirror of the Web API served by the desktop app — attractive, but unreachable from this WSL setup (binds Windows localhost; NAT mode can't see it), and it requires Zotero running. The SQLite snapshot covers the same ground, including the 1,142 unsynced annotations | WSL mirrored networking is enabled and a spike shows it reachable; then `zel local` can share the web client's parsing |
| Group library support               | Personal-library tool; endpoint builder constructs `/users/<id>/` paths only, so groups are unreachable by construction | A deliberate future group mode, never by accident          |
| `since`-based cached dump refresh   | YAGNI at 2,006 items — a full dump is ~21 requests run occasionally; a persistent cache adds invalidation state       | Dumps become an actual bottleneck                          |
| `Zotero-Write-Token` idempotency    | Redundant: precomputed client-generated keys already make creates idempotent and give undo the key                    | —                                                          |
| Plan checksums / approval metadata  | Prototype posture: the plan file is the approved artifact and is executed byte-for-byte                               | Multiple approvers or out-of-chat approval flows           |
| Flat registered tags                | Original taxonomy design; superseded by namespaced `family:value` tags with registry-declared colours                 | —                                                          |
| File upload/download, saved searches, `/publications`, OAuth, Atom/export formats, server-side search | No consumer in any milestone; full-dump-then-filter makes query plumbing pointless surface area | A milestone that actually needs one                        |

## Process

Branch → TDD → `/review` → merge. `uv` for everything. Semantic commits.

## Library facts (as of 2026-07-19 audit snapshot)

2,006 real items; 1,510 tags; sync live to zotero.org (user `noah-art3mis`, ID 11868292); 1,142 PDF annotations (Dec 2024 – May 2025) exist only locally and never synced — investigate separately; Better BibTeX installed; two group libraries present but out of scope.
