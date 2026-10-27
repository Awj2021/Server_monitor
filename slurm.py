"""Fetch SLURM cluster occupancy from the aisurrey submit node.

Unlike collector.py there is no background polling: the aisurrey page
refreshes only when first opened or when the user presses Refresh, so
data is fetched on demand and cached until the next explicit request.
Only read-only commands (sinfo) are run remotely.
"""

import re
import subprocess
import threading
import time

SLURM_HOST = "aisurrey"
SSH_TIMEOUT = 25

# One line per (node, partition); fields are space-free so .split() works.
SINFO_CMD = (
    'sinfo -N -h -O "NodeList:40,Partition:40,StateCompact:16,CPUsState:20,Gres:80,GresUsed:90"'
)

# Node state prefixes that mean "cannot run new jobs".
UNAVAILABLE_STATES = ("drain", "drng", "down", "fail", "maint", "inval", "unk", "boot")

GPU_PRETTY = {
    "nvidia_geforce_rtx_2080_ti": "RTX 2080 Ti",
    "nvidia_geforce_rtx_3090": "RTX 3090",
    "nvidia_rtx_a6000": "RTX A6000",
    "nvidia_a100-sxm4-80gb": "A100 80GB",
    "quadro_rtx_8000": "Quadro RTX 8000",
    "quadro_rtx_5000": "Quadro RTX 5000",
    "nvidia_l40s": "L40S",
    "nvidia_rtx_pro6000": "RTX PRO 6000",
    "rtxpro6000": "RTX PRO 6000",
    "nvidia_rtxpro6000": "RTX PRO 6000",
}


def pretty_gpu(raw):
    if not raw:
        return "GPU"
    if raw.lower() in GPU_PRETTY:
        return GPU_PRETTY[raw.lower()]
    words = re.split(r"[_\s]+", raw.lower().replace("nvidia", " ").replace("geforce", " "))
    out = []
    for w in words:
        if not w:
            continue
        out.append(w.upper() if re.fullmatch(r"[a-z]*\d[\w-]*|rtx|gtx|sxm|nvl|pcie", w) else w.capitalize())
    return " ".join(out) or raw


def _parse_gres(text):
    """'gpu:nvidia_rtx_a6000:2(S:0-1)' -> ('nvidia_rtx_a6000', 2)."""
    m = re.search(r"gpu:([^:(,]+):(\d+)", text)
    if m:
        return m.group(1), int(m.group(2))
    m = re.search(r"gpu:(\d+)", text)
    if m:
        return None, int(m.group(1))
    return None, 0


def node_available(state):
    base = state.rstrip("*~#!%$@^+-").lower()
    return not any(base.startswith(s) for s in UNAVAILABLE_STATES)


def parse_sinfo_output(text):
    """Group sinfo -N lines into partitions with per-node GPU occupancy."""
    partitions = {}
    order = []
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) != 6:
            continue
        node, part, state, cpus, gres, gres_used = tokens
        is_default = part.endswith("*")
        part = part.rstrip("*")
        try:
            cpu_alloc, cpu_idle, cpu_other, cpu_total = (int(x) for x in cpus.split("/"))
        except ValueError:
            continue
        gtype, gtotal = _parse_gres(gres)
        _, gused = _parse_gres(gres_used)
        available = node_available(state)
        if part not in partitions:
            partitions[part] = {"name": part, "default": is_default, "nodes": []}
            order.append(part)
        partitions[part]["default"] = partitions[part]["default"] or is_default
        partitions[part]["nodes"].append({
            "name": node,
            "state": state,
            "available": available,
            "cpus_alloc": cpu_alloc,
            "cpus_idle": cpu_idle,
            "cpus_total": cpu_total,
            "gpu_type": gtype,
            "gpus_total": gtotal,
            "gpus_used": gused,
            "gpus_free": max(0, gtotal - gused) if available else 0,
        })

    result = []
    for name in order:
        p = partitions[name]
        avail_nodes = [n for n in p["nodes"] if n["available"]]
        avail_total = sum(n["gpus_total"] for n in avail_nodes)
        used = sum(min(n["gpus_used"], n["gpus_total"]) for n in avail_nodes)
        free = sum(n["gpus_free"] for n in avail_nodes)
        offline = sum(n["gpus_total"] for n in p["nodes"] if not n["available"])
        types = [n["gpu_type"] for n in p["nodes"] if n["gpu_type"]]
        if not avail_total:
            status = "down"
        elif free > 0:
            status = "free"
        else:
            status = "full"
        result.append({
            "name": p["name"],
            "default": p["default"],
            "gpu_type": pretty_gpu(types[0] if types else None),
            "status": status,
            "gpus_free": free,
            "gpus_used": used,
            "gpus_avail_total": avail_total,
            "gpus_offline": offline,
            "nodes": p["nodes"],
        })
    return result


def fetch_slurm():
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", SLURM_HOST, SINFO_CMD],
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
    partitions = parse_sinfo_output(result.stdout)
    if not partitions:
        return None, "sinfo returned no partitions"
    return partitions, None


class SlurmCache:
    """On-demand cache: fetched on first request or explicit refresh only."""

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = None

    def get(self, force=False):
        with self._lock:
            if self._snapshot is not None and not force:
                return self._snapshot
            partitions, error = fetch_slurm()
            if partitions is not None:
                self._snapshot = {"updated": time.time(), "partitions": partitions, "error": None}
            elif self._snapshot is not None:
                # keep stale data visible, but report the failed refresh
                self._snapshot = dict(self._snapshot, error=error)
            else:
                return {"updated": None, "partitions": [], "error": error}
            return self._snapshot
