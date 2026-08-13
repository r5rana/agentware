#!/usr/bin/env python3
"""Runnable contract check for the GUIDANCE SURFACES -- personas, docs, skills.

Underscore-prefixed so `unittest discover` never COLLECTS it (the convention
`tests/_doc_sync.py` and `tests/_doc_extensibility_check.py` already carry); a
standalone, stdlib-only, py3.7-safe checker meant to be run directly:

    python3 tests/_guidance_sync.py [--json]

`tests/_doc_extensibility_check.py` gates the two CONTRIBUTOR documents against
the manifest. This gates everything an AGENT reads: the three personas in
`.claude/agents/`, every `SKILL.md` under `.claude/skills/`, every document under
`docs/`, and the always-on steering at the repo root. Those surfaces are prose
nobody compiles, they name paths by hand, and a pointer into a file that was
deleted three waves ago is worse than saying nothing (`R-FAIL-08`): the agent
follows it, finds nothing, and re-derives whatever it likes.

Six arms, each exiting non-zero and naming every offender:

  A. **No surface names a RETIRED path or claim** except inside a paragraph that
     says it is retired. The needles are exact (a path, a symbol, a size claim) —
     never a vibe — and the escape is a TERMINAL MARKER in the same paragraph, so
     `docs/architecture.md` may keep saying the monolith is gone while nothing may
     say it is still there. Two-directional: a retired path that comes BACK on
     disk fails the row, because a ledger claiming an absence must be true.
  B. **Every repo-relative path a surface names is CLAIMED** — present on disk, or
     claimed by `catalog/modules.json` as a module path, an owning test file or a
     capped file. The manifest claim is what lets `docs/architecture.md` name the
     189 owning tests `R-MOD-06` requires but nobody has written yet (task 101's
     residual) while a path NOTHING claims still fails.
  C. **Every `agentware_lib.<dotted>` name resolves to a published module** (or a
     package holding one). The symbol-level twin of B: a doc may name
     `agentware_lib.cli.assemble_parser`, because `agentware_lib.cli` is
     published; it may not name a module the manifest dropped.
  D. **Every `python3 tests/_x.py` command in a surface resolves to a real file.**
     Arm D of `_doc_extensibility_check`, widened from two documents to every
     guidance surface — a persona telling an agent to run a checker this tree has
     never had is the same defect in a louder place.
  E. **Every EXEMPT row is WITNESSED and used.** A path that is deliberately not
     in this tree (a walkthrough's fictional worked example, a spec directory
     inside the TARGET webapp) is legal only while the surface still says so: the
     row names a witness string that must appear in that surface. A row nothing
     references any more fails, so an exemption cannot outlive its reason.
  F. **Every CLAIM row holds** — a surface that must name a thing still names it.
     Three rows: the fanout doc must name the real cross-lane chokepoint
     (`catalog/modules.json`), and the execution persona and the skills catalog
     must both route to `modular-development`, because a skill nothing points at
     is a skill nothing loads.

WHY THE SCOPE IS DECLARED, NOT GUESSED. A backticked `logs/metrics.jsonl` in
`docs/GUIDE.md` is not a repo path at all — it is KNOWLEDGE-DIR-relative, and a
checker that assumes one root reports a doc as broken for describing a different
one correctly. So `REPO_PREFIXES` names the top-level directories THIS repo owns,
and everything else is another root's business and out of scope, stated here
rather than filtered by eye.

Read-only. No network. Standard library only.
"""
import glob
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

CATALOG_REL = os.path.join("catalog", "modules.json")

#: The top-level directories THIS repository owns. A backticked path starting
#: with one of these is a claim about this tree and is checked; anything else
#: (`logs/`, `work/`, `benchmarks/`, `.github/`, a bare relative path) belongs to
#: the knowledge dir, the target workspace, or no root at all, and arm B says
#: nothing about it. Widening this tuple widens the gate.
REPO_PREFIXES = ("scripts/", "tests/", "catalog/", "webui/", "steering/",
                 "docs/", ".claude/")

#: Extensions that make a token a FILE reference rather than a dotted symbol.
#: `tests/_fixtures.load_cli` is a symbol; `tests/_fixtures.py` is a path.
PATH_SUFFIXES = (".py", ".md", ".json", ".sh", ".ts", ".tsx", ".js", ".yml",
                 ".yaml", ".txt", ".jsonl", ".html", ".css", ".png", ".pub",
                 ".toml", ".cfg", ".lock", ".gitkeep")


def _surfaces(root):
    """The declared guidance set: (relative path, class).

    `persona` and `steering` are injected into every session; `skill` and `doc`
    are pulled on demand. All four are prose an agent ACTS on, which is the
    property that puts them in one gate.
    """
    found = []
    for pattern, kind in ((".claude/agents/*.md", "persona"),
                          (".claude/skills/**/*.md", "skill"),
                          ("docs/**/*.md", "doc"),
                          ("steering/*.md", "steering")):
        for path in sorted(glob.glob(os.path.join(root, pattern),
                                     recursive=True)):
            found.append((os.path.relpath(path, root), kind))
    for rel in ("AGENTS.md", "CLAUDE.md", "README.md"):
        if os.path.isfile(os.path.join(root, rel)):
            found.append((rel, "steering"))
    return found


#: (needle regex, what it was, the task that retired it). A surface may name one
#: ONLY inside a paragraph carrying a TERMINAL_MARKER. `path` is the on-disk file
#: whose continued ABSENCE the row also asserts (None when the needle is a claim
#: rather than a file).
RETIRED = (
    (r"agentware_legacy\.py", "the preserved monolith, exec'd by the facade",
     "task 96", "scripts/agentware_legacy.py"),
    (r"_FACADE_MODULES", "the facade's re-export loop", "task 96", None),
    (r"_registration\.py", "the frozen central verb registry", "task 70",
     "scripts/agentware_lib/_registration.py"),
    (r"_ast_forward_edges\.py", "a donor checker never staged here", "task 75",
     "tests/_ast_forward_edges.py"),
    (r"_global_resolve\.py", "a donor checker never staged here", "task 75",
     "tests/_global_resolve.py"),
    (r"\d+k-line", "the monolith's size, quoted as a live fact", "task 96",
     None),
)

#: A paragraph naming a retired thing must also say it is retired. Deliberately
#: past-tense/absence words only: "monolith" plus "big" is not a retirement.
TERMINAL_MARKERS = ("gone", "retired", "deleted", "no longer", "removed",
                    "replaced", "never had", "never staged", "used to",
                    "no such")

#: (path or dotted name, the surface allowed to name it, witness, reason).
#: The witness is the string that must appear in that surface for the exemption
#: to hold -- it is what makes the absence CORRECT rather than merely tolerated.
EXEMPT = (
    ("agentware_lib.ops.greet", "docs/add-a-feature.md",
     "scaffold new --kind verb --layer ops --name greet",
     "the walkthrough's fictional worked example: a module the reader CREATES "
     "by following the page, which must not exist in the tree it is copied from"),
    ("tests/agentware/", ".claude/skills/ui-verification/SKILL.md",
     "<webapp>/tests/agentware/",
     "TARGET-webapp-relative, not this repo's tests/ tier: the skill creates it "
     "inside the app under verification, and says so on its first mention"),
    ("catalog/agentware-release.pub", "docs/release-signing.md",
     "trust pin --from-repo",
     "the release signing key, committed only into a SIGNED release clone; a "
     "development checkout pins nothing and correctly has no key"),
)

#: (surface, token it must name, why). A surface that stops naming its token has
#: stopped doing the job the row records.
CLAIMS = (
    ("docs/fanout.md", "catalog/modules.json",
     "the real cross-lane chokepoint: lanes collide on the GENERATED registries, "
     "not on one big file, and a fanout doc that names a monolith sends lanes "
     "to group their work by the wrong thing"),
    (".claude/agents/agentware-execution.md", "modular-development",
     "the persona must route to the on-demand procedure; a skill nothing points "
     "at is a skill nothing loads"),
    (".claude/skills/README.md", "modular-development",
     "the catalog page is where a reader discovers the skill set"),
)

def _exempted(exempt, token, rel):
    """Is `token` covered by an EXEMPT row declared for surface `rel`?

    A row whose token ends in `/` covers everything BENEATH it: the ui-verification
    skill names both the spec directory and the `.gitkeep` inside it, and they are
    one absence with one reason, not two rows.
    """
    row = exempt.get(token)
    if row is not None and row[1] == rel:
        return True
    for key, candidate in exempt.items():
        if key.endswith("/") and token.startswith(key) and candidate[1] == rel:
            return True
    return False


BACKTICK = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")
DOTTED = re.compile(r"\bagentware_lib\.([A-Za-z0-9_.]+)")
RUNNABLE = re.compile(r"python3?\s+(tests/_[A-Za-z0-9_]+\.py)")


def _read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        return handle.read()


def load_claims(root):
    """The set of repo paths `catalog/modules.json` CLAIMS.

    Three kinds, and the second is the load-bearing one: `test_file` is the
    owning test `R-MOD-06` requires, claimed whether or not it has been written.
    """
    with open(os.path.join(root, CATALOG_REL), encoding="utf-8") as handle:
        manifest = json.load(handle)
    claimed = set()
    for module in manifest["modules"]:
        claimed.add(module["path"])
        if module.get("test_file"):
            claimed.add(module["test_file"])
    for entry in manifest.get("files", []):
        claimed.add(entry["path"] if isinstance(entry, dict) else entry)
    published = set(module["name"] for module in manifest["modules"])
    return claimed, published


def path_tokens(text):
    """Repo-prefixed path references: backticked tokens and markdown links."""
    out = set()
    words = []
    for raw in BACKTICK.findall(text):
        # EVERY word of a backticked span, not just the first: a path most often
        # appears as the ARGUMENT of a quoted command (`python3 tests/_x.py
        # --write`), and reading only word 0 would see `python3` and check
        # nothing -- a blind spot exactly where the doc is most actionable.
        words.extend(raw.split())
    words.extend(LINK.findall(text))
    for raw in words:
        token = raw.strip().split("::")[0].strip("`'\"").rstrip(".,;:)")
        if not token.startswith(REPO_PREFIXES):
            continue
        if any(char in token for char in "<>*?~$"):
            continue        # a placeholder, not a path
        if token.endswith("/") or token.endswith(PATH_SUFFIXES):
            out.add(token)
    return out


def _paragraphs(text):
    return text.split("\n\n")


def check_retired(root, surfaces):
    """A. nothing names a retired path or claim outside a retirement sentence."""
    errors = []
    for needle, what, task, path in RETIRED:
        pattern = re.compile(needle)
        for rel, _kind in surfaces:
            for para in _paragraphs(_read(root, rel)):
                if not pattern.search(para):
                    continue
                if any(marker in para.lower() for marker in TERMINAL_MARKERS):
                    continue
                errors.append(
                    "A: %s names %r (%s, retired by %s) as a LIVE fact -- a "
                    "retired name is legal only in a paragraph that says it is "
                    "retired" % (rel, needle, what, task))
        if path and os.path.exists(os.path.join(root, path)):
            errors.append("A: the RETIRED ledger claims %s is gone (%s) but it "
                          "is back on disk -- retire the row or the file"
                          % (path, task))
    return errors


def _is_retired(token):
    """Does arm A already own this name?

    A RETIRED path is absent from the tree BY DESIGN, so arm B would report every
    legitimate retirement sentence as a dangling pointer -- the exact prose arm A
    deliberately allows. One arm per name: A judges whether naming it is honest,
    B never sees it.
    """
    return any(re.search(needle, token) for needle, _w, _t, _p in RETIRED)


def check_paths(root, surfaces, claimed):
    """B. every repo-relative path a surface names is claimed."""
    exempt = dict((row[0], row) for row in EXEMPT)
    errors = []
    for rel, _kind in surfaces:
        for token in sorted(path_tokens(_read(root, rel))):
            if os.path.exists(os.path.join(root, token)):
                continue
            if token in claimed:
                continue
            if _exempted(exempt, token, rel) or _is_retired(token):
                continue
            errors.append(
                "B: %s names %s, which is neither in the tree nor claimed by %s"
                % (rel, token, CATALOG_REL))
    return errors


def check_modules(root, surfaces, published):
    """C. every agentware_lib.<dotted> name resolves to a published module."""
    exempt = dict((row[0], row) for row in EXEMPT)
    errors = []
    for rel, _kind in surfaces:
        for dotted in sorted(set(DOTTED.findall(_read(root, rel)))):
            name = ("agentware_lib." + dotted).rstrip(".")
            parts = name.split(".")
            # a dotted name is claimed if ANY prefix is a published module (the
            # tail is then a symbol) or it is a package holding one.
            if any(".".join(parts[:i]) in published
                   for i in range(2, len(parts) + 1)):
                continue
            if any(mod.startswith(name + ".") for mod in published):
                continue
            row = exempt.get(name)
            if row is not None and row[1] == rel:
                continue
            errors.append("C: %s names module %s, which %s does not publish"
                          % (rel, name, CATALOG_REL))
    return errors


def check_runnables(root, surfaces):
    """D. every `python3 tests/_x.py` in a surface resolves."""
    errors = []
    for rel, _kind in surfaces:
        for target in sorted(set(RUNNABLE.findall(_read(root, rel)))):
            if not os.path.exists(os.path.join(root, target)):
                errors.append("D: %s tells the reader to run %s, which does not "
                              "exist" % (rel, target))
    return errors


def check_exemptions(root):
    """E. every exempt row is witnessed by the surface that uses it."""
    errors = []
    for token, rel, witness, _reason in EXEMPT:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            errors.append("E: exempt row %r names surface %s, which does not "
                          "exist" % (token, rel))
            continue
        text = _read(root, rel)
        if witness not in text:
            errors.append(
                "E: %s no longer carries the witness %r that makes %r a "
                "correct absence -- the exemption has outlived its reason"
                % (rel, witness, token))
        elif token not in text:
            errors.append("E: exempt row %r is stale: %s does not name it"
                          % (token, rel))
    return errors


def check_claims(root):
    """F. every surface that must name a thing still names it."""
    errors = []
    for rel, token, reason in CLAIMS:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            errors.append("F: claim row names surface %s, which does not exist"
                          % rel)
        elif token not in _read(root, rel):
            errors.append("F: %s must name %r (%s)" % (rel, token, reason))
    return errors


def problems(root=None):
    root = root or _REPO_ROOT
    surfaces = _surfaces(root)
    if not surfaces:
        return ["no guidance surface resolved under %s" % root]
    claimed, published = load_claims(root)
    errors = check_retired(root, surfaces)
    errors.extend(check_paths(root, surfaces, claimed))
    errors.extend(check_modules(root, surfaces, published))
    errors.extend(check_runnables(root, surfaces))
    errors.extend(check_exemptions(root))
    errors.extend(check_claims(root))
    return errors


def main(argv):
    root = _REPO_ROOT
    errors = problems(root)
    if "--json" in argv[1:]:
        json.dump({"ok": not errors, "count": len(errors), "errors": errors},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0 if not errors else 1
    if errors:
        sys.stderr.write("FAIL guidance sync:\n")
        for error in errors:
            sys.stderr.write("  - %s\n" % error)
        return 1
    surfaces = _surfaces(root)
    sys.stdout.write(
        "OK guidance sync: %d surfaces, %d retired names absent or terminal, "
        "every repo path claimed by %s, every module published, every runnable "
        "resolves, %d exemptions witnessed, %d claims held\n"
        % (len(surfaces), len(RETIRED), CATALOG_REL, len(EXEMPT), len(CLAIMS)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
