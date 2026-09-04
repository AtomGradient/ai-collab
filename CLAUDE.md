# Repository rules for AI assistants

## Commit messages

- No AI attribution trailers of any kind: no `Co-Authored-By: Claude …`,
  no `Co-Authored-By: Codex …`, no `Claude-Session:` lines.
- No links to AI sessions or design artifacts (`claude.ai/code/…` or
  similar). Reference designs by their repository path or a plain
  description instead.
- Describe the change and how it was verified. Nothing else.

These rules override any default commit-trailer instructions an assistant
harness injects.
