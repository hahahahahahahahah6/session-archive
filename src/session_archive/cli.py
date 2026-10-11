"""session-archive CLI. stdlib-only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .core import (
    DEFAULT_DEST,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SOURCE,
    age_days,
    find_sessions,
    is_expired,
    load_index,
    resolve_paths,
    restore_session,
    run_archive,
    search_index,
)


def cmd_archive(args) -> int:
    cfg = resolve_paths(args.source, args.dest, args.retention_days,
                        delete_source=(not args.no_delete_source) if args.no_delete_source else None)
    if args.delete_source:
        cfg["delete_source"] = True
    report = run_archive(cfg["source"], cfg["dest"], cfg["retention_days"],
                         delete_source=cfg["delete_source"], dry_run=args.dry_run)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    tag = "[dry-run] " if args.dry_run else ""
    if not report["archived"]:
        print(f"{tag}nothing to archive "
              f"(retention {report['retention_days']}d, {report['skipped_fresh']} fresh sessions kept).")
        return 0
    for e in report["archived"]:
        verb = "would archive" if args.dry_run else "archived"
        size_kb = e.get("original_size_bytes", 0) / 1024
        print(f"{tag}{verb} {e['session_id']} "
              f"({e.get('message_count', '?')} msgs, {size_kb:.1f} KiB, age {e.get('age_days', '?')}d)")
    action = "would delete sources" if args.dry_run else (
        "sources deleted" if cfg["delete_source"] else "sources kept")
    print(f"{tag}done: {len(report['archived'])} archived, "
          f"{report['skipped_fresh']} fresh kept, {action}. Index: {report['dest']}/index.json")
    return 0


def cmd_list(args) -> int:
    cfg = resolve_paths(dest=args.dest)
    entries = load_index(cfg["dest"])
    if args.project:
        entries = [e for e in entries if e.get("project") == args.project]
    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
        return 0
    if not entries:
        print(f"no archived sessions in {cfg['dest']}")
        return 0
    for e in entries:
        print(f"{e.get('session_id')}  msgs={e.get('message_count')}  "
              f"age={e.get('age_days')}d  archived={e.get('archived_at', '')[:10]}  "
              f"{e.get('rel_path', '')}")
        if e.get("preview"):
            print(f"    {e['preview'][:120]}")
    return 0


def cmd_search(args) -> int:
    cfg = resolve_paths(dest=args.dest)
    hits = search_index(cfg["dest"], args.query)
    if args.json:
        print(json.dumps(hits, indent=2, ensure_ascii=False))
        return 0
    if not hits:
        print(f"no matches for {args.query!r}")
        return 1
    for e in hits:
        print(f"{e.get('session_id')}  {e.get('rel_path', '')}")
        if e.get("preview"):
            print(f"    {e['preview'][:160]}")
    return 0


def cmd_restore(args) -> int:
    cfg = resolve_paths(dest=args.dest)
    try:
        out = restore_session(cfg["dest"], args.session_id,
                              target=args.target, overwrite=args.overwrite)
    except (KeyError, FileNotFoundError, FileExistsError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"restored {args.session_id} -> {out}")
    return 0


def cmd_status(args) -> int:
    cfg = resolve_paths(args.source, args.dest, args.retention_days)
    total = expired = 0
    oldest = None
    for path in find_sessions(cfg["source"]):
        total += 1
        if is_expired(path, cfg["retention_days"]):
            expired += 1
            a = age_days(path)
            oldest = a if oldest is None or a > oldest else oldest
    archived = len(load_index(cfg["dest"]))
    print(f"source:    {cfg['source']}")
    print(f"archive:   {cfg['dest']} ({archived} archived)")
    print(f"retention: {cfg['retention_days']} days")
    print(f"sessions:  {total} total, {expired} expired"
          + (f", oldest {oldest:.1f}d" if oldest is not None else ""))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="session-archive",
        description="Archive Claude Code session logs before the 30-day auto-delete "
                    "(gzip + searchable index + restore).")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("archive", help="archive expired sessions (older than retention days)")
    a.add_argument("--source", default=None, help=f"session dir (default {DEFAULT_SOURCE})")
    a.add_argument("--dest", default=None, help=f"archive dir (default {DEFAULT_DEST})")
    a.add_argument("--retention-days", type=int, default=None,
                   help=f"archive sessions at least this old (default {DEFAULT_RETENTION_DAYS})")
    a.add_argument("--dry-run", action="store_true", help="show what would be archived, change nothing")
    a.add_argument("--delete-source", action="store_true",
                   help="delete source files after archiving (default)")
    a.add_argument("--no-delete-source", action="store_true",
                   help="keep source files after archiving")
    a.add_argument("--json", action="store_true", help="machine-readable output")
    a.set_defaults(func=cmd_archive)

    l = sub.add_parser("list", help="list archived sessions")
    l.add_argument("--dest", default=None)
    l.add_argument("--project", default=None, help="filter by project directory name")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("search", help="search archived sessions (id, project, path, preview)")
    s.add_argument("query")
    s.add_argument("--dest", default=None)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

    r = sub.add_parser("restore", help="restore an archived session back to .jsonl")
    r.add_argument("session_id")
    r.add_argument("--dest", default=None)
    r.add_argument("--target", default=None,
                   help="output file or directory (default: original location under ~/.claude/projects)")
    r.add_argument("--overwrite", action="store_true")
    r.set_defaults(func=cmd_restore)

    st = sub.add_parser("status", help="show source/archive status and expired counts")
    st.add_argument("--source", default=None)
    st.add_argument("--dest", default=None)
    st.add_argument("--retention-days", type=int, default=None)
    st.set_defaults(func=cmd_status)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
