#!/usr/bin/env bash
# PingAgent installer
# 默认 symlink 到 ~/.local/bin/，git pull 后自动生效。
# 用 --copy 改为拷贝（适合不想依赖 repo 路径的场景）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_SRC="$REPO_ROOT/bin"
BIN_DEST="${PINGAGENT_BIN:-$HOME/.local/bin}"
CONF_DIR="$HOME/.config/ai-collab"

MODE="symlink"
for arg in "$@"; do
  case "$arg" in
    --copy) MODE="copy" ;;
    --symlink) MODE="symlink" ;;
    -h|--help)
      echo "Usage: $0 [--symlink|--copy]"
      echo "  --symlink  (default) link bin/* into $BIN_DEST"
      echo "  --copy     copy bin/* into $BIN_DEST"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

mkdir -p "$BIN_DEST" "$CONF_DIR"

# Install scripts
for script in ai-pane-register ai-pane-unregister ai-pane-doctor ai-ping ai-collab-watch ai-harness-transport; do
  src="$BIN_SRC/$script"
  dst="$BIN_DEST/$script"
  if [[ ! -f "$src" ]]; then
    echo "Missing: $src" >&2; exit 1
  fi
  rm -f "$dst"
  if [[ "$MODE" == "symlink" ]]; then
    ln -s "$src" "$dst"
    echo "  link  $dst -> $src"
  else
    install -m 0755 "$src" "$dst"
    echo "  copy  $src -> $dst"
  fi
done

# Install AGENTS.md template (always symlink — easy to keep in sync)
TPL_DST="$CONF_DIR/AGENTS-template.md"
rm -f "$TPL_DST"
ln -s "$REPO_ROOT/AGENTS.md" "$TPL_DST"
echo "  link  $TPL_DST -> $REPO_ROOT/AGENTS.md"

# ai-ping CLI reference (also symlinked so AIs can Read it from anywhere)
GUIDE_DST="$CONF_DIR/ai-ping.md"
rm -f "$GUIDE_DST"
ln -s "$REPO_ROOT/docs/ai-ping.md" "$GUIDE_DST"
echo "  link  $GUIDE_DST -> $REPO_ROOT/docs/ai-ping.md"

echo ""
echo "Installed to: $BIN_DEST"

# PATH check
if ! echo ":$PATH:" | grep -q ":$BIN_DEST:"; then
  echo ""
  echo "WARNING: $BIN_DEST is not in your PATH."
  echo "Add this to your shell rc:"
  echo "  export PATH=\"$BIN_DEST:\$PATH\""
fi

# fswatch hint
if ! command -v fswatch >/dev/null 2>&1; then
  echo ""
  echo "Optional: install fswatch for event-driven watching (otherwise 1s polling)"
  echo "  brew install fswatch"
fi

# osascript check
if ! command -v osascript >/dev/null 2>&1; then
  echo ""
  echo "WARNING: osascript not found. PingAgent requires macOS + iTerm2."
fi

echo ""
echo "Next steps:"
echo "  1) cd <your-project>"
echo "  2) cp $REPO_ROOT/AGENTS.md ./AGENTS.md   # protocol for the AIs"
echo "  3) echo '.ai-mailbox/' >> .gitignore"
echo "  4) In each iTerm2 pane, before starting the AI:"
echo "       ai-pane-register codex     # in pane A"
echo "       ai-pane-register claude    # in pane B"
echo "  5) Then start codex / claude in each pane respectively."
echo "  6) When you're done with a pane (optional cleanup):"
echo "       ai-pane-unregister         # auto-detects role from current pane"
