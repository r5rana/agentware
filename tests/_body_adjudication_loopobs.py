"""ADJUDICATIONS — task 99 wave 4, the LOOP-OBSERVABILITY family (5 rows).

The sibling half of `tests/_body_adjudication.py`, split out the way task 91
split `_body_adjudication_shimretirement` off it: at the seam a SHARED
adjudication reason already names, not at a line count. The host file's own debt
row says its length measures the adjudicated-divergence COUNT and that its
legitimate splits are the ones a shared reason names — and every row here has the
same one: they are the five bodies of ONE ceiling-paydown wave, split together,
proven together by ONE byte-identical two-boundary capture, and pinned together
by the five owning unit files that landed in the same commit.

Nothing here is forgiven and nothing changed in the move: same names, same
floors, same absorbers, same rationales. `_body_adjudication` merges this dict
into `ADJUDICATIONS`, which stays the ONE name every consumer imports.
"""


# --------------------------------------------------------------------------
# DECOMPOSED -- task 99 wave 4: the LOOP-OBSERVABILITY family's ceiling rows
#
# Six bodies, five modules, one wave. Each was a declared `paydown` row in
# `tests/fixtures/ceiling-debt.json` naming its own seam, and each split is a
# PURE refactor proven at two boundaries: a 175 KB capture across the CLI
# (`metrics` in both formats, four filters, a price table, the retrieval report)
# and the FUNCTION boundary (the dashboard derivations the CLI cannot reach, plus
# every degradation path -- an unreadable live stream, a `content` that is not a
# list, a `True` masquerading as an iteration number, a terminal with no
# iteration count, a foreign session's dominant feature) is BYTE-IDENTICAL before
# and after, with the capture's own determinism pinned first by a before->before
# re-run.
#
# What makes each rationale a CHECKABLE claim rather than a description: every
# one of these modules gained its OWN unit file in the same commit -- five of
# them had none at all, which is why a 43-branch fold and a 33-branch reader
# could sit unowned -- and the named test classes below are where the split's
# contracts are pinned behaviourally, not by length.
# --------------------------------------------------------------------------

_LOOPTRACE = "scripts/agentware_lib/ops/looptrace.py"
_LOOPANALYTICS = "scripts/agentware_lib/ops/loopanalytics.py"
_LOOPHEALTH = "scripts/agentware_lib/ops/loophealth.py"
_AGENTACTIVITY = "scripts/agentware_lib/ops/agentactivity.py"

#: The step body's receivers. The first two are the ONE main.jsonl decode: it was
#: two near-verbatim copies with the same eight guards in the same order -- the
#: token-share reader and this body's fallback arm -- and they are claimed HERE
#: because the shares reader is lib-only and so carries no divergence row of its
#: own to claim them from.
_STEP_RECEIVERS = [_LOOPTRACE + "::" + n for n in (
    "_iter_main_jsonl_objects",       # open + line decode + the mapping guard
    "_iter_main_assistant_tooluse",   # assistant/content/tool_use + the ts norm
    "_read_live_rows",                # the PRIMARY source, strict about ACCESS
    "_steps_from_live_rows",          # live rows -> steps (result + status)
    "_steps_from_main_transcript",    # the FALLBACK: called, never how it went
    "_attach_step_tokens",            # index AND name must agree, else None
    "_attach_step_durations")]        # next.ts - this.ts, negatives discarded

_TRACE_RECEIVERS = [_LOOPTRACE + "::" + n for n in (
    "_resolve_group_feature",         # session scope recovers its own feature
    "_collect_session_steps",         # stamps the session id onto every step
    "_group_steps_by_iteration")]     # buckets, incl. the unknown bucket LAST

_ANALYTICS_RECEIVERS = [_LOOPANALYTICS + "::" + n for n in (
    "_fold_loop_events",              # the one pass over the emission channel
    "_fold_main_event",               # last-wins remaining, summed delta
    "_iteration_metrics",             # terminal-first, fold-second precedence
    "_self_heal_count",               # MAX over running totals, never the sum
    "_terminal_latency_s")]           # both ends must exist and parse

_DUP_RECEIVERS = [_LOOPHEALTH + "::" + n for n in (
    "_dup_scope_sessions",            # DOMINANT-feature attribution
    "_dup_signature_counts",          # (session, iteration, tool, args)
    "_dup_worst_offender",            # strictly-greater: the earliest tie wins
    "_dup_status")]                   # the three ordered tiers

_ACTIVITY_RECEIVERS = [_AGENTACTIVITY + "::" + n for n in (
    "_select_agent_sessions",         # resolved identity, noise dropped, COPIED
    "_active_marker",                 # newest session only, `end` then `start`
    "_agent_status",                  # active | complete | incomplete | ended
    "_activity_breakdowns")]          # per-day and per-feature rollups

LOOP_OBSERVABILITY_ROWS = {
    "_trace_steps_for_session_uncached": {
        "adjudication": "decomposed",
        "host": _LOOPTRACE,
        "floor_chars": 792,
        "absorbs": list(_STEP_RECEIVERS),
        "rationale": "Five sequential stages fused into one 33-branch body, with "
                     "the result that its ONE genuine decision -- live.jsonl is "
                     "primary, main.jsonl is the fallback -- was invisible among "
                     "them. Each stage owns a failure domain the others do not: "
                     "`_read_live_rows` is tolerant of CONTENT and strict about "
                     "ACCESS (an OSError mid-walk discards the PARTIAL read, "
                     "because a prefix is indistinguishable from a short session "
                     "and would suppress the fallback with half a stream); "
                     "`_steps_from_main_transcript` produces steps with no result "
                     "and a neutral status, since the transcript records what was "
                     "CALLED and nothing about how it went; `_attach_step_tokens` "
                     "credits a share only when index AND name agree, so drift "
                     "between two recordings of one run yields None rather than a "
                     "plausible number from the wrong call; `_attach_step_durations` "
                     "discards a NEGATIVE delta, because two unsynchronised clocks "
                     "can order two steps backwards and that is a measurement "
                     "error, not a fast call. The fallback arm's main.jsonl decode "
                     "was a near-verbatim COPY of the token-share reader's, same "
                     "eight guards in the same order, and is now the one lazy "
                     "generator both use: `_iter_main_jsonl_objects` owns the LINE "
                     "(open, blank, decode, mapping guard) and "
                     "`_iter_main_assistant_tooluse` the TURN (assistant "
                     "discriminator, content-is-a-list, tool_use item filter, "
                     "timestamp normalisation). A turn with no tool_use item is "
                     "not yielded, which is the SAME behaviour both callers "
                     "already had, written twice. The token-share read is deliberately "
                     "kept FIRST, ahead of the live read, because that order is "
                     "what the shared read cache sees. Parent 33 -> 1. Pinned by "
                     "`LiveRowsTest`, `MainFallbackStepsTest`, "
                     "`StepTokenAlignmentTest`, `StepDurationTest` and "
                     "`SessionStepsTest` in `tests/unit/ops/test_looptrace.py`.",
    },
    "derive_trace": {
        "adjudication": "decomposed",
        "host": _LOOPTRACE,
        "floor_chars": 1790,
        "absorbs": list(_TRACE_RECEIVERS),
        "rationale": "A straight pipeline whose three stages each carry a rule "
                     "worth stating alone. `_resolve_group_feature` is the only "
                     "way a SESSION-scoped trace can be bucketed into iterations "
                     "at all -- it recovers the feature from the session's own "
                     "parsed row -- and it leaves the feature unresolved rather "
                     "than guessing when the row will not parse. "
                     "`_collect_session_steps` stamps the originating session onto "
                     "every step BEFORE the merge re-sorts them by timestamp, "
                     "after which the origin is unrecoverable; the dup-check "
                     "downstream scopes its signatures by that stamp precisely so "
                     "a long-lived feature is not read as a dead loop. "
                     "`_group_steps_by_iteration` creates a bucket on FIRST "
                     "reference by EITHER stream, so an iteration that produced "
                     "only a transition still appears, and files a `True` "
                     "iteration under the unknown bucket rather than letting it "
                     "fabricate iteration 1. Parent 19 -> 8. Pinned by "
                     "`GroupFeatureTest`, `CollectStepsTest`, `GroupingTest` and "
                     "`DeriveTraceTest` in `tests/unit/ops/test_looptrace.py`.",
    },
    "_loop_analytics_for_feature": {
        "adjudication": "decomposed",
        "host": _LOOPANALYTICS,
        "floor_chars": 1831,
        "absorbs": list(_ANALYTICS_RECEIVERS),
        "rationale": "The widest body left in the loop domain (43 branches / 128 "
                     "lines) and the one whose decisions were least visible: it "
                     "was reached only through `derive_loop_analytics`, whose "
                     "callers read six of its sixteen output keys. The split "
                     "follows the obligations. `_fold_loop_events` is the ONE pass "
                     "over the emission channel, and its every isinstance guard is "
                     "a coercion of a line written by a shell script -- a "
                     "non-numeric `phase_wall_s` contributes zero rather than "
                     "raising. `_fold_main_event` carries the rule the three "
                     "overlapping phase counters make load-bearing: only "
                     "main-phase events reach the burndown, `tasks_remaining` is "
                     "LAST-WINS (a repeated observation of one quantity) while "
                     "`tasks_done_delta` is SUMMED (an increment), and an event "
                     "with no usable iteration is dropped rather than filed under "
                     "a default. `_iteration_metrics` holds the terminal-first / "
                     "fold-second precedence every number here needs, because a "
                     "killed run has no terminal event, plus the non-positive "
                     "rejection without which a zero `iterations_used` divides by "
                     "zero. `_self_heal_count` recovers the count as the MAX and "
                     "not the sum -- each event reports the RUNNING TOTAL, so "
                     "summing multiplies one heal by the iterations that saw it -- "
                     "and keys its fallback on `is None` so a terminal ZERO still "
                     "wins. Parent 43/128 -> 5/47. Pinned by "
                     "`FoldMainEventTest`, `FoldLoopEventsTest`, "
                     "`IterationMetricsTest`, `SelfHealTest`, "
                     "`TerminalLatencyTest` and `AnalyticsForFeatureTest` in "
                     "`tests/unit/ops/test_loopanalytics.py`, the module's first "
                     "owning test file.",
    },
    "_loop_health_dup_check": {
        "adjudication": "decomposed",
        "host": _LOOPHEALTH,
        "floor_chars": 1191,
        "absorbs": list(_DUP_RECEIVERS),
        "rationale": "The dead-loop detector (R-FAIL-04), and the one check in "
                     "this module whose false positives were observed in "
                     "production: a broad substring match once folded a single "
                     "chatty session into EVERY feature's count and made 100% of "
                     "runs read `critical`. Both halves of the fix were buried in "
                     "the sixteen branches and are now named. "
                     "`_dup_scope_sessions` keeps the DOMINANT-feature filter, "
                     "including the part that matters most -- an index that "
                     "filters EMPTY stays empty, because re-widening it is exactly "
                     "how the false-critical returned -- while `None` (no index "
                     "supplied) is passed through untouched, since the trace "
                     "resolves its own scope then. `_dup_signature_counts` keeps "
                     "the (session, iteration, tool, args) signature that makes "
                     "`critical` mean \"stuck repeating one call\" rather than "
                     "\"long-lived feature\". `_dup_worst_offender` is "
                     "strictly-greater, so the EARLIEST of any tied offenders "
                     "wins and the report is reproducible across runs. "
                     "`_dup_status` is the three ordered tiers, with the at-risk "
                     "tier still DERIVED from the threshold by the caller so the "
                     "two cannot be configured into disagreeing. Parent 16 -> 4. "
                     "Pinned by `DupSignatureTest`, `DupOffenderTest`, "
                     "`DupStatusTest` and `DupScopeTest` in "
                     "`tests/unit/ops/test_loophealth.py`, whose "
                     "`test_a_foreign_session_cannot_supply_this_features_offender` "
                     "puts the tree's WORST raw repeat count in a FOREIGN session, "
                     "so a filter that did not run is visible in the offender's "
                     "NAME and not only in a count.",
    },
    "derive_agent_activity": {
        "adjudication": "decomposed",
        "host": _AGENTACTIVITY,
        "floor_chars": 2103,
        "absorbs": list(_ACTIVITY_RECEIVERS),
        "rationale": "Decomposed AND MOVED: `ops/derive.py` was at 587 of its "
                     "600-line cap, so the helpers could not land in place, and "
                     "the seam was already there to be read -- every other "
                     "`derive_*` is a pure fold over parsed rows with no notion of "
                     "WHO ran them and no reference instant, while this one "
                     "carries an identity taxonomy AND a wall-clock `now`. It now "
                     "owns `ops/agentactivity.py`, with `ops.derive` importing the "
                     "name back so `derive_agents` and every by-name caller still "
                     "resolve it. `_select_agent_sessions` keeps the two rules "
                     "that decide the whole payload: selection is on the RESOLVED "
                     "identity and never on the passive `stage` label a "
                     "transcript's prose produces (a session that merely discussed "
                     "planning is not a planner), and every selected row is COPIED "
                     "before `_identity` and `cost_usd` are written onto it, "
                     "because the originals are held in the shared parse cache. "
                     "`_active_marker` keeps `end` preferred over `start` so a "
                     "session still being written does not age out mid-run, and "
                     "refuses to call an unparseable stamp active -- an idle agent "
                     "read as running is the failure the window exists to prevent. "
                     "`_activity_breakdowns` keeps the asymmetry between the two "
                     "rollups: a session with no DATE is left out of the day "
                     "series (it is plotted, and an invented bucket would draw) "
                     "while a session with no FEATURE is counted under the "
                     "unattributed key (dropping it would make the per-feature "
                     "costs stop summing to the total). Parent 17 -> 6. Pinned by "
                     "`SelectSessionsTest`, `ActiveMarkerTest`, `AgentStatusTest`, "
                     "`BreakdownsTest` and `DeriveAgentActivityTest` in "
                     "`tests/unit/ops/test_agentactivity.py`, which lands WITH the "
                     "module, and whose no-mutation assertion is the mutation the "
                     "curated parity row `mod:unit:agentactivity` names.",
    },
}
