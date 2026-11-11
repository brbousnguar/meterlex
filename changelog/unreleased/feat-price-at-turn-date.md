### Changed

- Each turn is priced at the rate in force on its day, not today's. The rate card pull now mirrors model-prices' price history into a new `price_points` table, and a history change alone triggers a reprice. GPT-5.6 Sol turns before its 21 Aug cut now cost the $5 / $30 launch rate instead of $4 / $20, and Claude Sonnet 5.5 turns before 7 Oct keep its first cache-read rate. Closes #95.
