#!/usr/bin/env python3
"""
Fetch Structure forest JSON from Jira Structure REST and emit normalized rows
for use with normalize_forest.py (same rows schema). Supports two targets,
selected together as one atomic choice via --target (never mix hosts/creds
across targets):

  --target dc     Jira Data Center / Server + Structure DC REST. Structure
                   REST 2.0 under the Jira DC host. Forest response is a
                   flat comma-separated "formula" string:
                   rowId:depth:itemIdentity.
  --target cloud  Jira Cloud + Structure Cloud REST. A SEPARATE host and
                   auth scheme from Jira Cloud itself (see
                   references/structure-rest.md). Forest response is a
                   NESTED JSON tree (rows[].children[]), not a flat formula
                   string -- a different shape from DC, not just a
                   different host. itemId values are self-describing, e.g.
                   "1/jira:issue/29781" (issue) or
                   "2/structure:folder/40218" (folder); no itemTypes lookup
                   needed (unlike DC's type/id form).

Environment (dc):
  JIRA_PERSONAL_TOKEN   (required) A Jira Data Center / Server Personal
                        Access Token.
  JIRA_URL              (required) Base URL of your Jira instance, e.g.
                        https://jira.example.com. No default is assumed --
                        a wrong or missing Jira host should fail loudly, not
                        silently point somewhere unexpected.

Environment (cloud):
  STRUCTURE_CLOUD_BASE_URL  (required) e.g. https://api.structure.app/v1
                            (Americas; note NO "/api" path segment --
                            Tempo's own docs show an inconsistent example)
                            or the eu-central-1 regional host. Same host
                            regardless of which Jira Cloud tenant you use.
  STRUCTURE_CLOUD_TOKEN     (required) Structure-specific personal access
                            token (Bearer) -- NOT your Jira Cloud API
                            token, and tied to one specific Jira tenant
                            (confirmed: a token from one tenant silently
                            resolves the same structure id to a DIFFERENT,
                            unrelated board on another tenant rather than
                            erroring -- generate a fresh token per tenant at
                            https://<tenant>.atlassian.net/plugins/servlet/ac/com.almworks.jira.structure/personal-structure-settings).
  JIRA_CLOUD_BASE_URL       (required) e.g. https://your-tenant.atlassian.net
  JIRA_CLOUD_EMAIL          (required) Atlassian account email (Basic auth).
  JIRA_CLOUD_API_TOKEN      (required) Atlassian API token (Basic auth),
                            from id.atlassian.com/manage-profile/security/api-tokens.
  All five are required together for --target cloud; the script fails hard
  listing whichever are missing rather than falling back or mixing with dc
  config, since a Cloud-sourced numeric issue id resolved against the wrong
  Jira host returns a valid-looking but WRONG issue key, not an error.

Optional file (not committed to git):
  <skill-dir>/.env   KEY=VALUE lines; loaded automatically if the file
                     exists. Override path with --env-file PATH. See
                     .env.example, or run scripts/setup.py for an
                     interactive prompt that writes this file for you.

Usage:
  fetch_structure_forest.py --target dc --structure-id 12345 [--out path.json]
  fetch_structure_forest.py --target dc --url 'https://jira.example.com/secure/StructureBoard.jspa?s=12345'
  fetch_structure_forest.py --target cloud --url 'https://your-tenant.atlassian.net/jira/apps/<appId>/<installId>/structure/board/67890'
  fetch_structure_forest.py --target cloud --structure-name 'My Board' [--out path.json]

Defaults:
  By default the script does NOT call Jira issue REST for each row (only two
  Structure GETs plus optional metadata). Issue rows use numeric ids as keys.
  Pass --resolve-issues to map ids to issue keys (many extra API calls).

Options:
  --target {dc,cloud}   Select host+auth pair for both Structure and Jira
                        issue-resolution calls together (default: dc).
  --resolve-issues      Call Jira REST to map numeric issue ids to keys (PROJ-1).
  --resolve-names       Resolve custom-field-option grouping rows (e.g. an
                        S-JQL generator grouping by a custom field) to their
                        display text via Jira REST, added as a "name" field
                        on those rows (DC only; plain folder/generator rows
                        still have no resolvable name).
  --structure-only      Only fetch Structure metadata + forest; output includes
                        formulaRaw and forestMeta, rows stay empty (fast, tiny file).
  --exclude-non-issues  Drop non-issue rows (may break depth ordering vs UI).
  --structure-name NAME Resolve a structure by name instead of numeric id
                        (structure ids are app-internal and may not survive
                        a DC->Cloud migration; treat --structure-id as a
                        cache and --structure-name as the durable lookup).

ASCII only on stdout/stderr.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

AuthBearer = Tuple[str, str]  # ("bearer", token)
AuthBasic = Tuple[str, str, str]  # ("basic", email, token)
Auth = Union[AuthBearer, AuthBasic]

MAX_429_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 0.5
RETRY_MAX_DELAY_SECONDS = 5.0


def _skill_root_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_optional_env_file(path: str) -> None:
    """Set os.environ keys from KEY=VALUE lines; skip keys already set."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _env_required(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        sys.stderr.write(f"error: {name} is not set\n")
        sys.exit(1)
    return v


def _auth_header_value(auth: Auth) -> str:
    kind = auth[0]
    if kind == "bearer":
        return f"Bearer {auth[1]}"
    if kind == "basic":
        _, email, token = auth
        raw = f"{email}:{token}".encode("utf-8")
        return f"Basic {base64.b64encode(raw).decode('ascii')}"
    raise ValueError(f"unknown auth kind: {kind!r}")


class ResolutionError(Exception):
    """Raised for a single per-item REST lookup failure (e.g. one bad issue
    id or deleted custom-field option) when the caller asked to continue
    past it (required=False) instead of aborting the whole run."""


def _request_json(
    method: str,
    url: str,
    auth: Auth,
    data: Optional[bytes] = None,
    required: bool = True,
) -> Any:
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Authorization", _auth_header_value(auth))
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")

    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_429_RETRIES:
                # Bounded backoff for a single request, not a monitoring loop.
                attempt += 1
                delay = min(RETRY_BASE_DELAY_SECONDS * attempt, RETRY_MAX_DELAY_SECONDS)
                time.sleep(delay)
                continue
            err_body = e.read().decode("utf-8", errors="replace")
            if not required:
                raise ResolutionError(f"HTTP {e.code} {e.reason}: {err_body[:500]}") from e
            sys.stderr.write(f"error: HTTP {e.code} {e.reason}\n{err_body[:2000]}\n")
            sys.exit(1)
        except urllib.error.URLError as e:
            # Connection refused, DNS failure, or socket timeout -- not an
            # HTTP response at all, so no status code/body to report. On a
            # board with thousands of per-id lookups (--resolve-issues /
            # --resolve-names), a single transient network blip here must
            # not be allowed to silently defeat --best-effort's whole
            # purpose by propagating as an uncaught exception.
            if not required:
                raise ResolutionError(f"network error: {e.reason}") from e
            sys.stderr.write(f"error: network error: {e.reason}\n")
            sys.exit(1)


@dataclass
class TargetConfig:
    target: str  # "dc" or "cloud"
    structure_base_url: str
    structure_auth: Auth = field(repr=False)
    jira_base_url: str
    jira_auth: Auth = field(repr=False)


def resolve_base_url(board_url: Optional[str]) -> str:
    """Resolve the Jira DC base URL.

    Precedence: JIRA_URL env -> scheme://host of a supplied board --url.
    No hardcoded default -- a missing JIRA_URL must fail loudly rather than
    silently pointing at an unrelated host. Only used for --target dc: a
    missing JIRA_CLOUD_BASE_URL is always a hard failure for --target
    cloud, with no fallback of any kind.
    """
    env = os.environ.get("JIRA_URL", "").strip()
    if env:
        return env.rstrip("/")
    if board_url:
        parts = urllib.parse.urlsplit(board_url.strip())
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    sys.stderr.write(
        "error: JIRA_URL is not set and no --url was given to infer it from\n"
    )
    sys.exit(1)


def resolve_target_config(target: str, board_url: Optional[str]) -> TargetConfig:
    if target == "dc":
        base = resolve_base_url(board_url)
        token = _env_required("JIRA_PERSONAL_TOKEN")
        auth: Auth = ("bearer", token)
        return TargetConfig(
            target="dc",
            structure_base_url=base,
            structure_auth=auth,
            jira_base_url=base,
            jira_auth=auth,
        )

    if target == "cloud":
        required = {
            "STRUCTURE_CLOUD_BASE_URL": os.environ.get("STRUCTURE_CLOUD_BASE_URL", "").strip(),
            "STRUCTURE_CLOUD_TOKEN": os.environ.get("STRUCTURE_CLOUD_TOKEN", "").strip(),
            "JIRA_CLOUD_BASE_URL": os.environ.get("JIRA_CLOUD_BASE_URL", "").strip(),
            "JIRA_CLOUD_EMAIL": os.environ.get("JIRA_CLOUD_EMAIL", "").strip(),
            "JIRA_CLOUD_API_TOKEN": os.environ.get("JIRA_CLOUD_API_TOKEN", "").strip(),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            sys.stderr.write(
                "error: --target cloud requires all of "
                "STRUCTURE_CLOUD_BASE_URL, STRUCTURE_CLOUD_TOKEN, "
                "JIRA_CLOUD_BASE_URL, JIRA_CLOUD_EMAIL, JIRA_CLOUD_API_TOKEN "
                f"(missing: {', '.join(missing)}). Refusing to fall back to "
                "dc config or run with a partial cloud config.\n"
            )
            sys.exit(1)
        return TargetConfig(
            target="cloud",
            structure_base_url=required["STRUCTURE_CLOUD_BASE_URL"].rstrip("/"),
            structure_auth=("bearer", required["STRUCTURE_CLOUD_TOKEN"]),
            jira_base_url=required["JIRA_CLOUD_BASE_URL"].rstrip("/"),
            jira_auth=("basic", required["JIRA_CLOUD_EMAIL"], required["JIRA_CLOUD_API_TOKEN"]),
        )

    sys.stderr.write(f"error: unknown --target {target!r} (expected dc or cloud)\n")
    sys.exit(1)


def detect_target_from_url(url: str) -> Optional[str]:
    """Return "dc" or "cloud" if the URL shape unambiguously identifies its
    target, else None. Used to catch a --url/--target mismatch before it
    silently fetches a valid-looking but WRONG structure (a Cloud board id
    resolved against the dc host, or vice versa) -- the two id spaces are
    confirmed distinct, so this is not just a defensive guess."""
    u = url.strip()
    if re.search(r"/structure/board/(\d+)", u):
        return "cloud"
    if re.search(r"[?&]s=(\d+)", u) or re.search(r"structureId=(\d+)", u, re.I):
        return "dc"
    return None


def parse_structure_board_url(url: str) -> Optional[int]:
    # DC board URL shape: .../secure/StructureBoard.jspa?s=<id>.
    # Cloud board URL shape:
    # .../jira/apps/<appId>/<installId>/structure/board/<id>.
    # Cloud structure ids are a separate id space from DC's -- do not assume
    # a DC id and a Cloud id for "the same" board are numerically related.
    u = url.strip()
    m = re.search(r"/structure/board/(\d+)", u)
    if m:
        return int(m.group(1))
    m = re.search(r"[?&]s=(\d+)", u)
    if m:
        return int(m.group(1))
    m = re.search(r"structureId=(\d+)", u, re.I)
    if m:
        return int(m.group(1))
    return None


def parse_formula_segment(seg: str) -> Tuple[str, int, str]:
    """
    One formula row from Structure REST. Format per Tempo docs:
    rowId:depth:itemIdentity[:optionalSemantic]
    Slashes in itemIdentity must not be split (e.g. 5/240).
    """
    seg = seg.strip()
    if not seg:
        raise ValueError("empty formula segment")
    first = seg.find(":")
    second = seg.find(":", first + 1)
    if first < 0 or second < 0:
        raise ValueError(f"bad formula segment (need two colons): {seg!r}")
    row_id = seg[:first]
    depth = int(seg[first + 1 : second])
    rest = seg[second + 1 :].strip()
    # Optional trailing :semantic (integer) after item identity
    m = re.match(r"^(.+):(\d+)$", rest)
    if m:
        left, right = m.group(1), m.group(2)
        if "/" in left or left.isdigit():
            rest = left
    return row_id, depth, rest


def classify_item_identity_dc(
    item_identity: str, item_types: Dict[str, Any]
) -> Tuple[str, Optional[int]]:
    """
    Returns (kind, issue_numeric_id or None).
    Per Structure docs, a Jira issue row uses a plain numeric issue id.
    type/id forms (generators, folders, etc.) are not plain issues. A
    "cf_option" kind (custom-field-option grouping value, e.g. an S-JQL
    generator grouping by a custom field) is a non-issue row whose human
    name IS resolvable, unlike plain folders/generators -- see
    cf_option_name().
    """
    item_identity = item_identity.strip()
    if re.fullmatch(r"\d+", item_identity):
        return "issue_id", int(item_identity)
    m = re.fullmatch(r"(\d+)/(\d+)", item_identity)
    if m:
        type_key, ref = m.group(1), m.group(2)
        tname = str(
            item_types.get(type_key) or item_types.get(str(type_key)) or ""
        ).lower()
        if "issue" in tname and "folder" not in tname and "generator" not in tname:
            return "issue_id", int(ref)
        if "cf-option" in tname:
            return "cf_option", int(ref)
        return "non_issue", None
    return "non_issue", None


def classify_item_identity_cloud(item_identity: str) -> Tuple[str, Optional[int]]:
    """
    Returns (kind, issue_numeric_id or None) for a Cloud forest itemId.
    Cloud itemIds are self-describing: "<n>/<namespace>:<kind>/<id>", e.g.
    "1/jira:issue/29781" (issue) or "2/structure:folder/40218" (folder).
    Unlike DC, no itemTypes lookup table is needed since the namespace:kind
    is spelled out in the string. Custom-field-option grouping values have
    not been observed on Cloud; "cf_option" name resolution (see
    cf_option_name()) is DC-only until a Cloud equivalent itemId shape is
    confirmed.
    """
    item_identity = item_identity.strip()
    m = re.fullmatch(r"\d+/([A-Za-z]+):([A-Za-z]+)/(\d+)", item_identity)
    if m:
        namespace, kind, ref = m.group(1).lower(), m.group(2).lower(), m.group(3)
        if namespace == "jira" and kind == "issue":
            return "issue_id", int(ref)
        return "non_issue", None
    if re.fullmatch(r"\d+", item_identity):
        return "issue_id", int(item_identity)
    return "non_issue", None


def jira_issue_key(cfg: TargetConfig, issue_numeric_id: int, required: bool = True) -> str:
    # Same /rest/api/2/issue/{id}?fields=key path works on both DC and
    # Cloud -- only the host and auth scheme differ, both captured in cfg.
    u = f"{cfg.jira_base_url}/rest/api/2/issue/{issue_numeric_id}?fields=key"
    data = _request_json("GET", u, cfg.jira_auth, required=required)
    if not isinstance(data, dict):
        raise ValueError("unexpected issue response")
    key = data.get("key")
    if not isinstance(key, str):
        raise ValueError("issue response missing key")
    return key


def cf_option_name(cfg: TargetConfig, option_id: int, required: bool = True) -> str:
    # Standard Jira REST (not Structure-specific): a Structure "cf-option"
    # grouping row's ref is a customFieldOption id, and this endpoint
    # returns its display text (e.g. option 51011 -> "Leg 1A" on a board
    # that groups by a "Leg" field). Same path on Cloud per the Jira REST
    # API, though cf_option rows are not currently produced by
    # classify_item_identity_cloud (see its docstring).
    u = f"{cfg.jira_base_url}/rest/api/2/customFieldOption/{option_id}"
    data = _request_json("GET", u, cfg.jira_auth, required=required)
    if not isinstance(data, dict):
        raise ValueError("unexpected customFieldOption response")
    value = data.get("value")
    if not isinstance(value, str):
        raise ValueError("customFieldOption response missing value")
    return value


def forest_spec_structure_id(structure_id: int) -> str:
    return json.dumps({"structureId": structure_id}, separators=(",", ":"))


def fetch_forest(cfg: TargetConfig, structure_id: int) -> Any:
    if cfg.target == "cloud":
        # POST (not GET -- GET returns 405) with a JSON body. Empty body {}
        # returns the latest forest for the structure named in the path.
        url = f"{cfg.structure_base_url}/structure/{structure_id}/forest"
        return _request_json("POST", url, cfg.structure_auth, data=b"{}")
    spec = forest_spec_structure_id(structure_id)
    q = urllib.parse.urlencode({"s": spec})
    url = f"{cfg.structure_base_url}/rest/structure/2.0/forest/latest?{q}"
    return _request_json("GET", url, cfg.structure_auth)


def fetch_structure_meta(cfg: TargetConfig, structure_id: int) -> Any:
    if cfg.target == "cloud":
        # Same {id, name, ...} shape as DC's structure-metadata response.
        url = f"{cfg.structure_base_url}/structure/{structure_id}"
        return _request_json("GET", url, cfg.structure_auth)
    url = f"{cfg.structure_base_url}/rest/structure/2.0/structure/{structure_id}"
    return _request_json("GET", url, cfg.structure_auth)


def resolve_structure_id_by_name(cfg: TargetConfig, name: str) -> int:
    """Resolve a structure id from its name.

    Structure ids are app-internal to the Structure plugin and are not
    documented as being preserved across a Jira DC->Cloud migration. Name
    lookup is the durable path across targets; a numeric --structure-id
    should be treated as a cache of a previously resolved id for a given
    target, not assumed portable between dc and cloud.
    """
    if cfg.target == "cloud":
        # GET /structure (no path id) lists all structures visible to the
        # token.
        url = f"{cfg.structure_base_url}/structure"
        data = _request_json("GET", url, cfg.structure_auth)
        structures = data.get("structures") if isinstance(data, dict) else None
        if not isinstance(structures, list):
            raise ValueError("unexpected Cloud structure-list response shape")
        wanted = name.strip().lower()
        matches = [
            s
            for s in structures
            if isinstance(s, dict)
            and str(s.get("name", "")).strip().lower() == wanted
        ]
        if not matches:
            names = [s.get("name") for s in structures if isinstance(s, dict)]
            raise ValueError(
                f"no Cloud structure named {name!r} (available: {names})"
            )
        if len(matches) > 1:
            ids = [m.get("id") for m in matches]
            raise ValueError(
                f"multiple Cloud structures named {name!r}: ids {ids}; "
                "use --structure-id instead"
            )
        sid = matches[0].get("id")
        if not isinstance(sid, int):
            raise ValueError("Cloud structure-list entry missing integer id")
        return sid
    raise NotImplementedError(
        "Structure-by-name lookup is not implemented for --target dc; "
        "pass --structure-id or --url instead."
    )


def parse_cloud_forest_segments(forest_json: Any) -> List[Tuple[str, int, str]]:
    """Flatten a Cloud forest's nested rows[].children[] tree into the same
    (row_id, depth, item_identity) segment shape DC's flat formula string
    parses into. Depth-first pre-order; row_id is a synthetic sequential
    counter (Cloud rows have no explicit row id, unlike DC's formula)."""
    if not isinstance(forest_json, dict):
        raise ValueError("forest response must be an object")
    rows = forest_json.get("rows")
    if not isinstance(rows, list):
        raise ValueError('forest response missing "rows" array')

    segments: List[Tuple[str, int, str]] = []
    counter = 0

    def walk(nodes: List[Any], depth: int) -> None:
        nonlocal counter
        for node in nodes:
            if not isinstance(node, dict):
                continue
            item_id = node.get("itemId")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            counter += 1
            segments.append((str(counter), depth, item_id))
            children = node.get("children")
            if isinstance(children, list) and children:
                walk(children, depth + 1)

    walk(rows, 0)
    return segments


def build_rows_from_segments(
    segments: List[Tuple[str, int, str]],
    classify: Any,
    cfg: TargetConfig,
    resolve_issues: bool,
    include_non_issues: bool,
    resolve_names: bool = False,
    best_effort: bool = False,
) -> List[Dict[str, Any]]:
    """best_effort=False (default, unchanged): a single failed Jira/Structure
    lookup aborts the whole run via sys.exit, same as before this option
    existed. best_effort=True: a failed lookup is skipped with a stderr
    warning and the row keeps a non-fatal fallback (numeric id as key, or no
    name) instead of sinking every other row's resolution."""
    rows_out: List[Dict[str, Any]] = []
    issue_cache: Dict[int, str] = {}
    name_cache: Dict[int, Optional[str]] = {}

    for row_id, depth, item_identity in segments:
        kind, ref_num = classify(item_identity)

        key: Optional[str] = None
        name: Optional[str] = None
        if kind == "issue_id" and ref_num is not None:
            if resolve_issues:
                if ref_num not in issue_cache:
                    try:
                        issue_cache[ref_num] = jira_issue_key(
                            cfg, ref_num, required=not best_effort
                        )
                    except ResolutionError as e:
                        sys.stderr.write(
                            f"warning: skipping unresolvable issue id {ref_num}: {e}\n"
                        )
                        issue_cache[ref_num] = str(ref_num)
                key = issue_cache[ref_num]
            else:
                key = str(ref_num)
        elif kind == "cf_option" and ref_num is not None:
            if include_non_issues:
                key = f"STRUCTURE-NONISSUE-{row_id}"
                if resolve_names:
                    if ref_num not in name_cache:
                        try:
                            name_cache[ref_num] = cf_option_name(
                                cfg, ref_num, required=not best_effort
                            )
                        except ResolutionError as e:
                            sys.stderr.write(
                                f"warning: skipping unresolvable customFieldOption "
                                f"{ref_num}: {e}\n"
                            )
                            name_cache[ref_num] = None
                    name = name_cache[ref_num]
        elif kind == "non_issue":
            if include_non_issues:
                key = f"STRUCTURE-NONISSUE-{row_id}"

        if key is None:
            continue

        row_out: Dict[str, Any] = {
            "key": key,
            "depth": depth,
            "rowId": row_id,
            "itemIdentity": item_identity,
        }
        if name is not None:
            row_out["name"] = name
        rows_out.append(row_out)

    return rows_out


def forest_to_rows(
    forest_json: Any,
    cfg: TargetConfig,
    resolve_issues: bool,
    include_non_issues: bool,
    resolve_names: bool = False,
    best_effort: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Dispatch on cfg.target: DC's flat "formula" string vs Cloud's nested
    rows[].children[] tree are different response shapes, not just different
    hosts -- each needs its own segment parser and item-identity classifier."""
    if cfg.target == "cloud":
        segments = parse_cloud_forest_segments(forest_json)
        rows_out = build_rows_from_segments(
            segments,
            classify_item_identity_cloud,
            cfg,
            resolve_issues,
            include_non_issues,
            resolve_names,
            best_effort,
        )
        meta = {"state": forest_json.get("state") if isinstance(forest_json, dict) else None}
        return rows_out, meta

    if not isinstance(forest_json, dict):
        raise ValueError("forest response must be an object")
    formula = forest_json.get("formula")
    if not isinstance(formula, str) or not formula.strip():
        raise ValueError('forest response missing string "formula"')
    item_types = forest_json.get("itemTypes") or {}
    if not isinstance(item_types, dict):
        item_types = {}

    raw_segments = [s.strip() for s in formula.split(",") if s.strip()]
    segments = [parse_formula_segment(s) for s in raw_segments]
    rows_out = build_rows_from_segments(
        segments,
        lambda item_identity: classify_item_identity_dc(item_identity, item_types),
        cfg,
        resolve_issues,
        include_non_issues,
        resolve_names,
        best_effort,
    )
    meta = {
        "spec": forest_json.get("spec"),
        "version": forest_json.get("version"),
        "itemTypes": item_types,
    }
    return rows_out, meta


def _row_issue_numeric_id(item_identity: str) -> Optional[int]:
    """Extract the Jira issue numeric id from a row's itemIdentity, if it is
    an issue row, independent of --resolve-issues/--target. DC issue rows
    are plain digits; Cloud issue rows are the self-describing
    "<n>/jira:issue/<id>" shape (see classify_item_identity_cloud). Anything
    else (DC type/id non-issue rows, Cloud non-issue shapes) returns None --
    used by --anchor-issue-id to find an anchor without first requiring a
    full --resolve-issues pass."""
    item_identity = item_identity.strip()
    if re.fullmatch(r"\d+", item_identity):
        return int(item_identity)
    m = re.fullmatch(r"\d+/jira:issue/(\d+)", item_identity)
    if m:
        return int(m.group(1))
    return None


def extract_anchor_subtree(
    rows: List[Dict[str, Any]],
    anchor_issue_id: Optional[int],
    anchor_row_id: Optional[str],
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Slice `rows` (already in forest/formula order) down to an anchor row
    and everything nested under it -- every following row with depth
    strictly greater than the anchor's, up to (not including) the next row
    at or above the anchor's own depth. This is Structure's own nesting
    rule; there is no JQL equivalent since board position is not a Jira
    field.

    Pass exactly one of anchor_issue_id or anchor_row_id. anchor_issue_id is
    preferred: it is resolved fresh against this same forest fetch, so it
    cannot go stale the way a rowId carried over from an earlier call can
    (Structure's rowId is not guaranteed stable across separate fetches).
    If the issue occupies more than one row on this board, raises ValueError
    listing every match so the caller can retry with anchor_row_id instead.

    Also builds a `groups` dict when the anchor's immediate children are
    resolved custom-field-option rows (i.e. --resolve-names was passed and
    produced a `name` on them) -- the common "group by Leg/bucket" shape --
    mapping each bucket's name to its own issue keys. Returns (subtree_rows,
    groups); groups is None when the anchor has no such buckets.
    """
    if bool(anchor_issue_id is not None) == bool(anchor_row_id is not None):
        raise ValueError(
            "pass exactly one of anchor_issue_id or anchor_row_id, not both/neither"
        )

    if anchor_issue_id is not None:
        matches = [
            i
            for i, r in enumerate(rows)
            if _row_issue_numeric_id(r["itemIdentity"]) == anchor_issue_id
        ]
        if not matches:
            raise ValueError(
                f"issue id {anchor_issue_id} does not appear as a row in this forest"
            )
        if len(matches) > 1:
            locations = ", ".join(
                f"{{rowId: {rows[i]['rowId']!r}, depth: {rows[i]['depth']}}}"
                for i in matches
            )
            raise ValueError(
                f"issue id {anchor_issue_id} appears at {len(matches)} rows in "
                f"this forest -- pass --anchor-row-id to pick one: {locations}"
            )
        anchor_idx = matches[0]
    else:
        row_matches = [i for i, r in enumerate(rows) if r["rowId"] == anchor_row_id]
        if not row_matches:
            raise ValueError(f"rowId {anchor_row_id!r} not found in this forest")
        anchor_idx = row_matches[0]

    anchor_depth = rows[anchor_idx]["depth"]
    subtree = [rows[anchor_idx]]
    i = anchor_idx + 1
    while i < len(rows) and rows[i]["depth"] > anchor_depth:
        subtree.append(rows[i])
        i += 1

    # Boundaries must come from EVERY immediate child (named or not) -- an
    # unnamed sibling between two named headers (a folder, generator,
    # ungrouped ticket, or a cf-option whose name lookup failed under
    # --best-effort) still ends the previous bucket. Using only named
    # headers as boundaries would silently fold that sibling's descendants
    # into the previous bucket's issueKeys instead of excluding them.
    immediate_children = [
        (idx, r) for idx, r in enumerate(subtree[1:], start=1) if r["depth"] == anchor_depth + 1
    ]
    if not any(r.get("name") for _, r in immediate_children):
        return subtree, None

    groups: Dict[str, Any] = {}
    for j, (idx, child) in enumerate(immediate_children):
        if not child.get("name"):
            continue
        end = immediate_children[j + 1][0] if j + 1 < len(immediate_children) else len(subtree)
        bucket_rows = subtree[idx + 1 : end]
        issue_keys = [
            r["key"] for r in bucket_rows if not r["key"].startswith("STRUCTURE-NONISSUE-")
        ]
        if child["name"] in groups:
            raise ValueError(
                f"two sibling buckets under this anchor both resolved to the "
                f"name {child['name']!r} (rowIds {groups[child['name']]['rowId']!r} "
                f"and {child['rowId']!r}) -- cannot key groups by name "
                "unambiguously; inspect the board's custom-field options for a "
                "duplicate value"
            )
        groups[child["name"]] = {
            "rowId": child["rowId"],
            "issueCount": len(issue_keys),
            "issueKeys": issue_keys,
        }
    return subtree, groups


def _formula_raw(forest: Any, target: str) -> str:
    # Only DC has a "formula" string to echo for debugging; Cloud's raw tree
    # is preserved verbatim under forestMeta.rawRows instead (see
    # _forest_meta_light).
    if target == "dc" and isinstance(forest, dict):
        f = forest.get("formula")
        if isinstance(f, str):
            return f
    return ""


def _forest_meta_light(forest: Any, target: str) -> Dict[str, Any]:
    if target == "cloud":
        if not isinstance(forest, dict):
            return {"state": None, "rawRows": None}
        return {"state": forest.get("state"), "rawRows": forest.get("rows")}
    if not isinstance(forest, dict):
        return {"spec": None, "version": None, "itemTypes": {}}
    it = forest.get("itemTypes") or {}
    if not isinstance(it, dict):
        it = {}
    return {
        "spec": forest.get("spec"),
        "version": forest.get("version"),
        "itemTypes": it,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch Structure forest via REST")
    ap.add_argument(
        "--target",
        choices=("dc", "cloud"),
        default=None,
        help="Select host+auth pair for Structure and Jira issue-resolution "
        "calls together. $JIRA_TARGET is equivalent to passing this flag "
        "(same explicit priority; errors if it contradicts --url's shape). "
        "If neither this flag nor $JIRA_TARGET is set, inferred from "
        "--url's shape when possible, else defaults to dc.",
    )
    ap.add_argument("--structure-id", type=int, default=None)
    ap.add_argument(
        "--structure-name",
        default=None,
        help="Resolve structure id by name instead of --structure-id "
        "(Cloud only -- not implemented for dc; structure ids may not "
        "survive a dc->cloud migration)",
    )
    ap.add_argument("--url", default=None, help="StructureBoard.jspa or similar")
    ap.add_argument("--out", default=None, help="Write JSON to this path")
    ap.add_argument(
        "--env-file",
        default=None,
        help="Optional KEY=VALUE file (default: <skill>/.env if present)",
    )
    ap.add_argument(
        "--resolve-issues",
        action="store_true",
        help="Resolve numeric Jira ids to issue keys via Jira REST (many calls)",
    )
    ap.add_argument(
        "--structure-only",
        action="store_true",
        help="Only fetch Structure metadata + forest; output includes "
        "formulaRaw and forestMeta, rows stay empty (fast, tiny file)",
    )
    ap.add_argument(
        "--exclude-non-issues",
        action="store_true",
        help="Drop non-issue rows (may break depth ordering vs Structure UI)",
    )
    ap.add_argument(
        "--resolve-names",
        action="store_true",
        help="Resolve custom-field-option grouping rows (e.g. an S-JQL "
        "generator grouping by a custom field) to their display text via "
        "Jira REST, added as a 'name' field (DC only; folder/generator rows "
        "still have no resolvable name -- see references/structure-rest.md)",
    )
    ap.add_argument(
        "--best-effort",
        action="store_true",
        help="With --resolve-issues/--resolve-names: skip a single "
        "unresolvable id (deleted issue, deleted custom-field option) with "
        "a stderr warning instead of aborting the whole run. Default "
        "(off) is unchanged: any resolution failure exits non-zero.",
    )
    ap.add_argument(
        "--anchor-issue-id",
        type=int,
        default=None,
        help="Narrow output to this issue's row and everything nested under "
        "it (Structure's own nesting rule: every following row with depth "
        "greater than the anchor's, up to the next row at or above it). "
        "Find the numeric issue id first with your Jira MCP's search (e.g. "
        "by summary text) -- this script does not do text search. Errors "
        "listing every match if the issue occupies more than one row; pass "
        "--anchor-row-id instead to disambiguate. Mutually exclusive with "
        "--anchor-row-id.",
    )
    ap.add_argument(
        "--anchor-row-id",
        default=None,
        help="Narrow output to this Structure rowId's subtree. Only useful "
        "with a rowId from a --resolve-issues/--anchor-issue-id error "
        "message or a forest fetched in this same run -- rowId is not "
        "stable across separate fetches. Mutually exclusive with "
        "--anchor-issue-id.",
    )
    args = ap.parse_args()

    if args.anchor_issue_id is not None and args.anchor_row_id is not None:
        sys.stderr.write(
            "error: pass at most one of --anchor-issue-id / --anchor-row-id\n"
        )
        return 1

    default_env = os.path.join(_skill_root_dir(), ".env")
    if args.env_file:
        load_optional_env_file(args.env_file)
    elif os.path.isfile(default_env):
        load_optional_env_file(default_env)

    url_shape = detect_target_from_url(args.url) if args.url else None

    if args.target is not None:
        target = args.target
        target_explicit = True
    elif os.environ.get("JIRA_TARGET", "").strip():
        target = os.environ["JIRA_TARGET"].strip()
        target_explicit = True
    else:
        target = None
        target_explicit = False

    if target_explicit and url_shape is not None and target != url_shape:
        sys.stderr.write(
            f"error: --url looks like a {url_shape} board URL (per its shape) "
            f"but --target {target} was requested. Refusing to fetch a "
            f"{url_shape}-shaped URL's numeric id against the {target} host -- "
            "the two id spaces are confirmed distinct, so this would silently "
            f"return a valid-looking but WRONG structure. Pass --target "
            f"{url_shape}, or use a --url/--structure-id matching --target "
            f"{target}.\n"
        )
        return 1

    if target is None:
        target = url_shape or "dc"

    cfg = resolve_target_config(target, args.url)

    sid = args.structure_id
    if args.url:
        parsed = parse_structure_board_url(args.url)
        if parsed is None:
            sys.stderr.write("error: could not parse structure id from --url\n")
            return 1
        sid = parsed
    if sid is None and args.structure_name:
        try:
            sid = resolve_structure_id_by_name(cfg, args.structure_name)
        except (NotImplementedError, ValueError) as e:
            sys.stderr.write(f"error: {e}\n")
            return 1
    if sid is None:
        sys.stderr.write(
            "error: provide --structure-id, --structure-name, or --url\n"
        )
        return 1

    include_non = not bool(args.exclude_non_issues)
    resolve_issues = bool(args.resolve_issues)
    resolve_names = bool(args.resolve_names)
    best_effort = bool(args.best_effort)
    has_anchor = args.anchor_issue_id is not None or args.anchor_row_id is not None

    if args.structure_only and (args.resolve_issues or args.resolve_names or has_anchor):
        sys.stderr.write(
            "note: --structure-only skips row parsing; --resolve-issues, "
            "--resolve-names, and --anchor-issue-id/--anchor-row-id have no effect\n"
        )

    try:
        meta_struct = fetch_structure_meta(cfg, sid)
        forest = fetch_forest(cfg, sid)
    except SystemExit:
        raise
    except NotImplementedError as e:
        sys.stderr.write(f"error: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"error: {e}\n")
        return 1

    name = None
    if isinstance(meta_struct, dict):
        name = meta_struct.get("name")

    formula_raw = _formula_raw(forest, cfg.target)

    if args.structure_only:
        out_obj: Dict[str, Any] = {
            "target": cfg.target,
            "structureId": sid,
            "structureName": name,
            "formulaRaw": formula_raw,
            "rows": [],
            "forestMeta": _forest_meta_light(forest, cfg.target),
        }
    else:
        try:
            rows, forest_meta = forest_to_rows(
                forest,
                cfg,
                resolve_issues=resolve_issues,
                include_non_issues=include_non,
                resolve_names=resolve_names,
                best_effort=best_effort,
            )
        except ValueError as e:
            sys.stderr.write(f"error: {e}\n")
            return 1

        groups: Optional[Dict[str, Any]] = None
        if has_anchor:
            try:
                rows, groups = extract_anchor_subtree(
                    rows, args.anchor_issue_id, args.anchor_row_id
                )
            except ValueError as e:
                sys.stderr.write(f"error: {e}\n")
                return 1

        out_obj = {
            "target": cfg.target,
            "structureId": sid,
            "structureName": name,
            "formulaRaw": formula_raw,
            "rows": rows,
            "forestMeta": forest_meta,
        }
        if has_anchor:
            out_obj["anchorIssueId"] = args.anchor_issue_id
            out_obj["anchorRowId"] = rows[0]["rowId"] if rows else args.anchor_row_id
            out_obj["groups"] = groups

    text = json.dumps(out_obj, indent=2) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stdout.write(f"wrote {args.out}\n")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
