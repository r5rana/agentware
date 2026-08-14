#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/retrieval.sh — the RETRIEVAL-QUALITY gate (donor capability, plan
# task 33).
#
# A NEW lib for the same reason as orglint.sh: the re-injected wiring must not land
# in the already-large hooks lib.
#
# Runs the own-gold recall regression check after the main phase. `eval --gate`
# scores the current corpus against `benchmarks/recall-gold.json` and compares it to
# the best prior ledger row, failing on a regression beyond tolerance. This is the
# only gate in the loop that can see the failure mode where a run's NEW entries are
# individually valid — index validate passes, steering lint passes — but collectively
# confuse retrieval, so an existing query stops finding the entry it used to. Nothing
# else in the post-hook chain reads ranking at all.
#
# ---- TWO NO-OPS ----
#
#  1. KILL SWITCH. AGENTWARE_DISABLE_RECALL_GATE skips the gate, logged, never
#     silent — matching the AGENTWARE_DISABLE_RELEASE_GATE / _TARGET_GATE posture.
#
#  2. NO GOLD SET. A fresh or empty knowledge base has no recall-gold.json, so there
#     is no baseline to regress against and the gate returns before spawning a
#     subprocess. This keeps a first run, and any KB that never opted into the gold
#     corpus, byte-identical to today.
#
# Sourced by agentware.sh before hooks.sh (which calls it). Function definitions
# only, so sourcing standalone under `set -e` exits 0.

# run_retrieval_quality_gate — 0 to proceed; exits 1 on a confirmed regression.
# Reads $KDIR, which run_post_hooks has already re-resolved live.
run_retrieval_quality_gate() {
  [[ -x scripts/agentware ]] || return 0

  if [[ -n "${AGENTWARE_DISABLE_RECALL_GATE:-}" ]]; then
    log "[post-hook] AGENTWARE_DISABLE_RECALL_GATE set — skipping the retrieval-quality gate."
    return 0
  fi

  [[ -n "${KDIR:-}" ]] || return 0

  local _gold_path="$KDIR/benchmarks/recall-gold.json"
  [[ -f "$_gold_path" ]] || return 0

  log "[post-hook] scripts/agentware eval --strategy bm25+acr --gate (retrieval quality)"
  if ! scripts/agentware eval --strategy bm25+acr --gate; then
    echo "Error: [post-hook] retrieval quality gate FAILED — Recall@5 regressed."
    echo "New/modified entries may have created confusion. Fix entry summaries/tags"
    echo "to differentiate, or set AGENTWARE_DISABLE_RECALL_GATE=1 to bypass."
    exit 1
  fi
}
