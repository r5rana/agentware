"""ADJUDICATIONS -- the rows task 99 WAVE 11's skill-lifecycle paydown created.

The FOURTH sibling of `tests/_body_adjudication.py`, split off for the same
mechanical reason as the three before it and at the same seam the roster's own
docstring anticipates: `_body_adjudication_ceilingpaydown` reached its 600-line
`tests/**` cap, and that cap is a SHRINK-ONLY ratchet, so a wave that grew the
file past it would be worsening one ceiling in the commit whose purpose is to
pay six others down. The roster makes a new sibling cost the host nothing.

The seam is a WAVE, and that is a real obligation rather than a convenience: the
two rows below share a boundary proof, a family, and a single claim -- that the
skill lifecycle's six ceiling rows were paid down without changing one observable
byte. They are read together or not at all.

Nothing here is forgiven. Each row still carries the TWO obligations the gate
enforces on every run: the host plus its named receivers must clear the winner's
length, AND the host alone must clear its own measured floor -- so a parent
cannot be hollowed out behind bulky helpers.

`_body_adjudication_siblings` merges this into `SIBLING_ROWS`, which the host
merges into `ADJUDICATIONS` -- the ONE name every consumer imports.
"""

SKILL_LIFECYCLE_ROWS = {}

# --------------------------------------------------------------------------
# DECOMPOSED -- task 99 wave 11: the SKILL LIFECYCLE
# --------------------------------------------------------------------------
# Two of the wave's six ceiling rows sit on bodies the differ tracks, and they
# fail its floor in OPPOSITE ways, which is why both are re-adjudicated here
# rather than one being left alone.
#
# `cmd_skill_add` arrives with a `decomposed` row ALREADY -- task 54's, naming
# the tiered vetter and the index registration. It is MOVED here (not copied:
# the sibling tables are asserted disjoint, and the host declaration is gone)
# because this wave adds two more receivers and lowers the host floor, and
# growing `_body_adjudication.py` is precisely what its own shrink-only
# `file_over_cap` row forbids. Task 54's finding is carried forward verbatim in
# the rationale, because a moved row that loses why it exists is a deleted row
# with extra steps.
#
# `cmd_skill_validate` arrives as a plain `longer-body` row -- the differ's
# DEFAULT, meaning nobody had ever adjudicated it. Its winner was the donor's
# 2813-char body, which this tree satisfied by holding a body that RE-IMPLEMENTED
# `_validate_skill_dir` inline. Paying the ceiling row down deleted the
# duplicate, so the length proxy reads a 2000-char loss where the actual change
# is a de-duplication. That is the exact case `decomposed` exists for, and the
# receivers below are what the duplicate became.
#
# Neither row is forgiven. The host + its named receivers still clear the
# winner's length, AND each host clears a floor of its own measured after the
# split -- so a later port cannot hollow the handler out behind bulky helpers.
#
# Pure refactor PROVEN, not asserted: `evidence/task99/skilllifecycle_boundary.py`
# captures 154 rows across the CLI and function boundaries -- every `skill`
# validate/add/remove/list/search/sync/candidates invocation, the SKILL.md
# frontmatter reader's whole 27-document scalar taxonomy, the coverage index and
# its matcher, `_skill_promote_run` over every arm of its report, and the twelve
# two-mode-flag combinations that pin `cmd_skill_sync`'s precedence CHAIN -- and
# the capture is BYTE-IDENTICAL before and after (193,854 bytes, empty diff),
# with its own determinism pinned first by a before->before re-run.

_SKILLS_W11 = "scripts/agentware_lib/features/skills.py"
_SKILLMANIFEST = "scripts/agentware_lib/features/skillmanifest.py"
_SKILLENTRY_W11 = "scripts/agentware_lib/knowledge/skillentry.py"

SKILL_LIFECYCLE_ROWS.update({
    "cmd_skill_add": {
        "adjudication": "decomposed",
        "host": _SKILLS_W11,
        "floor_chars": 1387,
        "absorbs": [
            _SKILLS_W11 + "::_resolve_add_source",      # catalog vs --source
            _SKILLS_W11 + "::_vet_or_refuse",           # the tiered vetter (task 54)
            _SKILLS_W11 + "::_skill_add_kb",            # the INDEXED destination
            _SKILLMANIFEST + "::_emit_skill_add",       # the one report, both dests
            _SKILLENTRY_W11 + "::skill_register_entry"],  # the locked index write
        "rationale": "Task 99 wave 11: 19 branches / 105 lines -> 8 / 46. Task "
                     "54's finding is CARRIED FORWARD and still holds: the "
                     "vetter call was not a decomposition when the port started, "
                     "it was a downgrade -- the modular copy called "
                     "`vet_skill_dir` (the danger-marker scan) directly instead "
                     "of the BLOCK-dominant `_skill_verdict`, so a skill tree "
                     "containing a SYMLINK installed cleanly and the "
                     "install-name validation and REVIEW tier were bypassed "
                     "entirely. `_vet_or_refuse` is where that tiered verdict "
                     "lives, and it stays a named receiver for that reason. "
                     "This wave adds three more, each a step an operator can "
                     "lose: drop `_resolve_add_source` and the remote-URL "
                     "refusal goes with it, turning `skill add --source "
                     "https://...` from a refusal into a path that never "
                     "resolves; drop `_skill_add_kb` and the DEFAULT "
                     "destination stops registering an index entry while still "
                     "copying the tree, so the skill installs and is "
                     "unfindable. `_emit_skill_add` is named because it is now "
                     "the SINGLE renderer for BOTH destinations, and the two "
                     "documents are deliberately NOT the same: a harness-path "
                     "install carries `indexed: false` and no `id`/`path`, the "
                     "KB install carries both, in a fixed key ORDER. The "
                     "payload is therefore built by the CALLER and passed in -- "
                     "re-deriving it inside the renderer is exactly how the two "
                     "documents would silently converge. What did NOT move is "
                     "the ORDER of the two external-source gates: the vet runs "
                     "BEFORE the `--allow-external` confirmation, so a "
                     "dangerous source is BLOCKED outright rather than offered "
                     "a flag that would install it. The only input separating "
                     "the two orderings is a source that is both dangerous and "
                     "unconfirmed, and it is driven at the boundary and pinned "
                     "by `tests/unit/features/test_skills.py`.",
    },
    "cmd_skill_validate": {
        "adjudication": "decomposed",
        "host": _SKILLS_W11,
        "floor_chars": 840,
        "absorbs": [
            _SKILLS_W11 + "::_skill_dir_errors",           # the ONE folder check
            _SKILLS_W11 + "::_catalog_validate_results",   # the per-entry loop
            _SKILLMANIFEST + "::_emit_validate"],          # the one renderer
        "rationale": "Task 99 wave 11: 29 branches / 79 lines -> 10 / 25, the "
                     "second-widest body in the wave. The row was a plain "
                     "`longer-body` -- never adjudicated -- and this tree "
                     "satisfied the donor's 2813-char floor by holding a body "
                     "that RE-IMPLEMENTED `_validate_skill_dir` inline, "
                     "eighteen lines of it, beside the helper it duplicated. "
                     "Deleting a duplicate reads to a LENGTH proxy as a loss, "
                     "which is why the row moves to `decomposed`: the receivers "
                     "below are what the duplicate became, and together they "
                     "still clear the winner. The two arms are NOT one call, "
                     "and `_skill_dir_errors` is parameterised rather than "
                     "unified because of it: the `--source` arm spells its two "
                     "file-level refusals with the ABSOLUTE resolved path while "
                     "the catalog arm spells them with the RELATIVE `source` "
                     "the catalog declared, and the catalog arm carries a "
                     "catalog-name mismatch that sits BETWEEN the folder "
                     "mismatch and the description check. A refactor that "
                     "simply called `_validate_skill_dir` from the catalog loop "
                     "-- which is what the ceiling row's own seam text proposed "
                     "-- would have changed both, silently. The input that "
                     "makes it observable is a catalog entry wrong in all three "
                     "places at once, and it is driven at the boundary and "
                     "pinned by `tests/unit/features/test_skills.py`.",
    },
})
