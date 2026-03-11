#!/usr/bin/env python3
"""
Stuart — Performance Monitoring MCP Server

Exposes system performance and resource monitoring as MCP tools for
Claude Code. Wraps procfs/sysfs kernel interfaces and standard Linux
CLI tools. All tools are read-only.

Usage:
    python3 performance_mcp.py

Tested on:
    - Fedora 43, procps-ng 4.x, sysstat 12.x, Python 3.14

Underlying tools (phased):
    Phase 1: /proc/*, /sys/*, ps, free (procps-ng — always available)
             mpstat, iostat, pidstat (sysstat — optional, JSON output)
    Phase 2: sensors (lm_sensors — optional, JSON output)
             sar, sadf (sysstat — optional, historical data)

Argument tier decisions (see docs/TOOL_CONVENTION.md):
    Tier 1 (exposed as params):
        ps: sort field, count, user filter, command filter
        mpstat: per-core toggle
        iostat: device filter
        pidstat: pid filter, resource type
        /proc/pressure: resource selector
    Tier 2 (param or separate tool):
        mpstat -P ALL — per-core vs summary (param: per_core)
    Tier 3 (handled internally):
        ps: --no-headers, -o format string
        mpstat/iostat/pidstat: -o JSON, interval/count
        free: -b (bytes for calculation)
    Tier 4 (omitted):
        Process management: kill, renice, nice, taskset
        CPU tuning: cpupower frequency-set, turbostat
        Kernel tuning: sysctl -w
        Continuous modes: top, iotop, watch
        Any write or state-changing operations
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from privilege import PrivilegeHelper
from tool_check import ToolCache

server = FastMCP(
    name="sysops-performance",
    instructions=(
        "Monitor system performance: CPU, memory, disk I/O, processes, "
        "thermal state, and pressure stall information. All tools are "
        "read-only."
    ),
)

# ── ToolCache instances ────────────────────────────────────────────
# Phase 1 — always available (procps-ng)
_tools: dict[str, ToolCache] = {
    "ps": ToolCache("ps", "/usr/bin/ps", ["--version"], ["--help"]),
    "free": ToolCache("free", "/usr/bin/free", ["--version"], ["--help"]),
    "vmstat": ToolCache("vmstat", "/usr/bin/vmstat", ["--version"], ["--help"]),
}

# Phase 1 — optional (sysstat), provides JSON output
_OPTIONAL_TOOLS = {
    "mpstat": ("/usr/bin/mpstat", ["-V"], []),
    "iostat": ("/usr/bin/iostat", ["-V"], []),
    "pidstat": ("/usr/bin/pidstat", ["-V"], []),
}

for _name, (_path, _vargs, _hargs) in _OPTIONAL_TOOLS.items():
    _tools[_name] = ToolCache(_name, _path, _vargs, _hargs)

# Phase 2 — optional (lm_sensors)
_tools["sensors"] = ToolCache(
    "sensors", "/usr/bin/sensors", ["--version"], ["--help"]
)

_PACKAGE_HINTS = {
    "mpstat": "sysstat",
    "iostat": "sysstat",
    "pidstat": "sysstat",
    "sensors": "lm_sensors",
}

_priv = PrivilegeHelper()


# ── Shared runner ──────────────────────────────────────────────────

def _run_cmd(
    tool_key: str,
    args: list[str],
    max_lines: int = 200,
    timeout: int = 30,
) -> str:
    """Run a monitoring command. Returns stdout or error message."""
    info = _tools[tool_key].info()
    if not info.get("exists"):
        pkg = _PACKAGE_HINTS.get(tool_key, "")
        hint = f" Install with: sudo dnf install {pkg}" if pkg else ""
        return f"Error: {tool_key} is not installed.{hint}"

    cmd = [info["path"]] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Error: {tool_key} timed out after {timeout}s."
    except FileNotFoundError:
        return f"Error: {tool_key} not found at {info['path']}."

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if result.stdout.strip():
            output = result.stdout.strip()
            if stderr:
                output += f"\n\n[stderr]: {stderr}"
        else:
            return f"Error from {tool_key}: {stderr or '(no output)'}"
    else:
        output = result.stdout or result.stderr or "(no output)"

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return output.strip()


# ── Kernel file readers ────────────────────────────────────────────

def _read_proc(path: str) -> str | None:
    """Read a /proc file. Returns contents or None on error."""
    try:
        return Path(path).read_text()
    except (OSError, PermissionError):
        return None


def _parse_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into dict of field -> value in bytes."""
    text = _read_proc("/proc/meminfo")
    if not text:
        return {}
    result = {}
    for line in text.strip().split("\n"):
        match = re.match(r"^(\S+):\s+(\d+)\s*kB", line)
        if match:
            result[match.group(1)] = int(match.group(2)) * 1024
        elif re.match(r"^(\S+):\s+(\d+)$", line):
            m = re.match(r"^(\S+):\s+(\d+)$", line)
            result[m.group(1)] = int(m.group(2))
    return result


def _parse_loadavg() -> dict:
    """Parse /proc/loadavg."""
    text = _read_proc("/proc/loadavg")
    if not text:
        return {}
    parts = text.strip().split()
    running, total = parts[3].split("/")
    return {
        "load_1m": float(parts[0]),
        "load_5m": float(parts[1]),
        "load_15m": float(parts[2]),
        "running_threads": int(running),
        "total_threads": int(total),
    }


def _parse_uptime() -> dict:
    """Parse /proc/uptime."""
    text = _read_proc("/proc/uptime")
    if not text:
        return {}
    parts = text.strip().split()
    total_seconds = float(parts[0])
    days = int(total_seconds // 86400)
    hours = int((total_seconds % 86400) // 3600)
    minutes = int((total_seconds % 3600) // 60)
    return {
        "total_seconds": total_seconds,
        "uptime": f"{days}d {hours}h {minutes}m",
    }


def _parse_pressure(resource: str) -> dict | None:
    """Parse a single /proc/pressure/<resource> file."""
    text = _read_proc(f"/proc/pressure/{resource}")
    if not text:
        return None
    result = {}
    for line in text.strip().split("\n"):
        parts = line.split()
        if not parts:
            continue
        label = parts[0]  # "some" or "full"
        metrics = {}
        for part in parts[1:]:
            key, val = part.split("=")
            metrics[key] = float(val) if "." in val else int(val)
        result[label] = metrics
    return result


def _format_bytes(n: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} PiB"


def _parse_cpu_stat() -> list[dict]:
    """Parse /proc/stat for CPU time counters. Returns list of CPU dicts."""
    text = _read_proc("/proc/stat")
    if not text:
        return []
    cpus = []
    for line in text.strip().split("\n"):
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        name = parts[0]
        vals = [int(v) for v in parts[1:]]
        # Fields: user, nice, system, idle, iowait, irq, softirq, steal
        fields = ["user", "nice", "system", "idle", "iowait",
                  "irq", "softirq", "steal"]
        entry = {"cpu": name}
        for i, field in enumerate(fields):
            entry[field] = vals[i] if i < len(vals) else 0
        cpus.append(entry)
    return cpus


def _parse_diskstats() -> list[dict]:
    """Parse /proc/diskstats. Returns list of device dicts."""
    text = _read_proc("/proc/diskstats")
    if not text:
        return []
    devices = []
    for line in text.strip().split("\n"):
        parts = line.split()
        if len(parts) < 14:
            continue
        name = parts[2]
        # Include whole disks and device-mapper. Exclude partitions,
        # loop devices, and zram (compressed swap).
        if re.match(r"^(sd[a-z]+|nvme\d+n\d+|dm-\d+)$", name) and not name.startswith(("loop", "zram")):
            devices.append({
                "device": name,
                "reads_completed": int(parts[3]),
                "reads_merged": int(parts[4]),
                "sectors_read": int(parts[5]),
                "read_ms": int(parts[6]),
                "writes_completed": int(parts[7]),
                "writes_merged": int(parts[8]),
                "sectors_written": int(parts[9]),
                "write_ms": int(parts[10]),
                "io_in_progress": int(parts[11]),
                "io_ms": int(parts[12]),
                "weighted_io_ms": int(parts[13]),
            })
    return devices


# ── Standard tools ─────────────────────────────────────────────────

@server.tool()
def tool_info() -> str:
    """Return version and availability for all performance monitoring tools.

    Call at session start to see which tools are installed. Core tools
    (ps, free, vmstat) are always available. sysstat tools (mpstat,
    iostat, pidstat) provide richer JSON output when installed.
    """
    result = {}
    for name, cache in sorted(_tools.items()):
        info = cache.info()
        result[name] = {
            "exists": info.get("exists", False),
            "path": info.get("path"),
            "version": info.get("version_raw"),
        }
    # Note kernel data sources (always available, no tool needed)
    result["_kernel_sources"] = {
        "/proc/pressure": Path("/proc/pressure").is_dir(),
        "/proc/meminfo": Path("/proc/meminfo").exists(),
        "/proc/stat": Path("/proc/stat").exists(),
        "/proc/loadavg": Path("/proc/loadavg").exists(),
        "/proc/diskstats": Path("/proc/diskstats").exists(),
        "/sys/class/thermal": Path("/sys/class/thermal").is_dir(),
        "/sys/devices/system/cpu/cpu0/cpufreq": Path(
            "/sys/devices/system/cpu/cpu0/cpufreq"
        ).is_dir(),
    }
    result["_privilege"] = _priv.policy_status()
    return json.dumps(result, indent=2)


@server.tool()
def read_manual(
    tool: str,
    section: str = "",
) -> str:
    """Read the man page for a performance monitoring command.

    Args:
        tool: Command name, e.g. "ps", "mpstat", "iostat", "pidstat",
              "vmstat", "free", "sensors".
        section: Section to extract, e.g. "OPTIONS", "DESCRIPTION".
                 Leave empty for full page (truncated).
    """
    if tool not in _tools:
        return (
            f"Unknown tool '{tool}'. "
            f"Available: {', '.join(sorted(_tools.keys()))}"
        )
    return _tools[tool].read_man(section=section)


# ── System overview ────────────────────────────────────────────────

@server.tool()
def check_system_health() -> str:
    """Quick system health overview in one call.

    Returns load averages, CPU count, PSI pressure indicators,
    memory summary, top thermal zone temperatures, and uptime.
    No parameters needed — always works.
    """
    sections = []

    # Uptime
    uptime = _parse_uptime()
    if uptime:
        sections.append(f"Uptime: {uptime['uptime']}")

    # Load averages
    load = _parse_loadavg()
    cpu_count = os.cpu_count() or 1
    if load:
        load_1m = load["load_1m"]
        status = ""
        if load_1m > cpu_count * 2:
            status = " !! HIGH"
        elif load_1m > cpu_count:
            status = " ! elevated"
        sections.append(
            f"Load: {load['load_1m']:.2f} / {load['load_5m']:.2f} / "
            f"{load['load_15m']:.2f}  "
            f"(CPUs: {cpu_count}){status}\n"
            f"  Threads: {load['running_threads']} running, "
            f"{load['total_threads']} total"
        )

    # PSI
    psi_lines = []
    for resource in ("cpu", "memory", "io"):
        psi = _parse_pressure(resource)
        if psi and "some" in psi:
            avg10 = psi["some"].get("avg10", 0)
            avg60 = psi["some"].get("avg60", 0)
            flag = ""
            if avg10 > 50:
                flag = " !! HIGH"
            elif avg10 > 25:
                flag = " ! elevated"
            psi_lines.append(
                f"  {resource:<8s}  some: avg10={avg10:6.2f}%  "
                f"avg60={avg60:6.2f}%{flag}"
            )
    if psi_lines:
        sections.append("PSI (Pressure Stall Information):\n"
                        + "\n".join(psi_lines))
    elif not Path("/proc/pressure").is_dir():
        sections.append("PSI: not available (kernel <4.20)")

    # Memory
    mem = _parse_meminfo()
    if mem:
        total = mem.get("MemTotal", 0)
        available = mem.get("MemAvailable", 0)
        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        swap_used = swap_total - swap_free

        avail_pct = (available / total * 100) if total else 0
        mem_flag = ""
        if avail_pct < 5:
            mem_flag = " !! CRITICAL"
        elif avail_pct < 10:
            mem_flag = " ! LOW"

        lines = [
            f"  Total: {_format_bytes(total)}  "
            f"Available: {_format_bytes(available)} "
            f"({avail_pct:.1f}%){mem_flag}",
        ]
        if swap_total > 0:
            swap_pct = (swap_used / swap_total * 100) if swap_total else 0
            swap_flag = ""
            if swap_pct > 80:
                swap_flag = " ! heavy swap"
            lines.append(
                f"  Swap: {_format_bytes(swap_used)} / "
                f"{_format_bytes(swap_total)} "
                f"({swap_pct:.1f}% used){swap_flag}"
            )
        else:
            lines.append("  Swap: none configured")
        sections.append("Memory:\n" + "\n".join(lines))

    # Thermal
    thermal_base = Path("/sys/class/thermal")
    if thermal_base.is_dir():
        zones = []
        for zone_dir in sorted(thermal_base.iterdir()):
            if not zone_dir.name.startswith("thermal_zone"):
                continue
            temp_file = zone_dir / "temp"
            type_file = zone_dir / "type"
            if not temp_file.exists():
                continue
            try:
                temp_c = int(temp_file.read_text().strip()) / 1000.0
                zone_type = type_file.read_text().strip() if type_file.exists() else zone_dir.name
                # Check for critical trip point
                crit = None
                for tp in sorted(zone_dir.glob("trip_point_*_type")):
                    if tp.read_text().strip() == "critical":
                        num = re.search(r"trip_point_(\d+)_type", tp.name)
                        if num:
                            crit_file = zone_dir / f"trip_point_{num.group(1)}_temp"
                            if crit_file.exists():
                                crit = int(crit_file.read_text().strip()) / 1000.0
                        break
                zones.append((zone_type, temp_c, crit))
            except (OSError, ValueError):
                continue

        if zones:
            # Show top 5 hottest zones
            zones.sort(key=lambda z: z[1], reverse=True)
            temp_lines = []
            for zone_type, temp_c, crit in zones[:5]:
                flag = ""
                if crit and temp_c > crit * 0.9:
                    flag = " !! NEAR CRITICAL"
                elif temp_c > 85:
                    flag = " ! hot"
                crit_str = f" (crit: {crit:.0f}°C)" if crit else ""
                temp_lines.append(
                    f"  {zone_type:<20s} {temp_c:5.1f}°C{crit_str}{flag}"
                )
            sections.append("Thermal (top 5):\n" + "\n".join(temp_lines))

    return "\n\n".join(sections) if sections else "Unable to gather system health data."


# ── Process monitoring ─────────────────────────────────────────────

@server.tool()
def list_processes(
    sort_by: str = "cpu",
    count: int = 15,
    user: str = "",
    filter_command: str = "",
) -> str:
    """List processes sorted by resource usage.

    Args:
        sort_by: Sort field — "cpu", "mem", "rss", "time". Default "cpu".
        count: Number of processes to return. Default 15.
        user: Filter to a specific user. Leave empty for all users.
        filter_command: Filter by command name substring (case-insensitive).
    """
    sort_map = {
        "cpu": "-%cpu",
        "mem": "-%mem",
        "rss": "-rss",
        "time": "-time",
    }
    sort_key = sort_map.get(sort_by, "-%cpu")

    fmt = "pid,ppid,user:12,%cpu,%mem,rss:10,stat,start_time,time:10,comm:20,args:60"
    args = [
        "-eo", fmt,
        f"--sort={sort_key}",
        "--no-headers",
    ]
    if user:
        args += ["-u", user]

    # Use generous max_lines — we truncate to count ourselves below
    output = _run_cmd("ps", args, max_lines=5000)
    if output.startswith("Error:"):
        return output

    lines = output.strip().split("\n")

    # Apply command filter if specified
    if filter_command:
        pattern = filter_command.lower()
        lines = [l for l in lines if pattern in l.lower()]

    # Truncate to requested count
    lines = lines[:count]

    if not lines:
        msg = "No matching processes found."
        if filter_command:
            msg += f" (filter: '{filter_command}')"
        if user:
            msg += f" (user: '{user}')"
        return msg

    # Add header
    header = f"{'PID':>7s} {'PPID':>7s} {'USER':<12s} {'%CPU':>5s} {'%MEM':>5s} {'RSS':>10s} {'STAT':<4s} {'START':<8s} {'TIME':>10s} {'COMMAND':<20s} ARGS"
    return header + "\n" + "\n".join(lines)


# ── CPU monitoring ─────────────────────────────────────────────────

@server.tool()
def get_cpu_stats(
    per_core: bool = False,
) -> str:
    """CPU utilisation breakdown: user, system, iowait, idle, etc.

    Uses mpstat (sysstat) for JSON output when available, otherwise
    falls back to /proc/stat with a 1-second sample.

    Args:
        per_core: Include per-core breakdown. Default: summary only.
    """
    # Try mpstat first (JSON output)
    if _tools["mpstat"].info().get("exists"):
        args = ["-o", "JSON", "1", "1"]
        if per_core:
            args = ["-P", "ALL"] + args
        output = _run_cmd("mpstat", args, timeout=10)
        if not output.startswith("Error:"):
            # Parse JSON and format
            try:
                data = json.loads(output)
                hosts = data.get("sysstat", {}).get("hosts", [])
                if hosts:
                    stats = hosts[0].get("statistics", [])
                    if stats:
                        cpus = stats[0].get("cpu-load", [])
                        lines = []
                        for cpu in cpus:
                            name = cpu.get("cpu", "all")
                            if name == "all" or per_core:
                                lines.append(
                                    f"  {name:<6s}  "
                                    f"usr={cpu.get('usr', 0):5.1f}%  "
                                    f"sys={cpu.get('sys', 0):5.1f}%  "
                                    f"iowait={cpu.get('iowait', 0):5.1f}%  "
                                    f"irq={cpu.get('irq', 0):5.1f}%  "
                                    f"soft={cpu.get('soft', 0):5.1f}%  "
                                    f"steal={cpu.get('steal', 0):5.1f}%  "
                                    f"idle={cpu.get('idle', 0):5.1f}%"
                                )
                        if lines:
                            return "CPU utilisation (1s sample):\n" + "\n".join(lines)
            except (json.JSONDecodeError, KeyError, IndexError):
                pass  # Fall through to /proc/stat

    # Fallback: /proc/stat with 1-second delta
    sample1 = _parse_cpu_stat()
    if not sample1:
        return "Error: unable to read /proc/stat."

    time.sleep(1)
    sample2 = _parse_cpu_stat()
    if not sample2:
        return "Error: unable to read /proc/stat (second sample)."

    lines = []
    for s1, s2 in zip(sample1, sample2):
        if s1["cpu"] != s2["cpu"]:
            continue
        name = s1["cpu"]
        if name != "cpu" and not per_core:
            continue

        # Calculate deltas
        fields = ["user", "nice", "system", "idle", "iowait",
                  "irq", "softirq", "steal"]
        deltas = {f: s2[f] - s1[f] for f in fields}
        total = sum(deltas.values())
        if total == 0:
            continue

        pcts = {f: deltas[f] / total * 100 for f in fields}
        label = "all" if name == "cpu" else name
        lines.append(
            f"  {label:<6s}  "
            f"usr={pcts['user'] + pcts['nice']:5.1f}%  "
            f"sys={pcts['system']:5.1f}%  "
            f"iowait={pcts['iowait']:5.1f}%  "
            f"irq={pcts['irq']:5.1f}%  "
            f"soft={pcts['softirq']:5.1f}%  "
            f"steal={pcts['steal']:5.1f}%  "
            f"idle={pcts['idle']:5.1f}%"
        )

    if not lines:
        return "Error: unable to compute CPU stats from /proc/stat."

    return "CPU utilisation (1s sample, /proc/stat):\n" + "\n".join(lines)


# ── Memory monitoring ──────────────────────────────────────────────

@server.tool()
def get_memory_stats() -> str:
    """Detailed memory breakdown from /proc/meminfo.

    Returns total, free, available, buffers, cached, swap, dirty
    pages, shared memory, anonymous pages, and hugepages — with
    human-readable sizes and percentages.
    """
    mem = _parse_meminfo()
    if not mem:
        return "Error: unable to read /proc/meminfo."

    total = mem.get("MemTotal", 0)
    if total == 0:
        return "Error: MemTotal is 0 in /proc/meminfo."

    def field(key: str, label: str = "") -> str:
        val = mem.get(key, 0)
        pct = val / total * 100 if total else 0
        lbl = label or key
        return f"  {lbl:<20s} {_format_bytes(val):>12s}  ({pct:5.1f}%)"

    lines = [
        "Memory:",
        field("MemTotal", "Total"),
        field("MemFree", "Free"),
        field("MemAvailable", "Available"),
        field("Buffers", "Buffers"),
        field("Cached", "Cached"),
        field("SReclaimable", "Slab reclaimable"),
        "",
        "Swap:",
    ]

    swap_total = mem.get("SwapTotal", 0)
    if swap_total > 0:
        swap_free = mem.get("SwapFree", 0)
        swap_used = swap_total - swap_free
        swap_cached = mem.get("SwapCached", 0)
        lines += [
            f"  {'Total':<20s} {_format_bytes(swap_total):>12s}",
            f"  {'Used':<20s} {_format_bytes(swap_used):>12s}  "
            f"({swap_used / swap_total * 100:.1f}%)",
            f"  {'Free':<20s} {_format_bytes(swap_free):>12s}",
            f"  {'Cached':<20s} {_format_bytes(swap_cached):>12s}",
        ]
    else:
        lines.append("  No swap configured")

    lines += [
        "",
        "Activity:",
        field("Dirty", "Dirty pages"),
        field("AnonPages", "Anonymous pages"),
        field("Mapped", "Mapped"),
        field("Shmem", "Shared memory"),
    ]

    # Hugepages
    hp_total = mem.get("HugePages_Total", 0)
    if hp_total > 0:
        hp_free = mem.get("HugePages_Free", 0)
        hp_size = mem.get("Hugepagesize", 0)
        lines += [
            "",
            "Hugepages:",
            f"  {'Total':<20s} {hp_total}",
            f"  {'Free':<20s} {hp_free}",
            f"  {'Page size':<20s} {_format_bytes(hp_size)}",
        ]

    return "\n".join(lines)


# ── Disk I/O monitoring ────────────────────────────────────────────

@server.tool()
def get_disk_io_stats(
    device: str = "",
) -> str:
    """Disk I/O statistics per device: IOPS, throughput, latency, utilisation.

    Uses iostat (sysstat) for detailed stats when available, otherwise
    falls back to /proc/diskstats with a 1-second sample.

    Args:
        device: Specific device name, e.g. "sda", "nvme0n1".
                Leave empty for all devices.
    """
    # Try iostat first (JSON output)
    if _tools["iostat"].info().get("exists"):
        args = ["-xd", "-o", "JSON", "1", "2"]
        if device:
            args = ["-xd", "-o", "JSON", device, "1", "2"]
        output = _run_cmd("iostat", args, timeout=15)
        if not output.startswith("Error:"):
            try:
                data = json.loads(output)
                hosts = data.get("sysstat", {}).get("hosts", [])
                if hosts:
                    stats = hosts[0].get("statistics", [])
                    # Take second sample (first is since-boot average)
                    report = stats[-1] if len(stats) > 1 else stats[0]
                    disks = report.get("disk", [])
                    if not disks:
                        return "No disk I/O data available."
                    lines = []
                    for disk in disks:
                        name = disk.get("disk_device", "?")
                        # Skip loop and zram devices unless explicitly requested
                        if not device and (name.startswith("loop") or name.startswith("zram")):
                            continue
                        lines.append(
                            f"  {name:<12s}  "
                            f"r/s={disk.get('r/s', 0):7.1f}  "
                            f"w/s={disk.get('w/s', 0):7.1f}  "
                            f"rMB/s={disk.get('rMB/s', 0):6.2f}  "
                            f"wMB/s={disk.get('wMB/s', 0):6.2f}  "
                            f"await={disk.get('r_await', 0):6.2f}ms  "
                            f"aqu-sz={disk.get('aqu-sz', 0):5.2f}  "
                            f"%util={disk.get('util', 0):5.1f}%"
                        )
                    return "Disk I/O (1s sample):\n" + "\n".join(lines)
            except (json.JSONDecodeError, KeyError, IndexError):
                pass  # Fall through to /proc/diskstats

    # Fallback: /proc/diskstats with 1-second delta
    sample1 = _parse_diskstats()
    if not sample1:
        return "Error: unable to read /proc/diskstats."

    time.sleep(1)
    sample2 = _parse_diskstats()
    if not sample2:
        return "Error: unable to read /proc/diskstats (second sample)."

    # Index by device name
    s1_map = {d["device"]: d for d in sample1}
    s2_map = {d["device"]: d for d in sample2}

    lines = []
    for name in s2_map:
        if device and name != device:
            continue
        if name not in s1_map:
            continue
        d1, d2 = s1_map[name], s2_map[name]

        reads = d2["reads_completed"] - d1["reads_completed"]
        writes = d2["writes_completed"] - d1["writes_completed"]
        # Sectors are typically 512 bytes
        read_mb = (d2["sectors_read"] - d1["sectors_read"]) * 512 / 1048576
        write_mb = (d2["sectors_written"] - d1["sectors_written"]) * 512 / 1048576
        io_ms = d2["io_ms"] - d1["io_ms"]
        util_pct = io_ms / 10  # io_ms per 1000ms sample

        lines.append(
            f"  {name:<12s}  "
            f"r/s={reads:7.1f}  "
            f"w/s={writes:7.1f}  "
            f"rMB/s={read_mb:6.2f}  "
            f"wMB/s={write_mb:6.2f}  "
            f"io_ms={io_ms:5d}  "
            f"%util={min(util_pct, 100):5.1f}%"
        )

    if not lines:
        msg = "No disk I/O data."
        if device:
            msg += f" Device '{device}' not found in /proc/diskstats."
        return msg

    return "Disk I/O (1s sample, /proc/diskstats):\n" + "\n".join(lines)


# ── PSI monitoring ─────────────────────────────────────────────────

@server.tool()
def get_pressure_stats(
    resource: str = "all",
) -> str:
    """Pressure Stall Information (PSI) for CPU, memory, I/O, and IRQ.

    PSI shows the percentage of time tasks are stalled waiting for a
    resource. avg10/avg60/avg300 are 10s/60s/300s moving averages.
    "some" = at least one task stalled; "full" = all tasks stalled.

    Args:
        resource: "cpu", "memory", "io", "irq", or "all" (default).
    """
    if not Path("/proc/pressure").is_dir():
        return "PSI not available (requires kernel 4.20+)."

    resources = ["cpu", "memory", "io", "irq"] if resource == "all" else [resource]
    sections = []

    for res in resources:
        psi = _parse_pressure(res)
        if psi is None:
            if res == "irq":
                sections.append(f"{res}: not available (requires kernel 5.x+)")
            else:
                sections.append(f"{res}: unable to read /proc/pressure/{res}")
            continue

        lines = []
        for label in ("some", "full"):
            if label not in psi:
                continue
            m = psi[label]
            lines.append(
                f"  {label:<6s}  "
                f"avg10={m.get('avg10', 0):6.2f}%  "
                f"avg60={m.get('avg60', 0):6.2f}%  "
                f"avg300={m.get('avg300', 0):6.2f}%  "
                f"total={m.get('total', 0)} µs"
            )
        sections.append(f"{res}:\n" + "\n".join(lines))

    return "\n\n".join(sections)


# ── Thermal monitoring ─────────────────────────────────────────────

@server.tool()
def get_thermal_stats() -> str:
    """System temperatures from kernel thermal zones.

    Reads /sys/class/thermal/thermal_zone* for temperatures, zone types,
    and critical trip points. No root required.
    """
    thermal_base = Path("/sys/class/thermal")
    if not thermal_base.is_dir():
        return "No thermal zones found in /sys/class/thermal/."

    zones = []
    for zone_dir in sorted(thermal_base.iterdir()):
        if not zone_dir.name.startswith("thermal_zone"):
            continue
        temp_file = zone_dir / "temp"
        type_file = zone_dir / "type"
        if not temp_file.exists():
            continue
        try:
            temp_c = int(temp_file.read_text().strip()) / 1000.0
            zone_type = (
                type_file.read_text().strip()
                if type_file.exists()
                else zone_dir.name
            )
            # Find critical trip point
            crit = None
            for tp in sorted(zone_dir.glob("trip_point_*_type")):
                try:
                    if tp.read_text().strip() == "critical":
                        num = re.search(r"trip_point_(\d+)_type", tp.name)
                        if num:
                            crit_file = (
                                zone_dir
                                / f"trip_point_{num.group(1)}_temp"
                            )
                            if crit_file.exists():
                                crit = (
                                    int(crit_file.read_text().strip()) / 1000.0
                                )
                        break
                except (OSError, ValueError):
                    continue
            zones.append((zone_dir.name, zone_type, temp_c, crit))
        except (OSError, ValueError):
            continue

    if not zones:
        return "No thermal data available."

    lines = [
        f"  {ztype:<24s} {temp:5.1f}°C"
        + (f"  (crit: {crit:.0f}°C)" if crit else "")
        for _, ztype, temp, crit in zones
    ]
    return "Thermal zones:\n" + "\n".join(lines)


# ── CPU frequency ──────────────────────────────────────────────────

@server.tool()
def get_cpu_frequency() -> str:
    """CPU frequency scaling state: current frequency, governor, limits.

    Reads sysfs cpufreq interface. No root required.
    """
    cpufreq_base = Path("/sys/devices/system/cpu")
    if not cpufreq_base.is_dir():
        return "CPU frequency info not available."

    cpus = []
    for cpu_dir in sorted(cpufreq_base.iterdir()):
        if not re.match(r"^cpu\d+$", cpu_dir.name):
            continue
        freq_dir = cpu_dir / "cpufreq"
        if not freq_dir.is_dir():
            continue

        def _read(name: str) -> str:
            f = freq_dir / name
            try:
                return f.read_text().strip() if f.exists() else ""
            except OSError:
                return ""

        cur_khz = _read("scaling_cur_freq")
        min_khz = _read("cpuinfo_min_freq")
        max_khz = _read("cpuinfo_max_freq")
        base_khz = _read("base_frequency")
        governor = _read("scaling_governor")
        driver = _read("scaling_driver")
        epp = _read("energy_performance_preference")

        cpus.append({
            "cpu": cpu_dir.name,
            "cur_mhz": int(cur_khz) / 1000 if cur_khz.isdigit() else None,
            "min_mhz": int(min_khz) / 1000 if min_khz.isdigit() else None,
            "max_mhz": int(max_khz) / 1000 if max_khz.isdigit() else None,
            "base_mhz": int(base_khz) / 1000 if base_khz.isdigit() else None,
            "governor": governor,
            "driver": driver,
            "epp": epp,
        })

    if not cpus:
        return "No CPU frequency data available (cpufreq interface not found)."

    # Check if all CPUs share the same governor/driver
    governors = set(c["governor"] for c in cpus if c["governor"])
    drivers = set(c["driver"] for c in cpus if c["driver"])
    epps = set(c["epp"] for c in cpus if c["epp"])

    lines = []
    if len(governors) == 1 and len(drivers) == 1:
        # Summarise shared settings, show per-CPU frequencies
        c0 = cpus[0]
        lines.append(f"Driver: {c0['driver']}  Governor: {c0['governor']}")
        if c0["epp"]:
            lines.append(f"Energy perf preference: {c0['epp']}")
        if c0["base_mhz"]:
            lines.append(f"Base: {c0['base_mhz']:.0f} MHz  "
                         f"Range: {c0['min_mhz']:.0f}–{c0['max_mhz']:.0f} MHz")
        else:
            lines.append(
                f"Range: {c0['min_mhz']:.0f}–{c0['max_mhz']:.0f} MHz"
            )

        # Show frequency spread
        freqs = [c["cur_mhz"] for c in cpus if c["cur_mhz"] is not None]
        if freqs:
            lines.append(
                f"Current: min={min(freqs):.0f} MHz  "
                f"max={max(freqs):.0f} MHz  "
                f"avg={sum(freqs) / len(freqs):.0f} MHz  "
                f"({len(freqs)} cores)"
            )
    else:
        # Per-CPU detail
        for c in cpus:
            cur = f"{c['cur_mhz']:.0f}" if c["cur_mhz"] else "?"
            lines.append(
                f"  {c['cpu']:<6s}  {cur:>5s} MHz  "
                f"gov={c['governor']}  drv={c['driver']}"
            )

    return "CPU Frequency:\n" + "\n".join(lines)


if __name__ == "__main__":
    server.run(transport="stdio")
