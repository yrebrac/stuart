---
name: linux-performance
description: >
    Domain knowledge for system performance monitoring, process analysis,
    resource utilisation, and bottleneck identification. Load BEFORE
    using any performance MCP tool directly. Covers CPU, memory, disk
    I/O, thermal state, and pressure stall information.
---

# Performance Monitoring

## Session Start

1. Call `tool_info()` to check which tools are available
2. Note whether sysstat is installed — if not, `/proc` and `/sys` fallbacks are used (less detail)
3. If the user hasn't stated a specific problem, start with `check_system_health()`

## Common Tasks

### "Is my system healthy?"

1. `check_system_health()` — load, PSI, memory, temps in one call
2. If anything flagged: drill into that area with the specific tool

### "What's slow?" / "System feels sluggish"

1. `check_system_health()` — identify which resource is under pressure
2. `get_pressure_stats()` — confirm PSI indicators
3. Drill into the bottleneck:
   - High CPU pressure → `get_cpu_stats(per_core=True)` + `list_processes(sort_by="cpu")`
   - High memory pressure → `get_memory_stats()` + `list_processes(sort_by="mem")`
   - High I/O pressure → `get_disk_io_stats()` + `list_processes(sort_by="io")`

### "What's eating my CPU/memory?"

1. `list_processes(sort_by="cpu")` or `list_processes(sort_by="mem")`
2. Cross-reference: is the top consumer a service? Check with systemctl MCP `get_unit_status`

### "Disk I/O is high"

1. `get_disk_io_stats()` — which device is busy?
2. `list_processes(sort_by="io")` — which process? (requires sysstat for I/O sort)
3. Cross-reference with block-device MCP: `check_disk_usage` for capacity, `get_device_messages` for I/O errors

### "System is overheating"

1. `get_thermal_stats()` — current temps vs critical thresholds
2. `get_cpu_frequency()` — is the CPU throttling (frequency below base)?
3. `get_cpu_stats(per_core=True)` — which cores are under load?

### "Is my system swapping?"

1. `get_memory_stats()` — check Swap Used and Dirty pages
2. If swap is heavy: `list_processes(sort_by="mem")` to find the consumer
3. Check journald for OOM kills: use journald MCP `search_journals(grep="oom-kill")`

## Tool Selection

| Goal | Tool |
|------|------|
| Quick health check | `check_system_health` |
| Top resource consumers | `list_processes` |
| CPU utilisation breakdown | `get_cpu_stats` |
| Memory detailed breakdown | `get_memory_stats` |
| Disk I/O per device | `get_disk_io_stats` |
| Resource pressure (PSI) | `get_pressure_stats` |
| System temperatures | `get_thermal_stats` |
| CPU frequency / throttling | `get_cpu_frequency` |
| Tool availability | `tool_info` |
| Man page lookup | `read_manual` |

## Query Strategy

### Bottleneck isolation

Start broad, narrow to the bottleneck, then identify the cause:
1. `check_system_health` — which resource is stressed?
2. Specific tool for that resource — how bad is it?
3. `list_processes` — who is responsible?

### Interpreting key metrics

- **PSI** is the best indicator of resource contention — prefer over load average. `avg10 > 25%` = elevated, `> 50%` = high.
- **Load average** > CPU count = overloaded. But high load with low CPU usage = I/O wait, not CPU.
- **Memory "available"** is what matters, not "free". Cached and buffered memory is reclaimable. Available < 10% = warning.
- **Disk %util** can be misleading on NVMe (handles parallel I/O). Check queue depth (`aqu-sz`) and latency (`await`) instead.
- **CPU iowait** means processes are blocked on disk. Investigate disk I/O, not CPU.

### Cross-server workflows

- **OOM investigation**: `get_memory_stats` + journald MCP `search_journals(grep="oom-kill", priority="warning")`
- **Slow service**: systemd MCP `get_unit_status` + `list_processes(filter_command="service_name")`
- **Disk errors + I/O**: block-device MCP `get_device_messages` + `get_disk_io_stats`

### Efficiency

- Always start with `check_system_health` rather than calling individual tools — it gathers multiple metrics in one call
- Use `list_processes(count=5)` for a quick glance; increase only if needed
- Specify `device` in `get_disk_io_stats` when you know which device to investigate

## Preferences & Safety

- All tools are **read-only** — no process killing, no priority changes, no governor changes, no kernel tuning
- No privilege escalation needed for any tool
- If sysstat is not installed: "For richer metrics (JSON output, per-core CPU, disk latency), install sysstat: `sudo dnf install sysstat`"
- For detailed concepts (PSI interpretation, memory management, USE method), read REFERENCE.md in this skill directory
