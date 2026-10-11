# session-archive

Claude Code deletes your old session logs after 30 days by default
(`~/.claude/projects/*.jsonl`). **session-archive** backs up expired sessions
into a local archive directory — gzip-compressed, with a searchable JSON index —
so the full transcript is kept *before* anything is deleted.

Stdlib-only. No dependencies.

## Install

```bash
pip install session-archive
```

## Usage

Archive every session older than the retention window (default: 30 days).
Each archive is verified byte-for-byte before the original is deleted:

```bash
# archive expired sessions (deletes sources after verified backup)
session-archive archive

# preview what would be archived, change nothing
session-archive archive --dry-run

# keep the originals too
session-archive archive --no-delete-source

# custom retention window and locations
session-archive archive --retention-days 14 \
  --source ~/.claude/projects \
  --dest ~/backups/claude-sessions
```

Browse and recover:

```bash
session-archive list                    # all archived sessions
session-archive list --project myproj   # filter by project directory
session-archive search "login flow"      # search id / project / path / preview
session-archive restore sess-abc123     # restore to ~/.claude/projects/...
session-archive restore sess-abc123 --target ./recovered.jsonl --overwrite
session-archive status                  # source vs archive counts
```

Optional config file (`~/.config/session-archive/config.json`); every key is
overridable by CLI flags:

```json
{
  "source": "/home/hao/.claude/projects",
  "dest": "/home/hao/.claude/session-archive",
  "retention_days": 30,
  "delete_source": true
}
```

Archive layout: `<dest>/YYYY-MM/<session-id>.jsonl.gz` plus `<dest>/index.json`.
Each index entry carries the session id, original path, message counts,
first/last timestamps, a text preview, and a sha256 of the compressed file —
`restore` verifies the checksum before writing.

Run it on a schedule (cron/systemd timer) or manually. The point is to run it
*before* the 30-day window closes on sessions you still care about.

## Differentiation

- **vs [session-handover](https://github.com/hahahahahahahahah6/session-handover):**
  session-handover distills a session into a compact handover packet for the
  next agent — it deliberately throws away detail. session-archive is the
  opposite: a **full-fidelity backup**. Transcripts are kept verbatim
  (gzip of the original `.jsonl`), plus a searchable index so you can find and
  restore any session later. Backup and handover are two different jobs; this
  tool does backup.

## License

MIT
