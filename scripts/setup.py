#!/usr/bin/env python3
"""
Interactive credential setup for fetch_structure_forest.py. Prompts for a
target (dc/cloud) and the matching required values, then writes them to a
KEY=VALUE env file (default: .env next to this script's parent directory,
same file fetch_structure_forest.py loads automatically).

This is a convenience only -- everything it does, you can do by hand:
copy .env.example to .env and fill in the values yourself. This script
exists purely to make the first run easier; it adds no new environment
variables or behavior to fetch_structure_forest.py itself.

Secrets are read with getpass (not echoed while typing) and are never
printed back to the terminal, including in the final confirmation.

Usage:
  python3 scripts/setup.py [--out PATH]

ASCII only on stdout/stderr.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import Dict, List, Tuple


def _skill_root_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or default


def _prompt_secret(label: str) -> str:
    while True:
        val = getpass.getpass(f"{label} (input hidden): ").strip()
        if val:
            return val
        print("  (required, please enter a value)")


def _collect_dc() -> List[Tuple[str, str]]:
    print("\n-- Data Center / Server --")
    jira_url = _prompt("JIRA_URL (e.g. https://jira.example.com)")
    while not jira_url:
        print("  (required)")
        jira_url = _prompt("JIRA_URL (e.g. https://jira.example.com)")
    token = _prompt_secret("JIRA_PERSONAL_TOKEN")
    return [("JIRA_URL", jira_url), ("JIRA_PERSONAL_TOKEN", token)]


def _collect_cloud() -> List[Tuple[str, str]]:
    print("\n-- Cloud --")
    structure_base = _prompt(
        "STRUCTURE_CLOUD_BASE_URL", "https://api.structure.app/v1"
    )
    structure_token = _prompt_secret("STRUCTURE_CLOUD_TOKEN")
    jira_cloud_base = _prompt("JIRA_CLOUD_BASE_URL (e.g. https://your-tenant.atlassian.net)")
    while not jira_cloud_base:
        print("  (required)")
        jira_cloud_base = _prompt(
            "JIRA_CLOUD_BASE_URL (e.g. https://your-tenant.atlassian.net)"
        )
    jira_cloud_email = _prompt("JIRA_CLOUD_EMAIL")
    while not jira_cloud_email:
        print("  (required)")
        jira_cloud_email = _prompt("JIRA_CLOUD_EMAIL")
    jira_cloud_token = _prompt_secret("JIRA_CLOUD_API_TOKEN")
    return [
        ("STRUCTURE_CLOUD_BASE_URL", structure_base),
        ("STRUCTURE_CLOUD_TOKEN", structure_token),
        ("JIRA_CLOUD_BASE_URL", jira_cloud_base),
        ("JIRA_CLOUD_EMAIL", jira_cloud_email),
        ("JIRA_CLOUD_API_TOKEN", jira_cloud_token),
    ]


def _load_existing(path: str) -> Dict[str, str]:
    existing: Dict[str, str] = {}
    if not os.path.isfile(path):
        return existing
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            existing[key.strip()] = val.strip().strip('"').strip("'")
    return existing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        default=None,
        help="Where to write the env file (default: <skill-dir>/.env)",
    )
    args = ap.parse_args()

    out_path = args.out or os.path.join(_skill_root_dir(), ".env")

    existing = _load_existing(out_path)
    if existing:
        print(f"{out_path} already exists with {len(existing)} variable(s).")
        resp = input("Overwrite it? [y/N]: ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted -- no changes made.")
            return 1

    target = ""
    while target not in ("dc", "cloud"):
        target = _prompt("Target (dc/cloud)", "dc").strip().lower()
        if target not in ("dc", "cloud"):
            print("  please enter 'dc' or 'cloud'")

    pairs = _collect_dc() if target == "dc" else _collect_cloud()

    lines = [
        "# Written by scripts/setup.py -- do not commit this file.",
        f"# Target: {target}",
    ]
    for key, val in pairs:
        lines.append(f"{key}={val}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass  # best-effort on platforms where chmod semantics differ

    names = ", ".join(k for k, _ in pairs)
    print(f"\nWrote {out_path} with {len(pairs)} variable(s): {names}")
    print("(Values are not printed here. Edit the file directly if you need to change one.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
