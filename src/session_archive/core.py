"""Core logic: find expired sessions, archive them (gzip + index), list/search/restore.

Everything here is stdlib-only: pathlib, json, gzip, hashlib, datetime, shutil.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import json
import shutil
from pathlib import Path

DEFAULT_SOURCE = Path.home() / ".claude" / "projects"
DEFAULT_DEST = Path.home() / ".claude" / "session-archive"
DEFAULT_RETENTION_DAYS = 30
INDEX_FILENAME = "index.json"
CONFIG_FILENAME = "config.json"
CONFIG_DIR = Path.home() / ".config" / "session-archive"


def utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def load_config() -> dict:
    """Load user config from ~/.config/session-archive/config.json (all keys optional)."""
    path = CONFIG_DIR / CONFIG_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_paths(source=None, dest=None, retention_days=None, delete_source=None) -> dict:
    """Merge CLI args over config file over built-in defaults."""
    cfg = load_config()
    return {
        "source": Path(source or cfg.get("source") or DEFAULT_SOURCE),
        "dest": Path(dest or cfg.get("dest") or DEFAULT_DEST),
        "retention_days": int(retention_days if retention_days is not None
                              else cfg.get("retention_days", DEFAULT_RETENTION_DAYS)),
        "delete_source": (delete_source if delete_source is not None
                          else cfg.get("delete_source", True)),
    }


def find_sessions(source: Path):
    """Yield every *.jsonl session log under source, recursively."""
    source = Path(source)
    if not source.is_dir():
        return
    for path in sorted(source.rglob("*.jsonl")):
        if path.is_file():
            yield path


def age_days(path: Path) -> float:
    """Age of a session file in days, based on mtime."""
    mtime = path.stat().st_mtime
    now = _dt.datetime.now(_dt.timezone.utc).timestamp()
    return (now - mtime) / 86400.0


def is_expired(path: Path, retention_days: int) -> bool:
    return age_days(path) >= retention_days


def _extract_text(message) -> str:
    """Best-effort text extraction from a Claude Code JSONL message payload."""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
    return ""


def summarize_session(path: Path) -> dict:
    """Parse a session .jsonl and return counts, timestamps and a preview string."""
    message_count = 0
    user_count = 0
    assistant_count = 0
    first_ts = None
    last_ts = None
    preview = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message_count += 1
                rtype = rec.get("type", "")
                if rtype == "user":
                    user_count += 1
                    if not preview:
                        text = _extract_text(rec.get("message")).strip().replace("\n", " ")
                        preview = text[:240]
                elif rtype == "assistant":
                    assistant_count += 1
                ts = rec.get("timestamp")
                if isinstance(ts, str):
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts
    except OSError:
        pass
    return {
        "message_count": message_count,
        "user_count": user_count,
        "assistant_count": assistant_count,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "preview": preview,
    }


def _unique_archive_path(dest: Path, session_id: str, month: str) -> Path:
    month_dir = dest / month
    month_dir.mkdir(parents=True, exist_ok=True)
    candidate = month_dir / f"{session_id}.jsonl.gz"
    n = 1
    while candidate.exists():
        n += 1
        candidate = month_dir / f"{session_id}-{n}.jsonl.gz"
    return candidate


def archive_session(path: Path, dest: Path, source: Path, delete_source: bool = True,
                    dry_run: bool = False) -> dict:
    """Archive one session file. Returns the index entry (or a dry-run plan entry)."""
    path = Path(path)
    session_id = path.stem
    rel_path = str(path.relative_to(source)) if _is_relative(path, source) else path.name
    project = rel_path.split("/")[0] if "/" in rel_path else ""
    summary = summarize_session(path)
    month = _dt.datetime.fromtimestamp(
        path.stat().st_mtime, tz=_dt.timezone.utc).strftime("%Y-%m")

    if dry_run:
        return {
            "session_id": session_id,
            "rel_path": rel_path,
            "project": project,
            "age_days": round(age_days(path), 1),
            "original_size_bytes": path.stat().st_size,
            "dry_run": True,
            **{k: v for k, v in summary.items() if k != "preview"},
        }

    archive_path = _unique_archive_path(dest, session_id, month)
    data = path.read_bytes()
    compressed = gzip.compress(data, compresslevel=9)
    archive_path.write_bytes(compressed)

    # Verify: decompress what we wrote and compare byte-for-byte.
    with gzip.open(archive_path, "rb") as fh:
        if fh.read() != data:
            archive_path.unlink(missing_ok=True)
            raise RuntimeError(f"verification failed for {archive_path}")

    entry = {
        "session_id": session_id,
        "rel_path": rel_path,
        "project": project,
        "archive_path": str(archive_path.relative_to(dest)),
        "archived_at": utcnow_iso(),
        "source_mtime": _dt.datetime.fromtimestamp(
            path.stat().st_mtime, tz=_dt.timezone.utc).isoformat(timespec="seconds"),
        "age_days": round(age_days(path), 1),
        "original_size_bytes": len(data),
        "size_bytes": len(compressed),
        "sha256": hashlib.sha256(compressed).hexdigest(),
        "message_count": summary["message_count"],
        "user_count": summary["user_count"],
        "assistant_count": summary["assistant_count"],
        "first_ts": summary["first_ts"],
        "last_ts": summary["last_ts"],
        "preview": summary["preview"],
    }

    if delete_source:
        path.unlink()

    return entry


def _is_relative(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def load_index(dest: Path) -> list:
    path = Path(dest) / INDEX_FILENAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_index(dest: Path, entries: list) -> None:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / INDEX_FILENAME).write_text(
        json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def upsert_entry(entries: list, entry: dict) -> list:
    """Insert or replace an entry with the same session_id (latest archive wins)."""
    entries = [e for e in entries if e.get("session_id") != entry.get("session_id")]
    entries.append(entry)
    entries.sort(key=lambda e: e.get("archived_at", ""), reverse=True)
    return entries


def run_archive(source: Path, dest: Path, retention_days: int,
                delete_source: bool = True, dry_run: bool = False) -> dict:
    """Archive every expired session. Returns a report dict."""
    source, dest = Path(source), Path(dest)
    archived = []
    skipped = 0
    entries = [] if dry_run else load_index(dest)
    for path in find_sessions(source):
        if not is_expired(path, retention_days):
            skipped += 1
            continue
        entry = archive_session(path, dest, source,
                                delete_source=delete_source, dry_run=dry_run)
        if not dry_run:
            entries = upsert_entry(entries, entry)
        archived.append(entry)
    if not dry_run:
        save_index(dest, entries)
    return {
        "archived": archived,
        "skipped_fresh": skipped,
        "retention_days": retention_days,
        "dest": str(dest),
        "dry_run": dry_run,
    }


def search_index(dest: Path, query: str) -> list:
    """Case-insensitive substring search over session_id, project, rel_path, preview."""
    q = query.lower()
    hits = []
    for entry in load_index(dest):
        haystack = " ".join([
            str(entry.get("session_id", "")),
            str(entry.get("project", "")),
            str(entry.get("rel_path", "")),
            str(entry.get("preview", "")),
        ]).lower()
        if q in haystack:
            hits.append(entry)
    return hits


def find_entry(dest: Path, session_id: str):
    for entry in load_index(dest):
        if entry.get("session_id") == session_id:
            return entry
    return None


def restore_session(dest: Path, session_id: str, target: Path | None = None,
                    overwrite: bool = False) -> Path:
    """Restore an archived session back to a .jsonl file. Returns the restored path."""
    dest = Path(dest)
    entry = find_entry(dest, session_id)
    if entry is None:
        raise KeyError(f"no archived session with id {session_id!r}")
    archive_path = dest / entry["archive_path"]
    if not archive_path.exists():
        raise FileNotFoundError(f"archive file missing: {archive_path}")

    compressed = archive_path.read_bytes()
    if hashlib.sha256(compressed).hexdigest() != entry.get("sha256"):
        raise RuntimeError(f"checksum mismatch for {session_id!r}; archive may be corrupt")

    data = gzip.decompress(compressed)
    out_path = Path(target) if target else (DEFAULT_SOURCE / entry.get("rel_path", f"{session_id}.jsonl"))
    if out_path.is_dir():
        out_path = out_path / f"{session_id}.jsonl"
    if out_path.exists() and not overwrite:
        raise FileExistsError(f"{out_path} already exists (use --overwrite)")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return out_path
