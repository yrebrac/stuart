---
name: system-profile
description: >
    Utility skill for gathering, caching, and auditing system information.
    Collects hardware, distro, and toolchain profiles so Stu doesn't
    rediscover stable system facts every session. Use when you need
    system context or want to check tool health.
---

# System Profile

## Guide

This file covers system profiling — gathering and caching stable system facts.

- **Domain Model** — profile types, what's cached vs queried live, staleness
- **Heuristics** — when to suggest profiling, when to skip
- **Anti-patterns** — common mistakes with system profiling
- **Procedures** — system overview, refresh, tool audit, delegation context
- **Tools** — gathering approach (Bash + MCP tool_info)
- **Query Strategy** — profile reading, staleness checks
- **Safety** — privacy, local-only data, graceful degradation
- **Quirks** — XDG variables in non-interactive shells, chassis type integers
- **Domain Deep Knowledge** → `system-profile-deep-knowledge.md` for YAML schemas, gathering commands, tool audit detail

## Domain Model

**Three profile types** — stable system facts cached in `${CLAUDE_PLUGIN_ROOT}/profiles/`:

| Profile | Contents | Changes when |
|---------|----------|-------------|
| hardware.yaml | CPU, RAM, disks, GPU, chassis | Hardware swap (rare) |
| distro.yaml | OS, kernel, arch, desktop, init | OS upgrade, kernel update |
| toolchain.yaml | MCP server tool dependencies | Package install/remove |

**Not for dynamic state**: processes, network connections, USB peripherals, container lists, CPU load — query these live.

**Staleness thresholds**: hardware 30 days, distro 7 days, toolchain 7 days.

## Heuristics

1. Don't nag about profiling. Mention `/profile-system` once if no profiles exist. If user declines, move on.
2. Only suggest profile refresh when the current task relates to the stale profile. Don't suggest hardware refresh for a network question.
3. When delegating context to investigations, extract relevant profile facts — don't dump the whole profile.
4. If `tool_info()` during a task disagrees with toolchain.yaml, the profile is stale. Note it, don't interrupt.

## Anti-patterns

- Don't cache dynamic state (processes, connections, load) in profiles — query it live.
- Don't assume profiles exist — check first and handle gracefully.
- Don't overwrite profiles without archiving the old ones first.
- Don't share or transmit profile data — it stays local.
- Don't ask about profiling more than once per session.

## Procedures

### System overview
When user asks "what system am I working with?" or you need context.

1. Read `hardware.yaml` and `distro.yaml` from `${CLAUDE_PLUGIN_ROOT}/profiles/`
2. IF profiles exist and fresh:
     Present concise summary: CPU, RAM, OS, kernel, desktop, key storage
   IF no profiles:
     Mention `/profile-system` command, offer to gather
   IF stale:
     Note age if relevant to current task, suggest refresh
3. VERIFY: User has the context they need

### Profile refresh
When user requests a profile update or profiles are stale.

1. Read existing profiles from `${CLAUDE_PLUGIN_ROOT}/profiles/`
2. IF files exist: copy to `profiles/archive/` with ISO timestamp suffix
3. Re-gather data (see deep-knowledge file for commands)
4. Write new YAML with `gathered_at` timestamp
5. Summarise what changed from previous profile
6. VERIFY: New profiles written with current timestamp

### Tool audit
When user asks "what tools am I missing?" or "audit tools."

1. Call `tool_info()` on each active MCP server (all 9 — see deep-knowledge file)
2. Collect tools where `exists: false`
3. For missing tools: `search_provider("<tool>")` from packages server
4. Check `_privilege` in responses — report escalation status
5. Present table: server, tool, status, package, install command
6. IF privilege not configured: note, suggest `/setup check-privileges`
7. VERIFY: User knows what's missing and how to install it
8. CROSS-DOMAIN: For package installation → `linux-packages-rules.md`

### Delegation context extraction
When passing context to domain investigations.

1. Read relevant profile(s)
2. Extract only what's relevant:
   - Package query → distro ID, package managers
   - Disk investigation → hardware disks, transport types
   - Network issue → (no profile yet — query live)
3. Include extracted facts in investigation context
4. VERIFY: Relevant context included, no unnecessary data

## Tools

This domain uses Bash for data gathering and MCP `tool_info()` for toolchain audits. No dedicated MCP server.

| Goal | Approach |
|------|----------|
| Hardware profile | Bash: `lscpu`, `free -h`, `lsblk`, `lspci`, `hostnamectl` |
| Distro profile | Bash: `/etc/os-release`, `uname`, package manager detection |
| Toolchain profile | `tool_info()` on all 9 MCP servers |
| Missing tool packages | packages MCP: `search_provider` |

## Query Strategy

1. Read profiles before starting investigations in the relevant domain.
2. Check `gathered_at` against staleness thresholds before relying on profile data.
3. When `tool_info()` disagrees with toolchain.yaml, trust `tool_info()` — it's live.
4. Call `tool_info()` on all 9 servers in parallel for toolchain audits.
5. Be suspicious of empty results — profiles may not exist yet.

## Safety

### Privacy

- Profiles stay in the plugin install directory. Never commit, transmit, or share.
- Ask before first gather. Explain briefly: hardware specs, OS version, tool availability. Data stays local.
- Respect refusal — ask once per session at most.

### Graceful degradation

- All gathering commands are read-only. No sudo required for baseline.
- If a command or tool is unavailable (no `lspci`, no sudo), skip that field — don't fail.
- Archive before overwrite — never destroy previous profiles.

### Cross-references

- For package installation of missing tools → `linux-packages-rules.md`
- If hardware profile shows disk issues → `linux-block-device-rules.md`
- If toolchain shows degraded servers → investigate specific domain

## Quirks

- **XDG variables in non-interactive shells**: `XDG_CURRENT_DESKTOP` and `XDG_SESSION_TYPE` may not be inherited by Claude Code's Bash. Fallback: `loginctl show-session`.
- **Chassis type integers**: DMI chassis_type is an integer (1=Other, 3=Desktop, 8=Portable, 9=Laptop). `hostnamectl` provides human-readable string.
- **Container and packages server output**: These return different structures from `tool_info()`. See deep-knowledge file for parsing guidance.
- **Profile archive naming**: ISO timestamp suffix, e.g. `hardware.2026-03-06T143000.yaml`.

## Domain Deep Knowledge → system-profile-deep-knowledge.md

Read when:
- Gathering profiles (need exact commands, parsing guidance, YAML schemas)
- Running a tool audit (need server list, response format, package lookup procedure)
- Need staleness logic or invalidation trigger details
- Working with legacy/minimal systems where gathering commands differ
