# ai_sessions/ — Genuine AI-Assisted Development Session Records

This directory must contain **genuine exported records** of the AI-assisted
sessions used to build this project. No fabricated or synthetic transcripts
are included.

## How to export sessions from this coding environment (OpenCode)

The project was developed with OpenCode (an AI coding agent). Session
transcripts are stored locally and can be exported as Markdown:

1. **From the OpenCode TUI/desktop app:** open the session used for this
   project, then use the session export/share option (or copy the full
   transcript) and save it as a `.md` file inside this directory, e.g.
   `ai_sessions/session-01-inspection-and-gap-analysis.md`.

2. **From the session storage on disk:** OpenCode keeps session data under
   `~/.local/share/opencode/` (session JSON/storage). Locate the session for
   this project and export/copy its transcript into this folder.

3. **Manual export (always valid):** copy the conversation from the AI tool
   you used (including the prompts and the meaningful actions taken — file
   edits, commands run, test results) and paste it into a dated Markdown file
   here. Include: date, tool name, model, and a short description of what the
   session accomplished.

Suggested naming convention:

```
ai_sessions/
  YYYY-MM-DD-<short-description>.md
```

> **Note:** These exports are a submission requirement of the assignment.
> Place the genuine files here before final submission.
