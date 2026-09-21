### Changed

- Claude Code usage counts toward a repository instead of the raw folder a reply ran in: folders roll up to the nearest `.git`, worktrees (also gone `<repo>-wt/<name>` ones) to their main checkout, and dependency checkouts to the project using them. 281 folder keys became 54 projects on the Mac mini's Server sessions.
- A session started in a parent repository such as `~/Server` now charges each reply to the repository its tool calls worked on (files read or edited, `cd` targets), and the replies after it stay there until it works elsewhere. The `~/Server` share of those sessions fell from 31% to 21%.

### Added

- Each Claude Code reply stores its git branch; `/api/spend` returns `by_branch` and each project in `/api/overview` lists its top branches. A `hash` machine hashes branch names.
- `meterlex_collector.py run --reattribute` re-reads every transcript and replaces the project and branch the hub stored; older rows, whose transcripts are gone, are rolled up by each machine (see the fix below).
