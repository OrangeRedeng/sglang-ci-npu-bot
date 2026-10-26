# Migrating the running openEuler v15 installation to Git-based v16

Existing server: `/home/sglang-npu-bot`; existing config: `/etc/sglang-npu-bot/bot.env`.

1. Copy `npu-diffusion-ci-kit-v16-git.tar.gz` to the server in `/home/swx1199799`.
2. Install this self-contained Git checkout without requiring the remote repository yet:

```bash
sudo tar -xzf /home/swx1199799/npu-diffusion-ci-kit-v16-git.tar.gz -C /opt
cd /opt/npu-diffusion-ci-kit
sudo bash deploy/update-from-git.sh --install
```

The installer overwrites only version-controlled installed scripts, tests, skills, deployment helpers and systemd units. It preserves `bot.env`, reports, history and SQLite budget, and does not modify DNS. Existing timer active state is preserved.

3. Refresh old Issues #1 and cluster Issues (no GLM or 30-minute full collection):

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues.sh 2026-10-20
```

4. Restore schedule if it had been manually disabled:

```bash
sudo systemctl enable --now npu-diffusion-ci.timer
```

5. Publish your source repo: create empty `OrangeRedeng/npu-diffusion-ci-kit` on GitHub, push `main` and version tags as described in `docs/GIT_DEPLOYMENT_RU.md`, then future updates use `sudo bash deploy/update-from-git.sh --latest`.

**Caution:** old snapshots do not contain verified run event/branch metadata. Issue sync fetches it from GitHub for at most 200 unique runs. Failures with insufficient provenance are labeled `unconfirmed`, not `main`. Local tag `v16.0.0` is already available in the tarball, even before remote publication.


## v17: обновить Issues по сохранённому snapshot с помощью GLM

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh 2026-10-20 --publish
```

Результат: английские Issue #1 и cluster Issues, verified PR hyperlinks (tested branch, possible cause, potential fix); raw CI Actions logs не загружаются повторно. Нужен исправный BigModel API и GitHub PAT с правами на Issues. Проверьте `journalctl -u <unit>` из вывода команды.


## Recovery v18: v17 `update-existing-issues-with-glm.sh` exited 1

Сначала проверьте точную причину сбоя, не перезапуская дорогой анализ:

```bash
sudo journalctl -u npu-glm-issue-v17-20261020-338584.service -n 180 --no-pager -o cat
```

Для установки Git-релиза v18 из перенесённого bundle:

```bash
cd /opt/npu-diffusion-ci-kit
sudo git fetch /home/swx1199799/npu-diffusion-ci-kit-v18.bundle \
  refs/tags/v18.0.0:refs/tags/v18.0.0
sudo git checkout --detach v18.0.0
sudo bash deploy/update-from-git.sh --install
```

Повторное расследование с сохранением успешных стадий и разрешением явно
помеченной частичной публикации, если synthesis снова завершится ошибкой:

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish --allow-partial
```

Обновить текущие Issues БЕЗ НОВОГО GLM при наличии сохранённого старого анализа:

```bash
sudo test -s /home/sglang-npu-bot/reports/analysis/npu-diffusion-ci-2026-10-20-full.md
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish --reuse-analysis \
  reports/analysis/npu-diffusion-ci-2026-10-20-full.md
```

В последнем сценарии `--reuse-analysis` означает ПОВТОРНОЕ ИСПОЛЬЗОВАНИЕ
уже сохранённого GLM-ответа, а не свежую генерацию. Релевантные PR
дополнительно проверяются по GitHub API; скачивания CI logs нет.

При отсутствии файла старого анализа `--reuse-analysis` завершится ошибкой
без платы за GLM. При ошибках публикации v18 всегда выводит журнал.

Промежуточные результаты v17, не сохранённые на диск, восстановить нельзя.
Содержимое реального биллинга BigModel не определяется локальным ledger.

## v19: устранение пустых ответов при high reasoning

В v17 ручной `--publish` наследовал из ежедневного `bot.env` лимит
`GLM_MAX_OUTPUT_TOKENS=8000`. На Qwen-Image и synthesis эти 8000 токенов
полностью расходовались на reasoning (`finish_reason=length`), поэтому Issues
не обновлялись. Скрипт v19 принудительно задаёт отдельный профиль:
24000 общий лимит, 20000 для кластера, 24000 для synthesis, тайм-аут 720 с.
При исчерпании генерационного бюджета разрешён один условный повтор с low
reasoning; он может тарифицироваться дополнительно.

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish
```

Для повторного использования v18 checkpoint-файлов необходимы идентичные
prompt, модель, reasoning и лимиты. Ответы v17 не сохранялись в такой кэш.
Отчёт сохраняется на английском, ссылки на PR проверяются через GitHub API.

### Восстановление Issue после v19 GLMBudgetExceeded (v20)

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish --allow-partial \
  --reuse-recovery reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md
```

Этот вызов не обращается к платному GLM и сохраняет статус `partial` (общий synthesis отсутствует). Для предварительного просмотра используйте `--preview` вместо `--publish`.

### Синхронизация статусов Issue v21.0.2

Команда `sudo bash /home/sglang-npu-bot/scripts/sync-incident-continuity.sh 2026-10-21 --publish`
обновляет уже существующие Issues только по локальному сохраненному snapshot,
без GLM расходов. Старые ежедневные отчеты можно автоматически архивировать;
отсутствующие в очередном отчете ошибки остаются открытыми до доказанного PASS.
