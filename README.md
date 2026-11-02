# Server Monitor

A private, localhost-only dashboard that shows at a glance which lab GPU
servers are free, plus SLURM partition occupancy and your own jobs on the
`aisurrey` cluster. Pure Python 3 stdlib and a single static HTML page —
no third-party dependencies.

## Pages

- **Rack** — the four lab servers (`a6000server1`, `ulws070`, `clws00272`,
  `ulws183`), polled every 30 s: per-GPU memory bars, who is using each GPU,
  CPU load vs. cores. A GPU counts as occupied above 1 GiB memory or 15%
  utilization; a server is free only when all of its GPUs are idle.
- **aisurrey** — SLURM partitions with free GPUs counted over schedulable
  nodes only, and per-node free CPU counts. Loads on demand, never
  auto-refreshes.
- **my jobs** — your running/pending queue and the last 24 h of history
  from `sacct`. Also on demand only.

## Configuration

Everything is reached over SSH host aliases — no IPs or usernames live in
this repo.

1. **Define the aliases in `~/.ssh/config`.** The collector runs
   `ssh <alias> ...`, so each alias must carry the right `HostName`, `User`,
   and (where needed) `ProxyJump`. This setup uses `a6`, `a61`, `a62`, `a63`
   for the rack and `aisurrey` for the SLURM login node, e.g.:

   ```
   Host a61
     HostName <reachable-host>
     User <you>

   Host a6
     HostName <internal-host>
     User <you>
     ProxyJump a61
   ```

2. **Set up passwordless SSH** (keys + agent). Probes run with
   `BatchMode=yes`, so anything that would prompt for a password shows up
   as "Unreachable".

3. **Adapt the host lists if yours differ:**
   - `collector.py` — `HOSTS` (the SSH aliases to poll) and `MACHINE_NAMES`
     (alias → display name shown in the UI).
   - `slurm.py` / `jobs.py` — `SLURM_HOST` (the SLURM login-node alias).

## Run

```bash
python3 app.py          # serves http://127.0.0.1:8787
```

The server binds to 127.0.0.1 only; keep it that way — there is no auth.

## Tests

```bash
python3 -m unittest discover -s tests
```

All parsing/classification logic is pure and tested against captured probe
output; no SSH is needed to run the tests.
