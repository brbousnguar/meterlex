### Added
- A Quota screen for the Claude Max week: used against elapsed (ahead or behind pace), the projection at reset, the week's budget in tokens, what is left per day, tokens per day of the window, a weekday × hour map, and past weeks (#35)
- `statusline`: the collector doubles as Claude Code's status line, records each new rate-limit reading, and `run` sends them; `/api/quota` and an optional `quota` list on `/api/ingest` (#35)
