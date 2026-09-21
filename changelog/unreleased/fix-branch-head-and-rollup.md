### Fixed

- `HEAD` is no longer stored as a branch: Claude Code records it outside a repository, and it made plain folders (`~`, `~/Dev/opensource`) look like repositories. Rows already stored with it are cleared at startup.
- History is rolled up by each machine instead of guessed by the hub: `meterlex_collector.py rollup [--apply]` resolves the machine's stored folders on its own disk (`GET /api/projects/mine`, `POST /api/projects/rename`). `manage.py rollup-projects` is gone: it treated every repository without recent activity as a plain folder and would have folded old apps into `~/Server`.
