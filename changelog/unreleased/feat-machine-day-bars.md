### Changed
- **Machines**: each meter now shows tokens per day (per month over a year) as bars with the busiest day named, instead of a sparkline with no numbers. Hovering a bar gives the day and its count.
- Each meter also lists the **folders** that machine worked in and the **harnesses** that ran there, so its number is explained on the spot.
- Folder names show the folder, not the whole path; the full path stays in the tooltip.

### Added
- `/api/overview` carries `projects` and `sources` per machine, so the screen is still one request.
