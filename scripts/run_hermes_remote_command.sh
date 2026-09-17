#!/usr/bin/env bash
set -euo pipefail

# Use this file as the ForceCommand/authorized_keys command for the dedicated
# Hermes key. Only the exact automation commands below are accepted.
case "${SSH_ORIGINAL_COMMAND:-}" in
  source-refresh)
    exec /home/lxc/chip-recommend/run_source_refresh.sh
    ;;
  source-auto-apply)
    exec /home/lxc/chip-recommend/run_source_auto_apply.sh
    ;;
  data-agent)
    exec /home/lxc/chip-recommend/run_data_agent.sh
    ;;
  data-agent-claim)
    exec /home/lxc/chip-recommend/run_data_agent_claim.sh
    ;;
  data-agent-snapshot)
    exec /home/lxc/chip-recommend/run_data_agent_snapshot.sh
    ;;
  data-agent-finish)
    exec /home/lxc/chip-recommend/run_data_agent_finish.sh
    ;;
  candidate-publish)
    exec /home/lxc/chip-recommend/run_candidate_publish.sh
    ;;
  *)
    echo "Hermes command rejected" >&2
    exit 126
    ;;
esac
