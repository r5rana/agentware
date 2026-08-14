"""The INDEX READ-SAFETY suite (plan task 23).

Two claims, deliberately separated so a green result can never be mistaken for the
wrong one:

  1. POSITIVE — the tree under measurement satisfies every read-safety invariant:
     one shared lenient normalizer, in the layer-legal owner, WIRED at every raw
     index read site, with the strict `validate_index` half still rejecting the
     poison it makes the readers survive.
  2. REFUTATION — each invariant is broken, one at a time, against a synthetic
     mini-tree, and the gate must name the specific failure. This defect class is
     silent in BOTH directions: a raw read crashes only once a poisoned index
     exists (which is a hand-edit or an out-of-policy commit, so it happens rarely
     and catastrophically), and leniency WITHOUT the strict validator hides the
     corruption forever instead. Neither half turns anything else red.

The behavioural half — an actual poisoned index.json fed to recall, query, the
graph-integrity audit and `index migrate-frontmatter` — rides with the core
(tests/integration/test_poisoned_index.py). This file proves the STATIC gate that
keeps it true for every verb ported in tasks 37-54.

The gate rides with the migration: before the modular core lands there is no
`agentware_lib` to measure, so the positive half is inert rather than failing. The
refutation half always runs — it builds its own tree and depends on nothing in
this checkout.

Stdlib only, deterministic, hermetic (`ast` over source text; no import of the
code under test, no subprocess, no network).
"""

import contextlib
import os
import shutil
import sys
import tempfile
import unittest

try:
    from tests import _indexreader_gate as isr
except ImportError:  # allow `python3 -m unittest tests.test_index_reader_safety`
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _indexreader_gate as isr

_NO_CORE = not isr.lib_present()
_NO_CORE_REASON = "scripts/agentware_lib is not present in this tree yet"


# --- a minimal COMPLIANT synthetic tree ---------------------------------------
# Three modules, one per property: the normalizer's owner (which is ALSO a
# required call site, the constraint that forces it into `core`), a reader, and
# the strict validator with the raw reads that are legitimately exempt.
_FRONTMATTER = '''\
def _entry_list_field(entry, name):
    """The shared lenient normalizer."""
    v = entry.get(name)
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, str)]
    return []


def render_frontmatter(fields):
    tags = fields.get("tags") or []
    return ", ".join(tags)


def build_entry_frontmatter(entry, kdir):
    return {"tags": _entry_list_field(entry, "tags"),
            "relates": _entry_list_field(entry, "relates")}
'''

_GRAPH = '''\
import agentware_lib.core.frontmatter as frontmatter


def build_adjacency(data):
    forward = {}
    for e in data.get("entries") or []:
        for tok in frontmatter._entry_list_field(e, "relates"):
            forward.setdefault(e.get("id"), []).append(tok)
    return forward


def cmd_query(args, data):
    tags_map = data.get("tags") or {}
    return sorted(tags_map)
'''

_VALIDATE = '''\
def _entry_optional_errors(entry, label):
    errors = []
    etags = entry.get("tags")
    if etags is not None and not isinstance(etags, list):
        errors.append("entry '%s' tags is not a list" % label)
    elif isinstance(etags, list) and not all(isinstance(x, str) for x in etags):
        errors.append("entry '%s' tags has a non-string element" % label)
    erel = entry.get("relates")
    if erel is not None and not isinstance(erel, list):
        errors.append("entry '%s' relates is not a list" % label)
    elif isinstance(erel, list) and not all(isinstance(x, str) for x in erel):
        errors.append("entry '%s' relates has a non-string element" % label)
    return errors
'''

_FILES = {
    "core/frontmatter.py": _FRONTMATTER,
    "knowledge/graph.py": _GRAPH,
    "knowledge/index/indexvalidate.py": _VALIDATE,
}

_SYNTH_REQUIRED = (
    ("core/frontmatter.py", "build_entry_frontmatter", "the frontmatter migrator"),
    ("knowledge/graph.py", "build_adjacency", "the graph adjacency builder"),
)
_SYNTH_EXEMPT = (
    ("core/frontmatter.py", "render_frontmatter", "tags", "parser product"),
    ("knowledge/graph.py", "cmd_query", "tags", "index tags MAP"),
    ("knowledge/index/indexvalidate.py", "_entry_optional_errors", "tags",
     "the strict gate must see the raw value"),
    ("knowledge/index/indexvalidate.py", "_entry_optional_errors", "relates",
     "the strict gate must see the raw value"),
)


class _SyntheticTree(object):
    """A throwaway tree the gate can measure, with the tables pointed at it."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="agentware-readergate-")
        self.lib = os.path.join(self.root, "scripts", "agentware_lib")
        for rel, body in _FILES.items():
            path = os.path.join(self.lib, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)

    def write(self, rel, body):
        path = os.path.join(self.lib, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    def patch(self, rel, old, new):
        path = os.path.join(self.lib, *rel.split("/"))
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        assert old in src, "refutation needle not found in %s: %r" % (rel, old)
        with open(path, "w", encoding="utf-8") as f:
            f.write(src.replace(old, new))

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


@contextlib.contextmanager
def _measuring(tree, required=_SYNTH_REQUIRED, exempt=_SYNTH_EXEMPT):
    """Point the gate's roots AND its two tables at `tree`, then restore."""
    saved = (isr.REPO_ROOT, isr.LIB_DIR, isr.REQUIRED_NORMALIZED,
             isr.RAW_READ_EXEMPT)
    isr.REPO_ROOT, isr.LIB_DIR = tree.root, tree.lib
    isr.REQUIRED_NORMALIZED, isr.RAW_READ_EXEMPT = required, exempt
    try:
        yield
    finally:
        (isr.REPO_ROOT, isr.LIB_DIR, isr.REQUIRED_NORMALIZED,
         isr.RAW_READ_EXEMPT) = saved


class _RefutationCase(unittest.TestCase):
    def setUp(self):
        self.tree = _SyntheticTree()
        self.addCleanup(self.tree.cleanup)

    def errors(self):
        with _measuring(self.tree):
            return isr.all_errors()

    def assertNames(self, needle):
        errs = self.errors()
        self.assertTrue(any(needle in e for e in errs),
                        "no error mentioned %r; got %r" % (needle, errs))

    def assertGreen(self):
        self.assertEqual(self.errors(), [])


class SyntheticBaselineTest(_RefutationCase):
    """The unmodified synthetic tree must be GREEN, or every refutation below is
    proving nothing but its own scaffolding."""

    def test_the_compliant_tree_is_green(self):
        self.assertGreen()


class NormalizerRefutationTest(_RefutationCase):
    def test_no_normalizer_at_all_reddens(self):
        self.tree.patch("core/frontmatter.py",
                        "def _entry_list_field(entry, name):",
                        "def _unused_helper(entry, name):")
        self.assertNames("no _entry_list_field anywhere")

    def test_a_second_definition_reddens(self):
        self.tree.write("knowledge/dup.py",
                        "def _entry_list_field(entry, name):\n"
                        "    return [x for x in entry.get(name) or []\n"
                        "            if isinstance(x, str)]\n")
        self.assertNames("is defined 2 times")

    def test_the_wrong_owning_module_reddens(self):
        # `core` may import nothing above it, and build_entry_frontmatter — a
        # required call site — is IN core. A normalizer under knowledge/ would
        # therefore be unreachable from its own call site.
        self.tree.patch("core/frontmatter.py",
                        "def _entry_list_field(entry, name):",
                        "def _moved_away(entry, name):")
        self.tree.write("knowledge/index/indexread.py",
                        "def _entry_list_field(entry, name):\n"
                        "    v = entry.get(name)\n"
                        "    if isinstance(v, (list, tuple)):\n"
                        "        return [x for x in v if isinstance(x, str)]\n"
                        "    return []\n")
        self.assertNames("but it must live in")

    def test_dropping_the_string_filter_reddens(self):
        # A scalar guard ALONE is insufficient: ['ok', {...}] passes is-a-list and
        # still raises `unhashable type: 'dict'` at set()/tags.get().
        self.tree.patch("core/frontmatter.py",
                        "        return [x for x in v if isinstance(x, str)]",
                        "        return list(v)")
        self.assertNames("does not filter for STRING elements")

    def test_returning_the_caller_s_own_list_reddens(self):
        self.tree.patch("core/frontmatter.py",
                        "        return [x for x in v if isinstance(x, str)]",
                        "        return v")
        self.assertNames("returns the raw field unchanged")


class RequiredSiteRefutationTest(_RefutationCase):
    def test_a_reader_that_stops_calling_the_normalizer_reddens(self):
        self.tree.patch("knowledge/graph.py",
                        'frontmatter._entry_list_field(e, "relates")',
                        '(e.get("relates") or [])')
        self.assertNames("does not call _entry_list_field")

    def test_renaming_the_owning_function_reddens_rather_than_passing_silently(self):
        # The failure mode task 21 recorded: a gate pinned to a name disarms
        # itself on the next decomposition and goes GREEN.
        self.tree.patch("knowledge/graph.py", "def build_adjacency(data):",
                        "def build_adjacency_v2(data):")
        self.assertNames("build_adjacency is gone")

    def test_a_deleted_module_reddens(self):
        os.remove(os.path.join(self.tree.lib, "knowledge", "graph.py"))
        self.assertNames("missing module: knowledge/graph.py")


class RawReadRefutationTest(_RefutationCase):
    def test_a_new_unexempted_raw_read_reddens(self):
        # The inverted default: this is what keeps tasks 37-54 honest as ~44
        # verbs are ported onto these readers.
        self.tree.write("features/newverb.py",
                        "def cmd_newverb(data):\n"
                        "    out = []\n"
                        '    for e in data.get("entries") or []:\n'
                        '        for t in (e.get("tags") or []):\n'
                        "            out.append(t)\n"
                        "    return out\n")
        self.assertNames("reads `tags` RAW")

    def test_a_raw_read_via_subscript_reddens_too(self):
        self.tree.write("features/newverb.py",
                        "def cmd_newverb(entry):\n"
                        '    return entry["relates"]\n')
        self.assertNames("reads `relates` RAW")

    def test_a_stale_exemption_row_reddens(self):
        # A row that forgives a site which no longer exists is how an allowlist
        # quietly outlives the thing it was written for.
        self.tree.patch("knowledge/graph.py",
                        '    tags_map = data.get("tags") or {}\n'
                        "    return sorted(tags_map)\n",
                        "    return []\n")
        self.assertNames("matches nothing")

    def test_the_exempt_sites_themselves_stay_green(self):
        # The tags MAP, the parser product and the strict gate are all legitimate
        # raw reads; the gate must not demand the normalizer of them.
        self.assertGreen()


class StrictValidatorRefutationTest(_RefutationCase):
    def test_dropping_a_strict_message_reddens(self):
        self.tree.patch(
            "knowledge/index/indexvalidate.py",
            '''    erel = entry.get("relates")
    if erel is not None and not isinstance(erel, list):
        errors.append("entry '%s' relates is not a list" % label)
    elif isinstance(erel, list) and not all(isinstance(x, str) for x in erel):
        errors.append("entry '%s' relates has a non-string element" % label)
''',
            '''    erel = entry.get("relates")
    if erel is not None:
        pass
''')
        self.assertNames("relates is not a list")

    def test_dropping_the_non_string_element_check_reddens(self):
        self.tree.patch(
            "knowledge/index/indexvalidate.py",
            '''    elif isinstance(etags, list) and not all(isinstance(x, str) for x in etags):
        errors.append("entry '%s' tags has a non-string element" % label)
''', "")
        self.assertNames("tags has a non-string element")

    def test_renaming_the_strict_gate_reddens(self):
        self.tree.patch("knowledge/index/indexvalidate.py",
                        "def _entry_optional_errors(entry, label):",
                        "def _entry_optional_checks(entry, label):")
        self.assertNames("re-point STRICT_SITE")


@unittest.skipIf(_NO_CORE, _NO_CORE_REASON)
class RealTreeTest(unittest.TestCase):
    """The POSITIVE half, against the tree actually under measurement."""

    def test_the_gate_is_green(self):
        problems = isr.all_errors()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_gate_measured_something(self):
        # A gate that walked an empty tree reports 0 problems too. Both numbers
        # are asserted non-trivial so "green" can never mean "looked at nothing".
        self.assertGreater(len(isr.lib_files()), 50)
        self.assertGreater(len(isr.raw_read_sites()), 10)

    def test_every_exemption_row_carries_a_reason(self):
        for row in isr.RAW_READ_EXEMPT:
            self.assertEqual(len(row), 4, row)
            self.assertTrue(row[3].strip(), row)

    def test_the_four_readers_the_plan_names_are_all_required(self):
        named = {(r, f) for r, f, _why in isr.REQUIRED_NORMALIZED}
        for site in (("knowledge/graph.py", "build_adjacency"),
                     ("knowledge/graph.py", "graph_relation_errors"),
                     ("retrieval/corpus.py", "_entry_doc_text"),
                     ("core/frontmatter.py", "build_entry_frontmatter")):
            self.assertIn(site, named)


if __name__ == "__main__":
    unittest.main()
