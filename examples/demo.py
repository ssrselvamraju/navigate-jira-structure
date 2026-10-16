#!/usr/bin/env python3
"""Offline demonstration using synthetic data and the production traversal helper."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from fetch_structure_forest import extract_anchor_subtree
from normalize_forest import load_rows, validate_forest


def main():
    data = json.loads((ROOT / 'examples' / 'forest.json').read_text())
    validate_forest(load_rows(data))
    print('OFFLINE DEMO | synthetic data | no network or credentials\n')
    print('Structure board:')
    for row in data['rows']:
        print('  ' * row['depth'] + (row.get('name') or row['key']))
    print('\nTask: List issues under DEMO-100, grouped by bucket.\n')
    subtree, groups = extract_anchor_subtree(data['rows'], None, '1')
    assert groups is not None
    for name, group in groups.items():
        print(f"{name}: {', '.join(group['issueKeys'])}")
    keys = [r['key'] for r in subtree if not r['key'].startswith('STRUCTURE-NONISSUE-')]
    assert keys == ['DEMO-100', 'DEMO-101', 'DEMO-102', 'DEMO-103']
    assert groups['Platform']['issueKeys'] == ['DEMO-101', 'DEMO-102']
    assert groups['Experience']['issueKeys'] == ['DEMO-103']
    print('\nBoundary verified: DEMO-200 and DEMO-201 are outside this subtree.')
    print('Group names and issue keys are prefilled; no Cloud name lookup is shown.')


if __name__ == '__main__':
    main()
