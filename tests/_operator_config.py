#!/usr/bin/env python3
"""Machine-level operator-config isolation for tests that call IN-PROCESS.

Feature 260813-modular-core-adoption, task 89.

WHY THIS EXISTS
---------------
`tests/_fixtures.run_cli` already argues this case for the CLI path, and states
the principle: *"one seam covering every current and future CLI test instead of a
per-class setUp each new test must remember (two classes had already missed it)"*.
It neutralises `AGENTWARE_DREAM` and the nested-unittest guard for the duration
of a `run_cli` call, so a synthetic-KB assertion depends on the KB under test
rather than on the operator's laptop.

A test that calls a probe DIRECTLY — no subprocess, no `run_cli` — gets none of
that, and several probes read machine-level settings that no synthetic KB writes:
`resolve_kb_mode()` and `resolve_user_handle()` both fall through env to
`~/.agentware/config.env`. That is not a hypothetical leak. It is the second half
of the defect plan task 89 owns:

    `tests.unit.ops.test_checkup_aggregate.FlywheelTests` asserted that a BARE
    knowledge base proposes no work, and called `_checkup_aggregate` directly. On
    an operator whose real config carries `kb_mode: team`, the multi-tenancy
    signal fires from that flag alone — correctly, since team mode IS multi-
    tenancy — so the assertion failed for a setting the knowledge base under test
    knows nothing about. It stayed red long enough to be recorded in the
    known-failure ledger, where its diagnosis named only the OTHER cause (the
    placeholder handle counted as a second person). Two causes, one symptom, and
    the machine-dependent one was invisible on any machine configured solo.

`tests.unit.ops.test_checkup_triggers` had hand-rolled the same isolation in its
own `setUp` and was therefore green on the same machine and the same commit. A
seam that one sibling remembers and the other forgets is the shape this module
removes: both now call the same function.

Stdlib only, no network, no writes anywhere outside the caller's own temp dir.
"""

import os

#: The MACHINE-level operator settings an in-process probe reads, in the order a
#: reader meets them: the env var wins, then `~/.agentware/config.env`. The org
#: pair is here because R-ORG-04's guarantee is about an ABSENT key, so a leaked
#: `AGENTWARE_ORG_ROOT` silently moves a flat-install assertion into org mode.
ISOLATED_OPERATOR_ENV = ("AGENTWARE_KB_MODE", "AGENTWARE_USER_HANDLE",
                         "AGENTWARE_ORG_ROOT", "AGENTWARE_TEAM")


def isolate_operator_config(case, mod, home_dir):
    """Point `mod` at an EMPTY config.env under `home_dir`; clear the env keys.

    `case` is the TestCase (its `addCleanup` restores everything, so a caller
    writes ONE line in `setUp`), `mod` the CLI module from `load_cli()`, and
    `home_dir` any directory the test already owns and cleans up.

    Rebinding `HOME_CONFIG`/`CONFIG_PATHS` on the CLI facade is sufficient
    because `_fixtures._ConfigMirrorModule` mirrors both onto every lib module
    that reads them — that mirror exists precisely so ~180 tests can keep
    isolating state through the facade after the port moved the readers.

    The config file is NOT created: an absent path reads as an empty config,
    which is the state under test. Returns the config path for a caller that
    wants to write a specific setting into the isolated config instead.
    """
    previous = (mod.HOME_CONFIG, mod.CONFIG_PATHS)
    saved = {k: os.environ.get(k) for k in ISOLATED_OPERATOR_ENV}

    def restore():
        mod.HOME_CONFIG, mod.CONFIG_PATHS = previous
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    case.addCleanup(restore)
    path = os.path.join(home_dir, ".agentware", "config.env")
    mod.HOME_CONFIG = path
    mod.CONFIG_PATHS = (path,)
    for key in ISOLATED_OPERATOR_ENV:
        os.environ.pop(key, None)
    return path
