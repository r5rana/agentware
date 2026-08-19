"""Shared header for the `test_plan_lint` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import glob
import json
import os
import re
import tempfile
import unittest
try:
    from tests._fixtures import run_cli as _raw_run_cli, load_cli, REPO_ROOT
except ImportError:  # when run via `discover -s tests`, tests/ is on sys.path
    from _fixtures import run_cli as _raw_run_cli, load_cli, REPO_ROOT
GOOD_PLAN = """\
# Plan — sample feature

> A throwaway plan used only by the linter tests.

## Target packages

- /tmp/test-target-dir

## Tasks

- ⬜ **1** Implement the first thing deterministically.
  *Verify:* `python3 -m unittest tests.test_sample` passes.

- ⬜ **2** Update the knowledge base after building (`R-KB-*`).
  Run `scripts/agentware learn` then `scripts/agentware index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **3** [e2e] Run the end-to-end regression against the real loop.
  *Verify:* `./agentware.sh sample` aborts/passes as expected.

## Acceptance criteria

- [ ] The sample feature works end to end.

<promise>SAMPLE_FEATURE_COMPLETE</promise>
"""
BAD_R3_CHECKBOXES = """\
# Plan — bad markers

## Tasks

- [ ] **T1** Implement the first thing.
  *Verify:* `python3 -m unittest` passes.

- [ ] **T2** Update the knowledge base (`R-KB`); run `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- [ ] **T3** [e2e] Run the end-to-end regression.
  *Verify:* loop run passes.

## Acceptance criteria

- [ ] Works.

<promise>BAD_MARKERS_COMPLETE</promise>
"""
BAD_R2_MISSING_SECTION = """\
# Plan — missing section

## Tasks

- ⬜ **1** Implement the thing; update KB via `scripts/agentware learn`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

<promise>MISSING_SECTION_COMPLETE</promise>
"""
BAD_R8_NO_PROMISE = """\
# Plan — no promise

## Tasks

- ⬜ **1** Implement the thing; update KB via `index validate` and `learn`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.
"""
BAD_R4_NUMBERING = """\
# Plan — bad numbering

## Tasks

- ⬜ **1** Implement the thing; update KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **1** Duplicate number one.
  *Verify:* something is checked.

- ⬜ **3** [e2e] Gap to three; end-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>BAD_NUMBERING_COMPLETE</promise>
"""
BAD_R5_NO_VERIFY = """\
# Plan — missing verify

## Tasks

- ⬜ **1** Implement the thing; update KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** This task has no verify line at all.

- ⬜ **3** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>MISSING_VERIFY_COMPLETE</promise>
"""
BAD_R6_NO_E2E = """\
# Plan — no e2e task

## Tasks

- ⬜ **1** Implement the thing; update KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** Do another thing.
  *Verify:* a unit test passes.

## Acceptance criteria

- [ ] Works.

<promise>NO_E2E_COMPLETE</promise>
"""
BAD_R7_NO_KB = """\
# Plan — no kb task

## Tasks

- ⬜ **1** Implement the thing.
  *Verify:* a unit test passes.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>NO_KB_COMPLETE</promise>
"""
GOOD_ALL_DONE = """\
# Plan — all done

## Target packages

- /tmp/test-target-dir

## Tasks

- ✅ **1** Implemented the thing; updated KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ✅ **2** [e2e] End-to-end regression done.
  *Verify:* loop passed.

## Acceptance criteria

- [ ] Works.

<promise>ALL_DONE_COMPLETE</promise>
"""
BAD_R9_BLOCKING = """\
# Plan — non-autonomous

## Target packages

- /tmp/test-target-dir

## Tasks

- ⬜ **1** Implement the thing; update KB via `learn` + `index validate`.
  STOP and ask the operator which approach to take before proceeding.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>NON_AUTONOMOUS_COMPLETE</promise>
"""
GOOD_R9_CARVEOUT = """\
# Plan — autonomous with carve-out

> Author: operator

## Target packages

- /tmp/test-target-dir

## Tasks

- ⬜ **1** Obtain self-extension confirmation (`R-PKG-03`).
  STOP and ask the operator before editing package files; the operator
  authoring + launching this plan is the explicit `R-PKG-03` confirmation, so
  proceed deterministically on that pre-decided pass rule. Update KB via
  `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>CARVEOUT_COMPLETE</promise>
"""
GOOD_PHASED = """\
# Plan — phased sample

> A phased plan used to prove the linter accepts `**N.M**` markers.

## Target packages

- /tmp/test-target-dir

## Tasks

### Phase 1: Foundation

- ⬜ **1.1** Implement the first thing deterministically.
  *Verify:* `python3 -m unittest tests.test_sample` passes.

- ⬜ **1.2** Update the knowledge base after building (`R-KB-*`).
  Run `scripts/agentware learn` then `scripts/agentware index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

### Phase 2: Verification

- ⬜ **2.1** [e2e] Run the end-to-end regression against the real loop.
  *Verify:* `./agentware.sh sample` aborts/passes as expected.

## Acceptance criteria

- [ ] The phased feature works end to end.

<promise>PHASED_FEATURE_COMPLETE</promise>
"""
GOOD_PROSE_BULLETS = """\
# Plan — prose bullets inside tasks

## Target packages

- /tmp/test-target-dir

## Tasks

- ⬜ **1** Implement the thing; update KB via `learn` + `index validate`.
  - **Note:** this sub-bullet is prose, not a task marker.
  - **Foundation first:** another prose bullet that must not trip R3.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>PROSE_BULLETS_COMPLETE</promise>
"""
BAD_R3_LETTERED = """\
# Plan — lettered task ids

## Tasks

- **T1** Lettered id instead of a number; update KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- **T2** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>LETTERED_IDS_COMPLETE</promise>
"""
BAD_R4_PHASED_GAP = """\
# Plan — bad phased numbering

## Tasks

### Phase 1

- ⬜ **1.1** First; update KB via `learn` + `index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **1.3** Gap in the minors (no 1.2).
  *Verify:* something is checked.

- ⬜ **2.1** [e2e] End-to-end regression.
  *Verify:* loop passes.

## Acceptance criteria

- [ ] Works.

<promise>BAD_PHASED_COMPLETE</promise>
"""
if __name__ == "__main__":
    unittest.main()
