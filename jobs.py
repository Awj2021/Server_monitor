"""Fetch the user's own SLURM jobs (queue + recent history) from aisurrey.

Same on-demand model as slurm.py: no background polling, one read-only
SSH round-trip per explicit request, cached until the next one.
"""

import re
import subprocess
import threading
import time

SLURM_HOST = "aisurrey"
SSH_TIMEOUT = 25

REMOTE_SCRIPT = (
    "echo '===QUEUE==='; "
    'squeue -u "$USER" -h -o "%i|%j|%P|%T|%M|%l|%R|%b|%C|%m|%V"; '
    "echo '===HISTORY==='; "
    "sacct -X -n -P -S now-24hours -o JobID,JobName,Partition,State,Elapsed,End,AllocTRES,ReqTRES"
)


def parse_duration(text):
    """'[D-]HH:MM:SS' / 'MM:SS' -> seconds; UNLIMITED/N-A/garbage -> None."""
    t = (text or "").strip()
    if not t or not t[0].isdigit():
        return None
    days = 0
    if "-" in t:
        d, t = t.split("-", 1)
        if not d.isdigit():
            return None
        days = int(d)
    parts = t.split(":")
    if not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    if len(nums) == 3:
        h, m, s = nums
    elif len(nums) == 2:
        h, (m, s) = 0, nums
    else:
        return None
    return ((days * 24 + h) * 60 + m) * 60 + s


def parse_gpus(tres):
    """GPU count from squeue %b ('gres/gpu:1', 'gres/gpu:a100:2', 'N/A')
    or sacct AllocTRES ('cpu=4,...,gres/gpu=1,gres/gpu:quadro_rtx_8000=1')."""
    counts = [int(n) for n in re.findall(r"gres/gpu[=:](\d+)", tres or "")]
    if counts:
        return max(counts)
    m = re.search(r"gres/gpu:[\w.-]+[=:](\d+)", tres or "")
    return int(m.group(1)) if m else 0


def parse_queue_line(line):
    fields = line.split("|")
    if len(fields) != 11:
        return None
    jobid, name, part, state, elapsed, limit, where, tres, cpus, mem, submit = (
        f.strip() for f in fields
    )
    return {
        "id": jobid,
        "name": name,
        "partition": part,
        "state": state,
        "elapsed": elapsed,
        "elapsed_s": parse_duration(elapsed),
        "limit": limit,
        "limit_s": parse_duration(limit),
        "where": where.strip("()"),
        "gpus": parse_gpus(tres),
        "cpus": cpus,
        "mem": mem,
        "submitted": submit,
    }


def parse_sacct_line(line):
    fields = line.split("|")
    if len(fields) != 8:
        return None
    jobid, name, part, state, elapsed, end, alloc, req = (f.strip() for f in fields)
    state_word = state.split()[0] if state else ""
    return {
        "id": jobid,
        "name": name,
        "partition": part,
        "state": state_word,
        "elapsed": elapsed,
        "end": end,
        "gpus": parse_gpus(alloc) or parse_gpus(req),
    }


def parse_jobs_output(text):
    section = None
    running, pending, history = [], [], []
    gpu_map = {}  # squeue %b is N/A on this cluster; sacct TRES is the truth
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("===") and stripped.endswith("==="):
            section = stripped.strip("=")
            continue
        if not stripped:
            continue
        if section == "QUEUE":
            job = parse_queue_line(stripped)
            if job:
                (running if job["state"] == "RUNNING" else pending).append(job)
        elif section == "HISTORY":
            job = parse_sacct_line(stripped)
            if job:
                if job["gpus"]:
                    gpu_map[job["id"]] = job["gpus"]
                    gpu_map.setdefault(job["id"].split("_")[0], job["gpus"])
                if job["state"] not in ("RUNNING", "PENDING", "REQUEUED"):
                    history.append(job)
    for job in running + pending:
        if not job["gpus"]:
            job["gpus"] = gpu_map.get(job["id"]) or gpu_map.get(job["id"].split("_")[0], 0)
    history.sort(key=lambda j: j["end"], reverse=True)
    return {"running": running, "pending": pending, "history": history[:30]}


def fetch_jobs():
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", SLURM_HOST, REMOTE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, "SSH to aisurrey timed out"
    except OSError as exc:
        return None, str(exc)
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        return None, lines[-1] if lines else f"ssh exited {result.returncode}"
    if "===QUEUE===" not in result.stdout:
        return None, "unexpected output from squeue"
    return parse_jobs_output(result.stdout), None


class JobsCache:
    """On-demand cache: fetched on first request or explicit refresh only."""

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = None

    def get(self, force=False):
        with self._lock:
            if self._snapshot is not None and not force:
                return self._snapshot
            jobs, error = fetch_jobs()
            if jobs is not None:
                self._snapshot = {"updated": time.time(), "error": None, **jobs}
            elif self._snapshot is not None:
                self._snapshot = dict(self._snapshot, error=error)
            else:
                return {"updated": None, "running": [], "pending": [], "history": [], "error": error}
            return self._snapshot
