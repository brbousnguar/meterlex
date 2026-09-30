### Changed
- **Atlas, cut square**: the app moves from Meter Room to the house Atlas theme (warm drafting paper, Fraunces / Inter / JetBrains Mono, cards that lift) with every shape cut square: bars, tiles, controls and the drums of the reading. The harness colours are re-derived from the Atlas set and validated as a colour-blind-safe set; the token split is an ink wash. `DESIGN.md` holds the system, and the Meter Room spec is kept in `docs/meter-room.md`. Closes #31.
- **Layout**: on a desktop the tabs sit in the header beside the brand and the page has a title; the folder map runs the full width with the folder list beside the harness and machine cards. The period and unit controls, and the tabs, slide instead of jumping. On the Harnesses screen, harnesses that burned nothing share one card instead of an empty card each.
- **A new mark**: a meter dial cut from paper in six facets on the clay Atlas tile, in the app icon, the favicon and the header.

### Fixed
- A ranked bar split by harness drew its parts one under another, so the second harness hung below the bar. They now sit in a row.
- Machine cards no longer stretch to the height of the tallest one, and a machine that sent nothing no longer shows a stub of a bar.

### Removed
- The dark theme and its toggle. Atlas is a light theme; the app now reads the same whatever the system is set to.
