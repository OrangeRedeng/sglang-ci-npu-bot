# SGLang NPU Diffusion CI Bot

A daily check on SGLang diffusion tests running on Huawei Ascend NPUs.
The report shows which tests failed, how their speed and image quality changed,
and whether earlier problems have returned. Links lead back to the CI jobs and
logs behind each finding.

The bot saves a full Markdown report and posts a shorter summary to GitHub
Issues. Optional GLM analysis helps investigate failures and suggests what to
check next. Problems seen on main can have their own incident Issues; PR-only
failures stay in the daily watchlist.

## What the report includes

- The latest observed main run, its tested commit, and the age of that evidence.
- Separate workflow, job, test-step, and testcase results.
- Generation timings, performance limits, image-quality metrics, and exceptions
  when the logs contain them.
- Failure history, relevant PRs, and proposed verification steps.
- Collection limits and missing evidence, so a sampled result is not presented
  as complete coverage.

A successful job alone does not prove every model testcase passed. An incident
can close automatically only with the controlled testcase evidence described
in the [recovery contract](docs/TESTCASE_PASS_VERIFICATION.md).

## Install and configure

Run the bot on an openEuler host with Python 3.9 or newer, Git, systemd,
and access to the GitHub API. It reads results from the CI runners, so the
reporting host does not need an NPU.

If `/opt/npu-diffusion-ci-kit` already exists, use the
[update procedure](docs/OPERATIONS.md) instead of cloning again. The installer
uses the current source checkout; `--install` does not fetch new code.

```bash
sudo git clone https://github.com/OrangeRedeng/sglang-ci-npu-bot.git /opt/npu-diffusion-ci-kit
cd /opt/npu-diffusion-ci-kit
sudo bash deploy/update-from-git.sh --install
sudoedit /etc/sglang-npu-bot/bot.env
```

Set `GITHUB_TOKEN` and `TARGET_REPOSITORY` for the repository that will receive
reports. `SOURCE_REPOSITORY` selects the CI source, normally
`sgl-project/sglang`. Hosting this code in a repository does not automatically
make that repository the report destination.

GLM is optional. To enable it, configure the API endpoint, model, key, prices,
and spending limits in `bot.env`. Existing installations keep their configuration
during updates. See the [configuration example](deploy/bot.env.example).

Check collection before enabling the daily schedule:

```bash
sudo bash /home/sglang-npu-bot/deploy/run-smoke-test.sh
sudo systemctl enable --now npu-diffusion-ci.timer
```

The smoke test reads CI results without publishing Issues or calling GLM.
The supplied timer runs daily at **13:15 Moscow time**.

## Update and refresh reports

Use the [operations guide](docs/OPERATIONS.md) to update an existing installation,
refresh yesterday's report from saved evidence, and collect today's report.
It includes commands that pause the timer, preserve configuration and reports,
and restore the schedule afterward.

Historical refresh uses retained snapshots and logs. A new current-day report
collects a fresh 24-hour window. GLM-enabled runs can consume the configured
API budget.

## Local files

The installed bot lives in `/home/sglang-npu-bot`.

| File | Contents |
| --- | --- |
| `reports/npu-diffusion-ci-YYYY-MM-DD.md` | Complete report, including run/job/testcase ledgers |
| `reports/npu-diffusion-ci-YYYY-MM-DD-issue.md` | Shorter GitHub Issue body |
| `reports/npu-diffusion-ci-YYYY-MM-DD.json` | Collected and parsed evidence |
| `reports/analysis/` | Model analysis and recovery checkpoints |
| `reports/raw-logs/` | Retained source logs, subject to configured retention limits |

Dates in report filenames use Moscow time. Raw logs and model credentials
should remain on the reporting host.

For a local replay without GitHub or GLM calls:

```bash
python3 scripts/preview_report.py \
  --snapshot /path/to/snapshot.json \
  --log JOB_ID=/path/to/job.log \
  --output /path/to/preview
```

## Tests and current limits

The offline suite passes 258 tests, including recovery after a report-rendering
crash without another GLM request. Collection has limits: the bot does not yet
read artifact contents or compare every observed testcase with a complete
expected inventory. See the [verification notes](docs/VERIFICATION.md).

Run the offline suite from the source checkout:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Project history

The current version is recorded in [VERSION](VERSION).
[CHANGELOG.md](CHANGELOG.md) keeps the version history. Use the operations guide
above for current installation and recovery commands.
