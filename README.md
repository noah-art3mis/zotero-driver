# zelador

A deterministic Python CLI over the Zotero Web API for reading, auditing, and reorganizing a personal Zotero library. The agent (Claude Code) proposes; deterministic code validates; the user approves; every applied change is logged and reversible. Full design in [SPEC.md](SPEC.md).

## Install

The repo is the install unit — there is no published package:

```bash
git clone https://github.com/noah-art3mis/zotero-driver
cd zotero-driver
uv sync
cp .env.example .env      # fill in your Zotero API key and user ID
cp config.example.yaml config.yaml   # optional
uv run zel --help
```

## Commands

Command surface lands milestone by milestone; the table below tracks what exists.

| Command | What it does |
| ------- | ------------ |

Data (backups, audit output, plans, logs) lives in the platform user data dir (`~/.local/share/zelador/` on Linux), overridable with `ZELADOR_DATA_DIR` — never inside this repo.
