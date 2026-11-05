### Fixed
- OpenAI's balance no longer counts twice the spend of the day it was read on. The costs API reports a day's spend hours late, so the part spent before the reading but reported after it came off the balance again. The hub now asks the collector for that day's usage by the hour and counts only the share that came after the reading. Collector 0.9.2 (#89)
