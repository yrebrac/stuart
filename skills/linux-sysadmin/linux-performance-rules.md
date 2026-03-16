---
name: linux-performance
description: >
    Domain knowledge for system performance monitoring, process analysis,
    resource utilisation, and bottleneck identification. Load BEFORE
    using any performance MCP tool directly. Covers CPU, memory, disk
    I/O, thermal state, and pressure stall information.
---

# Performance Monitoring

## Guide

This file covers system performance monitoring and bottleneck identification.

- **Domain Model** — USE method, resource types, bottleneck categories
- **Heuristics** — expert shortcuts for interpreting metrics and isolating bottlenecks
- **Anti-patterns** — common mistakes interpreting performance data
- **Procedures** — diagnostic workflows for health checks, sluggishness, resource consumers, I/O, thermal
- **Tools** — goal-to-tool lookup for the performance MCP server
- **Query Strategy** — bottleneck isolation, cross-server workflows, efficiency
- **Safety** — read-only tools, sysstat recommendation, cross-domain pointers
- **Quirks** — PSI availability, iostat first sample, NVMe %util, iowait meaning
- **Domain Deep Knowledge** → `linux-performance-deep-knowledge.md` for PSI interpretation, memory/disk/CPU deep-dives, methodology

## Domain Model

**USE Method** (Brendan Gregg) — for every resource, check:
- **Utilisation** — how busy is it? (CPU%, disk %util, memory used%)
- **Saturation** — is work queuing? (load average, disk queue depth, swap activity)
- **Errors** — are there failures? (I/O errors, OOM kills, ECC errors)

**Bottleneck categories:**

| Symptom | Likely bottleneck | Confirm with |
|---------|------------------|--------------|
| High load, high CPU | CPU-bound | `get_cpu_stats`, PSI cpu |
| High load, low CPU, high iowait | I/O-bound | `get_disk_io_stats`, PSI io |
| High load, low CPU, low iowait | Blocked on locks/network | Check sleeping processes |
| High swap activity | Memory pressure | `get_memory_stats`, PSI memory |
| Low load, feels slow | Single-threaded or thermal throttling | `get_cpu_frequency`, `get_thermal_stats` |

## Heuristics

1. PSI is the best indicator of resource contention — prefer over load average. `avg10 > 25%` = elevated, `> 50%` = high.
2. Load average > CPU count = overloaded. But high load with low CPU = I/O wait, not CPU.
3. Memory "available" is what matters, not "free." Linux uses almost all memory for cache. Available < 10% = warning.
4. Disk %util is misleading on NVMe (handles parallel I/O). Check queue depth (`aqu-sz`) and latency (`await`) instead.
5. CPU iowait means processes are blocked on disk. Investigate disk I/O, not CPU.
6. Always start with `check_system_health` — it gathers load, PSI, memory, and temps in one call.

## Anti-patterns

- Don't report "system is using 95% memory" as a problem — Linux aggressively caches. Check `MemAvailable`, not `MemFree`.
- Don't conclude NVMe is saturated from %util = 100% — NVMe handles parallel I/O. Check `aqu-sz` and `await`.
- Don't investigate CPU when iowait is high — iowait means the CPU is idle waiting for disk. The bottleneck is I/O.
- Don't use `ps %CPU` for real-time CPU usage — it's lifetime average, not instantaneous. Use `get_cpu_stats`.
- Don't ignore thermal throttling — a system that "feels slow" with low load may be frequency-limited due to heat.

## Procedures

### System health check
When user asks "how's my system?" or you need a baseline.

1. `check_system_health()` — load, PSI, memory, temps in one call
2. IF anything flagged: drill into that area with specific tool
3. IF nothing flagged: system is healthy, report key metrics
4. VERIFY: All resources within normal range

### Sluggish system investigation
When user reports "system feels slow" or general performance degradation.

1. `check_system_health()` — identify which resource is under pressure
2. `get_pressure_stats()` — confirm PSI indicators
3. IF high CPU pressure:
     `get_cpu_stats(per_core=True)` — which cores?
     `list_processes(sort_by="cpu")` — which process?
   IF high memory pressure:
     `get_memory_stats()` — available, swap, dirty pages
     `list_processes(sort_by="mem")` — which process?
   IF high I/O pressure:
     `get_disk_io_stats()` — which device?
     `list_processes(sort_by="io")` — which process? (requires sysstat)
   IF low load but feels slow:
     `get_cpu_frequency()` — thermal throttling?
     `get_thermal_stats()` — temps near critical?
4. VERIFY: Identified the bottleneck and the responsible process/resource
5. CROSS-DOMAIN: If top consumer is a service → systemd MCP `get_unit_status`

### Resource consumer identification
When user asks "what's eating my CPU/memory?"

1. `list_processes(sort_by="cpu")` or `sort_by="mem"` — top consumers
2. Cross-reference: is the top consumer a systemd service?
     Use systemctl MCP: `get_unit_status`
3. IF process is expected (e.g. compilation, database):
     Report and let user decide
   IF process is unexpected:
     Investigate further — what is it, who started it?
4. VERIFY: Identified the consumer and its purpose

### I/O bottleneck investigation
When disk I/O is high or an application is I/O-bound.

1. `get_disk_io_stats()` — which device is busy?
2. `list_processes(sort_by="io")` — which process? (requires sysstat)
3. IF %util high on HDD: likely saturated (single queue)
   IF %util high on NVMe: check `aqu-sz` and `await` — may not be saturated
4. CROSS-DOMAIN: `linux-block-device-rules.md` — `check_disk_usage` for capacity, `get_device_messages` for I/O errors
5. VERIFY: Identified I/O source and whether device is truly saturated

### Thermal investigation
When system is overheating or thermal throttling suspected.

1. `get_thermal_stats()` — current temps vs critical thresholds
2. `get_cpu_frequency()` — frequency below base = throttling
3. `get_cpu_stats(per_core=True)` — which cores under load?
4. IF throttling: identify heat source (CPU-bound process, poor airflow, dust)
5. VERIFY: Temperatures within safe range, no throttling

### OOM investigation
When investigating out-of-memory kills or heavy swap usage.

1. `get_memory_stats()` — swap used, dirty pages, available
2. `search_journals(grep="oom-kill", priority="warning", since="24h")` via journald MCP
3. `list_processes(sort_by="mem")` — current top consumers
4. `search_journals(grep="Out of memory", since="24h")` — detailed OOM messages
5. VERIFY: Identified OOM cause, no recurring kills
6. CROSS-DOMAIN: If OOM caused service failure → `linux-systemd-rules.md`

## Tools

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

1. Always start with `check_system_health` — it gathers multiple metrics in one call.
2. Use `list_processes(count=5)` for a quick glance; increase only if needed.
3. Specify `device` in `get_disk_io_stats` when you know which device.
4. Cross-server workflows:
   - OOM: `get_memory_stats` + journald `search_journals(grep="oom-kill")`
   - Slow service: systemd `get_unit_status` + `list_processes(filter_command="name")`
   - Disk errors + I/O: block-device `get_device_messages` + `get_disk_io_stats`
5. Be suspicious of empty results — PSI may not exist on kernels < 4.20; sysstat absence limits I/O process data.

## Safety

### Privilege

All performance tools are **read-only**. No process killing, priority changes, governor changes, or kernel tuning. No privilege escalation needed.

### High-risk operations

None — this domain is entirely observational. Fixes (killing processes, changing governors, tuning sysctl) require user action.

If sysstat is not installed: "For richer metrics (JSON output, per-core CPU, disk latency), install sysstat: `sudo dnf install sysstat`"

### Cross-references

- If I/O is the bottleneck → `linux-block-device-rules.md` "Disk health check" for hardware issues
- If a service is the top consumer → `linux-systemd-rules.md` "Service failure investigation"
- If memory pressure caused OOM → check journald for killed processes, then systemd for failed units
- If VM performance → `linux-virtual-rules.md` "VM performance investigation"
- If container resource usage → `container-runtime-rules.md` resource exhaustion workflow

## Quirks

- **PSI irq** requires kernel 5.x+ — not available on older distributions.
- **mpstat first sample** includes since-boot averages. Server uses `mpstat 1 1` for 1-second sample.
- **iostat first sample** is since-boot average. Server uses `iostat 1 2` and takes second sample.
- **ps %CPU** is lifetime average, not instantaneous. Use `get_cpu_stats` for current rates.
- **Thermal zone names** vary by hardware: `acpitz`, `x86_pkg_temp`, `TCPU`. Type file identifies each.
- **NVMe %util** can show 100% without saturation. Trust `aqu-sz` and `await`.
- **iowait is idle time** — CPU had nothing else to do while waiting for I/O. High iowait + low CPU = I/O bottleneck.

## Domain Deep Knowledge → linux-performance-deep-knowledge.md

Read when:
- Need PSI interpretation detail (thresholds, some vs full, PSI vs load average)
- Memory deep-dive (meminfo fields, OOM killer, swappiness)
- Disk I/O deep-dive (iostat metrics, schedulers, write-back)
- CPU deep-dive (governors, thermal throttling, context switches, CPU states)
- Historical analysis (sar, PCP)
- Methodology discussion (USE vs RED)
