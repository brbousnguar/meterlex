### Added
- Hermes Agent counts as a harness: the collector reads each session's usage per model from Hermes's `state.db` and from the ATIF trajectories NeMo Relay writes, and a session found in both is stored once. Hermes is plum on every chart, with no subscription (#33)
- `setup --path SOURCE=DIR` points a collector source at another folder, and `install` sets up a systemd user timer on Linux (#33)
