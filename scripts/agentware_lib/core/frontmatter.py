"""agentware_lib.core.frontmatter — restricted-YAML entry frontmatter I/O (layer: core).

Owns: the deterministic, stdlib-only frontmatter EMIT + PARSE (render_frontmatter,
split_frontmatter, strip_frontmatter and the _fm_* quote/scalar/tag/list helpers),
the legacy blockquote-header fallback parser (_parse_legacy_header + _LEGACY_*_RE),
read_entry_frontmatter, the frontmatter field vocabulary constants (FRONTMATTER_FIELDS
/ _FM_INDEX_SCALARS / RELATION_TYPES / DEFAULT_ENTRY_SOURCE), and the entry-frontmatter
BUILDERS build_entry_frontmatter / ensure_entry_frontmatter plus the _operator_handle
author-default resolver they use, and the lenient list-field normalizer
_entry_list_field adopted at every raw index read site (poisoned-index hardening).
Does NOT own: the index that CONSUMES frontmatter (agentware_lib.knowledge.index);
the personal-data leak scan (stays in the entrypoint this seam); date primitives
(agentware_lib.core.dates) or org-identity resolution (agentware_lib.core.config),
both imported here.
Layer deps: core -> stdlib (os, re) + agentware_lib.core.orgkeys / .dates / .config
(core->core, for _ORG_FRONTMATTER_FIELDS ordering, _today, and _entry_origin_team
respectively). Imports nothing above core.
Fan-in: 20 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import os
import re

import agentware_lib.core.orgkeys as orgkeys
import agentware_lib.core.dates as dates
import agentware_lib.core.config as config


# Per-entry `.md` files carry a leading YAML frontmatter block that is the SOURCE
# OF TRUTH for the index. `index.json` is a DERIVED cache (`index rebuild`
# reconstructs it from frontmatter alone). The format is a RESTRICTED,
# deterministic YAML subset (C-5): a block delimited by `---` lines holding flat
# `key: value` scalars plus one inline list (`tags: [a, b]`). Emit + parse are
# hand-rolled, stdlib only — NO PyYAML (preserves the zero-dependency moat).

# Canonical field order in every emitted block. `relates` is LAST and emitted
# ONLY when non-empty (see render_frontmatter) so entries without declared edges
# stay byte-identical to the pre-graph format (backward-compatible).
FRONTMATTER_FIELDS = ("id", "title", "category", "tags", "created",
                      "summary", "author", "source", "last_verified", "relates")
# The index columns derivable from frontmatter (tags handled separately).
_FM_INDEX_SCALARS = ("id", "title", "category", "created", "summary")

# Closed vocabulary of declared relation types for the `relates` graph edges.
# Edges are DECLARED-ONLY (never LLM-extracted): each token is "<type>:<target>"
# where target is another entry id. An out-of-vocab type is parsed/kept but
# flagged by the graph_integrity audit (advisory) — never silently dropped.
RELATION_TYPES = ("depends-on", "blocks", "supersedes", "relates-to")

DEFAULT_ENTRY_SOURCE = "agent"


def _entry_list_field(entry, name):
    """Normalize a list-typed index entry field (`relates`/`tags`) for READERS.

    Returns a FRESH list of the STRING elements when the field is a list/tuple,
    else `[]`. This is the shared, purely-defensive normalizer adopted at every
    RAW-index read site so a poisoned/imported index.json (a hand-edited or
    out-of-policy-committed index whose list field is a scalar, dict, string, or a
    list carrying non-string elements) can never crash the recall hot path
    (`build_adjacency`, `_entry_doc_text`), `audit`, `query`, or
    `index migrate-frontmatter`.

    Dropping non-string elements also kills the `unhashable type: 'dict'` crash in
    `graph_relation_errors`' `sorted(set(...))` (a dict element never reaches the
    set). BYTE-IDENTICAL — value-equal (`==`, a fresh copy, never `is`) — to the
    raw field for a valid list-of-strings, so downstream `list(...)`/`" ".join(...)`
    output is unchanged on all valid data. Fail-open by design: the DEDICATED
    integrity gates (`validate_index`, `audit --graph_integrity`) stay STRICT and
    surface the corruption loudly (recoverable via `index rebuild`). Pure + read-only.

    Lives in `core` — NOT in `knowledge.index` — because `build_entry_frontmatter`
    below is one of its four required call sites and `core` may import nothing above
    it. `core.frontmatter` already owns the `relates`/`tags` field vocabulary
    (FRONTMATTER_FIELDS, RELATION_TYPES, _fm_parse_tags/_fm_parse_relates), so the
    normalizer of those same two fields belongs with them.
    """
    v = entry.get(name)
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, str)]
    return []


def _fm_needs_quote(s):
    """True iff a scalar string must be double-quoted to survive round-trip."""
    if s == "":
        return True
    if s != s.strip():
        return True
    if s[0] in "\"'[]{}>|*&!#%@`,?:-":
        return True
    # A colon or hash mid-value can change YAML meaning; quote to be safe.
    return ":" in s or "#" in s


def _fm_quote(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fm_emit_scalar(value):
    s = "" if value is None else str(value)
    return _fm_quote(s) if _fm_needs_quote(s) else s


def _fm_emit_tag(t):
    t = "" if t is None else str(t)
    if t == "" or t != t.strip() or any(c in t for c in ',[]"\' '):
        return _fm_quote(t)
    return t


def render_frontmatter(fields):
    """Emit the restricted-YAML frontmatter block (including `---` delimiters)."""
    lines = ["---"]
    for k in FRONTMATTER_FIELDS:
        if k == "tags":
            tags = fields.get("tags") or []
            lines.append("tags: [%s]" % ", ".join(_fm_emit_tag(t) for t in tags))
        elif k == "relates":
            # Omit entirely when empty so a no-edge entry is byte-identical to the
            # pre-graph frontmatter format (backward-compatible). Tokens reuse the
            # tags inline-list machinery (`<type>:<target>`; ':' needs no quoting).
            rel = fields.get("relates") or []
            if rel:
                lines.append("relates: [%s]"
                             % ", ".join(_fm_emit_tag(t) for t in rel))
        else:
            lines.append("%s: %s" % (k, _fm_emit_scalar(fields.get(k))))
    # Org fields (feature 260721) — emitted AFTER the standard block, ONLY when
    # present, so a flat-KB entry (none present) is byte-identical to today (INV-1).
    # Ordering is the fixed _ORG_FRONTMATTER_FIELDS order for determinism.
    for k in orgkeys._ORG_FRONTMATTER_FIELDS:
        v = fields.get(k)
        if v:
            lines.append("%s: %s" % (k, _fm_emit_scalar(v)))
    lines.append("---")
    return "\n".join(lines) + "\n"


def _fm_parse_scalar(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return raw


def _fm_split_inline_list(s):
    """Split an inline list body on commas not inside double quotes."""
    parts, buf, in_q, esc = [], [], False, False
    for ch in s:
        if esc:
            buf.append(ch)
            esc = False
            continue
        if ch == "\\" and in_q:
            buf.append(ch)
            esc = True
            continue
        if ch == '"':
            in_q = not in_q
            buf.append(ch)
            continue
        if ch == "," and not in_q:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _fm_parse_tags(raw):
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    out = []
    for p in _fm_split_inline_list(raw):
        if p.strip() == "":
            continue
        out.append(_fm_parse_scalar(p))
    return out


def _fm_parse_relate_token(tok):
    """Split a `<type>:<target>` edge token on the FIRST ':'.

    Returns a normalized (type, target) tuple, or None for a malformed/empty
    token (no colon, or an empty type/target side). Deterministic, stdlib only.
    """
    tok = "" if tok is None else str(tok).strip()
    if not tok:
        return None
    type_, sep, target = tok.partition(":")
    type_, target = type_.strip(), target.strip()
    if not sep or not type_ or not target:
        return None
    return (type_, target)


def _fm_parse_relates(raw):
    """Parse the `relates` inline list into normalized `<type>:<target>` tokens.

    Reuses the quote-aware tags splitter. Each token is split on its FIRST ':'
    into (type, target); malformed/empty tokens are dropped deterministically.
    Out-of-vocab types are KEPT (the graph_integrity audit reports them) — only
    structurally-broken tokens are dropped. Order is preserved.
    """
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    out = []
    for p in _fm_split_inline_list(raw):
        if p.strip() == "":
            continue
        parsed = _fm_parse_relate_token(_fm_parse_scalar(p))
        if parsed is None:
            continue
        out.append("%s:%s" % parsed)
    return out


def split_frontmatter(text):
    """Return (fields_dict_or_None, body_text).

    Recognizes a frontmatter block ONLY when the file's first line is exactly
    `---` and a closing `---` follows. Otherwise returns (None, text) so legacy
    files and bodies that merely contain `---` horizontal rules are untouched.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close = i
            break
    if close is None:
        return None, text
    fields = {}
    for ln in lines[1:close]:
        if not ln.strip() or ":" not in ln:
            continue
        key, _, val = ln.partition(":")
        key = key.strip()
        if key == "tags":
            fields["tags"] = _fm_parse_tags(val)
        elif key == "relates":
            fields["relates"] = _fm_parse_relates(val)
        elif key in FRONTMATTER_FIELDS:
            fields[key] = _fm_parse_scalar(val)
        elif key in orgkeys._ORG_FRONTMATTER_FIELDS:
            # Org-model fields (origin/team/project/status/supersedes) are OPTIONAL
            # and captured for the team facet + org lint. They are NOT in
            # FRONTMATTER_FIELDS and are NEVER read by _entry_doc_text, so ranking
            # stays byte-identical whether present or absent (INV-1).
            fields[key] = _fm_parse_scalar(val)
    return fields, "\n".join(lines[close + 1:])


def strip_frontmatter(text):
    """Body text with any leading frontmatter block removed (lossless tail)."""
    fields, body = split_frontmatter(text)
    if fields is None:
        return text
    return body.lstrip("\n")


_LEGACY_CREATED_RE = re.compile(r">\s*\*\*Created\*\*:\s*(\S+)")
_LEGACY_CATEGORY_RE = re.compile(r">\s*\*\*Category\*\*:\s*(\S+)")
_LEGACY_TAGS_RE = re.compile(r">\s*\*\*Tags\*\*:\s*(.+)")


def _parse_legacy_header(text):
    """Best-effort extraction from a legacy blockquote-only entry (pre-migration).

    Returns whatever it can (title/created/category/tags) so callers degrade
    gracefully before the one-time migration runs.
    """
    out = {}
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    if m:
        out["title"] = m.group(1).strip()
    m = _LEGACY_CREATED_RE.search(text)
    if m:
        out["created"] = m.group(1).strip()
    m = _LEGACY_CATEGORY_RE.search(text)
    if m:
        out["category"] = m.group(1).strip()
    m = _LEGACY_TAGS_RE.search(text)
    if m:
        tags = re.findall(r"`([^`]+)`", m.group(1))
        if tags:
            out["tags"] = tags
    return out


def read_entry_frontmatter(path):
    """Parse an entry file's frontmatter into a dict (possibly partial).

    If the file carries a real frontmatter block, return its parsed fields.
    Otherwise tolerate a legacy blockquote-only file and return what it can.
    Returns {} on any read error.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    fields, _ = split_frontmatter(text)
    if fields is not None:
        return fields
    return _parse_legacy_header(text)


# --- entry-frontmatter builders (moved from entrypoint, wave 2 seam 2) --------
# build/ensure construct the 9-field frontmatter block for an index entry, stamping
# defaults (created/author) + org provenance. They read _today from core.dates and
# _entry_origin_team from core.config (both core->core), and _operator_handle below
# supplies the `author` default — its sole caller pattern, so it lives beside them.
def _operator_handle(kdir):
    """Best-effort operator handle for the `author` default. Stdlib only.

    Parses `**Handle**:` (then `**Operator**:`) out of the KB MAIN.md. Falls back
    to 'operator' so the writer never blocks on a missing profile. Tolerates a
    None/empty kdir (e.g. an unconfigured knowledge dir during a pure row-build
    test) by returning the safe default instead of crashing on os.path.join.
    """
    if not kdir:
        return "operator"
    try:
        with open(os.path.join(kdir, "MAIN.md"), "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return "operator"
    for pat in (r"\*\*Handle\*\*:\s*([^\s·|]+)", r"\*\*Operator\*\*:\s*([^\s·|]+)"):
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return "operator"


def build_entry_frontmatter(entry, kdir, author=None,
                            source=DEFAULT_ENTRY_SOURCE, last_verified=None):
    """Build the 9-field frontmatter dict for an index entry + defaults.

    Org mode (feature 260721): when kdir resolves to a teams/<team> dir, stamp
    `origin: team` + `team: <team>` so a NEW entry carries its provenance from
    birth (recall team-scoping + org lint O1 both rely on it). A shared/ write
    stamps origin: shared. A FLAT non-org kdir stamps neither → byte-identical
    to today (INV-1). These keys are ignored by _entry_doc_text (never ranked).
    """
    created = entry.get("created") or dates._today()
    fm = {
        "id": entry.get("id") or "",
        "title": entry.get("title") or "",
        "category": entry.get("category") or "",
        # `_entry_list_field` keeps `index migrate-frontmatter` from crashing on a
        # poisoned index entry (a scalar/dict `tags`, or a list carrying a
        # non-string element that `_fm_emit_tag` would later choke on).
        "tags": _entry_list_field(entry, "tags"),
        "created": created,
        "summary": entry.get("summary") or "",
        "author": author if author is not None else _operator_handle(kdir),
        "source": source,
        "last_verified": last_verified or entry.get("last_verified") or created,
        # Declared graph edges; defaults to [] so a no-edge entry emits no
        # `relates:` line and stays byte-identical to the pre-graph format.
        "relates": _entry_list_field(entry, "relates"),
    }
    origin, team = config._entry_origin_team({}, kdir)  # infer from the kdir path
    if origin:
        fm["origin"] = origin
    if team:
        fm["team"] = team
    return fm


def ensure_entry_frontmatter(kdir, entry, author=None,
                             source=DEFAULT_ENTRY_SOURCE):
    """Prepend a frontmatter block to `entry`'s file iff it lacks one.

    Idempotent: a file that already carries a frontmatter block is left untouched
    (returns False). PREPEND only — never edits the body. Returns True if it wrote
    a block. The block is derived from the index `entry` row + defaults so the
    file becomes the rebuildable source of truth (C-1, C-3).
    """
    rel = entry.get("path")
    if not rel:
        return False
    abs_path = os.path.join(kdir, rel)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    existing, _ = split_frontmatter(text)
    if existing is not None:
        return False
    fm = build_entry_frontmatter(entry, kdir, author=author, source=source)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(render_frontmatter(fm) + text)
    return True


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "FRONTMATTER_FIELDS",
    "_FM_INDEX_SCALARS",
    "RELATION_TYPES",
    "DEFAULT_ENTRY_SOURCE",
    "_entry_list_field",
    "_fm_needs_quote",
    "_fm_quote",
    "_fm_emit_scalar",
    "_fm_emit_tag",
    "render_frontmatter",
    "_fm_parse_scalar",
    "_fm_split_inline_list",
    "_fm_parse_tags",
    "_fm_parse_relate_token",
    "_fm_parse_relates",
    "split_frontmatter",
    "strip_frontmatter",
    "_LEGACY_CREATED_RE",
    "_LEGACY_CATEGORY_RE",
    "_LEGACY_TAGS_RE",
    "_parse_legacy_header",
    "read_entry_frontmatter",
    "_operator_handle",
    "build_entry_frontmatter",
    "ensure_entry_frontmatter",
]
