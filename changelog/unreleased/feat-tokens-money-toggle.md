### Added
- A **tokens ⇄ euros toggle** beside the period control: every reading on every screen — the odometer, the daily bars, the meters, and every ranked list — switches between token counts and what those tokens would have cost at API rates. The choice is remembered between visits.
- `/api/overview` series buckets carry `cost_by_source` and `cost_by_machine`, and the per-machine folder and harness strips carry `cost_eur`, so switching measure needs no second request.

### Changed
- Ranked lists re-sort to whatever is being read, so a cheap high-volume model cannot outrank an expensive small one in euros.
- The token split keeps reading tokens whatever the toggle says: it is a breakdown of what the tokens were, and has no per-component price.
