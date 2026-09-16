### Added
- **Meter Room**: the app is now built around the reading — tokens as an odometer, one meter card per machine, and separate screens for harnesses and models, over this week (Monday start), this month or this year. `DESIGN.md` holds the system.
- `GET /api/overview`: everything one screen needs for a period — totals with the input / output / cache-read / cache-write / reasoning split, the same-length period before it, breakdowns by machine, harness, model, project and origin, and a series that keeps its empty buckets.
- `GET /api/config`, so the UI can link to the rate card's own app (`PRICES_URL`) instead of rendering a local table.
- Installable as a PWA: manifest, icons, service worker, and the serving headers iOS needs.

### Changed
- Periods are counted in `LOCAL_TZ` (default `Europe/Paris`) instead of UTC: a week starts Monday 00:00 locally and a day at local midnight, so a late-evening reply counts on the day you had it.
- `period=weekly` is accepted everywhere a period is.
- Prices are read-only in Meterlex; subscriptions and the FX rate stay.
