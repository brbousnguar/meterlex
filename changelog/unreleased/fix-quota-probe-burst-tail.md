### Fixed
- The quota no longer goes stale when Claude Code stops less than 10 minutes after a probe. The collector remembers the latest Claude turn across runs and probes once that spend is unread and the last reading is 10 minutes old, still only within 15 minutes of real use. Collector 0.9.1 (#83)
