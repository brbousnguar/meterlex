### Added

- The chart is the hit target: pointing at the reading-over-time chart shows that bucket's figures, its harness split and its top models beside the bar (a panel above the chart on a phone), with arrow keys. Closes #16.
- Where the work happened is now a map: folders as tiles sized by the reading, each in the colour of the harness that did most of the work there. The ranked list under it splits its bars the same way, and so do the machine rows.
- The latest reading from each machine sits under the period's reading, so a silent collector is visible without opening the Machines screen.
- `/api/overview`: `by_project` rows carry their harness split, and each series bucket carries `by_model` / `cost_by_model`.

### Changed

- The euro reading keeps its cents on two muted drums; it used to round to whole euros.
- Charts are drawn with plain elements: Recharts is gone, and the bundle drops from 580 kB to 181 kB.
