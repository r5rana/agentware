"""agentware_lib.features.dreamsteps -- the dream cycle's maintenance STEP family + step registry (layer: features).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its fifteen domain submodules so every pre-split call site — which reaches the
module under an alias (`import agentware_lib.features.dreamsteps as dreamsteps`)
and reads `dreamsteps.DREAM_STEP_FUNCS` — keeps resolving unchanged, and so every
existing monkeypatch target (`<consumer>.dreamsteps.<name>`) keeps working.

Owns: nothing but the re-export surface. The union of the submodules' `_EXPORTS`
is a strict SUPERSET of the pre-split module's `_EXPORTS` — no name dropped; the
additions are the named helpers the oversized step bodies decomposed into.
Does NOT own: any step handler, primitive, or the registry — each lives in its
own submodule:

  * `dreamcapture`      — the perf clock, capture harness, file fingerprint and
                          the detect-report artifact path (a stdlib-only leaf).
  * `dreamgit`          — the hard-timeout git runner + `DREAM_PULL_TIMEOUT` and
                          the post-pull conflict check.
  * `stepkbpull`        — step "1", the KNOWLEDGE-BASE pull (step "0", the
                          package-repo pull, is RETIRED).
  * `stephealthgate`    — step "2", the convergence battery + its nine checks.
  * `stepindexrebuild`  — step "a", the derived-cache rebuild.
  * `stepbenchredact`   — step "b", benchmark redaction.
  * `stepaudit`         — step "c", the audit sweep.
  * `stepevalrecord`    — step "d", the retrieval eval record.
  * `stepdetectreport`  — step "e", the detect-report render + write.
  * `stepkbgit`         — step "f", the kb-git commit (+ optional push).
  * `stepstalepropose`  — step "s", the staleness review proposal.
  * `stepskillpropose`  — step "k", the skill-candidate proposal.
  * `steptranscriptmine`— step "m", the offline transcript miner ADAPTER (the
                          miner itself is `ops.sessionparse.minerscan`).
  * `steppreferclassify`— step "g", the prefer-queue drain ADAPTER (the drain
                          itself is `ops.preferclassify`).
  * `registry`          — `DREAM_STEP_FUNCS` + the `--steps` subset parser.

Steps s/k/m/g were absent from the donor's package and are restored here (task
46). Two of them are three-line ADAPTERS over machinery that lives in `ops`: the
handler belongs to the dream cycle, the capability does not, and splitting them
that way is what keeps `registry` importing siblings only and keeps `ops`
ignorant of the cycle that schedules it.

Layer deps: none of its own — it only re-exports its submodules, whose layer
deps are declared in their own docstrings. The package's internal graph is
acyclic: `dreamcapture` is the deepest leaf, every step imports downward or
sideways into it, and `registry` sits at the top importing the twelve handlers.
"""

from agentware_lib.features.dreamsteps.dreamcapture import (
    DREAM_REPORT_REL,
    _dream_capture,
    _dream_file_fp,
    _dream_perf,
)
from agentware_lib.features.dreamsteps.dreamgit import (
    DREAM_PULL_TIMEOUT,
    _dream_pull_post_check,
    _git_timed,
)
from agentware_lib.features.dreamsteps.stepkbpull import (
    _dream_pull_head_sha,
    _dream_pull_offline_reason,
    _dream_pull_ok,
    _dream_pull_resolve_derived,
    _dream_pull_skip_reason,
    _dream_step_kb_pull,
)
from agentware_lib.features.dreamsteps.stephealthgate import (
    _dream_health_gate_checks,
    _dream_hg_check_config,
    _dream_hg_check_cron,
    _dream_hg_check_gitignore,
    _dream_hg_check_identity,
    _dream_hg_check_kb_structure,
    _dream_hg_check_kdir_drift,
    _dream_hg_check_rebase,
    _dream_hg_check_sentinel,
    _dream_hg_check_templates,
    _dream_step_health_gate,
)
from agentware_lib.features.dreamsteps.stepindexrebuild import (
    _dream_step_index_rebuild,
)
from agentware_lib.features.dreamsteps.stepbenchredact import (
    _dream_step_bench_redact,
)
from agentware_lib.features.dreamsteps.stepaudit import (
    _dream_audit_read_capture,
    _dream_audit_unittest_counts,
    _dream_step_audit,
)
from agentware_lib.features.dreamsteps.stepevalrecord import (
    _dream_step_eval_record,
)
from agentware_lib.features.dreamsteps.stepdetectreport import (
    _dream_render_conflicts_section,
    _dream_render_detect_report,
    _dream_render_drift_section,
    _dream_render_markers_section,
    _dream_render_plan_only_section,
    _dream_render_stale_section,
    _dream_step_detect_report,
    _dream_write_detect_report,
)
from agentware_lib.features.dreamsteps.stepkbgit import _dream_step_kb_git
from agentware_lib.features.dreamsteps.stepstalepropose import (
    _dream_step_stale_propose,
)
from agentware_lib.features.dreamsteps.stepskillpropose import (
    _dream_step_skill_propose,
)
from agentware_lib.features.dreamsteps.steptranscriptmine import (
    _dream_step_transcript_mine,
)
from agentware_lib.features.dreamsteps.steppreferclassify import (
    _dream_step_prefer_classify,
)
from agentware_lib.features.dreamsteps.registry import (
    DREAM_STEP_FUNCS,
    _dream_parse_steps,
)
