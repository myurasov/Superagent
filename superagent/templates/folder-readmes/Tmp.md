# `Tmp/` — scratch space for in-flight work

Created lazily by any skill that needs scratch space.

Examples:

- `Tmp/audio-transcripts/<YYYY>/<MM>/` — raw audio transcript triples (txt + json + srt) before they're rolled into `Domains/<domain>/history.md` by `log-event` audio mode.
- `Tmp/ingest/<source>/<run-id>/` — raw downloads from an in-progress ingestor run.
- `Tmp/scratch/` — anything you (or a skill) want to inspect, transform, then discard.

## Hygiene

- `doctor` cleans anything in `Tmp/` older than **30 days**.
- Skills generally clean up after themselves; if they don't, that's a hygiene bug — surface it via an action signal (`@superagent there's still cruft from <skill> in Tmp/`).

## Privacy

Same gitignore as the rest of `workspace/`. Stays local.
