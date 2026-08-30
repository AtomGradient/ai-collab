# TODO

## TUI Startup Prompt Automation

- Add a provider-scoped startup gate state machine for Codex and Claude TUIs.
- Auto-confirm workspace trust prompts only when the iTerm window/session, foreground process, and current working directory all match the Harness-owned, verified workspace.
- Add an explicit known-prompt rule for vendor update/upgrade screens that selects Skip or Continue so the participant reaches the interactive TUI without manual intervention.
- Keep prompt matching strict: use full provider-specific screen patterns, not loose substring matching.
- Treat unknown numbered menus or unknown prompt screens as `Needs attention` with diagnostic evidence instead of sending arbitrary keystrokes.
- Keep readiness gated on a stable interactive prompt, such as Codex `>`/`›` or Claude `❯`, observed across multiple screen samples and not mixed with a choice menu.
- Cover the behavior with fixture-based screen tests for trust prompts, upgrade prompts, ready prompts, and unknown menus.
