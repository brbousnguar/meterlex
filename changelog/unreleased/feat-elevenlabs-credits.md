### Added
- ElevenLabs credits on the Quota screen: a third **ElevenLabs** view beside Week and 5 hours shows the month's credits used against the month gone, the pace, the day the credits run out at this rate or where they land at the reset, what a day can spend to last, and credits by day. The collector reads ElevenLabs' own API every 15 minutes with `credits --elevenlabs-env FILE`; the key never leaves that machine (#73)
- `GET /api/credits`, and an optional `credits` list on `/api/ingest` (#73)
