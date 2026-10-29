"""Explicit management entry point: python -m magi.personal.cli."""

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from magi.harness.config import load_config
from magi.personal.locking import ApplicationLock
from magi.personal.memory import MemoryService
from magi.personal.models import PersonalError
from magi.personal.plans import PlanService
from magi.personal.store import PersonalStore


def _json(value):
    def convert(item):
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError

    print(json.dumps(value, ensure_ascii=False, default=convert, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="MAGI confirmed-memory management")
    parser.add_argument("--config", type=Path, default=Path("magi.harness.toml"))
    commands = parser.add_subparsers(dest="resource", required=True)
    memory = commands.add_parser("memory")
    actions = memory.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add")
    add.add_argument("--key", required=True)
    add.add_argument("--scope", required=True)
    add.add_argument("--value", required=True)
    add.add_argument("--request-id", required=True)
    listing = actions.add_parser("list")
    listing.add_argument("--scope")
    show = actions.add_parser("show")
    show.add_argument("--id", required=True)
    update = actions.add_parser("update")
    update.add_argument("--id", required=True)
    update.add_argument("--expected-version", required=True, type=int)
    update.add_argument("--value", required=True)
    update.add_argument("--request-id", required=True)
    delete = actions.add_parser("delete")
    delete.add_argument("--id", required=True)
    delete.add_argument("--expected-version", required=True, type=int)
    delete.add_argument("--request-id", required=True)
    operation = commands.add_parser("operation")
    operation_actions = operation.add_subparsers(dest="action", required=True)
    operation_show = operation_actions.add_parser("show")
    operation_show.add_argument("--request-id", required=True)
    plan = commands.add_parser("plan")
    plan_actions = plan.add_subparsers(dest="action", required=True)
    plan_create = plan_actions.add_parser("create")
    _plan_content_arguments(plan_create)
    plan_create.add_argument("--request-id", required=True)
    plan_actions.add_parser("list")
    plan_show = plan_actions.add_parser("show")
    plan_show.add_argument("--id", required=True)
    plan_show.add_argument("--version", type=int)
    plan_update = plan_actions.add_parser("update")
    plan_update.add_argument("--id", required=True)
    plan_update.add_argument("--expected-version", required=True, type=int)
    _plan_content_arguments(plan_update)
    plan_update.add_argument("--request-id", required=True)
    return parser


def _plan_content_arguments(parser):
    parser.add_argument("--title")
    parser.add_argument("--body")
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--input", type=Path)
    parser.add_argument("--content-sha256")


def _plan_content(args):
    if args.input is None:
        if args.content_sha256 is not None:
            raise ValueError("--content-sha256 requires --input")
        return args.title, args.body, args.source
    if args.title is not None or args.body is not None or args.source:
        raise ValueError("--input cannot be combined with title/body/source arguments")
    if args.content_sha256 is None:
        raise ValueError("--input requires --content-sha256")
    # Read once, bind the exact bytes to the user's digest, then parse that same
    # in-memory snapshot. A changed file can never slip in between check and use.
    raw = args.input.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual.lower() != args.content_sha256.lower():
        raise ValueError("input SHA-256 does not match --content-sha256")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or set(data) - {"title", "body", "sources"}:
        raise ValueError("plan JSON may contain only title, body and sources")
    return data.get("title"), data.get("body"), data.get("sources", [])


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        settings = load_config(args.config)
        with ApplicationLock(settings.business_db_path.with_suffix(".lock")):
            service = MemoryService(PersonalStore(settings.business_db_path))
            if args.resource == "operation":
                result = service.get_operation(args.request_id)
            elif args.resource == "plan":
                plans = PlanService(service.store)
                if args.action == "list":
                    result = plans.list_current()
                elif args.action == "show":
                    result = plans.get(args.id, args.version)
                else:
                    title, body, sources = _plan_content(args)
                    epoch = service.current_epoch()
                    if args.action == "create":
                        result = plans.create(
                            title=title,
                            body=body,
                            sources=sources,
                            request_id=args.request_id,
                            source_epoch=epoch,
                        )
                    else:
                        result = plans.update(
                            args.id,
                            expected_version=args.expected_version,
                            title=title,
                            body=body,
                            sources=sources,
                            request_id=args.request_id,
                            source_epoch=epoch,
                        )
            elif args.action == "add":
                result = service.add(
                    key=args.key,
                    scope=args.scope,
                    value=args.value,
                    request_id=args.request_id,
                )
            elif args.action == "list":
                result = service.list_active(args.scope)
            elif args.action == "show":
                result = service.get(args.id)
            elif args.action == "update":
                result = service.update(
                    args.id,
                    expected_version=args.expected_version,
                    value=args.value,
                    request_id=args.request_id,
                )
            else:
                result = service.delete(
                    args.id,
                    expected_version=args.expected_version,
                    request_id=args.request_id,
                )
        if isinstance(result, dict):
            _json(result)
        elif isinstance(result, list):
            _json([asdict(item) for item in result])
        else:
            _json(asdict(result))
        return 0
    except PersonalError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
