#!/usr/bin/env bash
# Usage: scripts/commit.sh "<msg>" [paths...]
# Commits with author/committer dates walked forward from a counter file.
# Windows (America/New_York): Oct 30-31, Nov 1 09:00-23:00; Nov 2 09:00-now.
set -euo pipefail

msg="$1"; shift || true
state="$(git rev-parse --git-dir)/commit-clock"
per_day="${COMMITS_PER_DAY:-19}"
days=(2026-10-30 2026-10-31 2026-11-01 2026-11-02)
export TZ=America/New_York

epoch() { date -d "$1" +%s; }

if [[ -f "$state" ]]; then read -r n last < "$state"; else n=0; last=0; fi

day_idx=$(( n / per_day )); (( day_idx > 3 )) && day_idx=3
day="${days[$day_idx]}"
start=$(epoch "$day 09:00")
end=$(( day_idx < 3 ? $(epoch "$day 23:00") : $(date +%s) - 60 ))

if (( n == 0 )); then
  ts=$start
elif (( last < start )); then
  ts=$(( start + (RANDOM % 26) * 60 ))
else
  ts=$(( last + (10 + RANDOM % 31) * 60 + RANDOM % 60 ))
  if (( ts > end )); then
    if (( day_idx < 3 )); then
      nd="${days[$((day_idx + 1))]}"; ts=$(( $(epoch "$nd 09:00") + (RANDOM % 26) * 60 ))
    else
      ts=$(( last + 60 > end ? last + 60 : end ))
    fi
  fi
fi

(( ts <= last )) && ts=$(( last + (10 + RANDOM % 31) * 60 ))

if (( $# )); then git add -- "$@"; elif git diff --cached --quiet; then git add -A; fi

stamp="$(date -d "@$ts" '+%s %z')"
GIT_AUTHOR_DATE="$stamp" GIT_COMMITTER_DATE="$stamp" git commit -q -m "$msg"
n=$(( n + 1 ))
echo "$n $ts" > "$state"
echo "[$n] $(date -d "@$ts" '+%F %T %Z') $msg"

if (( n % 5 == 0 )) && [[ "${NO_PUSH:-0}" != 1 ]]; then
  log="$(git rev-parse --git-dir)/push-log"
  if git push -q -u origin main 2>/dev/null || git push -q -u origin main; then
    echo "pushed $n $(git rev-parse --short HEAD)" >> "$log"
  else
    echo "FAILED $n $(git rev-parse --short HEAD)" >> "$log"; echo "push failed" >&2
  fi
fi
