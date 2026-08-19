"""The INDEX READ-SAFETY gate — a poisoned index must never crash a reader.

Plan task 23. `load_index` does ZERO type validation: it `json.loads` index.json
and hands the raw types to every consumer. Any entry whose list-typed field
(`relates` / `tags`) is a scalar, a string, a mapping, or a list carrying
non-string elements propagates unfiltered — and several of the consumers sit on
the recall HOT PATH:

  * `build_adjacency` — a scalar raises TypeError; a STRING iterates per-CHARACTER;
    a mapping is silently dropped. Trust signals / superseded ids build adjacency,
    so a poisoned `relates` crashes `recall` itself, not merely `audit`.
  * `graph_relation_errors` — a SECOND, distinct crash a top-level is-a-list guard
    alone misses: a `relates` LIST holding a dict element reaches `sorted(set(...))`
    and raises `unhashable type: 'dict'`.
  * `_entry_doc_text` — the BM25 corpus. A scalar raises; a STRING `tags` silently
    corrupts the corpus into per-character tokens, which nothing turns red.
  * `build_entry_frontmatter` — the `index migrate-frontmatter` sleeper.
  * every `tags.get(t)` / `tag_counts[t]` site — unhashable again.

## The design this gate enforces: lenient READERS + a strict VALIDATOR

Readers fail OPEN through one shared normalizer, `_entry_list_field`, which
returns a fresh list of the STRING elements or `[]`. Dropping non-string elements
is what kills the unhashable case. It is value-equal (`==`) to a valid
list-of-strings, so output is byte-identical on all valid data.

`validate_index` is the DEDICATED strict gate and fails LOUD, because lenient
readers alone would let a poisoned index live forever unnoticed. Both halves are
required: leniency without the strict gate is a silent mask, and the strict gate
without leniency is a crash on the hot path.

## Why the default is INVERTED

The load-bearing check is not "the normalizer exists" — it is that NO raw read
survives anywhere in the lib. So every raw `.get("tags")` / `["relates"]` site
must be named in `RAW_READ_EXEMPT` WITH A REASON, and anything else fails. That
inversion is what makes the gate survive tasks 37–54, which port ~44 verbs onto
these readers: a newly-ported verb that reads a list field raw is red on arrival
rather than a crash discovered in production.

Both tables fail on a STALE row as well as on a missing one — an exemption whose
site no longer exists, or a required site whose function was renamed away, is an
error rather than a silent pass. That is the same failure mode task 21 recorded
(a gate pinning a name disarms itself on the next decomposition and goes GREEN).

## The detection boundary, written down so it is not re-litigated

`raw_read_sites` matches a CONSTANT field name only: `x.get("tags")`,
`x["relates"]`. A read through a variable (`x.get(field)` in a loop over
`("tags", "relates")`) is invisible to it, and deliberately so — inferring the
value of `field` needs dataflow, and a gate that guesses produces false positives
nobody trusts and then disables. That residue is covered from the other side: the
BEHAVIOURAL suite feeds a poisoned index to every named surface
(tests/integration/test_poisoned_index.py), so a dynamic raw read still crashes
something. If a future port genuinely needs a dynamic field read, prefer routing
it through the normalizer anyway — it takes the name as an argument precisely so
that stays possible.

Stdlib only, read-only, deterministic, py3.7-safe. Never imports the code it
measures — everything here is `ast` over source text.
"""

import ast
import os

try:
    from tests import _indexsafety_gate as isg
except ImportError:  # allow direct execution / sys.path-style import
    import _indexsafety_gate as isg

HERE = os.path.dirname(os.path.realpath(__file__))

# Same override idiom as _indexsafety_gate: run FROM the package repo AGAINST the
# staged merge tree during the migration, which is the only window in which the
# measured tree and this checkout differ.
REPO_ROOT = os.environ.get("AGENTWARE_INDEXREADER_ROOT") or os.path.dirname(HERE)
LIB_DIR = os.path.join(REPO_ROOT, "scripts", "agentware_lib")

NORMALIZER = "_entry_list_field"
# The layer-legal owner. It CANNOT live under knowledge/index: `core.frontmatter`
# holds one of the four required call sites (build_entry_frontmatter) and `core`
# may import nothing above it. `core.frontmatter` already owns the `relates`/`tags`
# field vocabulary, so the normalizer of those two fields belongs with them.
NORMALIZER_MODULE = ("core", "frontmatter.py")
LIST_FIELDS = ("tags", "relates")

# (module, function) sites that MUST route their list-field read through the
# normalizer. The four the plan names come first; the rest are the remaining raw
# index-entry readers the same scan found.
REQUIRED_NORMALIZED = (
    ("knowledge/graph.py", "build_adjacency", "the graph adjacency builder (recall hot path)"),
    ("knowledge/graph.py", "graph_relation_errors", "the relation linter (unhashable-dict crash)"),
    ("retrieval/corpus.py", "_entry_doc_text", "the BM25 corpus text builder"),
    ("core/frontmatter.py", "build_entry_frontmatter", "the frontmatter migrator"),
    ("knowledge/index/indexvalidate.py", "_validate_entry_tag_errors",
     "validate's entry->tag-map scan (tags.get(<dict>) is unhashable)"),
    ("knowledge/index/indexvalidate.py", "_validate_tag_map_errors",
     "validate's tag-map->entry back-ref (t not in <scalar>)"),
    ("knowledge/index/indexvalidate.py", "_tag_map_forward_errors",
     "audit's entry->tag-map scan"),
    ("knowledge/index/indexvalidate.py", "_tag_map_backref_errors",
     "audit's tag-map->entry back-ref"),
    ("ops/fleet/fleetreport.py", "_fleet_top_learning_tags",
     "fleet issues tag histogram (tag_counts[t] is unhashable on a dict element)"),
    ("retrieval/bench/benchledger.py", "cmd_bench_lint", "bench lint tag clusters"),
)

# Every REMAINING raw read of a list field, each with the reason it is safe.
# Keyed (module, function, field); a row matching zero sites is STALE and fails.
RAW_READ_EXEMPT = (
    # -- the index's tags MAP (a dict of tag -> [ids]), not an entry list field --
    ("features/mcp.py", "_mcp_query_lookup", "tags", "index tags MAP, not an entry field"),
    ("knowledge/graph.py", "cmd_query", "tags", "index tags MAP, not an entry field"),
    ("retrieval/corpus.py", "retrieve_tag", "tags", "index tags MAP, not an entry field"),
    ("knowledge/index/indexvalidate.py", "validate_index", "tags",
     "index tags MAP, not an entry field"),
    ("knowledge/index/indexvalidate.py", "tag_consistency_errors", "tags",
     "index tags MAP, not an entry field"),
    # -- the STRICT gate itself, which must see the RAW value to reject it -----
    ("knowledge/index/indexvalidate.py", "_entry_optional_errors", "tags",
     "the strict list-field gate: normalizing here would MASK the poison"),
    ("knowledge/index/indexvalidate.py", "_entry_optional_errors", "relates",
     "the strict list-field gate: normalizing here would MASK the poison"),
    # -- the SKILL CATALOG, a different document with a different schema -------
    ("features/skillpropose.py", "_existing_skill_coverage", "tags",
     "catalog/skills.json rows, not index.json entries — a different document "
     "with its own schema; the SAME function reads real index entries three "
     "lines below and routes those through _entry_list_field"),
    # -- a frontmatter FIELDS dict, always parser-normalized to a list ---------
    ("core/frontmatter.py", "render_frontmatter", "tags", "parser product, not an index entry"),
    ("core/frontmatter.py", "render_frontmatter", "relates", "parser product, not an index entry"),
    ("knowledge/curation/curationrender.py", "_render_learning", "tags",
     "parser product, not an index entry"),
    ("knowledge/index/indexrebuild.py", "_frontmatter_row", "tags",
     "parser product: rebuild re-derives FROM frontmatter, the recovery source"),
    ("knowledge/index/indexrebuild.py", "_frontmatter_row", "relates",
     "parser product: rebuild re-derives FROM frontmatter, the recovery source"),
    # -- the site WRITES the field rather than reading an entry ----------------
    ("core/frontmatter.py", "split_frontmatter", "tags", "writes the parsed list"),
    ("core/frontmatter.py", "split_frontmatter", "relates", "writes the parsed list"),
    ("core/frontmatter.py", "_parse_legacy_header", "tags", "writes the parsed list"),
    ("knowledge/index/indexrebuild.py", "build_index_from_frontmatter", "tags",
     "reads a row _frontmatter_row just built, never a loaded entry"),
    # -- `relate` (task 38): the .md frontmatter is the SOURCE OF TRUTH for the
    # edge set and the derived index row is reconciled TO it, so every read below
    # is a parser product / a locally-built dict / a write. Each of this module's
    # three genuine LOADED-ENTRY reads is normalized (_relate_confirm and
    # _declare_relation both call _entry_list_field(subject, "relates"), as does
    # _relate_commit_index before comparing) — those are deliberately NOT exempted.
    # Safe by construction, not by inspection: split_frontmatter routes `relates`
    # through _fm_parse_relates, which emits each element as `"%s:%s" % parsed`,
    # so a frontmatter-sourced list is list-of-str and cannot carry a dict/scalar.
    ("knowledge/relatecmd.py", "_relate_write_md", "relates",
     "parser product: the post-write re-parse of the .md, not a loaded entry"),
    ("knowledge/relatecmd.py", "_declare_relation", "relates",
     "parser product: the .md frontmatter edge set (the index read here IS normalized)"),
    ("knowledge/relatecmd.py", "_relate_commit_index", "relates",
     "writes the frontmatter-derived list (the read above it IS normalized)"),
    ("knowledge/relatecmd.py", "cmd_relate", "relates",
     "_declare_relation's own result dict, never a loaded entry"),
    # -- not the index at all --------------------------------------------------
    ("features/skills.py", "cmd_skill_search", "tags", "the skill CATALOG, not the index"),
    ("features/skills.py", "cmd_skill_add", "tags", "the skill CATALOG, not the index"),
    ("knowledge/curation/curationingest.py", "_ingest_register", "tags",
     "an ingest candidate this module built"),
    ("knowledge/curation/curationingest.py", "_ingest_write_staging", "tags",
     "an ingest candidate this module built"),
    # The entry BUILDER, not a loaded index entry: it reads the package-shipped
    # synthetic fixture (tests/fixtures/gold_fixture_suite.json), whose loader is
    # strict + fail-loud by design. Moved here from load_gold_fixture when task 59
    # split the construction out; the row follows the SITE, because a path- or
    # name-keyed exemption left behind forgives whatever lands there next.
    ("retrieval/goldfixture.py", "_corpus_entry", "tags",
     "the gold fixture entry builder, strict + fail-loud by design"),
)

# The four errors `validate_index` must be able to emit. Leniency in the readers
# is only safe BECAUSE these exist; drop one and a poisoned index lives forever.
STRICT_MESSAGES = (
    "tags is not a list",
    "tags has a non-string element",
    "relates is not a list",
    "relates has a non-string element",
)
STRICT_SITE = ("knowledge/index/indexvalidate.py", "_entry_optional_errors")


def lib_present():
    """True once the modular core exists in the tree under measurement."""
    return os.path.isdir(LIB_DIR)


def _rel(path):
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


def _abs(rel):
    return os.path.join(LIB_DIR, *rel.split("/"))


def lib_files():
    out = []
    for dirpath, dirnames, files in os.walk(LIB_DIR):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(files):
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


def _lib_rel(path):
    return os.path.relpath(path, LIB_DIR).replace(os.sep, "/")


def _enclosing_func(tree, lineno):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.lineno <= lineno <= node.end_lineno:
            if best is None or node.lineno > best.lineno:
                best = node
    return best


def raw_read_sites():
    """[(lib_rel, function, field, lineno)] for every RAW list-field read."""
    sites = []
    for path in lib_files():
        tree = isg._parse(path)
        for node in ast.walk(tree):
            field = None
            if isinstance(node, ast.Call) and \
                    isg._attr_chain(node.func).split(".")[-1] == "get" and \
                    node.args and isinstance(node.args[0], ast.Constant) and \
                    node.args[0].value in LIST_FIELDS:
                field = node.args[0].value
            elif isinstance(node, ast.Subscript) and \
                    isinstance(node.slice, ast.Constant) and \
                    node.slice.value in LIST_FIELDS:
                field = node.slice.value
            if field is None:
                continue
            owner = _enclosing_func(tree, node.lineno)
            sites.append((_lib_rel(path), owner.name if owner else "<module>",
                          field, node.lineno))
    return sorted(sites)


def normalizer_errors():
    """The normalizer exists exactly ONCE, in the layer-legal owner, and is lenient."""
    errs = []
    owner = os.path.join(LIB_DIR, *NORMALIZER_MODULE)
    defs = []
    for path in lib_files():
        for node in ast.walk(isg._parse(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name == NORMALIZER:
                defs.append((path, node))
    if not defs:
        return ["%s: no %s anywhere under the lib — every reader is raw"
                % (_rel(LIB_DIR), NORMALIZER)]
    if len(defs) > 1:
        errs.append("%s is defined %d times (%s) — two normalizers WILL diverge"
                    % (NORMALIZER, len(defs),
                       ", ".join(sorted(_rel(p) for p, _ in defs))))
    path, fn = defs[0]
    if os.path.abspath(path) != os.path.abspath(owner):
        errs.append("%s defines %s, but it must live in %s: `core` may import "
                    "nothing above it and build_entry_frontmatter (a required "
                    "call site) is IN core"
                    % (_rel(path), NORMALIZER, _rel(owner)))
    src = ast.dump(fn)
    # Leniency has two halves and each is separately necessary: a scalar guard
    # alone still crashes on ['ok', {...}] at set()/tags.get().
    if "isinstance" not in src or "'str'" not in src.replace('"', "'"):
        errs.append("%s: %s does not filter for STRING elements — a list carrying "
                    "a dict element still raises `unhashable type` at set()/get()"
                    % (_rel(path), NORMALIZER))
    if not any(isinstance(n, (ast.ListComp, ast.List)) for n in ast.walk(fn)):
        errs.append("%s: %s does not build a FRESH list — returning the caller's "
                    "own list aliases it and invites mutation"
                    % (_rel(path), NORMALIZER))
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Name):
            errs.append("%s: %s returns the raw field unchanged on some path"
                        % (_rel(path), NORMALIZER))
    return errs


def required_site_errors():
    """Every named reader routes its list-field read through the normalizer."""
    errs = []
    for rel, fname, why in REQUIRED_NORMALIZED:
        path = _abs(rel)
        if not os.path.exists(path):
            errs.append("missing module: %s (%s)" % (rel, why))
            continue
        fn = isg._funcs(isg._parse(path)).get(fname)
        if fn is None:
            errs.append("%s: %s is gone — %s can no longer be checked here "
                        "(re-point REQUIRED_NORMALIZED rather than deleting it)"
                        % (rel, fname, why))
            continue
        if NORMALIZER not in isg._called_names(fn):
            errs.append("%s:%d %s() does not call %s — %s reads a list field RAW, "
                        "so a poisoned index crashes it"
                        % (rel, fn.lineno, fname, NORMALIZER, why))
    return errs


def raw_read_errors():
    """No raw list-field read survives that is not exempted WITH a reason."""
    errs = []
    exempt = {(r, f, fld): why for r, f, fld, why in RAW_READ_EXEMPT}
    seen = set()
    for rel, fname, field, lineno in raw_read_sites():
        key = (rel, fname, field)
        if key in exempt:
            seen.add(key)
            continue
        errs.append("%s:%d %s() reads `%s` RAW — route it through %s, or add an "
                    "exemption row saying why this is not a loaded index entry"
                    % (rel, lineno, fname, field, NORMALIZER))
    for key in sorted(set(exempt) - seen):
        errs.append("%s: the exemption for %s() `%s` matches nothing — a stale "
                    "row silently forgives a site that no longer exists (%s)"
                    % (key[0], key[1], key[2], exempt[key]))
    return errs


def strict_validator_errors():
    """The strict half: `validate_index` still REJECTS every poisoned shape."""
    rel, fname = STRICT_SITE
    path = _abs(rel)
    if not os.path.exists(path):
        return ["missing module: %s (the strict list-field gate)" % rel]
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    fn = isg._funcs(ast.parse(src, filename=path)).get(fname)
    if fn is None:
        return ["%s: %s is gone — the strict list-field gate can no longer be "
                "checked here (re-point STRICT_SITE)" % (rel, fname)]
    seg = ast.get_source_segment(src, fn) or ""
    errs = []
    for msg in STRICT_MESSAGES:
        if msg not in seg:
            errs.append("%s: %s no longer emits %r — WITHOUT the strict gate the "
                        "lenient readers silently MASK a poisoned index forever"
                        % (rel, fname, msg))
    return errs


def all_errors():
    """Every check, in a stable order. Empty list == the gate is green."""
    out = []
    out += normalizer_errors()
    out += required_site_errors()
    out += raw_read_errors()
    out += strict_validator_errors()
    return out


if __name__ == "__main__":  # pragma: no cover - manual invocation
    import sys
    if not lib_present():
        print("indexreader: %s absent — gate inert" % _rel(LIB_DIR))
        raise SystemExit(0)
    problems = all_errors()
    for p in problems:
        print("FAIL %s" % p)
    print("indexreader: %d problem(s) over %d lib file(s), %d raw read site(s)"
          % (len(problems), len(lib_files()), len(raw_read_sites())))
    sys.exit(1 if problems else 0)
