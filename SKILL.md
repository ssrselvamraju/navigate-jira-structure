---
name: navigate-jira-structure
description: >-
  Access a Jira Structure board -- Data Center (StructureBoard.jspa URL or
  structure id, the s= query parameter) or Cloud (a <tenant>.atlassian.net
  /jira/apps/.../structure/board/<id> link) -- and pull its live forest/issues
  using your Jira PAT (DC) or Structure Cloud PAT + Jira Cloud API token
  (Cloud). Use when asked to access, read, or open a Jira Structure board, a
  StructureBoard.jspa?s=<id> link, a Jira Cloud Structure board link, or a
  numeric structure id -- and for Structure (ALM Works / Tempo) forests,
  automation, S-JQL, subtree models, forest exports, and traversing from an
  anchor issue.
---

# Jira Structure

## What this skill covers

Jira **Structure** (by ALM Works / Tempo) is a separate product area from
native Epic/parent/subtask links. A **forest** is an ordered list of **rows**
with **depth** values that encode one or more trees. **Automation**
(generators, inserters, extenders, transformations) can pull issues using
**S-JQL** or other rules. Do **not** assume Structure order equals Jira issue
link order unless the user confirms it.

## When plain Jira MCP is enough

If the user only needs JQL search, issue fields, comments, or transitions on
known keys, use the Jira MCP directly without building a Structure model.

## When you need Structure-specific reasoning

Use this skill when any of the following hold:

- The user shares a **StructureBoard.jspa** link (e.g.
  `.../secure/StructureBoard.jspa?s=2790`) or a numeric **structure id**, or
  asks you to **access / read / open** a Structure board. This is the common
  case: you do **not** need WebFetch or browser access -- fetch the live forest
  via Structure REST with the Jira PAT (see below). This is the **Jira Data
  Center** flow. If your organization is on (or migrating to) Jira Cloud, a
  **Structure Cloud** flow (separate host, separate Structure-specific
  token, `--target cloud`) is **supported alongside it** -- see
  [references/structure-rest.md](references/structure-rest.md) for confirmed
  endpoint shapes and current known limits (e.g. pagination behavior on very
  large boards is unverified). Cloud board URLs look like
  `.../jira/apps/<appId>/<installId>/
  structure/board/<id>`, not the DC `.../secure/StructureBoard.jspa?s=<id>`
  shape -- both are recognized by `scripts/fetch_structure_forest.py`, which
  **auto-selects `--target` from the URL shape** when `--target` isn't
  passed explicitly. If you (or the user) pass an explicit `--target` that
  contradicts the URL's shape (e.g. a Cloud board link with `--target dc`),
  the script refuses and errors rather than silently fetching the wrong
  structure -- the numeric id spaces are confirmed distinct between DC and
  Cloud, so mixing them returns a valid-looking but wrong forest, not a 404.
- The user references a **Structure board**, **Structure row**, or **S-JQL**.
- The user pastes **Structure automation** text (generator/inserter settings).
- The user needs **child order under a root** that may not match Jira links.
- The user provides a **forest export** (JSON/CSV) or asks to traverse a tree.

## Fetching the forest from a Structure board URL (preferred when possible)

When the user supplies a board URL like
`/secure/StructureBoard.jspa?s=<structureId>` (or they give `<structureId>`
directly) **and** they have Jira credentials available (`JIRA_URL`,
`JIRA_PERSONAL_TOKEN` in the environment -- see
[README.md](README.md) for setup, including an interactive
`scripts/setup.py` helper), **prefer fetching the authoritative forest** via
Structure REST before asking for a manual export.

1. **Parse `structureId`** from the query string: parameter **`s=<id>`** is the
   usual form. Example: `...StructureBoard.jspa?s=12345` means structure id **12345**.
2. **Fetch** using Structure REST 2.0 (see [references/structure-rest.md](references/structure-rest.md)):
   `GET /rest/structure/2.0/forest/latest?s=` + URL-encoded JSON
   `{"structureId":<id>}`.
3. **Run the repo helper** (recommended): `scripts/fetch_structure_forest.py`.
   **Always pass `--out <path>`** (e.g. `--out /tmp/forest.json`) -- without
   it the script prints the full JSON to stdout, and a large board (a few
   thousand rows is normal in production) would dump the entire forest into
   the conversation. Read back only the slice you need afterward (e.g. `jq`
   a specific depth/anchor subtree, or a short Python snippet), not the
   whole file. Pass `--url` or `--structure-id`, plus **`--target {dc,cloud}`** if you
   want to set it explicitly (usually unnecessary: when `--url` is given,
   `--target` is **auto-inferred from the URL's shape** -- a DC `.jspa?s=`
   link implies `dc`, a Cloud `.../structure/board/<id>` link implies
   `cloud`; an explicit `--target` that contradicts the URL's shape is a
   hard error, not a silent fetch of the wrong structure). `--target` picks
   the Structure host+token and the Jira issue-resolution host+credential
   **together as one pair** -- never mix them. For `dc` it reads
   `JIRA_URL`/`JIRA_PERSONAL_TOKEN`; for `cloud` it reads
   `STRUCTURE_CLOUD_BASE_URL`/`STRUCTURE_CLOUD_TOKEN` plus
   `JIRA_CLOUD_BASE_URL`/`JIRA_CLOUD_EMAIL`/`JIRA_CLOUD_API_TOKEN` (all five
   required together). `--target cloud` forest/metadata fetching is
   implemented and verified against a live Cloud tenant (see
   [references/structure-rest.md](references/structure-rest.md) for the
   confirmed endpoint shapes -- a nested JSON tree, unlike DC's flat
   `formula` string). On **Cloud**, structure ids are confirmed to be a
   separate id space from DC's, so prefer **`--structure-name`** (Cloud
   only -- `--structure-id` resolution by name is not implemented for DC)
   over a hardcoded `--structure-id` when pointing at a Cloud tenant you
   haven't resolved an id for yet.
   **By default** it does **not** call Jira per issue;
   issue rows use **numeric Jira ids** as `key`. Pass **`--resolve-issues`** to
   map ids to issue keys (many extra API calls). Output includes **`formulaRaw`**
   (vendor formula string) for debugging. Use **`--structure-only`** for only the
   two Structure GETs plus `formulaRaw` and metadata, with an empty **`rows`**
   array (fast). JSON includes a **`rows`** array compatible with
   `scripts/normalize_forest.py` when not using `--structure-only`.
   If the user asks to find or group by a **named Leg/bucket/category**
   produced by an S-JQL generator grouping on a custom field (e.g. "Leg 1A",
   "Clearance"), those rows have **no name in the forest itself** -- pass
   **`--resolve-names`** (DC only) to resolve them via Jira's
   `customFieldOption` REST, added as a `name` field on the matching
   non-issue rows. Plain Structure folders/generators (not a custom-field
   grouping) still have no resolvable name; see
   [references/structure-rest.md](references/structure-rest.md). Pass
   **`--best-effort`** alongside `--resolve-issues`/`--resolve-names` on a
   large or messy board to skip a single deleted/unresolvable id with a
   stderr warning instead of aborting the whole fetch.
4. **For "find X under this anchor" tasks** (e.g. "find the Q3 Planning
   initiative and list past-due tickets under each Leg"): first resolve the
   anchor to a numeric Jira issue id with the Jira MCP's own search (e.g.
   `summary ~ "Q3 Planning"`) -- this script does not do text search,
   only forest navigation. Then pass that id as **`--anchor-issue-id`** to
   narrow the output to just the anchor's subtree (every row nested under
   it, per Structure's own depth-based nesting rule). Combined with
   `--resolve-names`, the response also includes **`groups`**: each
   immediate Leg/bucket header's name mapped to its own issue keys --
   exactly the "tickets under each Leg" shape, computed in one call instead
   of fetching the whole forest and slicing it by hand. If the anchor issue
   occupies more than one row on the board, the script errors and lists
   every occurrence so you can retry with `--anchor-row-id` to pick one.
5. **If REST fails** (401, 403, 404, no token, or network): fall back to the
   **manual** flows below. Do not block the user on Structure REST.

## Manual inputs (always supported)

These paths stay valid even when no URL or token is available:

- **Pasted JSON** with a **`rows`** array (`key`, `depth`, optional extra fields).
- **Structure automation** text only (reasoning about roots and child rules).
- **Explicit parent maps** or ordered key lists with depths.

If a unique forest cannot be built from automation text alone, ask for an
export, a structure id / URL for REST, or an explicit parent map, per **Gap
handling** below.

## Core model

**Forest**: sequence of `(row_id, depth)` pairs (conceptually). Same issue key
may appear more than once only if the user says so; default is unique rows.

**Subtree from anchor**: pick a row (or issue key tied to a row). The subtree
is all following rows with depth strictly greater than the anchor depth until
you hit a row with depth less than or equal to the anchor depth.

**Sibling order**: preserve source order from the forest or export. If order is
unknown, state the assumption and use stable key order only if the user agrees.

## Map automation text to intent

Structure UIs vary. From user-provided automation, extract:

1. **Root scope**: which top-level issues or JQL define roots.
2. **Child sources**: JQL inserter, linked issues, subtasks, filters, extenders.
3. **Direction and link types**: depends on / blocks / relates to, etc., if given.
4. **Ordering**: manual vs sorted field vs generator default.

If two interpretations remain, stop and ask one clarifying question.

## S-JQL vs JQL

Treat **S-JQL** as Structure query language. Do not paste S-JQL into generic JQL
search tools unless the user confirms the endpoint accepts it. Prefer Structure
REST or an export when S-JQL is involved.

## Traversal and matching

From the chosen anchor, walk the subtree in **depth-first pre-order** unless the
user asks for breadth-first.

**Rule vocabulary** (combine with AND unless the user says OR):

- `issue_type in (...)` or single type
- `status in (...)` or single status
- `labels includes <name>` (any label match)
- `assignee is <account>` or `assignee empty`
- `priority in (...)`
- `summary matches /regex/` or `summary contains "substring"` (case policy: ask
  if unclear)
- `custom field <id or name> <predicate>` only when field id is known from
  your Jira MCP's issue-get tool or the user specifies it

Skip or ignore rows whose `key` is a placeholder such as `STRUCTURE-NONISSUE-*`
when the user only cares about real Jira issues.

Return **issue keys** that match. De-duplicate keys while preserving first-seen
order unless the user wants all occurrences.

## Gap handling

If you cannot build a unique forest from automation text alone, ask for one of:

- A **Structure export** (JSON/CSV) for that board, or
- A **Structure board URL** or **structure id** and permission to run Structure
  REST (or the helper script) with `JIRA_URL` / `JIRA_PERSONAL_TOKEN`, or
- An explicit **parent map** or ordered key list with depths.

## Handoff to a Jira MCP

After you have target keys, use whichever Jira MCP server is configured in
your environment to act on them:

1. Fetch details with an issue-get tool (batch as needed) or an issue-search
   tool with a bounded JQL on `key in (...)`.
2. Apply updates with an issue-update tool, comments with an add-comment
   tool, status changes with a transition tool (checking available
   transitions first).

Exact tool names depend on which MCP server you're running -- this skill's
forest-fetching script has no MCP dependency itself, only this handoff step
does:

- **Jira Cloud**: [Atlassian's official Rovo MCP Server](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/set-up-atlassian-rovo-mcp-server/)
  is a hosted, zero-install option (OAuth 2.1 or API token) -- add it with
  e.g. `claude mcp add --transport http atlassian https://mcp.atlassian.com/v1/mcp/authv2`.
  Its tool names differ from the community server below; check your
  client's tool list.
- **Jira Data Center / Server, or a self-hosted Cloud option**: the
  community [`mcp-atlassian`](https://pypi.org/project/mcp-atlassian/)
  package (`uvx mcp-atlassian` or `pip install mcp-atlassian`) is what this
  skill has actually been tested against throughout its development. Its
  tool names follow a `jira_<verb>` pattern, e.g. `jira_get_issue`,
  `jira_search`, `jira_update_issue`, `jira_add_comment`,
  `jira_get_transitions` / `jira_transition_issue`.

See [README.md](README.md) for environment variable setup for either path.

## Gotcha: expired token

Jira **Personal Access Tokens** (`JIRA_PERSONAL_TOKEN`) expire and can be
revoked, and nothing auto-refreshes them. An expired token is the most common
failure here — symptoms are **401/403** from the REST helper and auth errors or
empty/denied results from the Jira MCP tools.

**Do not work around an auth failure** by retrying, switching endpoints, or
falling back to manual exports. Stop and ask the user to check token expiry
first: regenerate at your Jira instance's PAT settings page (usually
`<JIRA_URL>/secure/ViewProfile.jspa?selectedTab=com.atlassian.pats.pats-plugin:jira-user-personal-access-tokens`
on Data Center/Server), update `JIRA_PERSONAL_TOKEN`, then retry. Only
consider the manual fallbacks once the token is confirmed valid.

Same rule for `--target cloud`: a 401/403 there means check
`STRUCTURE_CLOUD_TOKEN` (Structure's own token -- **not** the same token as
`JIRA_CLOUD_API_TOKEN` -- generate a fresh one at
`https://<tenant>.atlassian.net/plugins/servlet/ac/com.almworks.jira.structure/personal-structure-settings`
for the specific tenant in use) or `JIRA_CLOUD_EMAIL`/`JIRA_CLOUD_API_TOKEN`
(from [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)).
A Structure Cloud token is tenant-specific -- confirmed empirically that a
token from one tenant does not work against another (the same structure id
silently resolved to a different, unrelated board rather than erroring). Do
not silently retry against `dc` config, or against a different tenant's
token, instead.

## Optional: authoritative forest (REST details)

See [references/structure-rest.md](references/structure-rest.md) for endpoints,
headers, and the `formula` field.

## Optional: normalize exports

If the user pastes JSON that resembles a forest export, you may run
`scripts/normalize_forest.py` to validate depth sequences. Output from
`fetch_structure_forest.py` includes a `rows` array that this script accepts.
See [README.md](README.md).
