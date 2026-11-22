# Concurrent sessions — one workspace, more than one machine

**Rule.** A workspace may be carried between machines by a folder-sync tool
and opened on more than one of them, sometimes at the same moment. Three
habits keep its files whole: **re-read a shared file just before you write
it**, **edit it in place** (never rewrite the whole file from an old read),
and **fold a reported conflict copy by hand, then delete it**. The two logs
every session appends to are split by machine, so they need none of this.

Applies on every harness, with or without a sync tool: on a single machine
the rule costs nothing.

## How a file gets hurt

A sync tool copies whole files. It cannot merge two versions of one file.

1. **Both machines write one file before a sync.** The tool keeps the newer
   write and saves the other as a conflict copy
   (`<stem>.sync-conflict-<date>-<time>-<device>.<ext>`). Nothing reads the
   copy, so its content is out of sight until someone folds it.
2. **A session opens on a machine that is behind and writes at once.** Its
   stale copy is the newer write, so it wins over fresher content.
3. **A session rewrites a whole file from an old read.** Rows another machine
   added since that read are dropped.
4. **Two machines mint the same id** ("highest in the file plus one").

## What the framework does by itself

| Protection | Where | Covers |
| --- | --- | --- |
| The on-open check writes no shared file: the once-a-day nag marker is machine-local (`~/.superagent/state/`) | `tools/cadence.py status --stamp` | case 2 |
| Each machine has a name and a beat file (`_memory/machines/<name>.json`, one writer each); the on-open check says who else wrote here lately | `tools/machines.py` | cases 1, 2 |
| New ids carry the machine's tag: `<kind>-<date>-<machine>-NNN` | `tools/next_id.py` | case 4 |
| The interaction log and the prompt log are one file per machine (`_memory/interaction-log/<machine>.yaml`, `_memory/user-queries/<machine>.jsonl`), read as one merged view | `signals log` / `signals log-show`, `log_user_query` / `log_user_query --show` | cases 1 to 3, by construction |
| The conflict sweep, at session open and in `doctor`: append-only logs merge by themselves, every other conflict copy is reported and left in place | `tools/heal.py sweep` | case 1 |
| Every scan of a registry, an index or a store skips conflict copies and files still arriving | `tools/syncnames.py` | a copy never becomes a second watcher, skill or document |

## What you do

1. **Read the presence line.** When the on-open check prints
   "`<machine>` wrote here N min ago", another machine is writing this
   workspace now or was a moment ago. Let the sync settle, and re-read any
   shared file right before you edit it. The line is as fresh as the last
   sync: no line is not a promise that nobody else is there.
2. **Re-read just before writing.** Never write a shared file from a read
   made earlier in the session. Read it, change it, write it, in one step.
3. **Edit in place.** Change the rows you mean to change and leave the rest of
   the file as it is. Never regenerate `todo.yaml`, `model-context.yaml`,
   `context.yaml`, `decisions.yaml` or an entity index from memory or from an
   old copy. The writer tools (`signals`, `cadence`, `watchlist`) already
   work this way.
4. **Log through the tools.** Interaction-log rows go through
   `uv run python -m superagent.tools.signals log`; nothing is appended to a
   log file by hand. Mint every other id with
   `uv run python -m superagent.tools.next_id`, so it carries the machine tag.
5. **Fold a reported conflict copy by hand, then delete it.** When the sweep
   reports one:
   - look at what differs: `uv run python -m superagent.tools.heal sweep --dry-run`
     prints a short diff (never for a sensitive file: open both yourself);
   - move what is only in the copy into the original, row by row. Both
     versions hold real work, so do not pick a side wholesale;
   - when two rows carry one id (a task added on each machine), give one of
     them a fresh id and, for tasks, a fresh `TASK-NNN` display number;
   - delete the copy, and say what you folded.
   Ask the user when the two versions disagree about a fact.
6. **Never fix a conflict by deleting the copy unread.** A copy that the sweep
   found identical to its original is removed for you; any other copy holds
   something the original lacks.

## Sync settings that make this safe

`uv run python -m superagent.tools.heal sync-check` reads the sync tool's
settings for this folder and names the ones that differ (`doctor` runs it):

- **Unlimited conflict copies** (`maxConflicts` -1). With a cap, the tool
  drops the oldest copy of a file, and with it the only trace of that version.
- **File versioning on**, on at least the main machine: a file the sync
  replaced or removed can be brought back.
- **`.git` kept out of the sync, without the delete flag.** Each machine keeps
  its own clone (`rules/development-tooling.md` § 6). An ignore line with the
  delete flag lets the tool remove a clone when a folder above it is removed.

Changing a sync setting is a change outside the checkout: show it and get the
user's approval first.

## Limits

- A change to `todo.yaml` or `model-context.yaml` on two machines before a
  sync still makes a conflict copy. It is reported at the next open and folded
  by hand (step 5).
- The presence line and the merged logs are as fresh as the last sync.
- Framework files sync too, so an edit to a tool on one machine is live on the
  others within seconds. Keep every saved state of a tool importable.
