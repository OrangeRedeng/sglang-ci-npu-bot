# v21.0.1 hotfix (2026-10-21): сбой установленного self-test

В v21.0.0 тест `test_installer_and_timer_lifecycle_static_contract` ошибочно
падал **только в установленной копии**: `deploy/systemd/npu-diffusion-ci.timer`,
`deploy/update-from-git.sh` и `deploy/install-openeuler.sh` не были скопированы
в `/home/sglang-npu-bot`. В v21.0.1 это исправлено, добавлен offline guard,
предотвращающий повторение ошибки. Исправлена запись SHA прежней
*установленной* версии для `--rollback` при импорте новой Git-копии.

Если сохранён Git-каталог `/opt/npu-diffusion-ci-kit` от v21, на сервер
достаточно передать `npu-diffusion-ci-kit-v21.0.1.bundle` и выполнить:

```bash
cd /opt/npu-diffusion-ci-kit
sudo git fetch /home/swx1199799/npu-diffusion-ci-kit-v21.0.1.bundle \
  refs/tags/v21.0.1:refs/tags/v21.0.1
sudo git -c advice.detachedHead=false checkout --detach v21.0.1
sudo bash deploy/update-and-run.sh
```

Последняя команда обновляет код (офлайн unit tests и GitHub preflight),
публикует исправленный отчёт за 2026-10-20 из сохранённого полноценного
GLM-анализа **без новых платных запросов**, запускает отчёт за 2026-10-21
и восстанавливает включённый таймер. Команда `update-and-run.sh` откажется
от live-запуска за 21 октября после окончания 21 октября по Москве.
До запуска должен присутствовать исходный JSON-отчёт за 20 октября и
сохранённый полный файл `english-pr-refresh-20261021T083242Z-full-analysis.md`.

Проверка:

```bash
sudo systemctl status npu-diffusion-ci.timer --no-pager -l
sudo systemctl status npu-diffusion-ci.service --no-pager -l
sudo journalctl -u npu-diffusion-ci.service -n 100 --no-pager
sudo -u sglang-ci sh -c 'cd /home/sglang-npu-bot && /usr/bin/python3 -m unittest discover -s tests -p "test_*.py" -q'
```

---

# v21: Combined historical refresh and missed daily run

After copying `npu-diffusion-ci-kit-v21.0.1.bundle` to `/home/swx1199799/`:

```bash
cd /opt/npu-diffusion-ci-kit
sudo git fetch /home/swx1199799/npu-diffusion-ci-kit-v21.0.1.bundle \
  refs/tags/v21.0.1:refs/tags/v21.0.1
sudo git checkout --detach v21.0.1
sudo bash deploy/update-and-run.sh
```

The last line is a **single coordinated operation**:

1. Install version v21 without activating the timer during historical reformatting.
2. Check `/home/sglang-npu-bot/reports/reanalysis/english-pr-refresh-20261021T083242Z-full-analysis.md` (already saved after successful GLM synthesis); re-publish **existing** Daily Issue and cluster Issues for 20 October, verifying fingerprints and PR URLs, with **zero BigModel calls**.
3. Start the normal daily `systemd` service for 21 October (`--no-block`), and activate the timer with missed-run catch-up. The live 21 October analysis may issue BigModel calls under normal daily budget settings. A second start of the same active systemd service is coalesced; the publisher also checks whether today's daily Issue already exists.

The combined script refuses to run on any date other than **2026-10-21 in Europe/Moscow** by default. If executed on 22 October or later, the historical 21 October CI window must be reconstructed from saved snapshots rather than presented as a fresh daily scan.

If the saved synthesis filename differs, use:

```bash
sudo bash /opt/npu-diffusion-ci-kit/deploy/update-and-run.sh \
  --analysis /home/sglang-npu-bot/reports/reanalysis/YOUR-FULL-ANALYSIS.md
```

Progress and completion:

```bash
sudo systemctl status npu-diffusion-ci.service --no-pager -l
sudo journalctl -fu npu-diffusion-ci.service --no-pager
sudo systemctl list-timers --all --no-pager | grep npu-diffusion-ci
```

`update-and-run.sh` checks for active publishers, stops the timer during the historical Issue refresh, and restores a previously enabled timer even after a failure. It does not restart paid historical GLM analysis or change `/etc/sglang-npu-bot/bot.env`. GitHub Issue writes are still gated on matching fingerprint and English-only content.

---

# Git deployment and release management (openEuler)

## Architecture

Use a **new, standalone** repository (recommended: `OrangeRedeng/npu-diffusion-ci-kit`) for source code and releases.
The existing `sglang-ci-npu-bot/sglang-ci-npu-bot` stays the bot's **Issue/report destination**; do not turn it into the deployment repository.
`sgl-project/sglang` remains a read-only CI evidence source.

`/opt/npu-diffusion-ci-kit` is a root-owned Git checkout. `/home/sglang-npu-bot` is the installed service runtime.
`/etc/sglang-npu-bot/bot.env` remains outside Git and is **never** reset by the updater.

## First installation from a published repository

```bash
sudo git clone https://github.com/OrangeRedeng/npu-diffusion-ci-kit.git /opt/npu-diffusion-ci-kit
cd /opt/npu-diffusion-ci-kit
sudo bash deploy/update-from-git.sh --to v21.0.1
sudo systemctl enable --now npu-diffusion-ci.timer
```

For an **unpublished** repository use the provided `npu-diffusion-ci-kit-v21.0.1-git.tar.gz` instead:

```bash
sudo tar -xzf npu-diffusion-ci-kit-v21.0.1-git.tar.gz -C /opt
cd /opt/npu-diffusion-ci-kit
sudo bash deploy/update-from-git.sh --install
```

After creating the empty repository on GitHub and pushing the release, set the remote:

```bash
sudo git -C /opt/npu-diffusion-ci-kit remote set-url origin https://github.com/OrangeRedeng/npu-diffusion-ci-kit.git
```

## Publishing this Git history to your own GitHub repo

1. Create an **empty** GitHub repository `OrangeRedeng/npu-diffusion-ci-kit` (do not auto-create README/.gitignore/license).
2. Push the provided Git history (from a machine with push access):

```bash
cd /opt/npu-diffusion-ci-kit
git remote set-url origin https://github.com/OrangeRedeng/npu-diffusion-ci-kit.git
git push -u origin main
git push origin --tags
```

Avoid embedding tokens in clone URLs and command history. For GitHub CLI, `gh repo create OrangeRedeng/npu-diffusion-ci-kit --public --source=. --remote=origin --push` is an optional shortcut if installed and authenticated.

## Updates

```bash
cd /opt/npu-diffusion-ci-kit
sudo bash deploy/update-from-git.sh --check
sudo bash deploy/update-from-git.sh --latest
# or a specific release, safer for staged rollouts:
sudo bash deploy/update-from-git.sh --to v21.0.1
# rollback to previous deployed Git revision:
sudo bash deploy/update-from-git.sh --rollback
```

Updater rejects a dirty source checkout or active reporting process; before modifying deployed scripts it saves a private backup of only installed code/systemd units, stops the timer, checks out the immutable version tag, installs and runs offline tests plus GitHub API preflight. If deployment fails it restores the prior checkout and installed files, then timer state. Reports, budget SQLite, DNS settings, and bot.env are outside the backup/rewrite set.

Source checkout tag pinning is reproducible but **not a signature guarantee**: enable GitHub protected tags and optionally signed annotated releases when you publish.

## Refresh existing daily and cluster Issues (no GLM charge)

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues.sh 2026-10-20
```

This reads the already saved `reports/npu-diffusion-ci-2026-10-20.{json,md}`, queries up to 200 GitHub Actions run metadata records (not job logs), verifies cluster fingerprints against the existing bot Issues, updates human-readable summary and synchronizes `main` (red), `not-merged` (purple), `unconfirmed` (lavender), `flaky` (yellow) and `infrastructure` (pale blue). No GLM API invocation or full scan.

**Important:** old v15 snapshots did not store event and head-repository provenance. When GitHub cannot return enough metadata to prove main or PR-only, labels remain `unconfirmed`. `flaky` is never asserted based only on a job-level success.

## Normal operation

```bash
sudo systemctl status npu-diffusion-ci.timer
sudo journalctl -u npu-diffusion-ci.service -n 100 --no-pager
sudo -u sglang-ci sh -c 'cd /home/sglang-npu-bot && /usr/bin/python3 scripts/glm-budget-status.py'
```


## Обновление уже существующих Issues с английским GLM-анализом (v17)

После установки v17 выполните **только одну** команду (при отсутствии активного отчёта):

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh 2026-10-20 --publish
```

Она **не скачивает CI-логи и не сканирует заново все workflow runs**. Использует сохранённый JSON, получает метаданные уже существующих Issues, по возможности проверяет GitHub run/PR, вызывает GLM в режиме `investigate` и перезаписывает существующие Issues на английском языке, сохранив резервные копии в `reports/issue-backups/`. Лимиты `GLM_BUDGET_*` остаются действующими.

Перед публикацией можно выполнить `--preview` вместо `--publish`: будут списания GLM, но не будет записи в Issues. При частичном/ошибочном ответе GLM публикация блокируется. При неизвестном fingerprint публикация блокируется. `--publish` не создаёт новых Issues.

Пример запуска обновления kit после загрузки нового Git-архива в `/opt`:

```bash
cd /opt/npu-diffusion-ci-kit
sudo bash deploy/update-from-git.sh --to v17.0.0
```

Если `origin` ещё не опубликован, доступен вариант `--install` из распакованного релиза.

При ручном refresh только для этого процесса применяется оценочный локальный бюджет до ¥3/сутки и 8 HTTP-запросов; основной ежедневный `bot.env` не меняется. Реальные списания определяются биллингом BigModel.


## v18: идемпотентное обновление Issues с GLM

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh 2026-10-20 --publish --allow-partial
```

Опция `--allow-partial` допускает публикацию **только содержательного**
частичного анализа с честным `partial`-статусом. Завершённые GLM-этапы
сохраняются по SHA256 полного prompt/model/reasoning/limits, что позволяет
повторить незавершённый synthesis без повторной оплаты идентичных этапов.
Ни один timeout не записывается как успешный checkpoint.

Если предварительный анализ уже сохранён в
`reports/analysis/npu-diffusion-ci-YYYY-MM-DD-full.md`, используйте
`--reuse-analysis` для публикации без BigModel-запросов. Кандидатные PR
проверяются заново. При любых ошибках shell выводит journal даже при
ненулевом exit status. История до v18 автоматически не реконструируется.

## v19: manual English GLM Issue refresh (fixed 8k-token exhaustion)

This **does not rescan CI**. It verifies GitHub PR leads, runs GLM for the
existing snapshot, and replaces existing Issues only when the analysis is
publishable. Full response visibility now uses manual output token caps,
not the economical daily `GLM_MAX_OUTPUT_TOKENS=8000` from `bot.env`.

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish
```

The default manual profile uses high reasoning, 24k total output tokens,
20k per cluster, 24k for synthesis, a 720-second HTTP timeout, and at most one
retry *only* on observed reasoning exhaustion. Paid requests are bounded by
local GLM budget and request-count ceilings. Logs are printed on exit.
Use `--allow-partial` only when knowingly accepting incomplete analysis.

Use `sudo env GLM_REFRESH_MAX_OUTPUT_TOKENS=28000 ... bash ...` for a one-off
manual budget override, without changing `bot.env` or automated daily behavior.

## v21: публикация сохранённого анализа без повторного GLM

v19 завершил три расследования и сохранил их в `reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md`, но финальный synthesis завершился исключением. Узнать класс исключения:

```bash
sudo head -n 3 /home/sglang-npu-bot/reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md
```

Публиковать только после подтверждения, что в recovery присутствуют все три инцидента. При `--preview` новые GLM вызовы и изменения GitHub Issues отсутствуют, но обычные GitHub API чтения допустимы:

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --preview --allow-partial \
  --reuse-recovery reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md
```

Публикация в уже существующие Issues, без GLM:

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish --allow-partial \
  --reuse-recovery reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md
```

Отклоняется, если fingerprint/ID или английский язык не совпадают. Публикуется честный статус `partial`; отсутствие финального synthesis отмечается явно. При необходимости нового synthesis можно повторить обычный GLM refresh, учитывая стоимость и дневные лимиты. Для предотвращения ошибочной маркировки завершённости `--reuse-analysis` отказывает в публикации файлов recovery.

## Восстановление GLM Issue без новых платных запросов (v20)

Если v19 создал файл `reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md` с тремя завершёнными расследованиями и `GLMBudgetExceeded` на финальном synthesis, используйте:

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish --allow-partial \
  --reuse-recovery reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md
```

Повторных вызовов BigModel нет. В Issues будет указано `partial: recovered investigations; synthesis unavailable`. Будут выполнены GitHub API запросы для проверки links и изменения существующих Issues; возможны сетевые ошибки или недостаточные права записи. Старые тела Issues сохраняются локально до первой мутации.

Контроль local GLM бюджета: `sudo -u sglang-ci sh -c 'cd /home/sglang-npu-bot && /usr/bin/python3 scripts/glm-budget-status.py'`.

## v21.0.2 — преемственность Issues без дополнительных GLM запросов

После обновления установленного kit, чтобы добавить сравнение с предыдущим днем
в уже опубликованный отчет за 21 октября, сначала выполнить предпросмотр:

```bash
sudo bash /home/sglang-npu-bot/scripts/sync-incident-continuity.sh 2026-10-21 --preview
```

Если предпросмотр верен, выполнить публикацию:

```bash
sudo bash /home/sglang-npu-bot/scripts/sync-incident-continuity.sh 2026-10-21 --publish
```

Вторая команда читает сохраненный `reports/npu-diffusion-ci-2026-10-21.json`,
не пересканирует CI и не вызывает GLM. Добавляет секцию cross-day continuity в
Issue за 21 октября; помечает D004/D005 как «not observed, not verified fixed».
Закрывает только уже замененный ежедневный отчет за 20 октября (#1) как
`completed` (опционально), но НЕ закрывает инциденты D004-D007 без проверенного
успешного прогона соответствующего testcase.

Следующие ежедневные отчеты будут формировать эту секцию автоматически.
Для отключения закрытия прошлых дневных отчетов: `ARCHIVE_PREVIOUS_DAILY_ISSUES=0`
в `/etc/sglang-npu-bot/bot.env` (сохранение файла с правами 0600).

Важно: `deploy/update-and-run.sh` предназначен для разового восстановления
именно за 20 и 21 октября. При обновлении kit после создания отчетов не нужно
перезапускать всю историю: используйте `deploy/update-from-git.sh --install`,
а затем одноразовый `sync-incident-continuity.sh`.
