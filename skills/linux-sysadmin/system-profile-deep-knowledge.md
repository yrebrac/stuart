---
name: system-profile-deep-knowledge
description: >
    Extended system profiling reference. Read on demand for YAML schemas,
    gathering commands, parsing guidance, tool audit procedure, and
    staleness logic. NOT auto-loaded.
---

# System Profile: Deep Knowledge

Extended reference for the system-profile domain. Read when directed by the rules file.

## Contents

- [YAML Schemas](#yaml-schemas)
- [Gathering Commands](#gathering-commands)
- [Toolchain Audit Details](#toolchain-audit-details)
- [Staleness Logic](#staleness-logic)

## YAML Schemas

### hardware.yaml

```yaml
gathered_at: "2026-03-06T14:30:00+11:00"
gathered_by: "stuart"

cpu:
    model: "AMD Ryzen 9 7950X"
    cores: 16
    threads: 32
    architecture: "x86_64"
    sockets: 1

memory:
    total: "62Gi"

disks:
    - device: "nvme0n1"
      size: "931.5G"
      type: "disk"
      transport: "nvme"
      partitions:
          - name: "nvme0n1p1"
            size: "600M"
            fstype: "vfat"
            mountpoint: "/boot/efi"

gpu:
    model: "AMD Radeon RX 7900 XTX"
    driver: "amdgpu"

chassis:
    type: "desktop"
    source: "hostnamectl"
```

### distro.yaml

```yaml
gathered_at: "2026-03-06T14:30:00+11:00"
gathered_by: "stuart"

os:
    name: "Fedora Linux"
    version: "43"
    pretty_name: "Fedora Linux 43 (Workstation Edition)"
    id: "fedora"
    variant: "Workstation Edition"

kernel:
    version: "6.18.12-200.fc43.x86_64"
    architecture: "x86_64"

package_managers:
    - name: "dnf5"
      path: "/usr/bin/dnf5"
    - name: "flatpak"
      path: "/usr/bin/flatpak"

desktop:
    environment: "GNOME"
    display_server: "wayland"

init_system: "systemd"
```

### toolchain.yaml

```yaml
gathered_at: "2026-03-06T14:30:00+11:00"
gathered_by: "stuart"

servers:
    journald:
        status: "ok"
        tools:
            journalctl:
                exists: true
                version: "systemd 256"
    network:
        status: "degraded"
        tools:
            ip:
                exists: true
                version: "ip utility, iproute2-6.11.0"
            ethtool:
                exists: false

summary:
    total_servers: 9
    ok: 7
    degraded: 2
    unavailable: 0
    missing_tools: 3
    missing_tools_list:
        - server: "network"
          tool: "ethtool"
```

## Gathering Commands

### Hardware

Run in a single Bash call:

```bash
echo "===LSCPU===" && lscpu && \
echo "===FREE===" && free -h && \
echo "===LSBLK===" && lsblk -o NAME,SIZE,TYPE,FSTYPE,TRAN,MOUNTPOINTS && \
echo "===GPU===" && (lspci 2>/dev/null | grep -i 'vga\|3d\|display' || echo "no gpu detected") && \
echo "===CHASSIS===" && (cat /sys/class/dmi/id/chassis_type 2>/dev/null || echo "unknown") && \
echo "===HOSTNAMECTL===" && (hostnamectl 2>/dev/null | grep -i 'chassis\|virtualization' || echo "unknown") && \
echo "===TIMESTAMP===" && date -Iseconds
```

**Parsing guidance:**
- `lscpu`: "Model name", "CPU(s)", "Core(s) per socket", "Socket(s)", "Architecture"
- `free -h`: "total" from "Mem:" row
- `lsblk`: TYPE "disk" = top-level devices; children = partitions
- `lspci`: first match = primary GPU
- `chassis_type`: 1=Other, 3=Desktop, 8=Portable, 9=Laptop, 10=Notebook
- No `lspci`: GPU = null (acceptable)

**Sudo-enhanced (optional):**
- `sudo dmidecode -t system` — manufacturer, product, serial
- `sudo smartctl -a /dev/nvme0n1` — SMART data

### Distro

Run in a single Bash call:

```bash
echo "===OS_RELEASE===" && cat /etc/os-release && \
echo "===KERNEL===" && uname -r && \
echo "===ARCH===" && uname -m && \
echo "===PKG_MANAGERS===" && for cmd in dnf5 dnf apt apt-get pacman zypper flatpak snap; do command -v "$cmd" 2>/dev/null && echo "found: $cmd"; done && \
echo "===DESKTOP===" && echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-unset}" && echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-unset}" && \
echo "===INIT===" && ps -p 1 -o comm= && \
echo "===TIMESTAMP===" && date -Iseconds
```

**Parsing guidance:**
- `/etc/os-release`: key=value. Extract NAME, VERSION_ID, PRETTY_NAME, ID, VARIANT (may not exist)
- `command -v`: returns path if found
- `XDG_CURRENT_DESKTOP`: may be colon-separated. Use last component. May be unset in non-interactive shell — fallback: `loginctl show-session`
- `ps -p 1`: almost always "systemd"

### Toolchain

Call `tool_info()` on each MCP server (all in parallel):

| Server | MCP tool |
|--------|----------|
| journald | `mcp__plugin_stuart_journald__tool_info` |
| systemd | `mcp__plugin_stuart_systemd__tool_info` |
| block-device | `mcp__plugin_stuart_block-device__tool_info` |
| syslog | `mcp__plugin_stuart_syslog__tool_info` |
| serial-device | `mcp__plugin_stuart_serial-device__tool_info` |
| container | `mcp__plugin_stuart_container__tool_info` |
| network | `mcp__plugin_stuart_network__tool_info` |
| virtual | `mcp__plugin_stuart_virtual__tool_info` |
| packages | `mcp__plugin_stuart_packages__tool_info` |

**Response format exceptions:**
- **packages**: Returns `distro`, `package_manager`, `tools`, `universal_formats` — extract `tools` dict
- **container**: Returns runtime detection, not a tools dict — parse for runtime availability

## Toolchain Audit Details

1. Call `tool_info()` on all 9 servers
2. Parse: `exists: true` = installed, `exists: false` = missing
3. Server status: all ok → `ok`, some missing → `degraded`, no response → `unavailable`
4. For missing tools: `search_provider("<tool>")` from packages server. Don't hardcode mappings — they vary by distro.
5. Generate install commands using distro profile's package manager
6. Present as table:

```
Server         Tool         Status     Package         Install Command
───────────────────────────────────────────────────────────────────────
block-device   smartctl     missing    smartmontools   sudo dnf install smartmontools
network        ethtool      missing    ethtool         sudo dnf install ethtool
```

## Staleness Logic

| Profile | Threshold | Rationale |
|---------|-----------|-----------|
| hardware | 30 days | Hardware changes are rare |
| distro | 7 days | Kernel updates happen weekly on rolling |
| toolchain | 7 days | Packages may change between sessions |

**Suggest refresh when:**
- Age exceeds threshold AND current task is relevant
- User reports OS upgrade, package changes, or hardware changes
- `tool_info()` contradicts toolchain.yaml

**Invalidation triggers:**
- `uname -r` differs from distro.yaml kernel → stale
- Package install/remove during session → toolchain stale
- Hardware: rely on age threshold (rarely detectable automatically)
