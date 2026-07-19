# zelador

A deterministic Python CLI over the Zotero Web API for reading, auditing, and reorganizing a personal Zotero library. The agent (Claude Code) proposes; deterministic code validates; the user approves; every applied change is logged and reversible. Full design in [SPEC.md](SPEC.md).

## Install

The repo is the install unit — there is no published package:

```bash
git clone https://github.com/noah-art3mis/zotero-driver
cd zotero-driver
uv sync
cp .env.example .env      # fill in your Zotero API key and user ID
cp config.example.yaml config.yaml       # optional
cp taxonomy.example.yaml taxonomy.yaml   # optional — the tag registry; edit to taste
uv run zel --help
```

## Commands

Command surface lands milestone by milestone; the table below tracks what exists.

| Command                  | What it does                                                                    |
| ------------------------ | ------------------------------------------------------------------------------- |
| `zel status`             | One-screen session orientation; the sessions' mandatory opener                  |
| `zel items [keys...]`    | Full paginated dump, or batch read; `--since`, `--json`, `--bib`                |
| `zel tags`               | Tag list with item counts and manual/auto type; `--json`                        |
| `zel collections`        | Collection tree; `--json`                                                       |
| `zel audit [check]`      | Audit checks (completeness, tags, collections, duplicates, registry); `--since` |
| `zel local <sql>`        | Read-only SQL over a fresh, integrity-checked SQLite snapshot                   |
| `zel backup`             | Full-library JSONL snapshot (items incl. trash, collections, colours)           |
| `zel debug whoami`       | Identity and access of the configured API key                                   |
| `zel debug paths`        | Resolved paths: data dir, config files, Zotero data dir                         |
| `zel debug probe <path>` | Raw GET of a user-scoped API path, pretty-printed                               |

Data (backups, audit output, plans, logs) lives in the platform user data dir (`~/.local/share/zelador/` on Linux), overridable with `ZELADOR_DATA_DIR` — never inside this repo.
