# Structure REST (Jira Data Center and Cloud)

**Structure Cloud is not a Cloud-hosted version of Structure DC** -- it is a
separate ALM Works/Tempo product with its own host, its own auth, and a
genuinely different response shape: a **nested JSON tree**, not DC's flat
`formula` string. Do not assume the DC paths or parsing logic below carry
over as-is.

## Data Center vs Cloud at a glance

| | Data Center | Cloud |
| --- | --- | --- |
| Host | Same as Jira (`JIRA_URL`) | Separate host, **not** your Jira Cloud tenant host: `https://api.structure.app/v1` (Americas -- confirmed; note **no** `/api` path segment, unlike Tempo's own doc example) or an eu-central-1 regional host |
| Auth | Jira DC PAT, `Authorization: Bearer <JIRA_PERSONAL_TOKEN>` | **Structure-specific** PAT generated on Structure's own personal-settings page in the Cloud instance, `Authorization: Bearer <STRUCTURE_CLOUD_TOKEN>` -- NOT the Jira Cloud email+API-token pair |
| Structure metadata | `GET /rest/structure/2.0/structure/{id}` | `GET /structure/{id}` -- confirmed, same `{id, name, ...}` shape |
| Forest fetch | `GET /rest/structure/2.0/forest/latest?s=<spec>` | **`POST /structure/{id}/forest`** (GET returns 405) with a JSON body (`{}` works) -- confirmed. Response is `{"state": {...}, "rows": [{"itemId": ..., "children": [...]}]}`, a **nested tree**, not a flat string |
| Item identity format | `type/id` numeric pairs, e.g. `5/240`, resolved via an `itemTypes` lookup table | Self-describing strings, e.g. `"1/jira:issue/29781"` (issue) or `"2/structure:folder/40218"` (folder) -- no lookup table needed |
| Structure id | Numeric | Numeric but a **separate id space from DC** -- confirmed across multiple tenants. Do not assume a DC id and its Cloud counterpart match; resolve by structure **name** where possible (`GET /structure` lists all, confirmed), cache the numeric id per-target |
| Board URL shape | `.../secure/StructureBoard.jspa?s=<id>` | `.../jira/apps/<appId>/<installId>/structure/board/<id>` -- confirmed against a live Cloud tenant; both shapes are recognized by `parse_structure_board_url` |
| Jira issue-key resolution | `GET /rest/api/2/issue/{issueId}?fields=key`, Bearer PAT | Same path, **Basic auth** (`JIRA_CLOUD_EMAIL:JIRA_CLOUD_API_TOKEN`) -- confirmed working, no need to move to `/rest/api/3/` |
| Rate limits | Not typically an issue | Cloud enforces stricter limits; `fetch_structure_forest.py` retries once on HTTP 429 with bounded backoff |

`scripts/fetch_structure_forest.py --target {dc,cloud}` selects the host and
auth pair for both Structure and Jira issue-resolution calls together as one
choice -- never mix a Cloud-sourced numeric issue id with a DC issue-key
lookup or vice versa, since that returns a valid-looking but wrong key
instead of an error.

## Data Center

### Base URL

Use the same host as Jira: `JIRA_URL` (example: `https://jira.example.com`).

### Authentication

Use a Jira **personal access token**: `JIRA_PERSONAL_TOKEN`.

Send on every request:

```http
Authorization: Bearer <JIRA_PERSONAL_TOKEN>
Accept: application/json
```

If you get **401**, check PAT scope, expiry, and org policy. If Structure denies the token while core Jira works, fall back to a manual Structure export (same as before this REST path existed).

### Endpoints used by the helper script

### Structure metadata

```http
GET {JIRA_URL}/rest/structure/2.0/structure/{structureId}
```

Returns JSON including `id` and `name` when permitted.

### Latest forest for a saved structure

ForestSpec JSON for a single saved structure:

```json
{"structureId": 12345}
```

Request (GET, URL-encoded `s` parameter):

```http
GET {JIRA_URL}/rest/structure/2.0/forest/latest?s=%7B%22structureId%22%3A12345%7D
```

Equivalent `s` value after decoding: `{"structureId":12345}`.

For very large ForestSpec values, the vendor documents **POST** to the same forest resource with a JSON body (no URL length limit). The helper script uses GET only.

### Response shape (formula)

The important field is **`formula`**: a comma-separated list of row segments. Each segment matches:

`rowId:depth:itemIdentity[:optionalSemantic]`

Do not split on every colon; the third field may contain `/` (example: `5/240`). See Tempo Structure (DC) documentation **Forest Resource**.

Typical response also includes:

- **`spec`**: echoes the forest specification (for example `structureId`).
- **`itemTypes`**: maps numeric type ids to Java class names for generators, folders, etc.
- **`version`**: `signature` and `version` for optimistic updates (not needed for read-only use).

### Mapping rows to Jira issue keys

- Rows that reference a **Jira issue** often use a **plain numeric** item identity (Jira internal issue id). Resolve to a key with Jira REST: `GET /rest/api/2/issue/{issueId}?fields=key`.
- Rows with **`type/id`** item identities are usually **generators, folders, or other Structure types**; use placeholders or skip depending on whether you need strict issue-only lists (see `scripts/fetch_structure_forest.py`).

### Resolving names for custom-field-option grouping rows (DC, confirmed live)

A common `type/id` row is **`itemTypes[type] == "...type-cf-option"`**: a
grouping value produced by an S-JQL generator that groups by a custom field
(e.g. a "Leg" field on a planning board, producing rows named "Leg 1A", "Leg
2", "Clearance", etc.). Unlike plain folders/generators, this grouping
value's `id` **is** resolvable to human text -- it is a Jira
`customFieldOption` id, via the **standard Jira REST API** (not
Structure-specific):

```http
GET {JIRA_URL}/rest/api/2/customFieldOption/{id}
```

Returns `{"id", "value", "disabled", "childrenIds", ...}`; `value` is the
display text (confirmed live against a production DC instance, e.g. one
option resolved to `"Leg 1A"`). `fetch_structure_forest.py --resolve-names`
wraps this, caching by option id, and adds a `name` field to the matching
rows.

**Caveat:** the `"cf-option"` substring match on `itemTypes[type]` is an
*observed* naming convention from one production board's custom-field
generator, not a documented Structure API contract -- the vendor docs don't
enumerate `itemTypes` class-name strings. If a future generator type on
another board uses a differently-named class that doesn't contain
`"cf-option"`, this degrades gracefully (falls through to a plain
`non_issue` row, no crash, just no `name`), but it's worth knowing this is
pattern-matched rather than guaranteed.

**Still unresolved:** plain `type-folder` and `type-generator` rows (not a
custom-field grouping) have no known name-lookup endpoint -- several
plausible paths (`/rest/structure/2.0/folder/{id}`, `/item/{type}/{id}`,
`/generator/{id}`) were tried live against a production DC instance and all
returned 404. If a user needs a plain folder's display name, there is no
current REST path; fall back to the Structure UI. The Cloud equivalent of a
cf-option grouping row (if one exists) is also unconfirmed -- see
`classify_item_identity_cloud`'s docstring.

### Helper script (`fetch_structure_forest.py`)

- **Default:** only Structure GETs (metadata + forest). **No** per-issue Jira GETs.
  Parsed JSON includes **`formulaRaw`** (full `formula` string; DC only --
  empty for Cloud, whose raw tree is under `forestMeta.rawRows` instead) plus
  **`rows`** with numeric ids as keys when applicable.
- **`--resolve-issues`:** opt in to Jira `GET /rest/api/2/issue/{id}` for each
  distinct issue id (heavy on large boards).
- **`--resolve-names`:** opt in to Jira `GET /rest/api/2/customFieldOption/{id}`
  for each distinct cf-option grouping row, adding a `name` field (DC only;
  see above).
- **`--best-effort`:** with `--resolve-issues`/`--resolve-names`, a single
  failed per-id lookup (deleted issue, deleted custom-field option) is
  skipped with a stderr warning instead of exiting the whole run. Off by
  default, matching the pre-existing hard-fail behavior.
- **`--anchor-issue-id`/`--anchor-row-id`:** narrow `rows` to one row's
  subtree (Structure's own nesting rule: every following row with depth
  greater than the anchor's, up to the next row at or above it). With
  `--resolve-names`, also returns a `groups` map of each immediate
  bucket-header's resolved name to its issue keys -- live-verified against a
  21,810-row production board: anchoring on one initiative's issue id
  correctly reproduced all 8 named buckets and their issue counts.
- **`--structure-only`:** skip parsing into **`rows`**; output **`formulaRaw`**
  and **`forestMeta`** only (still two Structure GETs).

### Version and fallbacks

If `GET /rest/structure/2.0/...` returns **404**, your instance may use a different Structure REST revision. Check the ALM Works / Tempo **Structure (DC)** documentation for your installed Structure version and adjust the prefix.

## Cloud (confirmed against a live tenant)

The endpoints below were verified directly with `curl` (not the
JS-rendered `apidocs.structure.app` console, which doesn't render without a
browser session) against two live Jira Cloud tenants using a Structure
Cloud PAT, and are implemented in `scripts/fetch_structure_forest.py`.

- **Base URL**: `https://api.structure.app/v1` (Americas). Note **no**
  `/api` path segment -- Tempo's own doc
  ([Using the Structure Cloud API](https://help.tempo.io/structure/latest/using-the-structure-cloud-api))
  shows an inconsistent `/api/v1` example that 404s; `/v1/...` is what
  actually works. The eu-central-1 regional host is analogous but unverified
  by us.
- **Auth**: `Authorization: Bearer <token>`, where the token is a
  **Structure-specific** PAT from
  `https://<tenant>.atlassian.net/plugins/servlet/ac/com.almworks.jira.structure/personal-structure-settings`
  -- a different token from the Jira Cloud API token used for core Jira
  calls.
- **List structures**: `GET /structure` -> `{"structures": [{"id", "name",
  "description", "url", "accessLevel"}, ...]}`. This is the name-lookup
  endpoint (`resolve_structure_id_by_name`). Note: this appears to list
  only structures the token's user has favorited/starred, not every
  structure in the tenant -- confirmed by comparing against a tenant where
  it returned an empty list despite known structures existing.
- **Structure metadata**: `GET /structure/{id}` -> same shape as one entry
  above.
- **Forest**: `POST /structure/{id}/forest` (GET returns **405**) with a
  JSON body -- `{}` is sufficient to get the latest forest. Response:
  `{"state": {"state": "latest", "reportedAt": ...}, "rows": [{"itemId":
  "<n>/<namespace>:<kind>/<id>"[, "children": [...]]}]}`. This is a
  **nested tree** (depth = recursion depth into `children`), not DC's flat
  comma-separated `formula` string -- `parse_cloud_forest_segments` in
  `fetch_structure_forest.py` flattens it depth-first pre-order into the
  same `(rowId, depth, itemIdentity)` shape DC's parser produces, with a
  synthetic sequential `rowId` (Cloud rows have no explicit row id).
  `itemId` values are self-describing (`jira:issue` vs `structure:folder`,
  etc.) -- no `itemTypes` lookup table needed, unlike DC.
- **Large structures / pagination**: confirmed working up to 5,968 rows
  returned in a single `POST` response with no pagination cursor or
  truncation observed. Structure's own migration notes mention a 30,000-
  issue cap per structure; behavior at or near that scale is unverified --
  if a larger production structure's forest response turns out to be
  paginated or truncated, that will need a follow-up fix to
  `fetch_forest`/`parse_cloud_forest_segments`.

`fetch_forest`, `fetch_structure_meta`, and `resolve_structure_id_by_name`
for `target == "cloud"` in `scripts/fetch_structure_forest.py` are
implemented per the above. Jira issue-key resolution for Cloud
(`jira_issue_key` with Basic auth) uses the same confirmed path.

## Secrets

Never commit tokens or a `.env`/`.mcp.json` file with real credentials. Scripts should read tokens only from the process environment.
