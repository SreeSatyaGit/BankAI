#!/usr/bin/env bash
# End-to-end demo. Regenerates artifacts + evidence for every scenario.
# Usage: bash scripts/run_demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
CUA="python -m cua.cli"

hr(){ printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

hr "DISCOVER: read_savings_balance (LLM-driven, MockPlanner)"
$CUA discover --preset balance --member-id 12345
$CUA approve  --cap read_savings_balance

hr "DISCOVER: open_subaccount"
$CUA discover --preset subaccount --member-id 12345 --account-type Savings
$CUA approve  --cap open_subaccount

hr "REPLAY: happy path -> SUCCESS (returns balance)"
$CUA replay --preset balance --member-id 12345

hr "REPLAY: unknown member -> BUSINESS_OUTCOME (not a failure)"
$CUA replay --preset balance --member-id 00000

hr "REPLAY: not authorized -> BUSINESS_OUTCOME"
$CUA replay --preset balance --member-id 99999

hr "REPLAY: privacy interstitial -> auto-recovered -> SUCCESS"
$CUA replay --preset balance --member-id 55555

hr "REPLAY: transient 503 -> retry -> SUCCESS"
$CUA replay --preset balance --member-id 60503

hr "REPLAY: unexpected state, NO operator -> FAILURE (typed, with evidence)"
$CUA replay --preset balance --member-id 77777 || true

hr "REPLAY: unexpected state, WITH operator -> human takes over live session -> SUCCESS"
$CUA replay --preset balance --member-id 77777 --operator

hr "REPLAY: risky/irreversible step blocked in unattended mode -> FAILURE"
$CUA replay --preset subaccount --member-id 12345 --account-type Savings || true

hr "REPLAY: risky step confirmed -> SUCCESS (returns reference)"
$CUA replay --preset subaccount --member-id 12345 --account-type Savings --confirm-risky

hr "REPLAY: required field rejected -> BUSINESS_OUTCOME (validation)"
$CUA replay --preset subaccount --member-id 12345 --account-type "" --confirm-risky

hr "ARTIFACTS ON DISK"
$CUA list
