#!/usr/bin/env python3
"""sysmon.py - optional system-monitor probes: CPU, RAM, GPU/VRAM. Stdlib only, forever.

One job: `snapshot()` returns a small dict of current machine metrics, with every field it
cannot measure set to None - it never raises and never blocks longer than its short probe
interval plus the nvidia-smi timeout. Consumed live by console.py's /live/system.json
endpoint (opt-in via console.json monitor.enabled) and printable from the CLI.

The samples are deliberately EPHEMERAL: they exist only on the live endpoint, never in the
journal, the record, or any built surface - a machine's load average is telemetry of the
moment, not history worth keeping, and a committed stat would name the machine.

Per-OS sources (nothing installed, nothing imported beyond the stdlib):
  Linux    /proc/meminfo, /proc/stat (two reads across a short interval)
  Windows  GlobalMemoryStatusEx and GetSystemTimes via ctypes
  macOS    sysctl hw.memsize + vm_stat; CPU shown as 1-minute load average only
  GPU      nvidia-smi when present (Linux and Windows ship it with the driver); Apple
           Silicon exposes no unprivileged GPU counters, so macOS honestly reports none
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

GPU_TIMEOUT_SECONDS = 2.0
CPU_PROBE_INTERVAL = 0.2


# --------------------------------------------------------------------------- memory

def _memory_windows() -> dict | None:
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return None
    total = int(stat.ullTotalPhys)
    return {"total": total, "used": total - int(stat.ullAvailPhys)}


def _memory_linux() -> dict | None:
    fields = {}
    with open("/proc/meminfo", encoding="ascii") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            fields[key.strip()] = rest
    def kb(key):
        m = re.search(r"(\d+)", fields.get(key, ""))
        return int(m.group(1)) * 1024 if m else None
    total, avail = kb("MemTotal"), kb("MemAvailable")
    if total is None:
        return None
    return {"total": total, "used": total - avail if avail is not None else None}


def _memory_macos() -> dict | None:
    out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                         timeout=GPU_TIMEOUT_SECONDS, check=False).stdout.strip()
    total = int(out) if out.isdigit() else None
    if total is None:
        return None
    vm = subprocess.run(["vm_stat"], capture_output=True, text=True,
                        timeout=GPU_TIMEOUT_SECONDS, check=False).stdout
    page = re.search(r"page size of (\d+)", vm)
    free = re.search(r"Pages free:\s+(\d+)", vm)
    inactive = re.search(r"Pages inactive:\s+(\d+)", vm)
    used = None
    if page and free:
        unclaimed = int(free.group(1)) + (int(inactive.group(1)) if inactive else 0)
        used = total - unclaimed * int(page.group(1))
    return {"total": total, "used": used}


# --------------------------------------------------------------------------- cpu

def _cpu_percent_windows(interval: float) -> float | None:
    import ctypes

    def times():
        idle, kernel, user = (ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64())
        if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        # kernel time INCLUDES idle time in this API.
        return idle.value, kernel.value + user.value

    a = times()
    time.sleep(interval)
    b = times()
    if not a or not b:
        return None
    idle_delta, busy_total = b[0] - a[0], b[1] - a[1]
    if busy_total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (busy_total - idle_delta) / busy_total))


def _cpu_percent_linux(interval: float) -> float | None:
    def sample():
        with open("/proc/stat", encoding="ascii") as fh:
            parts = fh.readline().split()
        if parts[:1] != ["cpu"]:
            return None
        values = [int(x) for x in parts[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
        return idle, sum(values)

    a = sample()
    time.sleep(interval)
    b = sample()
    if not a or not b or b[1] == a[1]:
        return None
    idle_delta, total_delta = b[0] - a[0], b[1] - a[1]
    return max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta))


def _load1() -> float | None:
    if hasattr(os, "getloadavg"):
        try:
            return round(os.getloadavg()[0], 2)
        except OSError:
            return None
    return None


# --------------------------------------------------------------------------- gpu

def _gpu() -> dict | None:
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=GPU_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0 or not res.stdout.strip():
        return None
    first = res.stdout.strip().splitlines()[0]
    try:
        util, used_mib, total_mib = [float(x.strip()) for x in first.split(",")[:3]]
    except (ValueError, IndexError):
        return None
    return {"util_percent": util,
            "vram_used": int(used_mib * 1024 * 1024),
            "vram_total": int(total_mib * 1024 * 1024)}


# --------------------------------------------------------------------------- snapshot

def snapshot(interval: float = CPU_PROBE_INTERVAL) -> dict:
    """One sample of the machine. Every probe is individually fail-open: a field that
    cannot be measured on this platform is None, never an exception."""
    out = {"os": sys.platform, "cpu_percent": None, "load1": None, "ram": None, "gpu": None}
    try:
        if sys.platform == "win32":
            out["ram"] = _memory_windows()
            out["cpu_percent"] = _cpu_percent_windows(interval)
        elif sys.platform.startswith("linux"):
            out["ram"] = _memory_linux()
            out["cpu_percent"] = _cpu_percent_linux(interval)
            out["load1"] = _load1()
        elif sys.platform == "darwin":
            out["ram"] = _memory_macos()
            out["load1"] = _load1()
    except Exception:
        pass
    try:
        out["gpu"] = _gpu()
    except Exception:
        pass
    return out


def main(argv: list | None = None) -> int:
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
