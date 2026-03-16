---
name: linux-performance-deep-knowledge
description: >
    Extended performance monitoring reference. Read on demand for PSI
    interpretation, memory/disk/CPU deep-dives, historical analysis,
    and methodology. NOT auto-loaded.
---

# Performance Monitoring: Deep Knowledge

Extended reference for the performance domain. Read when directed by the rules file.

## Contents

- [Performance Analysis Methodology](#performance-analysis-methodology)
- [Interpreting PSI](#interpreting-psi)
- [Memory Deep Dive](#memory-deep-dive)
- [Disk I/O Deep Dive](#disk-io-deep-dive)
- [CPU Deep Dive](#cpu-deep-dive)
- [sysstat Not Available](#sysstat-not-available)
- [PCP Guidance](#pcp-guidance)
- [sar Historical Analysis](#sar-historical-analysis)

## Performance Analysis Methodology

### USE Method (Brendan Gregg)

For every resource: **Utilisation** (how busy?), **Saturation** (is work queuing?), **Errors** (failures?). Stuart primarily uses USE for infrastructure.

### RED Method (Tom Wilkie)

For every service: **Rate** (requests/sec), **Errors** (failed requests), **Duration** (response time). RED is for applications, not infrastructure.

## Interpreting PSI

Pressure Stall Information (kernel 4.20+) measures time tasks spend waiting for resources.

### Metrics

- **some**: % time at least one task was stalled. Non-zero = some contention.
- **full**: % time all non-idle tasks were stalled. Non-zero = severe.
- **avg10/avg60/avg300**: Moving averages over 10s, 60s, 300s.
- **total**: Cumulative stall time in microseconds since boot.

### Thresholds (guidelines)

| avg10 | Interpretation |
|-------|---------------|
| < 5% | Normal |
| 5-25% | Moderate — investigate if sustained |
| 25-50% | Significant — active bottleneck |
| > 50% | Severe — major performance degradation |

### PSI vs load average

Load average counts runnable + uninterruptible processes. Interpretation depends on CPU count. PSI is direct impact measurement:
- Load 8 on 4-core: overloaded? Depends on I/O wait.
- PSI cpu some avg10=35%: tasks waiting for CPU 35% of the time. Actionable.

### cpu vs memory vs io

- **PSI cpu**: Waiting for CPU time. CPU saturation.
- **PSI memory**: Stalled on memory operations (reclaim, swap). Indicates pressure before OOM.
- **PSI io**: Waiting for I/O. Correlates with disk latency.
- **PSI irq** (kernel 5.x+): Stalled due to IRQ processing. Rare on modern systems.

## Memory Deep Dive

### Key /proc/meminfo fields

| Field | Meaning |
|-------|---------|
| MemTotal | Total physical RAM |
| MemFree | Truly unused (usually small — kernel aggressively caches) |
| MemAvailable | **This is what matters.** Available for new allocations without swapping. |
| Buffers | Block device I/O cache |
| Cached | Page cache (file data in memory) |
| SReclaimable | Slab memory that can be reclaimed |
| AnonPages | Process memory (not file-backed) |
| Dirty | Modified but not yet written to disk |
| Shmem | Shared memory (tmpfs, POSIX shm) |
| SwapCached | Swap pages also in memory |

### Why "free" is misleading

Linux uses almost all memory for page cache. 200MB "free" out of 16GB is healthy — 15.8GB is cache, reclaimable on demand. `MemAvailable` accounts for this.

### OOM killer

Triggered when kernel can't free enough memory. Check:
```
search_journals(grep="oom-kill", priority="warning", since="24h")
```
OOM score per process: `/proc/<pid>/oom_score` (higher = more likely killed).

### Swappiness

`vm.swappiness` (0-200, default 60): lower = prefer dropping cache; higher = prefer swapping. Check: `sysctl vm.swappiness`.

## Disk I/O Deep Dive

### Key iostat metrics

| Metric | Meaning |
|--------|---------|
| r/s, w/s | IOPS |
| rMB/s, wMB/s | Throughput |
| r_await, w_await | Latency per operation (ms) |
| aqu-sz | Average queue depth |
| %util | % time device busy |

### %util on modern hardware

- **HDD**: 100% = saturated (single queue)
- **SSD/NVMe**: 100% may mean one queue slot always busy — not necessarily saturated. Check `aqu-sz` and latency.

### I/O schedulers

| Scheduler | Best for | Default on |
|-----------|---------|-----------|
| none | NVMe | NVMe devices |
| mq-deadline | SSDs, mixed | SATA SSDs |
| bfq | Interactive, HDDs | Some distros for rotational |

Check: `read_sysfs(device="sda", attribute="queue/scheduler")`

### Write-back pressure

`Dirty` in meminfo = data written to memory, not yet flushed. High dirty pages = write-back pressure. Thresholds: `/proc/sys/vm/dirty_ratio`, `dirty_background_ratio`.

## CPU Deep Dive

### Frequency governors

| Governor | Behaviour |
|----------|-----------|
| performance | Always max |
| powersave | Always min |
| schedutil | Kernel-driven (modern default) |
| ondemand | Scale on load (legacy) |
| conservative | Gradual scaling |

### Thermal throttling

When CPU approaches critical trip point, kernel reduces frequency. Signs:
- `get_cpu_frequency` below base frequency
- `get_thermal_stats` near critical thresholds
- Lower-than-expected throughput

### Context switches

High context switches (visible in `vmstat`) indicate:
- Too many threads for available cores
- Frequent I/O (block/unblock rapidly)
- Excessive mutex contention

Check: `vmstat 1 5` via Bash (`cs` = context switches, `in` = interrupts).

### CPU states

- **user**: User-space code
- **nice**: Low-priority user code
- **system**: Kernel code
- **idle**: Doing nothing
- **iowait**: Idle with outstanding I/O (CPU available, just waiting)
- **irq**: Hardware interrupts
- **softirq**: Software interrupts (networking, block I/O)
- **steal**: Hypervisor-stolen (only in VMs)

## sysstat Not Available

Fallback when mpstat/iostat/pidstat missing:

| Feature | With sysstat | Without |
|---------|-------------|---------|
| CPU stats | mpstat JSON, per-core | /proc/stat delta (1s sleep) |
| Disk I/O | iostat JSON, latency | /proc/diskstats delta, no latency |
| Process I/O | pidstat -d | Not available |

Install: `sudo dnf install sysstat` / `sudo apt install sysstat` / `sudo pacman -S sysstat`

## PCP Guidance

Performance Co-Pilot — comprehensive metrics framework. If installed:

```bash
pminfo | head -50              # available metrics
pminfo -f kernel.all.load      # specific metric
pmstat -s 5                    # vmstat-like
pmval -s 3 kernel.percpu.cpu.user  # per-CPU
systemctl status pmlogger      # check if logging (~50MB/day)
```

PCP is powerful but complex. For routine monitoring, MCP tools are simpler. Use PCP for historical data, custom metrics, Grafana/Prometheus integration.

## sar Historical Analysis

If sysstat service is enabled (`systemctl status sysstat`), data in `/var/log/sa/`:

```bash
sar -u                         # CPU today
sar -u -f /var/log/sa/sa09     # CPU specific date
sar -r                         # Memory
sar -d                         # Disk I/O
sar -n DEV                     # Network
sadf -j /var/log/sa/sa09 -- -u # Export as JSON
```
