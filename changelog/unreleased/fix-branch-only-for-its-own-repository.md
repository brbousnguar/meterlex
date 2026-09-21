### Fixed

- A reply keeps its branch only when it counts toward the repository the session started in: Claude Code records that repository's branch on every line, whatever the working folder (`minerva` listed `~/Server`'s `chore/ports-minerva`).
- `run --reattribute` now clears a stored branch that no longer applies, instead of keeping it.
