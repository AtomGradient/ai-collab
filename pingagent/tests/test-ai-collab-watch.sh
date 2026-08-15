#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
WATCHER="$REPO_ROOT/bin/ai-collab-watch"
FIXTURE_BIN="$SCRIPT_DIR/fixtures/bin"
TEST_TMP_BASE="${TMPDIR:-/tmp}"
TEST_TMP_BASE="${TEST_TMP_BASE%/}"
TEST_ROOT=$(mktemp -d "$TEST_TMP_BASE/pingagent-watch-tests.XXXXXX")

cleanup() {
  case "$TEST_ROOT" in
    "$TEST_TMP_BASE"/pingagent-watch-tests.*) rm -rf "$TEST_ROOT" ;;
    *) printf 'refusing to clean unexpected test path: %s\n' "$TEST_ROOT" >&2 ;;
  esac
}
trap cleanup EXIT

pass_count=0

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

pass() {
  pass_count=$((pass_count + 1))
  printf 'ok %d - %s\n' "$pass_count" "$1"
}

make_case() {
  local name="$1" case_root mailbox
  case_root="$TEST_ROOT/$name"
  mailbox="$case_root/.ai-mailbox"
  mkdir -p "$mailbox/.panes" "$mailbox/inbox/receiver"
  printf '%s\n' '{"session_uuid":"fake-session"}' > "$mailbox/.panes/receiver.json"
  printf '%s\n' \
    '---' \
    "id: $name" \
    'from: sender' \
    'to: receiver' \
    'kind: msg' \
    '---' \
    '' \
    'test message' > "$mailbox/inbox/receiver/$name.md"
  printf '%s\n' "$case_root"
}

run_watcher() {
  local mode="$1" case_root="$2" mailbox calls log
  mailbox="$case_root/.ai-mailbox"
  calls="$case_root/osascript.calls"
  log="$case_root/watcher.log"

  PATH="$FIXTURE_BIN:$PATH" \
    AI_COLLAB_OSASCRIPT_BIN="$FIXTURE_BIN/osascript" \
    FAKE_OSASCRIPT_MODE="$mode" \
    FAKE_OSASCRIPT_CALLS="$calls" \
    "$WATCHER" receiver "$mailbox" > "$log" 2>&1
}

case_root=$(make_case normal-injection)
run_watcher success "$case_root"
sidecar="$case_root/.ai-mailbox/inbox/receiver/normal-injection.md.dispatched"
[[ -f "$sidecar" ]] || fail "normal injection creates dispatched sidecar"
grep -q '^arg=fake-session$' "$case_root/osascript.calls" || fail "normal injection targets the registered session"
grep -q '^arg=\[ai-collab 收信\] from=sender kind=msg id=normal-injection ' "$case_root/osascript.calls" || fail "normal injection passes the mailbox pointer notification"
grep -q 'delivery confirmed: normal-injection.md' "$case_root/watcher.log" || fail "normal injection is logged as confirmed"
pass "normal injection creates sidecar only after ok"

case_root=$(make_case session-missing)
run_watcher session_missing "$case_root"
sidecar="$case_root/.ai-mailbox/inbox/receiver/session-missing.md.dispatched"
[[ ! -e "$sidecar" ]] || fail "session missing must not create dispatched sidecar"
grep -q 'reason=session_not_found' "$case_root/watcher.log" || fail "session missing classification"
grep -q 'message remains undispatched' "$case_root/watcher.log" || fail "session missing retry state"
pass "session missing is a classified failure and remains undispatched"

case_root=$(make_case tcc-denied)
run_watcher tcc_denied "$case_root"
sidecar="$case_root/.ai-mailbox/inbox/receiver/tcc-denied.md.dispatched"
[[ ! -e "$sidecar" ]] || fail "TCC denial must not create dispatched sidecar"
grep -q 'reason=automation_denied_-1743' "$case_root/watcher.log" || fail "TCC denial classification"
pass "TCC -1743 is classified and remains undispatched"

case_root=$(make_case legacy-sidecar)
sidecar="$case_root/.ai-mailbox/inbox/receiver/legacy-sidecar.md.dispatched"
: > "$sidecar"
run_watcher success "$case_root"
[[ ! -e "$case_root/osascript.calls" ]] || fail "pre-existing sidecar must suppress reinjection"
[[ -f "$sidecar" ]] || fail "pre-existing sidecar must remain intact"
pass "pre-existing legacy sidecar remains a dedupe marker"

case_root=$(make_case unexpected-response)
run_watcher unexpected_success "$case_root"
sidecar="$case_root/.ai-mailbox/inbox/receiver/unexpected-response.md.dispatched"
[[ ! -e "$sidecar" ]] || fail "unexpected success output must not create dispatched sidecar"
grep -q 'reason=unexpected_osascript_response' "$case_root/watcher.log" || fail "unexpected response classification"
pass "only the explicit ok response confirms injection"

printf '1..%d\n' "$pass_count"
