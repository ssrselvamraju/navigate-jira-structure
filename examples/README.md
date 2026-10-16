# Offline demo

Run from the repository root:

```bash
python3 examples/demo.py
```

Uses only Python's standard library, a synthetic normalized forest in
[forest.json](forest.json), and the production `extract_anchor_subtree` function.
No network requests, `.env` loading, or credentials are needed. This is a
reproducible CLI demonstration, not a live Claude Code or Cursor recording.

The board contains two initiatives. Selecting `DEMO-100` returns:

```text
Platform: DEMO-101, DEMO-102
Experience: DEMO-103

Boundary verified: DEMO-200 and DEMO-201 are outside this subtree.
```

The fixture has prefilled group names and issue keys. In live use, issue-key
resolution requires `--resolve-issues`; custom-field grouping-name resolution
requires `--resolve-names` and is supported on Data Center only.

## Try your own board

Complete the credential setup in the main README, then fetch a Cloud subtree
using your board URL and the anchor's numeric Jira issue ID:

```bash
python3 scripts/fetch_structure_forest.py \
  --url 'https://your-company.atlassian.net/jira/apps/<appId>/<installId>/structure/board/67890' \
  --anchor-issue-id 100 --resolve-issues --out /tmp/structure-subtree.json
```

Replace the URL and ID with your own values. For Data Center, use a Data Center
board URL and `--target dc`; add `--resolve-names` for custom-field bucket names.
If an issue appears more than once, use `--anchor-row-id` to select its occurrence.
Do not share private exports or tokens in a public demo.
