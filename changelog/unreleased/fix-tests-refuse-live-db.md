### Fixed
- The tests refuse to start when `DB_PATH` already points at a real database: anything but unset, `:memory:` or a file under the temp folder or `/tmp` stops the run before a database is opened. A test run inside the live container can no longer write to the live database or drop its tables, as Cinelex's did on 2026-10-25; run them in a local venv or a throwaway container (#101)
