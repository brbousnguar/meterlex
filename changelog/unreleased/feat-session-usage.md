### Added
- `GET /api/sessions/{session_id}/usage?from=&to=`: one session's tokens, cost, per-model split and active time (gaps capped at 5 minutes), subagents included, so Worklex can show what each issue cost (#65)
