# AI logs

`claude-code-session.md` is the full working session with Claude Code (Claude Opus 5.5, desktop app)
that built this submission: reading the brief, the first rules-only baseline, each model run and what
it scored, the tuning decisions (including the ones measured and rejected), the model comparison, and
the write-up.

It was exported from the session transcript with `tools/export_log.py`, which keeps my messages,
Claude's replies and every command or file edit, and leaves out:

- tool output, because it printed challenge messages, which must not be committed;
- hidden reasoning and app plumbing (task lists, status boxes);
- the API key, email addresses and home-folder paths (redacted).

The conversation was summarised once when it got long; that point is marked in the log.
