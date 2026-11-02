### Fixed

- The backend no longer crash-loops: a boot reprices history only when the rate card really changed, and repricing pages through the turns instead of loading them all into memory, which ran the Docker VM out of memory. Closes #85.
