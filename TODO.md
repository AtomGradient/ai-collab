# TODO

## Post-0.1.13 Follow-Ups

- P0: Fix confirmation timeout replay for destructive/repair authorizations. A timeout-denied chain must not make the same operation intent permanently unretryable; replay checks should consider committed authorization/consumption status, and denial history should be append-only.
- Validate Codex exact resume on m2pro32g.local with a real Codex prompt before close/resume. The 0.1.13 release verified Claude exact resume and empty-TUI recreation, but Codex exact resume still needs a failure-sensitive real TUI test.
- Add Claude workspace trust prompt automation, matching Codex's strict known-prompt handling, so first launch of a new scenario does not stall waiting for manual confirmation.
- Validate a real Harness ai-ping round trip inside a running scenario. The 0.1.13 m2 test validated TUI conversation continuity, not the full delivery chain.
- Split startup gate diagnostics so a prompt that was accepted but never reached ready is distinguishable from a TUI that never displayed a handled prompt.
- Make repair actions state-aware enough that degraded states do not point users at currently ineligible repair exits.

## TUI Startup Prompt Automation

- Add a provider-scoped startup gate state machine for Codex and Claude TUIs.
- Auto-confirm workspace trust prompts only when the iTerm window/session, foreground process, and current working directory all match the Harness-owned, verified workspace.
- Add an explicit known-prompt rule for vendor update/upgrade screens that selects Skip or Continue so the participant reaches the interactive TUI without manual intervention.
- Keep prompt matching strict: use full provider-specific screen patterns, not loose substring matching.
- Treat unknown numbered menus or unknown prompt screens as `Needs attention` with diagnostic evidence instead of sending arbitrary keystrokes.
- Keep readiness gated on a stable interactive prompt, such as Codex `>`/`›` or Claude `❯`, observed across multiple screen samples and not mixed with a choice menu.
- Cover the behavior with fixture-based screen tests for trust prompts, upgrade prompts, ready prompts, and unknown menus.
