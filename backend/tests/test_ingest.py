import json
from datetime import datetime

from models import ModelPrice, UsageTurn
import ingest


def test_parser_helpers_cover_invalid_values():
    assert ingest._parse_ts("2026-07-30T12:00:00Z") == datetime(2026, 7, 30, 12, 0)
    assert ingest._parse_ts("not-a-date") is None
    assert ingest._parse_ts("") is None
    assert ingest._to_int("12") == 12
    assert ingest._to_int(None) == 0


def test_claude_jsonl_skips_bad_lines_and_ingests_complete_event(tmp_path, session):
    session.add(ModelPrice(model_id="claude-sonnet-4-6", prompt=3e-6, completion=15e-6,
                            cache_read=0.3e-6, cache_write=3.75e-6, source="seed"))
    session.commit()
    path = tmp_path / "session.jsonl"
    event = {
        "type": "assistant",
        "uuid": "turn-1",
        "sessionId": "session-1",
        "timestamp": "2026-07-30T12:00:00Z",
        "cwd": "/workspace/example",
        "message": {
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 4,
                "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 1,
            },
        },
    }
    path.write_text("{bad json}\n" + json.dumps({"type": "user"}) + "\n" + json.dumps(event) + "\n")

    added, offset, errors = ingest._scan_claude_file(
        session, path, "cc:test.jsonl", 0, 0.92, datetime(2026, 7, 30, 12, 1)
    )
    session.commit()

    row = session.get(UsageTurn, 1)
    assert (added, errors) == (1, 1)
    assert offset == path.stat().st_size
    assert row.total_tokens == 17
    assert row.source == "claude-code"
    assert row.cost_eur > 0


def test_incomplete_trailing_jsonl_line_is_left_for_next_scan(tmp_path, session):
    path = tmp_path / "partial.jsonl"
    path.write_bytes(b'{"type":"assistant"')
    added, offset, errors = ingest._scan_claude_file(
        session, path, "cc:partial.jsonl", 0, 0.92, datetime.utcnow()
    )
    assert (added, offset, errors) == (0, 0, 0)
