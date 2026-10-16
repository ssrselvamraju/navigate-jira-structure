# Changelog

## 0.1.0 — 2026-10-16

Initial public release of navigate-jira-structure.

- Agent instructions for Claude Code and Cursor.
- Fetch ordered Structure forests from Jira Cloud (default) or Data Center.
- Select an anchor subtree, with explicit handling of duplicate issue rows.
- Optional issue-key resolution; Data Center custom-field grouping-name resolution.
- Interactive credential setup and forest depth validation.
- Credential-free synthetic demo using the production subtree extraction helper.
- README workflow examples and Cloud/Data Center capability comparison.

Cloud structure-name lookup is supported; Data Center name lookup is not.
Cloud grouping-name resolution and behavior near the documented 30,000-issue
limit remain unverified or unsupported as described in the README.
