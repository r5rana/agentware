"""agentware_lib.features.configcmd.cfgroutes — the flag->handler ORDER table.

Owns: CONFIG_ROUTES — the ORDER-BEARING (predicate, handler) table the `config`
      verb dispatches on, and NOTHING else. The pre-split cmd_config was a flat
      chain of ~30 `if <flag> is not None: ...; return N` guard clauses, so the
      ORDER of those clauses WAS a behavioural contract (which flag wins when two
      are passed together). This module is that contract, made explicit and
      reviewable in one place.
Does NOT own: any handler BODY — each lives in its settings-family sibling
      (cfgidentity, cfgorg, cfgkbgit, cfgretrieval, cfgdream, cfgphases); the
      terminal resolved-config VIEW -> agentware_lib.features.configcmd.cfgshow;
      the walk itself + the argparse registrar ->
      agentware_lib.features.configcmd.cfgmain.
Layer deps: features.configcmd.cfgroutes -> its own package siblings ONLY (no
      core/retrieval/ops edge of its own; the handlers carry theirs).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Each row is ``(predicate, handler)``: ``predicate(args)`` is True when that branch
should fire, ``handler(args, as_json)`` returns the process exit code. The walker
takes the FIRST row whose predicate is True — identical to the first guard clause
that matched in the pre-split chain, in the SAME order.

Two rows are GROUP rows rather than one row per flag, because the pre-split code
handled them as one branch and the grouping is behavioural, not cosmetic:

  * the org row (cfgorg._org_triggered) — several org flags may be passed
    TOGETHER and are applied in a fixed order into ONE result payload;
  * the per-phase row (cfgphases._phase_triggered) — one generic nested loop
    covered all 18 --set-<phase>-<axis> / --<phase>-<axis>-only flags.

``cfgshow._show_config`` is DELIBERATELY ABSENT from this table. It is the
FALLTHROUGH — the "no flag matched, show the resolved config" default — and it
lives in the walker (cfgmain.cmd_config) so the table stays a pure flag->handler
map with no always-true row that a reordering could accidentally hoist above a
real branch.
"""

from .cfgdream import (
    _cli_only,
    _dream_max_runtime_only,
    _dream_only,
    _dream_schedule_only,
    _set_cli,
    _set_dream,
    _set_dream_max_runtime,
    _set_dream_schedule,
)
from .cfgidentity import (
    _record_onboarding,
    _set_knowledge_dir,
    _set_skill_propose_threshold,
    _set_user_handle,
    _skill_propose_threshold_only,
    _user_handle_only,
)
from .cfgkbgit import (
    _kb_autocommit_only,
    _kb_push_only,
    _set_autocommit,
    _set_push,
)
from .cfgsettings import (
    _checkup_only,
    _install_guards,
    _set_checkup,
    _set_skill_promote,
    _set_skill_promote_cap,
    _set_skill_promote_threshold,
    _set_updater,
    _skill_promote_only,
    _skill_promote_threshold_triggered,
    _uninstall_guards,
    _updater_only,
)
from .cfgorg import _handle_org, _kb_mode_only, _org_triggered, _set_kb_mode
from .cfgphases import _handle_phase, _phase_triggered
from .cfgretrieval import (
    _embed_model_only,
    _embedder_only,
    _retrieval_mode_only,
    _set_embed_model,
    _set_embedder,
    _set_mode,
    _set_retrieval_mode,
)


def _opt(dest):
    """Predicate for a VALUE-taking setter flag: fires when it is not None."""
    return lambda args: getattr(args, dest, None) is not None


def _flag(dest):
    """Predicate for a store_true reader flag: fires when it is truthy."""
    return lambda args: bool(getattr(args, dest, False))


# ORDER IS THE CONTRACT — this is the pre-split guard-clause order, top to
# bottom. --set-knowledge-dir stays FIRST (ahead of the org group), because the
# direct AGENTWARE_KNOWLEDGE_DIR pointer WINS in the resolver; the readers all
# sit BELOW the setters, so `--set-x --x-only` in one call still performs the
# write. Do not reorder to "tidy" the groups: any move is a behaviour change.
CONFIG_ROUTES = (
    (_opt("set_knowledge_dir"), _set_knowledge_dir),
    (_org_triggered, _handle_org),
    (_opt("set_autocommit"), _set_autocommit),
    (_opt("set_push"), _set_push),
    (_opt("set_mode"), _set_mode),
    (_opt("set_retrieval"), _set_retrieval_mode),
    (_opt("set_embedder"), _set_embedder),
    (_opt("set_embed_model"), _set_embed_model),
    (_flag("embedder_only"), _embedder_only),
    (_flag("embed_model_only"), _embed_model_only),
    (_opt("set_kb_mode"), _set_kb_mode),
    (_opt("set_cli"), _set_cli),
    (_phase_triggered, _handle_phase),
    (_opt("set_dream"), _set_dream),
    (_opt("set_dream_schedule"), _set_dream_schedule),
    (_opt("set_dream_max_runtime"), _set_dream_max_runtime),
    (_opt("set_skill_propose_threshold"), _set_skill_propose_threshold),
    (_opt("set_user_handle"), _set_user_handle),
    (_flag("kb_mode_only"), _kb_mode_only),
    (_flag("cli_only"), _cli_only),
    (_flag("dream_only"), _dream_only),
    (_flag("dream_schedule_only"), _dream_schedule_only),
    (_flag("dream_max_runtime_only"), _dream_max_runtime_only),
    (_flag("skill_propose_threshold_only"), _skill_propose_threshold_only),
    (_flag("user_handle_only"), _user_handle_only),
    (_flag("record_onboarding"), _record_onboarding),
    (_flag("retrieval_mode_only"), _retrieval_mode_only),
    (_flag("kb_autocommit_only"), _kb_autocommit_only),
    (_flag("kb_push_only"), _kb_push_only),
    # --- task 54: the flags cfgports registered and nothing routed ------
    # Setters first, then the readers, so `--set-x --x-only` in one call
    # still performs the write and then reports the NEW value — the same
    # ordering contract the rest of this table keeps.
    (_opt("set_updater"), _set_updater),
    (_opt("set_checkup"), _set_checkup),
    (_opt("set_skill_promote"), _set_skill_promote),
    (_opt("set_skill_promote_cap"), _set_skill_promote_cap),
    (_skill_promote_threshold_triggered, _set_skill_promote_threshold),
    (_flag("install_git_guards"), _install_guards),
    (_flag("uninstall_git_guards"), _uninstall_guards),
    (_flag("updater_only"), _updater_only),
    (_flag("checkup_only"), _checkup_only),
    (_flag("skill_promote_only"), _skill_promote_only),
)


_EXPORTS = ["CONFIG_ROUTES"]

__all__ = list(_EXPORTS)
