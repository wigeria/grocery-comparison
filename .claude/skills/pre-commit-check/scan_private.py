"""Scan the files git would publish for secrets, personal data and style slips.

Private values are read from .env and config.toml, which are gitignored, and
searched for in every file that would be committed. Only file:line and the kind
of match are printed, never the value itself.

    python .claude/skills/pre-commit-check/scan_private.py [--extra TEXT ...]

--extra adds more private strings to look for (server hostname, LAN IP, ...).
Exits 1 if anything is found.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SECRET_PATTERNS = {
    "Anthropic key or token": re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "bearer token": re.compile(r"Bearer [A-Za-z0-9._-]{30,}"),
    "private IPv4 address": re.compile(
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
    ),
}
# Characters that read as AI-written or don't belong in this repo's text.
# Built from code points so this file stays plain ASCII.
STYLE_PATTERNS = {
    "em or en dash": re.compile(f"[{chr(0x2013)}{chr(0x2014)}]"),
    "emoji": re.compile(f"[{chr(0x1F300)}-{chr(0x1FAFF)}{chr(0x2600)}-{chr(0x27BF)}]"),
    "smart quote": re.compile(f"[{chr(0x2018)}{chr(0x2019)}{chr(0x201C)}{chr(0x201D)}]"),
}
# Values in .env that are not private.
PUBLIC_ENV_VALUES = {"", "localhost", "internal", "duckdns", "1000", "501", "20"}
ENV_KEYS_TO_CHECK = {"CLAUDE_CODE_OAUTH_TOKEN", "DUCKDNS_TOKEN", "SITE_ADDRESS"}


def files_to_publish() -> list[Path]:
    """Files git would commit: tracked plus untracked-but-not-ignored."""
    if (ROOT / ".git").exists():
        command = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
        output = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
    else:
        # No repo yet: use a throwaway git dir so .gitignore rules still apply.
        with tempfile.TemporaryDirectory() as git_dir:
            base = ["git", f"--git-dir={git_dir}", f"--work-tree={ROOT}"]
            subprocess.run([*base, "init", "-q"], check=True)
            output = subprocess.run(
                [*base, "ls-files", "--others", "--exclude-standard"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
    return [ROOT / line for line in output.stdout.splitlines() if line]


def private_values(extra: list[str]) -> dict[str, str]:
    """Map label -> private string, from .env, config.toml and --extra."""
    values: dict[str, str] = {}

    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            value = value.strip().strip("\"'")
            if key.strip() in ENV_KEYS_TO_CHECK and value not in PUBLIC_ENV_VALUES:
                values[f".env {key.strip()}"] = value

    config_file = ROOT / "config.toml"
    if config_file.exists():
        config = tomllib.loads(config_file.read_text())
        address_id = config.get("zepto", {}).get("delivery_address_id")
        if address_id:
            values["config.toml delivery_address_id"] = address_id
        for key in ("latitude", "longitude"):
            coordinate = config.get("blinkit", {}).get(key)
            if coordinate:
                # Four decimals is about 10 m, close enough to identify a home.
                whole, _, fraction = str(coordinate).partition(".")
                values[f"config.toml {key}"] = f"{whole}.{fraction[:4]}"

    for index, text in enumerate(extra, start=1):
        values[f"--extra #{index}"] = text
    return values


def scan(paths: list[Path], values: dict[str, str]) -> list[str]:
    """Return one line per finding."""
    findings = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue  # binary files (screenshots) and files deleted since listing
        relative = path.relative_to(ROOT)
        for number, line in enumerate(text.splitlines(), start=1):
            for label, value in values.items():
                if value in line:
                    findings.append(f"{relative}:{number}: private value ({label})")
            for label, pattern in {**SECRET_PATTERNS, **STYLE_PATTERNS}.items():
                if pattern.search(line):
                    findings.append(f"{relative}:{number}: {label}")
    return findings


def main() -> int:
    """Scan and report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--extra", nargs="*", default=[], help="more private strings")
    args = parser.parse_args()

    paths = files_to_publish()
    values = private_values(args.extra)
    findings = scan(paths, values)

    print(f"Scanned {len(paths)} files for {len(values)} private values and common secrets.")
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} finding(s).")
        return 1
    print("Nothing found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
