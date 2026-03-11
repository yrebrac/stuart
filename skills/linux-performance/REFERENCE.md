---
name: linux-performance-reference
description: >
    Extended reference for performance monitoring. Read on demand when
    deeper knowledge is needed — not auto-loaded.
---

# Performance Monitoring: Reference

## Contents

- [Performance Analysis Methodology](#performance-analysis-methodology)
- [Interpreting PSI](#interpreting-psi)
- [Memory Deep Dive](#memory-deep-dive)
- [Disk I/O Deep Dive](#disk-io-deep-dive)
- [CPU Deep Dive](#cpu-deep-dive)
- [Cross-Server Workflows](#cross-server-workflows)
- [sysstat Not Available](#sysstat-not-available)
- [PCP Guidance](#pcp-guidance)
- [sar Historical Analysis](#sar-historical-analysis)
- [Known Quirks](#known-quirks)

## Performance Analysis Methodology

### USE Method (Brendan Gregg)

For every resource, check:
- **Utilisation** — how busy is it? (CPU%, disk %util, memory used%)
- **Saturation** — is work queuing? (load average, disk queue depth, swap activity)
- **Errors** — are there failures? (I/O errors, OOM kills, ECC errors)

### RED Method (Tom Wilkie)

For every service, check:
- **Rate** — requests per second
- **Errors** — failed requests
- **Duration** — response time

USE is for infrastructure; RED is for applications. Stuart primarily uses USE.

### Bottleneck categories

| Symptom | Likely bottleneck | Confirm with |
|---------|------------------|--------------|
| High load, high CPU | CPU-bound | `get_cpu_stats`, PSI cpu |
| High load, low CPU, high iowait | I/O-bound | `get_disk_io_stats`, PSI io |
| High load, low CPU, low iowait | Blocked on locks/network | Check sleeping processes, network |
| High swap activity | Memory pressure | `get_memory_stats`, PSI memory |
| Low load, system feels slow | Single-threaded bottleneck, thermal throttling | `get_cpu_frequency`, `get_thermal_stats` |

## Interpreting PSI

Pressure Stall Information (kernel 4.20+) measures the time tasks spend waiting for resources.

### Metrics

- **some**: Percentage of time *at least one task* was stalled. Non-zero = some contention.
- **full**: Percentage of time *all non-idle tasks* were stalled. Non-zero = severe contention.
- **avg10/avg60/avg300**: Moving averages over 10s, 60s, 300s windows.
- **total**: Cumulative stall time in microseconds since boot.

### Thresholds (guidelines, not absolutes)

| avg10 | Interpretation |
|-------|---------------|
| < 5% | Normal |
| 5–25% | Moderate contention — investigate if sustained |
| 25–50% | Significant pressure — active bottleneck |
| > 50% | Severe — system performance significantly degraded |

### PSI vs load average

Load average counts runnable + uninterruptible-sleep processes. It's a count, not a percentage — interpretation depends on CPU count. PSI is a direct measure of impact (% time stalled), making it more actionable:

- Load 8 on a 4-core system: overloaded? Maybe — depends on I/O wait.
- PSI cpu some avg10=35%: yes, tasks are waiting for CPU 35% of the time.

### cpu vs memory vs io

- **PSI cpu**: Tasks waiting for CPU time. Indicates CPU saturation.
- **PSI memory**: Tasks stalled on memory operations (reclaim, swapping, direct reclaim). Indicates memory pressure even before OOM.
- **PSI io**: Tasks waiting for I/O. Correlates with disk I/O latency.
- **PSI irq** (kernel 5.x+): Tasks stalled due to IRQ processing. Rare on modern systems.

## Memory Deep Dive

### Key /proc/meminfo fields

| Field | Meaning |
|-------|---------|
| MemTotal | Total physical RAM |
| MemFree | Truly unused memory (usually small — kernel aggressively caches) |
| MemAvailable | Estimated memory available for new allocations without swapping. **This is the field that matters.** |
| Buffers | Block device I/O cache |
| Cached | Page cache (file data in memory) |
| SReclaimable | Slab allocator memory that can be reclaimed |
| AnonPages | Memory used by processes (not backed by files) |
| Dirty | Memory modified but not yet written to disk |
| Shmem | Shared memory (tmpfs, POSIX shm) |
| SwapCached | Swap pages also in memory (avoids re-reading from swap) |

### Why "free" memory is misleading

Linux uses almost all memory for page cache. A system showing 200MB "free" out of 16GB is healthy — 15.8GB is actively used as cache, reclaimable on demand. `MemAvailable` accounts for reclaimable cache.

### OOM killer

Triggered when the kernel can't free enough memory. Check journald:
```
search_journals(grep="oom-kill", priority="warning", since="24h")
```
The OOM score for a process: `/proc/<pid>/oom_score` (higher = more likely to be killed).

### Swappiness

`vm.swappiness` (0–200, default 60) controls the kernel's preference for swapping anonymous pages vs dropping file cache. Lower = prefer dropping cache; higher = prefer swapping. Check current value:
```bash
sysctl vm.swappiness
```

## Disk I/O Deep Dive

### Key iostat metrics

| Metric | Meaning |
|--------|---------|
| r/s, w/s | Read/write IOPS (operations per second) |
| rMB/s, wMB/s | Read/write throughput |
| r_await, w_await | Average latency per operation (milliseconds) |
| aqu-sz | Average I/O queue depth |
| %util | Percentage of time device was busy |

### %util is misleading on modern hardware

- **HDD**: %util ≈ 100% means the disk is saturated (single queue).
- **SSD/NVMe**: Can serve many parallel requests. %util = 100% may mean only one queue slot is always busy — the device isn't necessarily saturated. Check `aqu-sz` and latency instead.

### I/O schedulers

| Scheduler | Best for | Default on |
|-----------|---------|-----------|
| none | NVMe (hardware handles scheduling) | NVMe devices |
| mq-deadline | SSDs, mixed workloads | SATA SSDs |
| bfq | Interactive desktops, HDDs | Some distros for rotational |

Check current scheduler:
```
read_sysfs(device="sda", attribute="queue/scheduler")
```

### Write-back vs write-through

`Dirty` pages in `/proc/meminfo` show data written to memory but not yet flushed to disk. High dirty pages = write-back pressure. Check `/proc/sys/vm/dirty_ratio` and `dirty_background_ratio` for thresholds.

## CPU Deep Dive

### Frequency governors

| Governor | Behaviour |
|----------|-----------|
| performance | Always max frequency |
| powersave | Always min frequency |
| schedutil | Kernel-driven scaling based on scheduler load (modern default) |
| ondemand | Scale up on load, scale down on idle (legacy, pre-schedutil) |
| conservative | Like ondemand but changes frequency gradually |

### Thermal throttling

When CPU temperature approaches the critical trip point, the kernel reduces frequency (thermal throttling). Signs:
- `get_cpu_frequency` shows current frequency below base frequency
- `get_thermal_stats` shows temperatures near critical thresholds
- `get_cpu_stats` shows lower-than-expected throughput

### Context switch cost

High context switches (visible in `vmstat`) can indicate:
- Too many threads competing for too few cores
- Frequent I/O (processes block and unblock rapidly)
- Excessive mutex contention

Check with: `vmstat 1 5` via Bash (columns `cs` = context switches, `in` = interrupts).

### CPU states

- **user**: Time running user-space code
- **nice**: Time running niced (low-priority) user-space code
- **system**: Time in kernel code
- **idle**: Time doing nothing
- **iowait**: Time idle with outstanding I/O (a form of idle — CPU is available, just waiting)
- **irq**: Time handling hardware interrupts
- **softirq**: Time handling software interrupts (networking, block I/O completion)
- **steal**: Time stolen by hypervisor (only relevant in VMs)

## Cross-Server Workflows

### OOM investigation

1. `get_memory_stats()` — current state
2. Journald MCP: `search_journals(grep="oom-kill", priority="warning", since="24h")`
3. `list_processes(sort_by="mem")` — current top consumers
4. Journald MCP: `search_journals(grep="Out of memory", since="24h")` — detailed OOM messages

### Slow service investigation

1. `check_system_health()` — overall system state
2. Systemd MCP: `get_unit_status(unit="service_name")` — is it running? resource usage?
3. `list_processes(filter_command="service_name")` — CPU/memory for that service
4. Journald MCP: `search_journals(unit="service_name", since="1h")` — recent logs

### Disk issues + performance

1. `get_disk_io_stats()` — I/O metrics per device
2. Block-device MCP: `get_device_messages(device="sda")` — kernel I/O errors
3. Block-device MCP: `check_disk_usage()` — is it full?
4. Block-device MCP: `check_smart_health(device="/dev/sda")` — hardware health

## sysstat Not Available

When sysstat (mpstat, iostat, pidstat) is not installed, the performance server falls back to `/proc` and `/sys` parsing. Differences:

| Feature | With sysstat | Without sysstat |
|---------|-------------|-----------------|
| CPU stats | mpstat JSON, clean per-core breakdown | /proc/stat delta (1s sleep), same fields |
| Disk I/O | iostat JSON, latency/queue depth | /proc/diskstats delta (1s sleep), no latency |
| Process I/O sort | pidstat -d (per-process I/O) | Not available (ps lacks I/O data) |

Recommendation: install sysstat for best results.
```
sudo dnf install sysstat       # Fedora/RHEL
sudo apt install sysstat       # Debian/Ubuntu
sudo pacman -S sysstat         # Arch
```

## PCP Guidance

Performance Co-Pilot (PCP) is a comprehensive metrics framework. If installed:

```bash
# List available metrics
pminfo | head -50

# Fetch a specific metric
pminfo -f kernel.all.load

# vmstat-like output
pmstat -s 5

# Per-CPU utilisation
pmval -s 3 kernel.percpu.cpu.user

# Check if pmlogger is running (writes ~50MB/day)
systemctl status pmlogger
```

PCP is powerful but complex. For routine monitoring, the performance MCP tools are simpler. Use PCP when you need historical data, custom metrics, or integration with Grafana/Prometheus.

## sar Historical Analysis

If sysstat service is enabled (`systemctl status sysstat`), data is collected to `/var/log/sa/`:

```bash
# CPU usage from today
sar -u

# CPU from a specific date
sar -u -f /var/log/sa/sa09

# Memory
sar -r

# Disk I/O
sar -d

# Network
sar -n DEV

# Export as JSON
sadf -j /var/log/sa/sa09 -- -u
```

## Known Quirks

- **PSI irq** requires kernel 5.x+ — not available on older distributions
- **mpstat first sample** includes since-boot averages. The server uses `mpstat 1 1` to get a 1-second sample.
- **iostat first sample** is since-boot average. The server uses `iostat 1 2` and takes the second sample for current rates.
- **ps %CPU** is the process's CPU usage over its lifetime, not instantaneous. For instant rates, use `get_cpu_stats`.
- **Thermal zone names** vary by hardware. ACPI zones may show as `acpitz`, CPU package as `x86_pkg_temp` or `TCPU`. The type file identifies each zone.
- **NVMe %util** can show 100% without saturation — NVMe handles parallel I/O. Trust `aqu-sz` and `await` instead.
- **iowait is CPU idle time** — it means the CPU had nothing else to do while waiting for I/O. High iowait with low CPU usage = I/O bottleneck. High iowait with high CPU usage = mixed workload, I/O is a factor but CPU is also busy.
