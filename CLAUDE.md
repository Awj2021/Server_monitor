# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Goal

A private (localhost-only) website that visualizes the occupation status of four GPU servers — `a6`, `a61`, `a62`, `a63` — so the user can see at a glance which server is free and which is occupied. "Occupied" is primarily defined by GPU usage (`nvidia-smi` memory/utilization), secondarily by CPU load.

## Commands

No third-party dependencies — everything is Python 3 stdlib plus vanilla HTML/JS.

```bash
python3 app.py                        # serve the dashboard on http://127.0.0.1:8787
python3 -m unittest discover -s tests # run all tests
python3 -m unittest tests.test_collector.TestClassify  # run one test class
```

## Architecture

Three pages served by one app: "Rack" (the four lab servers, auto-polled), "aisurrey" (SLURM partitions) and "my jobs" (the user's own SLURM queue + 24 h history). The two SLURM pages are fetched only on demand — by design they must never auto-refresh.

Rack page pieces:

- `collector.py` — all remote logic. `REMOTE_SCRIPT` is a single SSH round-trip per host that emits marker-delimited sections (GPU stats, GPU processes, uptime, pid→user map); `parse_probe_output()` turns it into a dict, `classify_gpu()`/`classify_server()` apply the occupancy rules (GPU occupied at ≥1 GiB memory or ≥15% util; server free only if all GPUs free, else partial/occupied; unreachable → unknown). `StatusCache` polls all hosts concurrently on a 30 s background thread and holds the latest snapshot; `refresh_now()` wakes the loop for an immediate poll.
- `app.py` — stdlib `ThreadingHTTPServer` bound to 127.0.0.1:8787. Routes: `/` (static page), `GET /api/status` (cached snapshot), `POST /api/refresh` (blocking immediate poll), plus the same GET/POST-refresh pair for `/api/slurm` and `/api/jobs` (cached-or-first-fetch / forced fetch). All responses send `Cache-Control: no-cache`.
- `static/index.html` — single-file page (inline CSS/JS, IBM Plex via Google Fonts), two views toggled by tabs and `#rack`/`#slurm` hashes. The rack view fetches `/api/status` every 10 s; its hero is a computed plain-English sentence answering "which server is free", then one row per server in rack-elevation style with a state-colored spine, per-GPU memory bars, users per GPU, and a CPU load bar (load/cores).

aisurrey (SLURM) page:

- `slurm.py` — runs one read-only `sinfo -N -O "...Gres,GresUsed"` on the `aisurrey` alias, groups (node, partition) lines into partitions, counts free GPUs over schedulable nodes only (drain/drng/down nodes contribute `gpus_offline`, never "free"), prettifies GPU type names. `SlurmCache.get(force=)` fetches only on first request or explicit refresh — no background polling; a failed refresh keeps the stale snapshot and sets `error`.
- The slurm view loads once when the tab is first opened; after that only its Refresh button re-fetches. Partition rows reuse the rack row styling: aggregate GPUs-in-use bar plus per-node chips (green = has free GPUs, struck-through = drained/down).

my jobs page:

- `jobs.py` — one SSH round-trip running `squeue -u $USER` (queue, pending reasons) and `sacct -X -P -S now-24hours` (history + TRES). Gotcha: on this cluster `squeue %b` (tres-per-node) returns `N/A`, so GPU counts come from sacct's AllocTRES/ReqTRES via an id→gpus map (array jobs fall back to the base id before `_`). Same on-demand `JobsCache` model as `SlurmCache`.

The parsing/classification functions in `collector.py`, `slurm.py` and `jobs.py` are pure — test changes there via `tests/` with captured probe text, no SSH needed.

## Server Topology (from ~/.ssh/config)

All monitoring data is collected over SSH using the host aliases defined in `~/.ssh/config` (never hardcode IPs/usernames — the aliases carry the right `User` and `ProxyJump`):

| Alias | Real host | Notes |
|-------|-----------|-------|
| `a61` | 131.227.95.53 (ulws070.surrey.ac.uk) | Only directly reachable host; jump host for the others. 2× RTX A6000 |
| `a6`  | 131.227.165.191 (a6000server1) | `ProxyJump a61`. 2× RTX A6000 |
| `a62` | 131.227.165.151 (clws00272) | `ProxyJump a61`. 2× RTX A6000 |
| `a63` | ulws183.surrey.ac.uk | `ProxyJump a61` |

Key facts for anyone writing the collector:

- Passwordless SSH (BatchMode) works for `a61`, `a6`, `a62`. Each has 2× NVIDIA RTX A6000 (49140 MiB each); `nvidia-smi` is available.
- Because `a6`, `a62`, `a63` tunnel through `a61`, every poll of those hosts costs two SSH hops — keep polling intervals modest (tens of seconds, not sub-second) and run the per-host queries concurrently.
- `a63`'s host key has changed and its old entry in `~/.ssh/known_hosts` makes strict checking fail. The collector must treat an unreachable host as a distinct "unknown" status (not "free"), and must never auto-edit `known_hosts` or disable host key checking — that is the user's call.

## Status Collection

The canonical per-host probe (verified to work):

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 <alias> \
  'nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader; uptime'
```

Classification used by this project: a GPU with high memory use or utilization (e.g. a62 was observed at 47 GiB / 49 GiB, 50% util) means that GPU is occupied; a server is "free" only if all its GPUs are idle. Surface per-GPU detail, not just a binary per server.

## Constraints

- Private tool for one user: the web server must bind to localhost only; no auth system, no public deployment.
- Collection is read-only: only run read-only commands (`nvidia-smi`, `uptime`, `who`, etc.) on the remote servers.
