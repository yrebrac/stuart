---
name: system-profile
description: >
    Utility skill for gathering, caching, and auditing system information.
    Collects hardware, distro, and toolchain profiles so Stu and sub-agents
    don't rediscover stable system facts every session. Use when you need
    system context, want to check tool health, or are preparing delegation.
---

# System Profile

## What This Skill Does

Profiles cache **stable system facts** — things that change on the scale of weeks, months, or years. Three profile types:

- **hardware.yaml** — CPU, RAM, disk(s), GPU, chassis (changes: hardware swap)
- **distro.yaml** — OS, kernel, arch, desktop environment, init system (changes: OS upgrade, kernel update)
- **toolchain.yaml** — Stuart's MCP server tool dependencies (changes: package install/remove)

Profiles are **not** for dynamic state. Running processes, network connections, USB peripherals, container lists, CPU load — query these live via MCP tools.

## Session Start

Read profiles from `${CLAUDE_PLUGIN_ROOT}/profiles/`:

1. **Profiles exist and fresh** (`gathered_at` within threshold): load relevant profiles, note key facts for context. You don't need to present them unless asked.
2. **No profiles exist**: mention the `/profile-system` command. Offer to gather — once per session, briefly. If the user declines, respect that and move on. Don't nag.
3. **Profiles are stale**: note the age if the current task relates to the stale profile. Suggest refresh, don't force. ("Your distro profile is 14 days old — want me to refresh before we look at this package issue?")

**Staleness thresholds:** hardware 30 days, distro 7 days, toolchain 7 days.

**Delegation context**: when delegating to a specialist, include relevant profile facts in the task description. A package query benefits from distro info. A disk investigation benefits from hardware info. Don't dump the whole profile — extract what's relevant.

## Common Tasks

### "What system am I working with?"

1. Read `hardware.yaml` and `distro.yaml`
2. Present a concise summary: CPU, RAM, OS, kernel, desktop, key storage
3. If no profiles exist, offer to gather them

### "Refresh my system profile"

1. Read any existing profiles from `${CLAUDE_PLUGIN_ROOT}/profiles/`
2. If files exist, copy them to `profiles/archive/` with ISO timestamp suffix (e.g. `hardware.2026-03-06T143000.yaml`)
3. Re-gather data (see Gathering Strategy)
4. Write new YAML files with `gathered_at` timestamp
5. Summarise what changed from the previous profile, if any

### "What tools am I missing?" / "Audit tools"

1. Call `tool_info()` on each active MCP server (all 9 — see Gathering Strategy)
2. Collect tools where `exists: false`
3. For missing tools, look up the providing package via `search_provider("<tool>")` from the packages server
4. Present a table: server, tool, status, package, install command
5. Optionally write results to `toolchain.yaml`
6. For the full audit procedure, read REFERENCE.md in this skill directory

### "Pass context to specialist"

Extract relevant profile fields for the delegation prompt:
- **Linux specialist**: distro ID, kernel version, init system, relevant hardware
- **Container specialist**: distro ID (affects container runtime paths)
- **Distro specialist**: full distro profile, available package managers

## Gathering Strategy

### Hardware

Run in a single Bash call: `lscpu`, `free -h`, `lsblk -o NAME,SIZE,TYPE,FSTYPE,TRAN,MOUNTPOINTS`, `lspci | grep -i vga`, `cat /sys/class/dmi/id/chassis_type`, `hostnamectl`.

Parse output and write to `${CLAUDE_PLUGIN_ROOT}/profiles/hardware.yaml`.

No sudo required for baseline. Some detail (dmidecode, smartctl) requires elevated privileges — Stuart auto-escalates via polkit when configured. Skip gracefully if unavailable.

### Distro

Run in a single Bash call: `cat /etc/os-release`, `uname -r`, `uname -m`, `command -v` for each package manager (`dnf5 dnf apt pacman zypper flatpak snap`), `echo $XDG_CURRENT_DESKTOP`, `echo $XDG_SESSION_TYPE`, `ps -p 1 -o comm=`.

Parse output and write to `${CLAUDE_PLUGIN_ROOT}/profiles/distro.yaml`.

### Toolchain

Call `tool_info()` on each active MCP server (call in parallel where possible):

| Server | MCP tool name |
|--------|---------------|
| journald | `mcp__plugin_stuart_journald__tool_info` |
| systemd | `mcp__plugin_stuart_systemd__tool_info` |
| block-device | `mcp__plugin_stuart_block-device__tool_info` |
| syslog | `mcp__plugin_stuart_syslog__tool_info` |
| serial-device | `mcp__plugin_stuart_serial-device__tool_info` |
| container | `mcp__plugin_stuart_container__tool_info` |
| network | `mcp__plugin_stuart_network__tool_info` |
| virtual | `mcp__plugin_stuart_virtual__tool_info` |
| packages | `mcp__plugin_stuart_packages__tool_info` |

Aggregate results and write to `${CLAUDE_PLUGIN_ROOT}/profiles/toolchain.yaml`.

For full command strings, output parsing guidance, fallback commands, and YAML schemas, read REFERENCE.md in this skill directory.

## Cache Format

- **Location**: `${CLAUDE_PLUGIN_ROOT}/profiles/`
- **Files**: `hardware.yaml`, `distro.yaml`, `toolchain.yaml`
- **Metadata**: each file includes `gathered_at` (ISO 8601, from `date -Iseconds`) and `gathered_by: "stuart"` at the top
- **Archive**: before overwriting, copy existing files to `profiles/archive/` with ISO timestamp suffix

Example top of any profile file:

```yaml
gathered_at: "2026-03-06T14:30:00+11:00"
gathered_by: "stuart"
```

For full YAML schemas with all fields and types, read REFERENCE.md in this skill directory.

## Preferences & Safety

- **Local only**: profiles stay in the plugin install directory. Never commit, transmit, or share profile data.
- **Read-only gathering**: all commands are read-only. No sudo required for the baseline profile.
- **Graceful degradation**: if a command or tool is unavailable (no `lspci`, no sudo), skip that field — don't fail the whole profile.
- **Archive before overwrite**: never destroy previous profiles. Always copy to `profiles/archive/` first.
- **Privacy**: some users may not want system information cached. Ask before first gather, explain briefly what's collected (hardware specs, OS version, tool availability), note that data stays local. Respect refusal — ask once per session at most.
