"""Meterlex admin commands, run inside the backend container:

    docker compose exec backend python manage.py machine-add NAME [--labels full|basename|hash]
    docker compose exec backend python manage.py machine-list
    docker compose exec backend python manage.py machine-revoke NAME
    docker compose exec backend python manage.py dedupe-legacy [--apply]
    docker compose exec backend python manage.py quota-plan PLAN --before ISO_UTC [--apply]

machine-add prints the machine's key once; give it to that machine's collector
(`meterlex_collector.py setup --hub … --key …`). Re-running it re-keys the machine.

quota-plan names the plan (e.g. default_claude_max_5x) of the quota readings
taken before BEFORE that carry none, from collectors older than 0.6.0, so a
plan change splits the past weeks where it happened.
"""
import argparse
import json
import sys
from datetime import datetime, timezone

from sqlmodel import Session, select, func

from database import engine, create_db
from models import Machine, UsageTurn
import ingest
import machines
import quota


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Meterlex admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("machine-add", help="add or re-key a machine and print its key")
    a.add_argument("name")
    a.add_argument("--labels", default="full", choices=machines.LABELS,
                   help="how its projects are stored: full path, folder name only, or a hash")
    sub.add_parser("machine-list")
    r = sub.add_parser("machine-revoke")
    r.add_argument("name")
    d = sub.add_parser("dedupe-legacy", help="fold old per-line Claude Code rows into one per reply")
    d.add_argument("--apply", action="store_true", help="change the database (default: report only)")
    q = sub.add_parser("quota-plan", help="name the plan of older quota readings that carry none")
    q.add_argument("plan", help="e.g. default_claude_max_5x")
    q.add_argument("--before", required=True, help="ISO time, UTC unless it carries an offset")
    q.add_argument("--apply", action="store_true", help="change the database (default: report only)")
    args = ap.parse_args(argv)

    create_db()
    with Session(engine) as session:
        if args.cmd == "machine-add":
            key = machines.create(session, args.name, args.labels)
            print(f"machine {args.name} (labels {args.labels})\nkey: {key}\nIt is shown once; only its hash is stored.")
        elif args.cmd == "machine-list":
            counts = dict(session.exec(select(UsageTurn.machine, func.count(UsageTurn.id)).group_by(UsageTurn.machine)).all())
            for m in session.exec(select(Machine)).all():
                print(f"{m.name:<22} labels={m.labels:<8} {'REVOKED ' if m.revoked else ''}"
                      f"turns={counts.get(m.name, 0):<8} last seen {m.last_seen_at or 'never'} "
                      f"collector {m.collector_version or '-'}")
        elif args.cmd == "machine-revoke":
            print("revoked" if machines.revoke(session, args.name) else "no such machine")
        elif args.cmd == "dedupe-legacy":
            print(json.dumps(ingest.dedupe_legacy(session, apply=args.apply), indent=1))
        elif args.cmd == "quota-plan":
            before = datetime.fromisoformat(args.before.replace("Z", "+00:00"))
            if before.tzinfo:
                before = before.astimezone(timezone.utc).replace(tzinfo=None)
            n = quota.stamp_plan(session, args.plan, before, apply=args.apply)
            print(f"{n} reading(s) before {before.isoformat()}Z {'now' if args.apply else 'would be'} on {args.plan}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
