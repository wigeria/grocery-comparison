---
name: pre-commit-check
description: Check the project is safe and clean to commit or push - lint, formatting, tests, and a scan of every file git would publish for secrets, personal data (address id, home coordinates, hostnames, IPs) and style slips like em dashes or emojis. Use before committing, pushing, publishing, or when the user asks if the repo is ready.
---

# Pre-commit check

This repo is public, while the app runs with private data (Zepto address id, home
coordinates, tokens, server details). This check makes sure none of that leaks into a commit.

## 1. Lint, format, tests

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q
```

If formatting fails, run `.venv/bin/ruff format .` and show the user which files changed.

## 2. Scan what git would publish

```bash
.venv/bin/python .claude/skills/pre-commit-check/scan_private.py --extra <server-host> <lan-ip>
```

The script lists the files git would commit (tracked plus untracked-but-not-ignored, and
works before `git init` too). It searches them for:

- private values read from `.env` and `config.toml` (tokens, site address, delivery address
  id, coordinates to 4 decimals), plus anything passed with `--extra`;
- common secret formats (Anthropic, GitHub and AWS keys, JWTs, private keys, long bearer
  tokens) and private IPv4 addresses;
- em or en dashes, emojis and smart quotes.

It prints file:line and the kind of match, never the value. Pass the server's SSH host alias
and LAN IP with `--extra` if you know them from the conversation; otherwise ask the user
whether there are other private strings to check.

## 3. Check the ignore rules

These must never be committed. Confirm each is ignored (`git check-ignore -v <path>` in a
repo):

`.env`, `config.toml`, `data/`, `db/*.sqlite3*`, `*PLAN.md`, `.venv/`.

Also check `git status --short` (or the scan's file list) for anything unexpected, such as
large binaries, stray test files or `.DS_Store`.

## 4. Report

Summarize: lint, format and test results; the number of files scanned; each finding with a
suggested fix. Don't commit anything unless the user asks. Commit messages must not mention
AI or Claude.

The `₹` symbol in the frontend and docs is intentional and not a finding.
