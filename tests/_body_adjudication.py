#!/usr/bin/env python3
"""ADJUDICATIONS — the hand-audited half of the body differ (task 30).

`_body_differ.py` computes every divergence mechanically and defaults each row
to the length proxy: the longer body wins, and the merged tree must keep at
least that many characters. That default is correct for 235 of the 242 rows.

The other seven are rows where the merged tree is ALREADY shorter than the
adjudicated winner, and each one was read line by line before it was written
down here. Three are the plan's own module splits, three are decisions the plan
made deliberately, and one is a real donor capability this tree has not adopted
yet. Nothing here forgives a row: every entry still leaves a floor the gate
enforces on every run.

Keep this table beside the mechanics, not inside them. A reviewer auditing "what
was allowed to shrink, and who said so" should be able to read one short file
and see all of it, with the reasoning attached to the row rather than buried in
a commit message.

Adjudication kinds and what each obliges (see `_body_differ` for the mechanics):

  decomposed  -> `host` + `absorbs` + `floor_chars`; the split's receivers are
                 named, must all still exist, and their lengths pay for the
                 shrunken parent -- while the parent itself keeps a floor, so it
                 cannot be emptied out behind them.
  superseded  -> `floor_chars`; a plan decision replaced the winner's body, so
                 the row now ratchets against the replacement.
  deferred    -> `floor_chars` + `follow_up`; the winner's body has not landed,
                 a named task owns it, and it may not get worse meanwhile.
"""

# --------------------------------------------------------------------------
# DECOMPOSED -- the plan's own splits (tasks 21 and 22)
# --------------------------------------------------------------------------

# Task 21 split the dream STEP module into a package with one file per step.
# The health-gate step's nine checks each became a named `_dream_hg_check_*`
# helper, so the parent shrank 4854 -> 699 while gaining nine receivers. Naming
# them individually is what makes this a split rather than a deletion: drop any
# one check and the gate reddens on a missing absorber.
_DREAM_HG = "scripts/agentware_lib/features/dreamsteps/stephealthgate.py"
_DREAM_HG_CHECKS = (
    "_dream_hg_check_config", "_dream_hg_check_cron", "_dream_hg_check_gitignore",
    "_dream_hg_check_identity", "_dream_hg_check_kb_structure",
    "_dream_hg_check_kdir_drift", "_dream_hg_check_rebase",
    "_dream_hg_check_sentinel", "_dream_hg_check_templates",
)

# Task 21 split the index module into read / write / rebuild, and task 22 landed
# duplicate-id dedup and quarantine-and-continue on top. The per-entry row build
# moved out to `_frontmatter_row`, which in turn calls `_entry_title_summary`
# (the skills title/summary backfill) and `_apply_org_facet` (the origin/team
# columns) -- the whole chain the donor's body used to hold inline.
_INDEX_REBUILD = "scripts/agentware_lib/knowledge/index/indexrebuild.py"
_INDEX_ROW_CHAIN = ("_frontmatter_row", "_entry_title_summary", "_apply_org_facet")

# Task 21 split `cmd_config`'s ~590-line guard-clause chain into per-setting
# handlers behind CONFIG_ROUTES: 19172 -> 720 chars, with every guard clause now
# a (predicate, handler) pair in a sibling module. Every handler in the route
# table is named here, so collapsing the table back into one function, or losing
# a setting on the way through a later port, reddens this row by name.
_CFG = "scripts/agentware_lib/features/configcmd/"
_CFG_HANDLERS = (
    [_CFG + "cfgdream.py::" + n for n in (
        "_cli_only", "_dream_max_runtime_only", "_dream_only",
        "_dream_schedule_only", "_set_cli", "_set_dream",
        "_set_dream_max_runtime", "_set_dream_schedule")]
    + [_CFG + "cfgidentity.py::" + n for n in (
        "_record_onboarding", "_set_knowledge_dir",
        "_set_skill_propose_threshold", "_set_user_handle",
        "_skill_propose_threshold_only", "_user_handle_only")]
    + [_CFG + "cfgkbgit.py::" + n for n in (
        "_kb_autocommit_only", "_kb_push_only", "_set_autocommit", "_set_push")]
    + [_CFG + "cfgorg.py::" + n for n in (
        "_handle_org", "_kb_mode_only", "_org_triggered", "_set_kb_mode")]
    + [_CFG + "cfgphases.py::" + n for n in ("_handle_phase", "_phase_triggered")]
    + [_CFG + "cfgretrieval.py::" + n for n in (
        "_embed_model_only", "_embedder_only", "_retrieval_mode_only",
        "_set_embed_model", "_set_embedder", "_set_mode", "_set_retrieval_mode")]
    + [_CFG + "cfgshow.py::_show_config"]
)


ADJUDICATIONS = {
    "_dream_health_gate_checks": {
        "adjudication": "decomposed",
        "host": _DREAM_HG,
        "floor_chars": 699,
        "absorbs": [_DREAM_HG + "::" + n for n in _DREAM_HG_CHECKS],
        "rationale": "Task 21 split the dream step module into one file per "
                     "step; the nine health checks became named helpers in the "
                     "same file. Logic relocated, not removed.",
    },
    "build_index_from_frontmatter": {
        "adjudication": "decomposed",
        "host": _INDEX_REBUILD,
        "floor_chars": 1302,
        "absorbs": [_INDEX_REBUILD + "::" + n for n in _INDEX_ROW_CHAIN],
        "rationale": "Task 21 split the index module into read/write/rebuild and "
                     "task 22 added duplicate-id dedup; the per-row build moved "
                     "to _frontmatter_row and its two helpers.",
    },
    "cmd_config": {
        "adjudication": "decomposed",
        "host": _CFG + "cfgmain.py",
        "floor_chars": 720,
        "absorbs": _CFG_HANDLERS,
        "rationale": "Task 21 split the ~590-line guard-clause chain into "
                     "per-setting handlers behind CONFIG_ROUTES; cmd_config is "
                     "now the walk over that table.",
    },

    # ----------------------------------------------------------------------
    # SUPERSEDED -- the plan decided against the winner's body
    # ----------------------------------------------------------------------

    # Task 15 deleted the KB-repointing module outright. What that removed from
    # cmd_dream is exactly the `_dream_maybe_repoint_kb()` call and the
    # `kb_repoint` field it stamped onto the cycle record -- a dream step that
    # mutated where the knowledge base points, which is the behaviour task 15
    # exists to end. The donor body is longer BECAUSE it still repoints.
    "cmd_dream": {
        "adjudication": "superseded",
        "floor_chars": 3603,
        "rationale": "Task 15 deleted the KB-repointing module; the donor's "
                     "extra 240 chars are the _dream_maybe_repoint_kb call and "
                     "its kb_repoint cycle field, deliberately removed.",
    },
    # Task 18 rewrote whereami as a marker-driven package resolver. The donor
    # body is longer because it inlines a vendor-specific walk (a `Config` file
    # whose parent directory must be named `src`); the merged body delegates to
    # `core.projectcontext.whereami_payload`, which resolves in any repository
    # layout. Longer, here, means less portable.
    "cmd_whereami": {
        "adjudication": "superseded",
        "floor_chars": 865,
        "rationale": "Task 18 replaced the donor's vendor-specific Config/src "
                     "walk with the marker-driven resolver in "
                     "core.projectcontext; the handler now only picks a start "
                     "directory and renders.",
    },
    # Zero code difference: the ONLY divergence is one docstring sentence, where
    # the donor cites an internal plan artifact by name ("rel_prefix (Plan B
    # Task 3): ...") that task 20 strips from this repository. Kept as a row
    # rather than waved through, because it is the clearest evidence that the
    # docstring-inclusive normalisation is doing its job.
    "git_entry_ids_at_ref": {
        "adjudication": "superseded",
        "floor_chars": 1648,
        "rationale": "Task 20 removed the internal plan-artifact reference from "
                     "the docstring. Prose only -- the code is byte-identical.",
    },

    # ----------------------------------------------------------------------
    # DEFERRED -- a real donor capability this tree has not adopted yet
    # ----------------------------------------------------------------------

    # The donor's dashboard loads the CLI with spec_from_loader +
    # module_from_spec + exec_module and puts `scripts/` on sys.path first; this
    # tree still calls the DEPRECATED SourceFileLoader.load_module(). The donor
    # is simply better here, and this is a genuine not-yet-adopted capability
    # rather than a decision. It is not fixed in task 30 because the dashboard
    # is task 69's subject -- that task decomposes it into a package and serves
    # the union of both trees' routes, and this loader rides in with it. Floored
    # at today's length so it cannot rot in the meantime.
    "get_cli": {
        "adjudication": "deferred",
        "floor_chars": 473,
        "follow_up": "task 69 (decompose the dashboard, serve the union)",
        "rationale": "The donor dashboard's importlib loader (spec_from_loader "
                     "+ module_from_spec + exec_module, with scripts/ on "
                     "sys.path) supersedes this tree's deprecated "
                     "loader.load_module(); the dashboard is not adopted until "
                     "task 69.",
    },
}

# Pinned by the suite. Growing this set requires editing BOTH this table and the
# test that asserts it, which is the point: a deferral must be argued for, never
# accumulated.
DEFERRED_NAMES = frozenset(
    name for name, row in ADJUDICATIONS.items()
    if row["adjudication"] == "deferred")
