#!/usr/bin/env bash
# Versioned, fail-closed openEuler git updater. This never touches bot.env/reports.
set -Eeuo pipefail
[[ $(id -u) == 0 ]] || { echo 'Use sudo.' >&2; exit 2; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOT_HOME=/home/sglang-npu-bot
STATE=/var/lib/sglang-npu-bot-updater
MODE=install
TARGET=''
case "${1:-}" in
  --check) MODE=check; shift ;;
  --to) MODE=update; TARGET="${2:-}"; shift 2 ;;
  --latest) MODE=update; TARGET=latest; shift ;;
  --main) MODE=main; shift ;;
  --install|'') MODE=install; [[ $# -eq 0 ]] || shift ;;
  --rollback) MODE=rollback; shift ;;
  *) echo 'Usage: sudo bash deploy/update-from-git.sh [--main|--install|--check|--latest|--to vX.Y.Z|--rollback]' >&2; exit 2 ;;
esac
DEFER_TIMER=0
if [[ "${1:-}" == --defer-timer ]]; then
  DEFER_TIMER=1
  shift
fi
[[ $# -eq 0 ]] || { echo 'Unexpected arguments.' >&2; exit 2; }
command -v git >/dev/null || { echo 'git not installed.' >&2; exit 2; }
git -C "$SRC" rev-parse --is-inside-work-tree 2>/dev/null | grep -qx true || {
 echo 'Not a git checkout. Use a git clone or the distributed .git repository tarball.' >&2; exit 2; }
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=normal)" ]] || {
 echo 'Refusing to update a dirty checkout. Commit/stash or remove local modifications.' >&2; exit 2; }
CURRENT="$(git -C "$SRC" rev-parse HEAD)"
# A freshly unpacked release checkout can already point to the *new* tag
# while the installed runtime is older. For rollback history use the actual
# deployed revision, not the new release archive's HEAD.
DEPLOYED_REVISION="$CURRENT"
if [[ -r "$STATE/current-revision" ]]; then
  installed_sha="$(cat "$STATE/current-revision")"
  if [[ "$installed_sha" =~ ^[0-9a-f]{40}$ ]] &&
     git -C "$SRC" cat-file -e "$installed_sha^{commit}" 2>/dev/null; then
    DEPLOYED_REVISION="$installed_sha"
  fi
fi
if [[ "$MODE" == main ]]; then
  git -C "$SRC" fetch https://github.com/OrangeRedeng/sglang-ci-npu-bot.git main
  MAIN_REVISION="$(git -C "$SRC" rev-parse FETCH_HEAD)"
fi
if [[ "$MODE" == check || "$MODE" == update ]]; then
  git -C "$SRC" remote get-url origin >/dev/null || {
    echo 'No origin remote. Add your published kit repository as origin before --check/--latest.' >&2; exit 2; }
  git -C "$SRC" fetch --tags --prune origin
fi
if [[ "$MODE" == rollback ]]; then
  [[ -r "$STATE/previous-revision" ]] || { echo 'No previous version recorded.' >&2; exit 1; }
  TARGET="$(cat "$STATE/previous-revision")"
elif [[ "$MODE" == check || "$TARGET" == latest ]]; then
  TAG="$(git -C "$SRC" tag -l 'v[0-9]*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)"
  [[ -n "$TAG" ]] || { echo 'No release tags available.' >&2; exit 1; }
  TARGET="$TAG"
fi
if [[ "$MODE" == check ]]; then
  echo "Installed source: $(git -C "$SRC" describe --always --tags) ($CURRENT)"
  echo "Latest release: $TARGET ($(git -C "$SRC" rev-parse "refs/tags/$TARGET^{commit}"))"
  exit 0
fi
if [[ "$MODE" == main ]]; then
  NEXT="$MAIN_REVISION"
elif [[ "$MODE" == update ]]; then
  [[ "$TARGET" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Require immutable semantic version tag vX.Y.Z.' >&2; exit 2; }
  NEXT="$(git -C "$SRC" rev-parse --verify "refs/tags/$TARGET^{commit}")"
elif [[ "$MODE" == rollback ]]; then
  [[ "$TARGET" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid rollback SHA.' >&2; exit 2; }
  NEXT="$(git -C "$SRC" rev-parse --verify "$TARGET^{commit}")"
else
  NEXT="$CURRENT"
fi
if [[ "$CURRENT" == "$NEXT" && "$MODE" == rollback ]]; then
  # Initial import from a v15 ZIP may have no earlier Git SHA; restore its
  # installed-code backup instead. Never touch config, reports or budget DB.
  previous_backup="$(cat "$STATE/last-backup" 2>/dev/null || true)"
  [[ -f "$previous_backup" ]] || { echo 'No earlier installed-code backup available.' >&2; exit 1; }
  was_active=0
  was_enabled=0
  systemctl is-active --quiet npu-diffusion-ci.timer && was_active=1 || true
  systemctl is-enabled --quiet npu-diffusion-ci.timer && was_enabled=1 || true
  systemctl stop npu-diffusion-ci.timer || true
  tar -C / -xzpf "$previous_backup"
  systemctl daemon-reload
  if (( was_active || was_enabled )); then systemctl enable --now npu-diffusion-ci.timer; fi
  echo 'Restored the installed-code backup made before first Git installation.'
  exit 0
fi
if systemctl is-active --quiet npu-diffusion-ci.service ||
   pgrep -u sglang-ci -f 'npu_diffusion_ci_report\.py|reformat_report\.py|reanalyze_and_publish\.py|recover_daily_report\.py|refresh_daily_snapshots\.py|reparse_failure_snapshots\.py|reconcile-legacy-issues\.py' >/dev/null 2>&1; then
  echo 'CI bot/report publication is active. Refusing to update files mid-run.' >&2; exit 1
fi
install -d -m 0700 -o root -g root "$STATE" "$STATE/backups"
backup="$STATE/backups/$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
paths=()
for p in home/sglang-npu-bot/VERSION home/sglang-npu-bot/scripts home/sglang-npu-bot/tests home/sglang-npu-bot/skills home/sglang-npu-bot/deploy \
  etc/systemd/system/npu-diffusion-ci.service etc/systemd/system/npu-diffusion-ci.timer; do
  [[ -e "/$p" ]] && paths+=("$p")
done
if (( ${#paths[@]} )); then tar -C / -czpf "$backup" "${paths[@]}"; fi
TIMER_ACTIVE=0
TIMER_ENABLED=0
systemctl is-active --quiet npu-diffusion-ci.timer && TIMER_ACTIVE=1 || true
systemctl is-enabled --quiet npu-diffusion-ci.timer && TIMER_ENABLED=1 || true
systemctl stop npu-diffusion-ci.timer || true
# Timer may have been enabled but left inactive by a previous updater.
# Restore enabled OR active state after successful installation.
restore_timer() {
  if (( TIMER_ENABLED || TIMER_ACTIVE )); then
    systemctl enable --now npu-diffusion-ci.timer
  fi
}
restore_on_failure() {
  # A failed update must never leave an enabled timer stopped.
  restore_timer || true
}
rollback_failed() {
  echo 'Upgrade failed: restoring installed code and original git SHA.' >&2
  git -C "$SRC" -c advice.detachedHead=false checkout --detach "$CURRENT" || true
  if [[ -s "$backup" ]]; then tar -C / -xzpf "$backup" || true; fi
  systemctl daemon-reload || true
  restore_on_failure
  exit 1
}
trap rollback_failed ERR
if [[ "$CURRENT" != "$NEXT" ]]; then
  git -C "$SRC" -c advice.detachedHead=false checkout --detach "$NEXT"
fi
bash "$SRC/deploy/install-openeuler.sh"
bash "$BOT_HOME/deploy/self-test.sh"
printf '%s\n' "$DEPLOYED_REVISION" >"$STATE/previous-revision"
printf '%s\n' "$NEXT" >"$STATE/current-revision"
printf '%s\n' "$backup" >"$STATE/last-backup"
chmod 0600 "$STATE/"*-revision "$STATE/last-backup"
trap - ERR
if (( DEFER_TIMER )); then
  echo 'Timer reactivation deferred to combined daily recovery command.'
else
  restore_timer
fi
printf 'Installed release: %s (%s)\n' "$(cat "$BOT_HOME/VERSION")" "$NEXT"
echo 'Saved previous revision for --rollback; previous bot.env and reports were preserved.'
