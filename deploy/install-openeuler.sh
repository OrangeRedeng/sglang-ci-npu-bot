#!/usr/bin/env bash
# Install / update NPU Diffusion CI collector without enabling the timer.
set -Eeuo pipefail
if [[ $(id -u) -ne 0 ]]; then echo 'Run: sudo bash deploy/install-openeuler.sh' >&2; exit 2; fi
SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOT_HOME=/home/sglang-npu-bot
BOT_USER=sglang-ci
id -u "$BOT_USER" >/dev/null 2>&1 || useradd --system --home-dir "$BOT_HOME" --shell /sbin/nologin "$BOT_USER"
# Correct an existing account that was created by the older /var/lib kit.
usermod --home "$BOT_HOME" "$BOT_USER"
install -d -o "$BOT_USER" -g "$BOT_USER" -m 0700 "$BOT_HOME"
install -d -o root -g root -m 0755 "$BOT_HOME/scripts"
install -d -o root -g root -m 0755 "$BOT_HOME/tests"
install -d -o "$BOT_USER" -g "$BOT_USER" -m 0700 "$BOT_HOME/reports"
# Older releases sometimes left this directory owned by root, aborting publish.
install -d -o "$BOT_USER" -g "$BOT_USER" -m 0700 "$BOT_HOME/reports/issue-backups"
install -d -o root -g root -m 0700 /etc/sglang-npu-bot
# New helpers must reach the installed tree; keep explicit CLI modes below.
for source in "$SOURCE_ROOT"/scripts/*.py; do
  install -o root -g root -m 0644 "$source" "$BOT_HOME/scripts/$(basename "$source")"
done
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/npu_diffusion_ci_report.py" "$BOT_HOME/scripts/npu_diffusion_ci_report.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/log_context.py" "$BOT_HOME/scripts/log_context.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/glm_http.py" "$BOT_HOME/scripts/glm_http.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/glm_budget.py" "$BOT_HOME/scripts/glm_budget.py"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/glm-budget-status.py" "$BOT_HOME/scripts/glm-budget-status.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/ci_investigation.py" "$BOT_HOME/scripts/ci_investigation.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/report_editor.py" "$BOT_HOME/scripts/report_editor.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/report_actions.py" "$BOT_HOME/scripts/report_actions.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/analysis_evidence.py" "$BOT_HOME/scripts/analysis_evidence.py"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/refresh_daily_snapshots.py" "$BOT_HOME/scripts/refresh_daily_snapshots.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/research_report.py" "$BOT_HOME/scripts/research_report.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/testcase_failures.py" "$BOT_HOME/scripts/testcase_failures.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/failure_history.py" "$BOT_HOME/scripts/failure_history.py"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/reparse_failure_snapshots.py" "$BOT_HOME/scripts/reparse_failure_snapshots.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/issue_taxonomy.py" "$BOT_HOME/scripts/issue_taxonomy.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/issue_sync.py" "$BOT_HOME/scripts/issue_sync.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/incident_lifecycle.py" "$BOT_HOME/scripts/incident_lifecycle.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/incident_verifier.py" "$BOT_HOME/scripts/incident_verifier.py"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/sync-incident-continuity.py" "$BOT_HOME/scripts/sync-incident-continuity.py"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/sync-incident-continuity.sh" "$BOT_HOME/scripts/sync-incident-continuity.sh"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/pr_forensics.py" "$BOT_HOME/scripts/pr_forensics.py"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/reanalyze_and_publish.py" "$BOT_HOME/scripts/reanalyze_and_publish.py"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/update-existing-issues-with-glm.sh" "$BOT_HOME/scripts/update-existing-issues-with-glm.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/update-existing-issues.sh" "$BOT_HOME/scripts/update-existing-issues.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/reconcile-legacy-issues.py" "$BOT_HOME/scripts/reconcile-legacy-issues.py"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/reconcile-legacy-issues.sh" "$BOT_HOME/scripts/reconcile-legacy-issues.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/reformat_report.py" "$BOT_HOME/scripts/reformat_report.py"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/reanalyze_snapshot.py" "$BOT_HOME/scripts/reanalyze_snapshot.py"
install -d -o root -g root -m 0755 "$BOT_HOME/skills"
install -o root -g root -m 0644 "$SOURCE_ROOT/skills/manifest.json" "$BOT_HOME/skills/manifest.json"
for source in "$SOURCE_ROOT"/skills/*/SKILL.md; do
  name="$(basename "$(dirname "$source")")"
  install -d -o root -g root -m 0755 "$BOT_HOME/skills/$name"
  install -o root -g root -m 0644 "$source" "$BOT_HOME/skills/$name/SKILL.md"
done
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/extract-log-context.py" "$BOT_HOME/scripts/extract-log-context.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/npu_network_check.py" "$BOT_HOME/scripts/npu_network_check.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/scripts/test_job_log_download.py" "$BOT_HOME/scripts/test_job_log_download.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_npu_diffusion_ci_report.py" "$BOT_HOME/tests/test_npu_diffusion_ci_report.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_log_context.py" "$BOT_HOME/tests/test_log_context.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_glm_http.py" "$BOT_HOME/tests/test_glm_http.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_glm_budget.py" "$BOT_HOME/tests/test_glm_budget.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_ci_investigation.py" "$BOT_HOME/tests/test_ci_investigation.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_pr_forensics.py" "$BOT_HOME/tests/test_pr_forensics.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_recovery_publish.py" "$BOT_HOME/tests/test_recovery_publish.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_report_editor.py" "$BOT_HOME/tests/test_report_editor.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_issue_taxonomy.py" "$BOT_HOME/tests/test_issue_taxonomy.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_multi_case_clusters.py" "$BOT_HOME/tests/test_multi_case_clusters.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_v21_regressions.py" "$BOT_HOME/tests/test_v21_regressions.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_v22_research_mode.py" "$BOT_HOME/tests/test_v22_research_mode.py"
# Install every tracked regression test, including new release tests, so the
# deployed self-test suite matches the shipped source-tree test suite.
for test_source in "$SOURCE_ROOT"/tests/test_*.py; do
  install -o root -g root -m 0644 "$test_source" "$BOT_HOME/tests/$(basename "$test_source")"
done
install -d -o root -g root -m 0755 "$BOT_HOME/tests/fixtures"
for fixture_source in "$SOURCE_ROOT"/tests/fixtures/*; do
  install -o root -g root -m 0644 "$fixture_source" "$BOT_HOME/tests/fixtures/$(basename "$fixture_source")"
done
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/__init__.py" "$BOT_HOME/tests/__init__.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_npu_network_check.py" "$BOT_HOME/tests/test_npu_network_check.py"
install -o root -g root -m 0644 "$SOURCE_ROOT/tests/test_nm_dns.py" "$BOT_HOME/tests/test_nm_dns.py"
install -d -o root -g root -m 0755 "$BOT_HOME/deploy"
install -o root -g root -m 0644 "$SOURCE_ROOT/deploy/bot.env.example" "$BOT_HOME/deploy/bot.env.example"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/recover-and-run.sh" "$BOT_HOME/deploy/recover-and-run.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/repair-and-run.sh" "$BOT_HOME/deploy/repair-and-run.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/configure-dns.py" "$BOT_HOME/deploy/configure-dns.py"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/run-smoke-test.sh" "$BOT_HOME/deploy/run-smoke-test.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/run-glm-test.sh" "$BOT_HOME/deploy/run-glm-test.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/self-test.sh" "$BOT_HOME/deploy/self-test.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/apply-research-profile.sh" "$BOT_HOME/deploy/apply-research-profile.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/update-and-run.sh" "$BOT_HOME/deploy/update-and-run.sh"
# Keep the deployed test tree self-contained. v21 regression tests read the
# updater, installer and timer source relative to the installed test package.
# Previously these were present only in /opt: source tests passed, but the
# installed self-test failed with FileNotFoundError and rolled back the update.
install -o root -g root -m 0644 "$SOURCE_ROOT/VERSION" "$BOT_HOME/VERSION"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/install-openeuler.sh" "$BOT_HOME/deploy/install-openeuler.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/deploy/update-from-git.sh" "$BOT_HOME/deploy/update-from-git.sh"
install -d -o root -g root -m 0755 "$BOT_HOME/deploy/systemd"
install -o root -g root -m 0644 "$SOURCE_ROOT/deploy/systemd/npu-diffusion-ci.service" "$BOT_HOME/deploy/systemd/npu-diffusion-ci.service"
install -o root -g root -m 0644 "$SOURCE_ROOT/deploy/systemd/npu-diffusion-ci.timer" "$BOT_HOME/deploy/systemd/npu-diffusion-ci.timer"
install -o root -g root -m 0644 "$SOURCE_ROOT/deploy/systemd/npu-diffusion-ci.service" /etc/systemd/system/
install -o root -g root -m 0644 "$SOURCE_ROOT/deploy/systemd/npu-diffusion-ci.timer" /etc/systemd/system/
if [[ ! -e /etc/sglang-npu-bot/bot.env ]]; then
  install -o root -g root -m 0600 "$SOURCE_ROOT/deploy/bot.env.example" /etc/sglang-npu-bot/bot.env
  echo 'Created /etc/sglang-npu-bot/bot.env; edit the GITHUB_TOKEN before enabling the service.'
else
  echo 'Preserved /etc/sglang-npu-bot/bot.env (existing secrets unchanged).'
  echo 'Check existing bot.env: remove/comment old GITHUB_PUBLIC_READ_PROXY (deprecated; API requests now always direct).'
fi
chmod 0600 /etc/sglang-npu-bot/bot.env
systemctl daemon-reload
echo 'Installed in /home/sglang-npu-bot. Existing timer state preserved.'
echo 'Next: /usr/bin/python3 /home/sglang-npu-bot/scripts/npu_network_check.py --sample-jobs'
echo 'Run offline tests: cd /home/sglang-npu-bot && /usr/bin/python3 -m unittest discover -s tests -p "test_*.py" -v'
echo 'Optional DNS change (NOT applied by installer): sudo python3 /home/sglang-npu-bot/deploy/configure-dns.py --list'
