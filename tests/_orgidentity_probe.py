"""The org-project IDENTITY as the live library actually resolves it.

The static half of this gate (`_orgidentity_gate`) reads the tree's SOURCE: that
both env-var key constants exist and spell what they should, that the marker
table is the vendor-neutral one, that every name it needs is defined somewhere.
None of that can say whether a marker's matching is a WHOLE TOKEN or a substring
— the difference between `Foo` resolving `Foo` and `Foo` also resolving
`Foo-Tests` — nor whether the deprecated spelling still arms the guard for an
operator whose `config.env` predates the rename. Those are behavioural facts,
and this module gets them by running the library in a sandbox.

It therefore owns the PROBE NAME-SPACE as well as the probe: the name always
asked for, the neighbouring declarations that must NOT satisfy it, one writable
body per marker the table may name, and what each identity case must observe.
The two env-var keys live here too, because they are what the probe removes from
the child's environment before it starts — the static scanner is a SECOND reader
of them, which is why the import runs scanner -> probe and never back.

Every probe here runs through `_probe_sandbox`, so AGENTWARE_CONFIG_HOME and
AGENTWARE_KNOWLEDGE_DIR are redirected to throwaway paths and the operator's
real config and knowledge base are never read or written.

Stdlib only.
"""

import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _probe_sandbox  # noqa: E402

#: The env-var key names. The current spelling, and the deprecated one that is
#: still READ (never written) so an existing config.env keeps the guard.
CURRENT_KEY = "AGENTWARE_ORG_PROJECT"
LEGACY_KEY = "AGENTWARE_ORG_PACKAGE"

#: The name the probe always ASKS for, and every declaration that must NOT
#: satisfy it. `FooV2` and `Foo-Tests` are real neighbours of a real checkout —
#: a version bump and a test package — which is what makes a substring match
#: unsafe; `PreFoo` covers the left side and `foo` the case fold.
PROBE_NAME_MATCH = "Foo"
PROBE_DECLARATIONS = ("Foo", "FooV2", "Foo-Tests", "PreFoo", "foo")


_PROBE_SRC = """
import json, os, sys
sys.path.insert(0, os.path.join(%(tree)r, "scripts"))
from agentware_lib.core import config, orgkeys
# The marker surface is read off whichever module owns it: its own core module
# when the tree splits it out, else the config module that still carries it.
markers = getattr(config, "orgidentity", config)
out = {"markers": [list(r) for r in markers.PROJECT_MARKERS],
       "name_file": markers.PROJECT_NAME_FILE,
       "cases": [], "identity": []}
name = %(match)r
bodies = %(bodies)s
for marker, _template in markers.PROJECT_MARKERS:
    if marker not in bodies:
        continue
    row = {"marker": marker, "declared": {}}
    for declared in %(declarations)r:
        d = os.path.join(%(scratch)r, "m-" + marker.replace(".", "_"),
                         "d-" + declared)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, marker), "w", encoding="utf-8") as fh:
            fh.write(bodies[marker] %% declared)
        row["declared"][declared] = bool(markers._dir_declares_project(d, name))
    out["cases"].append(row)
good = os.path.join(%(scratch)r, "declared")
os.makedirs(good, exist_ok=True)
with open(os.path.join(good, markers.PROJECT_NAME_FILE), "w") as fh:
    fh.write(name)
bad = os.path.join(%(scratch)r, "undeclared")
os.makedirs(bad, exist_ok=True)
for label, env in (("none", {}),
                   ("legacy", {orgkeys.ORG_PROJECT_LEGACY_KEY: name}),
                   ("current", {orgkeys.ORG_PROJECT_KEY: name}),
                   ("both", {orgkeys.ORG_PROJECT_KEY: name,
                             orgkeys.ORG_PROJECT_LEGACY_KEY: "SomethingElse"})):
    for key in (orgkeys.ORG_PROJECT_KEY, orgkeys.ORG_PROJECT_LEGACY_KEY):
        os.environ.pop(key, None)
    os.environ.update(env)
    out["identity"].append({
        "case": label,
        "resolved": config.resolve_org_project(),
        "declared_ok": config.org_project_identity_error(good) is None,
        "undeclared_ok": config.org_project_identity_error(bad) is None,
    })
json.dump(out, sys.stdout)
"""

#: One writable body per marker the table may name. A marker in the tree's table
#: with no body here is reported, never skipped: an unknown marker is exactly
#: what a vendor manifest sneaking back in would look like.
MARKER_BODIES = {
    "pyproject.toml": '[project]\nname = "%s"\n',
    "Cargo.toml": '[package]\nname = "%s"\n',
    "setup.cfg": "[metadata]\nname = %s\n",
    "setup.py": 'setup(\n    name="%s",\n)\n',
    "package.json": '{\n  "name": "%s",\n  "version": "1.0.0"\n}\n',
    "composer.json": '{\n  "name": "acme/%s"\n}\n',
    "go.mod": "module example.com/acme/%s\n\ngo 1.22\n",
    "pom.xml": "<project>\n  <artifactId>%s</artifactId>\n</project>\n",
    "build.gradle": "rootProject.name = '%s'\n",
    "build.gradle.kts": 'rootProject.name = "%s"\n',
    "settings.gradle": "rootProject.name = '%s'\n",
    "settings.gradle.kts": 'rootProject.name = "%s"\n',
    "CMakeLists.txt": "project(%s VERSION 1.0)\n",
    "Gemfile": "# gem %s\n",
    "mix.exs": "  def project do\n    [app: :%s]\n  end\n",
    "Project.toml": 'name = "%s"\n',
    ".agentware-project": "%s\n",
}


def run_probe(tree, scratch):
    """(payload, problems) — the behavioural facts, gathered in a subprocess.

    The sandbox is `_probe_sandbox`'s; the WORDING of each failure is this
    gate's own, because a caller reports into its own problem list and the
    truncation widths below are tuned to what a reader of THIS gate needs.
    """
    src = _PROBE_SRC % {"tree": tree, "scratch": scratch,
                        "match": PROBE_NAME_MATCH,
                        "declarations": PROBE_DECLARATIONS,
                        "bodies": repr(MARKER_BODIES)}
    env = _probe_sandbox.sandbox_env(scratch, drop=(CURRENT_KEY, LEGACY_KEY))
    probe = _probe_sandbox.run_json_probe(src, env, cwd=scratch, timeout=120)
    if probe.kind == _probe_sandbox.COULD_NOT_RUN:
        return None, ["probe did not run: %s" % probe.exc]
    if probe.kind == _probe_sandbox.NONZERO_EXIT:
        return None, ["probe exited %s: %s"
                      % (probe.returncode, (probe.stderr or "").strip()[:400])]
    if probe.kind == _probe_sandbox.NO_JSON:
        return None, ["probe emitted no JSON (%s): %r"
                      % (probe.decode_error, probe.stdout[:200])]
    return probe.payload, []


def check_exact(payload):
    """Check 3: whole-token matching, once per marker the tree actually has."""
    problems = []
    covered = {row["marker"] for row in payload["cases"]}
    for marker, _t in payload["markers"]:
        if marker not in MARKER_BODIES:
            problems.append("marker %r has no probe body here, so its matching "
                            "was never exercised" % marker)
        elif marker not in covered:
            problems.append("marker %r was not probed" % marker)
    for row in payload["cases"]:
        for declared in PROBE_DECLARATIONS:
            matched = row["declared"].get(declared)
            if matched is None:
                problems.append("marker %r never tried the declaration %r"
                                % (row["marker"], declared))
            elif declared == PROBE_NAME_MATCH and not matched:
                problems.append("marker %r does NOT match its own declared "
                                "name %r" % (row["marker"], PROBE_NAME_MATCH))
            elif declared != PROBE_NAME_MATCH and matched:
                problems.append("marker %r declaring %r SATISFIES the name %r "
                                "— the match is not a whole token"
                                % (row["marker"], declared, PROBE_NAME_MATCH))
    return problems


#: What each identity case must observe: (resolved name, declared root accepted,
#: undeclared root accepted). `None` means "any". The undeclared root must be
#: REFUSED in every case where a name resolves — that is the guard.
IDENTITY_EXPECTED = {
    "none": (None, True, True),
    "legacy": (PROBE_NAME_MATCH, True, False),
    "current": (PROBE_NAME_MATCH, True, False),
    "both": (PROBE_NAME_MATCH, True, False),
}


def check_legacy(payload):
    """Check 4: the deprecated spelling still arms the guard; current wins."""
    problems = []
    seen = {row["case"]: row for row in payload["identity"]}
    for case, (name, declared, undeclared) in sorted(IDENTITY_EXPECTED.items()):
        row = seen.get(case)
        if row is None:
            problems.append("identity case %r was not probed" % case)
            continue
        if row["resolved"] != name:
            problems.append("case %r resolved %r, expected %r"
                            % (case, row["resolved"], name))
        if row["declared_ok"] is not declared:
            problems.append("case %r: a root DECLARING the project was %s"
                            % (case, "refused" if declared else "accepted"))
        if row["undeclared_ok"] is not undeclared:
            # Describe what ACTUALLY happened, not what was expected: accepting
            # a root that does not declare the project means the guard is gone.
            actual = bool(row["undeclared_ok"])
            problems.append(
                "case %r: a root NOT declaring the project was %s — the guard "
                "is %s" % (case, "accepted" if actual else "refused",
                           "disarmed" if actual else "over-eager"))
    return problems
