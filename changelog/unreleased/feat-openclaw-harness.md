### Added
- **OpenClaw is a harness**, like Claude Code or Codex. The collector reads each agent's own database (`~/.openclaw/agents/<id>/agent/openclaw-agent.sqlite`), counting every `model.completed` event: 856 agent calls and 238M tokens on this machine were invisible until now, most of them `claude-sonnet-5` billed to Claude Max.
- The **agent is the project** (`nova`, `rex`, `forge`), so agent work is named where it happened, and workspace paths stay off the wire.
- A **link to openclaw-spend** on the More screen (`OPENCLAW_SPEND_URL`), for per-agent and per-session detail.

### Changed
- A model that someone else hosts is priced by its host whatever ran it: `ollama/<model>` takes Ollama Cloud's rate, or nothing when it ran locally.
- OpenClaw carries no subscription of its own — its agents run on plans already counted, so charging a fee here would count it twice.
- Meter-card odometers size themselves to fit: a ten-digit monthly reading no longer overflows a 320px phone.
