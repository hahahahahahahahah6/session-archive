"""session-archive: back up Claude Code session logs before the 30-day auto-delete.

Claude Code deletes session logs in ~/.claude/projects/*.jsonl that are older
than 30 days by default. This package archives expired sessions into a local
archive directory (gzip-compressed, with a searchable JSON index) so the full
transcript is kept before anything is deleted.

stdlib-only.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
