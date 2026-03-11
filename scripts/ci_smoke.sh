#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "./.venv/bin/python" ]]; then
    PYTHON_BIN="./.venv/bin/python"
  elif [[ -x "./venv/bin/python" ]]; then
    PYTHON_BIN="./venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

"$PYTHON_BIN" -m compileall -q .
docker compose config >/dev/null
"$PYTHON_BIN" scripts/export_local_state.py --help >/dev/null
"$PYTHON_BIN" scripts/import_to_postgres.py --help >/dev/null
"$PYTHON_BIN" scripts/reconcile_accounts_with_sessions.py --help >/dev/null
"$PYTHON_BIN" scripts/runtime_status.py --help >/dev/null
"$PYTHON_BIN" scripts/account_audit_report.py --help >/dev/null
"$PYTHON_BIN" scripts/reconcile_lifecycle.py --help >/dev/null
"$PYTHON_BIN" scripts/reset_user_state.py --help >/dev/null
"$PYTHON_BIN" scripts/send_digest_summary.py --help >/dev/null
"$PYTHON_BIN" scripts/preflight_runtime_env.py --help >/dev/null
"$PYTHON_BIN" scripts/enqueue_packaging_canary.py --help >/dev/null
"$PYTHON_BIN" scripts/enqueue_packaging_phone.py --help >/dev/null
"$PYTHON_BIN" scripts/account_onboarding_runner.py --help >/dev/null
"$PYTHON_BIN" scripts/session_topology_test.py
"$PYTHON_BIN" scripts/readiness_test.py
"$PYTHON_BIN" scripts/account_audit_test.py
"$PYTHON_BIN" scripts/onboarding_flow_test.py
"$PYTHON_BIN" scripts/human_gated_workflow_test.py
"$PYTHON_BIN" scripts/reset_state_test.py
"$PYTHON_BIN" scripts/digest_service_test.py
"$PYTHON_BIN" scripts/policy_replay_test.py
"$PYTHON_BIN" scripts/gate_test.py
if "$PYTHON_BIN" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
then
  "$PYTHON_BIN" scripts/e2e_dry_run.py
else
  echo "skip_e2e_dry_run_python_lt_3_10"
fi

echo "ci_smoke_ok"
