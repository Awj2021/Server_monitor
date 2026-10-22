"""Collect GPU occupation status from the lab servers over SSH.

All hosts are addressed by their ~/.ssh/config aliases; a6/a62/a63 hop
through a61 (ProxyJump), so polls are run concurrently and kept infrequent.
Every remote command is read-only.
"""

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HOSTS = ["a6", "a61", "a62", "a63"]

POLL_INTERVAL = 30  # seconds between background polls
SSH_TIMEOUT = 25  # hard cap per host (two hops can be slow)

# A GPU is "occupied" when something substantial is resident or running.
MEM_OCCUPIED_MIB = 1024
UTIL_OCCUPIED_PCT = 15

# Single round-trip probe: GPU stats, GPU processes, uptime, pid->user map.
REMOTE_SCRIPT = r"""
echo '===GPUS==='
nvidia-smi --query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits
echo '===PROCS==='
nvidia-smi --query-compute-apps=gpu_uuid,pid,used_gpu_memory --format=csv,noheader,nounits
echo '===UPTIME==='
uptime
echo '===CORES==='
nproc
echo '===USERS==='
for pid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader); do
  echo "$pid $(ps -o user= -p "$pid" 2>/dev/null)"
done
"""


def parse_probe_output(text):
    """Parse the REMOTE_SCRIPT output into {gpus, uptime, load}."""
    sections = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("===") and stripped.endswith("==="):
            current = stripped.strip("=")
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    pid_to_user = {}
    for line in sections.get("USERS", []):
        parts = line.split()
        if len(parts) >= 2:
            pid_to_user[parts[0]] = parts[1]

    uuid_to_users = {}
    for line in sections.get("PROCS", []):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            user = pid_to_user.get(parts[1])
            if user:
                uuid_to_users.setdefault(parts[0], [])
                if user not in uuid_to_users[parts[0]]:
                    uuid_to_users[parts[0]].append(user)

    gpus = []
    for line in sections.get("GPUS", []):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 6:
            continue
        try:
            gpus.append({
                "index": int(parts[0]),
                "name": parts[2],
                "memory_used": int(parts[3]),
                "memory_total": int(parts[4]),
                "utilization": int(parts[5]),
                "users": uuid_to_users.get(parts[1], []),
            })
        except ValueError:
            continue

    uptime_lines = [l.strip() for l in sections.get("UPTIME", []) if l.strip()]
    uptime = uptime_lines[0] if uptime_lines else ""
    load = None
    if "load average" in uptime:
        try:
            load = float(uptime.split("load average")[1].lstrip(":s ").split(",")[0])
        except (ValueError, IndexError):
            pass

    cores = None
    for line in sections.get("CORES", []):
        if line.strip().isdigit():
            cores = int(line.strip())
            break

    return {"gpus": gpus, "uptime": uptime, "load": load, "cores": cores}


def classify_gpu(gpu):
    if gpu["memory_used"] >= MEM_OCCUPIED_MIB or gpu["utilization"] >= UTIL_OCCUPIED_PCT:
        return "occupied"
    return "free"


def classify_server(gpu_statuses):
    """free = every GPU idle; occupied = every GPU busy; else partial."""
    if not gpu_statuses:
        return "unknown"
    if all(s == "free" for s in gpu_statuses):
        return "free"
    if all(s == "occupied" for s in gpu_statuses):
        return "occupied"
    return "partial"


def probe_host(host):
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, REMOTE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"host": host, "status": "unknown", "error": "SSH timed out", "gpus": [],
                "uptime": "", "load": None, "cores": None}
    except OSError as exc:
        return {"host": host, "status": "unknown", "error": str(exc), "gpus": [],
                "uptime": "", "load": None, "cores": None}

    if result.returncode != 0:
        error = result.stderr.strip().splitlines()
        return {
            "host": host,
            "status": "unknown",
            "error": error[-1] if error else f"ssh exited {result.returncode}",
            "gpus": [],
            "uptime": "", "load": None, "cores": None,
        }

    info = parse_probe_output(result.stdout)
    for gpu in info["gpus"]:
        gpu["status"] = classify_gpu(gpu)
    return {
        "host": host,
        "status": classify_server([g["status"] for g in info["gpus"]]),
        "gpus": info["gpus"],
        "uptime": info["uptime"],
        "load": info["load"],
        "cores": info["cores"],
        "error": None if info["gpus"] else "no GPU data in probe output",
    }


class StatusCache:
    """Polls all hosts in the background and serves the latest snapshot."""

    def __init__(self, hosts=None, interval=POLL_INTERVAL):
        self.hosts = hosts or HOSTS
        self.interval = interval
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._snapshot = {"updated": None, "polling": True, "servers": []}

    def snapshot(self):
        with self._lock:
            return dict(self._snapshot)

    def refresh_now(self):
        """Trigger an immediate poll and wait for it to land."""
        before = self.snapshot()["updated"]
        self._wake.set()
        deadline = time.time() + SSH_TIMEOUT + 5
        while time.time() < deadline:
            if self.snapshot()["updated"] != before:
                return self.snapshot()
            time.sleep(0.2)
        return self.snapshot()

    def _poll_once(self):
        with ThreadPoolExecutor(max_workers=len(self.hosts)) as pool:
            servers = list(pool.map(probe_host, self.hosts))
        with self._lock:
            self._snapshot = {
                "updated": time.time(),
                "polling": False,
                "servers": servers,
            }

    def run_forever(self):
        thread = threading.Thread(target=self._loop, daemon=True)
        thread.start()

    def _loop(self):
        while True:
            self._poll_once()
            self._wake.wait(timeout=self.interval)
            self._wake.clear()
