"""agentware_lib.features.skillmanifest -- the SKILL.md manifest's TEXT SURFACE (layer: features).

Owns: the two ends of the `skill` catalog verbs' text, and nothing between them
-- the frontmatter grammar a SKILL.md DECLARES on the way in (`_FOLDED_MARKERS`,
`_frontmatter_block`, `_folded_scalar`), and the fixed-format reports the verbs
render on the way out (`_emit_validate`, `_emit_skill_add`).

Does NOT own: the catalog and the install/vet surface -> features.skills (which
imports THIS module); the execution-cache install gate -> features.skillsync; the
lockfile primitives -> features.skilllock; the proposal lifecycle ->
features.skillpropose / features.proposals.

WHY THESE TWO HALVES ARE ONE MODULE
-----------------------------------
Not because they are both "helpers" -- a module named for what its contents are
NOT has no boundary (R-MOD-05). They are one module because they share a
property nothing else in the skill family has: both are PURE TEXT TRANSFORMS.
Neither reads the catalog, touches the index, walks a tree, shells git, or calls
the vetter. That is exactly what lets them sit BELOW `features.skills` with a
strictly one-way edge -- `skills` imports this module, this module imports
nothing from `skills` -- where a helper module that reached back for
`read_skill_frontmatter` or `vet_skill_dir` would close a cycle. The dependency
direction IS the boundary, and it is checkable rather than thematic.

THE FOLDED-SCALAR GUARD IS KEYED ON THE KEY
-------------------------------------------
`_folded_scalar` gathers the lines below a marker; it does NOT decide whether to
gather. That decision stays at the call site, because it is keyed on the KEY as
well as the value: `name: >` is not a folded scalar, it is the literal ">"
landing in `name`. Only `description` is ever gathered. Moving the test in here
would make the helper look total while quietly changing what `name` parses to.

Layer deps: `core.frontmatter` ONLY, and strictly DOWNWARD -- for the one fence
finder the package publishes there. Nothing sideways and nothing upward: the
property that matters is that this module never reaches back for
`read_skill_frontmatter` or `vet_skill_dir`, which is what would close a cycle
with `features.skills`. A `core` edge cannot, because `core` imports nothing.
"""

import json
import sys

import agentware_lib.core.frontmatter as frontmatter


#: The folded / block scalar markers YAML allows in place of an inline value.
#: An EMPTY value is one of them, which is why `description:` on a line of its
#: own gathers the lines below it rather than yielding "".
_FOLDED_MARKERS = (">", ">-", "|", "|-", "")


def _frontmatter_block(text):
    """The lines BETWEEN a SKILL.md's opening and closing `---`, or None.

    None means "there is no frontmatter here" for BOTH reasons a reader must
    distinguish from an empty one: no opening fence, and an opening fence that
    is never closed. A document whose fences touch yields [] -- a real, empty
    block -- so the caller's loop is a no-op rather than a bail-out.

    The FENCE itself is `core.frontmatter._frontmatter_block`, the package's ONE
    definition of it (task 99 wave 16; it was hand-written at nine sites before
    that). This keeps its own name because the SKILL.md readers want the fields
    and never the body, and a `[head, block, tail]` unpack at every call site
    would be three names for a value two of which are always discarded.
    """
    parts = frontmatter._frontmatter_block(text)
    return None if parts is None else parts[1]


def _folded_scalar(block, i):
    """Gather a folded/block scalar declared at `block[i]` -> (value, next_i).

    Continuation is "blank, or starting with a space or a tab", so a blank line
    INSIDE the scalar does not end it (blanks are then dropped from the join)
    and a tab-indented body is read the same as a space-indented one. `next_i`
    is where the caller resumes, which is the line that ENDED the scalar -- never
    i+1, or the key on that line would be skipped.
    """
    parts = []
    j = i + 1
    while j < len(block) and (block[j].strip() == "" or
                              block[j][:1] in (" ", "\t")):
        parts.append(block[j].strip())
        j += 1
    return " ".join(p for p in parts if p).strip(), j


def _emit_validate(results, ok, fmt, footer):
    """The ONE `skill validate` renderer, shared by both of the verb's arms.

    `footer` is the trailing verdict line, which is the only part the `--source`
    arm and the catalog arm spell differently. The JSON document is identical
    for both, `count` included: the `--source` arm passes a one-row list.
    """
    if fmt == "json":
        json.dump({"ok": ok, "count": len(results), "results": results},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    for r in results:
        if r["ok"]:
            print("OK    %s" % r["name"])
        else:
            print("FAIL  %s" % r["name"])
            for e in r["errors"]:
                print("        - %s" % e)
    print(footer)


def _emit_skill_add(fmt, payload, trust, provenance, copied, name, dest_dir,
                    extra_line=None, suffix=""):
    """The ONE `skill add` renderer, shared by both DESTINATIONS.

    The two destinations do NOT emit the same document, and the difference is
    the caller's to state rather than this function's to infer: a harness-path
    install carries `indexed: false` and no `id`/`path`, the KB install carries
    both. So `payload` arrives already built, in the key ORDER it must print --
    re-deriving it here is how the two documents would silently converge. The
    human report differs in exactly two places, and each has a parameter:
    `suffix` for the harness path's trailing note, `extra_line` for the KB
    install's index-registration line.
    """
    if fmt == "json":
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    print("VET PASS (%s, ref %s)" % (trust, provenance["pinned_ref"]))
    print("%s skill '%s' -> %s%s"
          % ("INSTALLED" if copied else "SKIPPED", name, dest_dir, suffix))
    if extra_line:
        print(extra_line)


_EXPORTS = [
    "_FOLDED_MARKERS",
    "_frontmatter_block",
    "_folded_scalar",
    "_emit_validate",
    "_emit_skill_add",
]

__all__ = list(_EXPORTS)
