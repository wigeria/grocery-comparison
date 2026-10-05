---
name: deploy
description: Copy this project to the home server over SSH without touching its secrets, then check the live site. Use when the user asks to deploy, update the server, or push changes to the server.
disable-model-invocation: true
---

# Deploy to the home server

Copies the code to the server, never its private files, and checks that the live site is
healthy afterwards. The server runs the app with Docker Compose (see README).

## 1. Ask first

Before running anything, ask the user for these three values (use AskUserQuestion, and don't
reuse values from memory or earlier sessions without asking):

- **SSH host**: the host alias from `~/.ssh/config`, e.g. `my-server`.
- **Remote project path**: the folder on the server, e.g. `~/grocery-comparison`.
- **Site URL**: the HTTPS address the app is served at, e.g. `https://name.duckdns.org`.

Use them as `$HOST`, `$REMOTE` and `$SITE` below.

## 2. Check locally

Stop if any of these fail, and report the failure:

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
```

## 3. Preview, then copy

These are excluded because they are private or machine-specific. Never remove an exclude:
`.env`, `config.toml`, `data/` (Zepto tokens, Claude sessions, photos), `db/*.sqlite3*`
(order history), `*PLAN.md`, `.git`, `.venv`, caches, `.claude/`.

```bash
EXCLUDES="--exclude .venv --exclude data --exclude db/*.sqlite3* --exclude .git \
  --exclude __pycache__ --exclude *.egg-info --exclude .pytest_cache --exclude .ruff_cache \
  --exclude .env --exclude config.toml --exclude .DS_Store --exclude *PLAN.md --exclude .claude"

rsync -azi --dry-run $EXCLUDES ./ "$HOST:$REMOTE/"   # show the user what will change
rsync -azi $EXCLUDES ./ "$HOST:$REMOTE/"
```

Notes:
- Don't use `--delete`. Removing files on the server can wait for the user to do it.
- macOS's built-in rsync has no `--chmod`. Never copy secret files with this command; if
  `.env` or tokens ever need copying, use `scp` into a file created with mode 600 first, and
  only when the user asks.

Then confirm the server's private files are still there and private:

```bash
ssh "$HOST" "cd $REMOTE && ls -l .env config.toml data/zepto_tokens.json"
```

`.env` and `data/zepto_tokens.json` should be `-rw-------`.

## 4. Rebuild

Check whether the SSH user can run Docker: `ssh "$HOST" docker ps`.

- If it can, run `ssh "$HOST" "cd $REMOTE && docker compose up -d --build"`.
- If it gets "permission denied", don't try sudo. Ask the user to run
  `sudo docker compose up -d --build` in `$REMOTE` on the server, and wait for them.

## 5. Verify the live site

After the rebuild:

```bash
curl -s -o /dev/null -w '%{http_code}\n' "$SITE/"                       # expect 200
curl -s "$SITE/api/status"                                               # zepto_logged_in: true
echo | openssl s_client -connect "${SITE#https://}:443" -servername "${SITE#https://}" \
  2>/dev/null | openssl x509 -noout -issuer -enddate                    # Let's Encrypt, not expiring
diff <(curl -s "$SITE/app.js") zepto_ordering/static/app.js && echo "frontend is current"
```

If `app.js` differs, the server is still running the old image: the rebuild didn't happen.

Don't place, review or cancel any order here. For an end-to-end check, use the
`live-smoke-test` skill.

## Report

Tell the user which files changed, whether the rebuild ran, and the results of each check.
