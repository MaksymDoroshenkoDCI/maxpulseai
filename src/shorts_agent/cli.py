import argparse
import os
import sys
from pathlib import Path

from shorts_agent.database import Database
from shorts_agent.models import ClipSegment, PublicationRequest, RightsRecord
from shorts_agent.render import render_vertical_clip


def _database_path() -> Path:
    return Path(os.getenv("SHORTS_AGENT_DATABASE") or "data/shorts-agent.sqlite3")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shorts-agent")
    commands = parser.add_subparsers(dest="command", required=True)

    source = commands.add_parser("source-add", help="Register a local, authorized media file")
    source.add_argument("path", type=Path)
    source.add_argument("--rights-basis", required=True, help="Ownership, license, or written permission")
    source.add_argument("--rights-evidence", required=True, help="Evidence reference, contract, or license URL")
    source.add_argument("--confirmed-by", required=True, help="Person confirming the rights record")

    render = commands.add_parser("render", help="Render a 9:16 clip from a registered source")
    render.add_argument("source_id")
    render.add_argument("--start", type=float, required=True)
    render.add_argument("--end", type=float, required=True)
    render.add_argument("--output", type=Path, required=True)

    approval = commands.add_parser("check-publication", help="Validate publication visibility policy")
    approval.add_argument("--visibility", choices=("private", "public", "scheduled"), default="private")
    approval.add_argument("--approved-by", help="Human approver; required for public/scheduled visibility")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    database = Database(_database_path())
    try:
        if args.command == "source-add":
            database.initialize()
            source = database.register_source(
                args.path,
                RightsRecord(args.rights_basis, args.rights_evidence, args.confirmed_by),
            )
            print(f"Registered authorized source: {source.source_id}")
        elif args.command == "render":
            database.initialize()
            source = database.get_source(args.source_id)
            output = render_vertical_clip(
                source, ClipSegment(args.start, args.end), args.output
            )
            print(f"Rendered: {output}")
        else:
            PublicationRequest(args.visibility, args.approved_by).validate()
            print(f"Publication policy passed for {args.visibility} visibility")
    except (OSError, ValueError, PermissionError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()