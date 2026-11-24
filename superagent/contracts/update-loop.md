# Update and Brief Contract (fetch and tell)

<!-- Citation form: `contracts/update-loop.md`. -->

Keeping the workspace current and telling the owner about it are two jobs, done by two skills. **`update` fetches, captures and reconciles. `brief` reads the stores and reports.** Neither does the other's work: a briefing that also fetches grows with every source added and cannot be shown twice, and a fetch that also talks cannot run at night. This contract defines the two, the entry points that join them, the lock that keeps two update cycles apart, the queue that carries questions to the owner, and how a duty is scheduled.

The loop needs no runtime. In a chat session the owner types "daily update" and both skills run one after the other. With agents running on their own (`contracts/agents.md` § 9), the Updater runs `update` from a scheduled wake and the lead runs `brief`.

## 1. Glossary

| Term | Meaning |
|---|---|
| **update cycle** | One run of the `update` skill, from taking the lock to the run report. |
| **tier** | How much a cycle does: `light` or `full` (§ 2). `auto` picks one by the full tier's stamp. |
| **brief** | One run of the `brief` skill: `daily` (full or short form), `weekly` or `monthly` (§ 4). |
| **entry point** | A cadence skill that runs `update` and then `brief` and holds no step of its own (§ 5). |
| **cycle lock** | The machine-local lock one update cycle holds (§ 3). |
| **ask** | A question for the owner, written to `_memory/asks.yaml` by a run that may not ask in chat (`contracts/autonomy.md` § 7). |
| **stamp** | `context.yaml.last_runs.<key>`: when a duty last ran to its end (§ 6). |
| **due** | A duty's scheduled time has come and it has not run since. The question a scheduler asks (§ 6). |
| **overdue** | A duty is late. The nag an attended session prints at open (§ 6). |

## 2. `update`: fetch, capture, reconcile

Skill `skills/update.md`; the Updater's (`agents/updater.agent.md`).

| Step | Light | Full |
|---|---|---|
| Take the cycle lock; start marker | yes | yes |
| Conflict sweep (`tools/heal.py sweep`) | yes | yes |
| Watchers that are due: detect, subagent dispatch, harvest (`watchlist check --due`, `contracts/watchlist.md` § 7) | yes | yes |
| Drain the drop folders (`tools/inbox.py list`, then `add-source`) | yes | yes |
| Capture what a detected change points at, with provenance | yes | yes |
| Reconcile the stores: expired asks, sources index, bill and date roll-forward | yes | yes |
| File tasks: overdue bills, projects with no next step, documents about to expire | yes | yes |
| Match transactions to bills (the Bookkeeper's step) | | yes |
| Project drift, queued as asks | | yes |
| Derived views (`world rebuild`, `events_derive rebuild`) | | yes |
| Finish stamp; release the lock; one log row; the run report | yes | yes |

Binding rules:

- **It renders no briefing.** Its only output is a run report of at most twelve lines: counts, what was filed, what needs attention.
- **It asks no question in chat and sends nothing**, with the owner present or not. What needs the owner's word becomes an ask with a key, so that a finding that returns every cycle asks once. The same run therefore works unattended.
- **It is idempotent.** A second cycle on the same day fetches no watcher that was already checked (each runs by its own schedule), files no document twice, and adds no second task for the same subject.
- **It never pays, never submits, never writes upstream**, and never runs a harvest the owner pinned to `capture_mode: manual`.
- **A source that cannot be reached is reported and kept.** It never stops the cycle.
- **Tier `auto`** is `full` when the full tier's stamp is due (`cadence due update-full`), else `light`.

Drop folders are `<workspace>/Inbox/` plus every folder in `config.preferences.update.drop_folders`. A document still arriving (changed in the last 30 seconds, or a sync tool's temporary file) is left for the next cycle.

## 3. The cycle lock

`tools/lock.py`. One update cycle runs at a time on a machine.

- **A kernel lock on a machine-local file**, `~/.superagent/state/workspace-<hash>.cycle.lock`. It is never inside the workspace: a workspace may sync between machines, and a lock file there would travel to machines it says nothing about.
- **Only the cycle orchestrator takes it**: the `update` skill. A briefing reads stores and takes no lock. The watchlist keeps its own short lock around its state file.
- **`hold --who <label> --wait <seconds>`** starts a small background holder that keeps the lock across the cycle's many commands and prints a token. **`release --token <token>`** frees it. A holder ends by itself at its time limit (`--ttl`, 2 hours by default), and the kernel frees the lock when the holder dies, so no stale lock is left behind. `release --force` frees the hold of a cycle that died.
- **A second run waits, then skips.** `hold` exits 1 when the lock stayed taken for the whole wait. The second cycle then does nothing and says so.
- **Two machines** are not kept apart by this lock. **One machine fetches**: when the Updater runs as an agent of its own, its registry row is `active` and names its `host`, and `agents may-fetch` exits 1 on every other machine; the `update` skill then skips the cycle there, unless the owner says otherwise in that session. With no separate Updater, every machine may fetch, and what the watchlist records in the shared state keeps most double work away: a watcher checked today on one machine is not due on the other once the state has synced. The files two machines both write are covered by `rules/concurrent-sessions.md`.

## 4. `brief`: read the stores and report

Skill `skills/brief.md`; the lead's.

- **Stores only.** It checks no watcher, runs no harvest, calls no live MCP or CLI source and opens no network connection. It reads files under `workspace/` and runs read-only tools over them. What the update found comes from `watchlist recap --since <time>` (`contracts/watchlist.md` § 12), the update's log rows, the alerts and the ingestion log.
- **It says when the stores are stale** and names `update`. It never refreshes a store itself.
- **Empty sections stay silent.**
- **Its writes are its own bookkeeping**: `context.yaml.last_check`, the duty stamp, alert housekeeping, `surfaced_count`, one log row, and what the owner answers.
- **Kinds**: `daily` (the full form, or the short form: the delta since the last check, 30 lines at most), `weekly`, `monthly`. Each full kind stamps the duty of its entry point (`daily_update`, `weekly_review`, `monthly_review`); the short form stamps `whatsup`.
- **Waiting on your call**: every kind lists the open asks.
- **Follow-ups need the owner.** The reflection prompt, the subscription decisions and the answers to asks happen only in a session the owner is in. With nobody present the briefing is rendered and nothing is asked.

## 5. Entry points

`daily-update`, `whatsup`, `weekly-review` and `monthly-review` keep their names and triggers. Each is two steps and nothing else:

| Entry point | `update` tier | `brief` kind |
|---|---|---|
| `whatsup` | `light` | `daily`, short form |
| `daily-update` | `auto` | `daily` |
| `weekly-review` | `auto` | `weekly` |
| `monthly-review` | `auto` | `monthly` |

An entry point carries no command of its own. A test keeps fetch steps out of the entry points and out of the brief. When the update cycle is skipped because another one holds the lock, the entry point goes on to the brief.

## 6. Duty stamps and scheduling

`tools/cadence.py`; duties are defined in `contracts/agents.md` § 3.

- **Every duty has a stamp**, `context.yaml.last_runs.<key>`. The key is the duty's `schedule` when that names a key of `config.preferences.cadences`, else the duty's id with `_` for `-`. `cadence start <duty>` writes `<key>_started`; `cadence finish <duty>` writes `<key>` and removes the started marker. A started marker newer than its stamp is a run that began and never finished.
- **A schedule value** is `"HH:MM"` (daily), `"<Weekday> HH:MM"` (weekly), `"<N>th HH:MM"` (monthly) or `"every N minutes|hours|days|weeks"`.
- **Scheduling goes by the stamp, never by what day it is.** A duty is **due** when its stamp is older than its latest scheduled time. A Sunday review that was missed is due on Monday; one that ran late on Wednesday is due again the next Sunday; a run that ends at 08:41 is due again at 08:30 the next day. An `every N` schedule is due when the stamp is N old.
- **Due is not overdue.** Due is what a scheduled wake asks before it runs (`agents check <agent> <duty>`, exit 0). **Overdue** is the nag an attended session prints at open (`cadence status`): no daily check in 24 hours, no weekly review in 7 days, the month's review not run past the 1st, the hygiene passes past their interval, a watcher past its period plus a day.
- **`<key>_auto: false`** in `config.preferences` switches a duty's schedule off: it is never due.
- **Held.** `config.preferences.update.work_hours` (`"07:00-22:00"`) and `off_days` (weekday names or dates) say when a scheduled update may run. Outside them the duties whose skill is `update` are held: `agents check` exits 1. A cycle the owner starts by hand always runs.
- **The update duties.** `update-full` is due by its stamp (`update_full`, daily at 06:00 by default). `update-light` is due when there is work: a watcher is due, or a document waits in a drop folder. Its cadence value (`update_light`, every 30 minutes by default) is how often it may look.

## 7. What this contract never does

- It never lets a briefing fetch, or an update cycle brief.
- It never asks the owner a question from an update cycle. The question goes to the asks queue, and never as a blocking ask: the cycle carries on.
- It never reports a cycle to the lead. After a cycle the Updater tells each agent that asked to hear about what moved (`contracts/agents.md` § 8a), and the lead only what is broken; the briefing reads the rest from the stores.
- It never runs two update cycles at once on one machine.
- It never treats an ask as an approval: an outward action still needs its own approval at the moment of action (`contracts/autonomy.md` § 4).
- It never runs as a scheduler. A cycle runs when a session, or a wake the owner granted, runs it.
