#!/usr/bin/env bash
# Runs a build command in CI. On failure, the last lines of its output are published as an error annotation and in
# the run summary, which (unlike the full log) can be read without signing in to GitHub.
#   scripts/package/ci_step.sh "<title>" <command...>
title="$1"; shift
log=$(mktemp)
"$@" 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
if [ "$status" -ne 0 ]; then
  tail -n 80 "$log" | sed 's/\x1b\[[0-9;]*m//g' > "$log.tail"
  { echo "### $title failed (exit $status)"; echo '```'; cat "$log.tail"; echo '```'; } >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
  msg=$(tail -n 25 "$log.tail" | sed -e 's/%/%25/g' -e 's/\r//g' | awk 'BEGIN{ORS="%0A"} {print}')
  echo "::error title=$title::$msg"
fi
exit "$status"
