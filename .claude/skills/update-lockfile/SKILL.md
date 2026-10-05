---
name: update-lockfile
description: Regenerate requirements.lock (exact versions for the Docker image) inside a Linux Python 3.12 container, then rebuild and test. Use after changing dependencies in pyproject.toml, when upgrading packages, or when the user asks to update the lock file.
---

# Update requirements.lock

`pyproject.toml` holds version ranges. `requirements.lock` pins the exact versions the
Docker image installs, so every build is the same. The lock must be generated on Linux with
the image's Python version, never on macOS: some packages (the Claude Agent SDK, curl_cffi)
ship platform-specific builds.

## 1. Generate

Check the Python version in `Dockerfile` (`FROM python:3.12-slim`) and use the same tag:

```bash
cp requirements.lock /tmp/requirements.lock.old
{
  echo "# Exact versions for the Docker image (linux, Python 3.12). Regenerate after changing"
  echo "# dependencies in pyproject.toml; see README."
  docker run --rm --platform linux/amd64 -v "$PWD/pyproject.toml:/src/pyproject.toml:ro" \
    python:3.12-slim sh -c 'cd /src && python -c "import tomllib; \
    print(chr(10).join(tomllib.load(open(\"pyproject.toml\",\"rb\"))[\"project\"][\"dependencies\"]))" \
    > /tmp/req.txt && pip install -q --disable-pip-version-check --root-user-action=ignore \
    -r /tmp/req.txt && pip freeze --disable-pip-version-check'
} > requirements.lock
diff /tmp/requirements.lock.old requirements.lock
```

`--platform linux/amd64` matches the x86_64 home server.

## 2. Review the changes

From the diff, list version changes for the direct dependencies (claude-agent-sdk, mcp,
fastapi, curl_cffi, httpx, httpx2, pillow, uvicorn). For a major or 0.x minor bump, check the
package's changelog for breaking changes. The MCP SDK and Claude Agent SDK have both renamed
APIs before.

## 3. Verify

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
docker compose build app
docker run --rm --entrypoint python grocery-comparison-app -c "import zepto_ordering.main"
```

The image name depends on the project folder name; use `docker compose images` if the one
above doesn't exist. If the build or import fails, restore the old lock with
`cp /tmp/requirements.lock.old requirements.lock` and report what broke.

## Report

Tell the user which packages changed and whether tests and the image build passed. To roll
it out, use the `deploy` skill.
