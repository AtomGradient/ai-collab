# TODO

## Post-0.1.13 Follow-Ups

- P0: Fix confirmation timeout replay for destructive/repair authorizations. A timeout-denied chain must not make the same operation intent permanently unretryable; replay checks should consider committed authorization/consumption status, and denial history should be append-only.
- Validate Codex exact resume on m2pro32g.local with a real Codex prompt before close/resume. The 0.1.13 release verified Claude exact resume and empty-TUI recreation, but Codex exact resume still needs a failure-sensitive real TUI test.
- Add Claude workspace trust prompt automation, matching Codex's strict known-prompt handling, so first launch of a new scenario does not stall waiting for manual confirmation.
- P0: Fix and validate the real Harness ai-ping round trip inside a running scenario. The 0.1.13 m2 test validated TUI conversation continuity, not the full delivery chain; the 0.1.15 m2 test `harness-ping-roundtrip-0135` proved startup, policy routing, durable enqueue, and `_pingagent_deliver` ACKs can all succeed while Claude Code only sees the final `e>.` characters of the typed delivery. Those bytes are exactly the tail of the constructed `<message>.` reply instruction in `scripts/ai_collab_participant_driver.py:3281`, so the notification assembly is not the likely failure point. `pingagent/bin/ai-harness-transport` is using the one-line AppleScript injection technique that was validated for legacy `ai-collab-watch`, but Harness typed delivery sends a multi-line report through that path; first run a zero-code discriminator comparing a single-line flattened notification with the original multi-line notification against a ready Claude session. Also treat `ack_accepted` as transport/session evidence only: it proves the transport found the exact iTerm session and returned its expected digest, but it cannot prove the agent received or understood the full payload. `AI_COLLAB_CONSUMED` / `consumption_ack` is the meaningful proof for "agent read it"; summaries and UI should not make a delivery stuck at `delivered` look like end-to-end success.
- Split startup gate diagnostics so a prompt that was accepted but never reached ready is distinguishable from a TUI that never displayed a handled prompt.
- Make repair actions state-aware enough that degraded states do not point users at currently ineligible repair exits.
- P0: Fix the participant close-failure cleanup-pending dead end around `store.py:2251`. The close failure branch degrades the participant but leaves `desired_state="running"`, while the scenario close has already committed `desired_state="closed"`. `_cleanup_pending_participant_is_settled` then refuses to settle because it requires `desired_state=="stopped"`; in startup-gate failures there are no unreleased leases, so the stale participant desired state is the only blocker. This leaves `scenario.open`, `scenario.repair`, `participant.recover`, and normal `scenario.destroy` all ineligible.

## TUI Startup Prompt Automation

- Add a provider-scoped startup gate state machine for Codex and Claude TUIs.
- Auto-confirm workspace trust prompts only when the iTerm window/session, foreground process, and current working directory all match the Harness-owned, verified workspace.
- Add an explicit known-prompt rule for vendor update/upgrade screens that selects Skip or Continue so the participant reaches the interactive TUI without manual intervention.
- Keep prompt matching strict: use full provider-specific screen patterns, not loose substring matching.
- Treat unknown numbered menus or unknown prompt screens as `Needs attention` with diagnostic evidence instead of sending arbitrary keystrokes.
- Keep readiness gated on a stable interactive prompt, such as Codex `>`/`›` or Claude `❯`, observed across multiple screen samples and not mixed with a choice menu.
- Cover the behavior with fixture-based screen tests for trust prompts, upgrade prompts, ready prompts, and unknown menus.
