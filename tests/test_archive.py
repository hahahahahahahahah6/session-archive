"""Smoke tests for session-archive. stdlib-only (unittest)."""

import gzip
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from session_archive import core
from session_archive.cli import main as cli_main

DAY = 86400


def make_session(path: Path, age_days: float, messages: int = 4):
    """Write a fake Claude Code session .jsonl and backdate its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(messages):
        role = "user" if i % 2 == 0 else "assistant"
        content = f"debugging the login flow step {i}" if role == "user" else f"ack {i}"
        lines.append(json.dumps({
            "type": role,
            "message": {"role": role, "content": [{"type": "text", "text": content}]},
            "timestamp": "2026-09-01T10:00:00Z",
            "sessionId": path.stem,
        }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    old = time.time() - age_days * DAY
    os.utime(path, (old, old))
    return path


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "projects"
        self.dest = self.root / "archive"

    def tearDown(self):
        self.tmp.cleanup()

    def test_detect_expired_sessions(self):
        """Files older than retention are expired; fresh ones are not."""
        old = make_session(self.source / "projA" / "sess-old.jsonl", age_days=45)
        fresh = make_session(self.source / "projA" / "sess-fresh.jsonl", age_days=3)
        self.assertTrue(core.is_expired(old, 30))
        self.assertFalse(core.is_expired(fresh, 30))

    def test_retention_days_config_respected(self):
        """A custom retention window changes what counts as expired."""
        mid = make_session(self.source / "projA" / "sess-mid.jsonl", age_days=10)
        self.assertTrue(core.is_expired(mid, 7))
        self.assertFalse(core.is_expired(mid, 30))

    def test_archive_creates_gzip_and_index(self):
        """Archiving writes a verified .jsonl.gz and an index entry with counts."""
        sess = make_session(self.source / "projA" / "sess1.jsonl", age_days=40, messages=6)
        report = core.run_archive(self.source, self.dest, 30, delete_source=False)
        self.assertEqual(len(report["archived"]), 1)
        entry = report["archived"][0]
        gz = self.dest / entry["archive_path"]
        self.assertTrue(gz.exists())
        self.assertEqual(gzip.decompress(gz.read_bytes()), sess.read_bytes())
        self.assertEqual(entry["message_count"], 6)
        self.assertEqual(entry["user_count"], 3)
        self.assertEqual(entry["assistant_count"], 3)
        self.assertIn("login flow", entry["preview"])
        index = core.load_index(self.dest)
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0]["session_id"], "sess1")

    def test_archive_deletes_source_by_default(self):
        """Default archive removes the original after verified backup."""
        sess = make_session(self.source / "projA" / "sess2.jsonl", age_days=40)
        core.run_archive(self.source, self.dest, 30, delete_source=True)
        self.assertFalse(sess.exists())

    def test_no_delete_source_keeps_original(self):
        """--no-delete-source leaves the original file in place."""
        sess = make_session(self.source / "projA" / "sess3.jsonl", age_days=40)
        core.run_archive(self.source, self.dest, 30, delete_source=False)
        self.assertTrue(sess.exists())

    def test_fresh_sessions_are_skipped(self):
        """Sessions younger than retention are never archived."""
        make_session(self.source / "projA" / "sess-fresh.jsonl", age_days=2)
        report = core.run_archive(self.source, self.dest, 30)
        self.assertEqual(report["archived"], [])
        self.assertEqual(report["skipped_fresh"], 1)
        self.assertEqual(core.load_index(self.dest), [])

    def test_dry_run_changes_nothing(self):
        """dry-run reports the plan but writes no archive, index, or deletion."""
        sess = make_session(self.source / "projA" / "sess4.jsonl", age_days=40)
        report = core.run_archive(self.source, self.dest, 30,
                                  delete_source=True, dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(len(report["archived"]), 1)
        self.assertTrue(sess.exists())
        self.assertFalse((self.dest / core.INDEX_FILENAME).exists())
        self.assertEqual(list(self.dest.rglob("*.gz")), [])

    def test_search_finds_keyword(self):
        """search matches preview text, project, and session id (case-insensitive)."""
        make_session(self.source / "myproj" / "sess5.jsonl", age_days=40)
        core.run_archive(self.source, self.dest, 30)
        self.assertEqual(len(core.search_index(self.dest, "LOGIN FLOW")), 1)
        self.assertEqual(len(core.search_index(self.dest, "myproj")), 1)
        self.assertEqual(len(core.search_index(self.dest, "sess5")), 1)
        self.assertEqual(core.search_index(self.dest, "no-such-thing"), [])

    def test_restore_round_trip(self):
        """restore reproduces the original .jsonl byte-for-byte (checksum verified)."""
        sess = make_session(self.source / "projA" / "sess6.jsonl", age_days=40)
        original = sess.read_bytes()
        core.run_archive(self.source, self.dest, 30, delete_source=True)
        target = self.root / "restored" / "sess6.jsonl"
        out = core.restore_session(self.dest, "sess6", target=target)
        self.assertEqual(out.read_bytes(), original)

    def test_rearchive_same_session_updates_index_once(self):
        """Archiving the same session id twice keeps a single index entry."""
        sess = make_session(self.source / "projA" / "sess7.jsonl", age_days=40)
        core.run_archive(self.source, self.dest, 30, delete_source=False)
        core.run_archive(self.source, self.dest, 30, delete_source=False)
        index = core.load_index(self.dest)
        self.assertEqual(len(index), 1)

    def test_cli_end_to_end(self):
        """CLI archive/list/search/restore round-trip through the real entrypoint."""
        make_session(self.source / "projB" / "sess8.jsonl", age_days=40)
        rc = cli_main(["archive", "--source", str(self.source), "--dest", str(self.dest),
                       "--retention-days", "30", "--no-delete-source"])
        self.assertEqual(rc, 0)
        rc = cli_main(["list", "--dest", str(self.dest), "--json"])
        self.assertEqual(rc, 0)
        rc = cli_main(["search", "login", "--dest", str(self.dest)])
        self.assertEqual(rc, 0)
        target = self.root / "cli-restored.jsonl"
        rc = cli_main(["restore", "sess8", "--dest", str(self.dest),
                       "--target", str(target)])
        self.assertEqual(rc, 0)
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
