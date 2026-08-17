#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司
# Remove PingAgent installed symlinks/copies. Does NOT touch any project's .ai-mailbox/.
set -euo pipefail

BIN_DEST="${PINGAGENT_BIN:-$HOME/.local/bin}"
CONF_DIR="$HOME/.config/ai-collab"

for script in ai-pane-register ai-pane-unregister ai-pane-doctor ai-ping ai-collab-watch ai-harness-transport; do
  if [[ -e "$BIN_DEST/$script" || -L "$BIN_DEST/$script" ]]; then
    rm -f "$BIN_DEST/$script"
    echo "  removed $BIN_DEST/$script"
  fi
done

if [[ -e "$CONF_DIR/AGENTS-template.md" || -L "$CONF_DIR/AGENTS-template.md" ]]; then
  rm -f "$CONF_DIR/AGENTS-template.md"
  echo "  removed $CONF_DIR/AGENTS-template.md"
fi
if [[ -e "$CONF_DIR/ai-ping.md" || -L "$CONF_DIR/ai-ping.md" ]]; then
  rm -f "$CONF_DIR/ai-ping.md"
  echo "  removed $CONF_DIR/ai-ping.md"
fi

echo ""
echo "Uninstalled. (Project-level .ai-mailbox/ directories were not touched.)"
echo "If you want to stop running watchers in existing panes:"
echo "  pkill -f ai-collab-watch"
