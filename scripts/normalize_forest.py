#!/usr/bin/env python3
"""
Validate a simple forest JSON and print a normalized (key, depth) list.

Expected JSON shape (example):
{
  "rows": [
    {"key": "PROJ-1", "depth": 0},
    {"key": "PROJ-2", "depth": 1}
  ]
}

Exit code 0 on success, 1 on validation error. ASCII output only.
"""

from __future__ import annotations

import json
import sys
from typing import Any, List, Tuple


def load_rows(data: Any) -> List[Tuple[str, int]]:
    if not isinstance(data, dict):
        raise ValueError("root must be a JSON object")
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError('"rows" must be a non-empty array')
    out: List[Tuple[str, int]] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"rows[{i}] must be an object")
        # Ignore extra fields (e.g. rowId from fetch_structure_forest.py)
        key = row.get("key")
        depth = row.get("depth")
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"rows[{i}].key must be a non-empty string")
        if not isinstance(depth, int):
            raise ValueError(f"rows[{i}].depth must be an integer")
        if depth < 0:
            raise ValueError(f"rows[{i}].depth must be >= 0")
        out.append((key.strip(), depth))
    return out


def validate_forest(rows: List[Tuple[str, int]]) -> None:
    if not rows:
        raise ValueError("empty forest")
    prev = -1
    for key, depth in rows:
        if depth > prev + 1:
            raise ValueError(
                f"invalid depth jump for {key}: depth {depth} after {prev}"
            )
        prev = depth


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write(
            "usage: normalize_forest.py <path-to-json>\n"
        )
        return 1
    path = sys.argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rows = load_rows(data)
        validate_forest(rows)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        sys.stderr.write(f"error: {e}\n")
        return 1
    for key, depth in rows:
        print(f"{key}\t{depth}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
