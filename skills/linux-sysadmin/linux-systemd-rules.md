---
name: linux-systemd
description: >
    Domain knowledge for systemd administration using journalctl and systemctl
    MCP tools. Load BEFORE using any systemd/journald MCP tool directly.
    Covers service troubleshooting, log analysis, dependency inspection,
    and query strategy.
---

# systemd

## Guide

This file covers systemd service management and journald log analysis.

- **Domain Model** — unit types, scopes, state model, and how units relate
- **Heuristics** — expert shortcuts for common systemd problems
- **Anti-patterns** — mistakes baseline Claude makes with systemd/journald
- **Procedures** — diagnostic workflows for failed services, logs, timers, boot issues
- **Tools** — goal-to-tool lookup for systemctl and journalctl MCP servers
- **Query Strategy** — scope-first approach, efficient log queries, when to use get_json_entries
- **Safety** — privilege, high-risk operations, cross-domain pointers
- **Quirks** — user unit filtering, priority levels, version-specific behavior
- **Domain Deep Knowledge** — version awareness, additional logging sources (inline)

## Domain Model

**Unit types**: service, timer, socket, mount, target, path, scope, slice. Always include the suffix (e.g., `nginx.service`, `backup.timer`).

**Scopes**: System units (`user=False`) run as root. User units (`user=True`) run under a user session (e.g., rclone, syncthing, pipewire). When a unit isn't found in one scope, try the other.

**State model** — three layers:
- **LoadState**: loaded, not-found, masked, error
- **ActiveState**: active, inactive, failed, activating, deactivating
- **SubState**: running, dead, waiting, exited, failed (varies by unit type)

**Unit relationships**:
- Dependencies: Wants, Requires, After, Before, WantedBy
- Triggers: timer→service, socket→service, path→service
- Targets group units into synchronisation points (e.g., `multi-user.target`)

**Two log sources**: systemd journal (structured, indexed) and application flat files. Many apps log to both at different verbosity. Journal gets operational status; flat files get verbose debug. Check both when troubleshooting.

## Heuristics

1. When a unit isn't found, try the other scope (user/system) before concluding it doesn't exist. User services are invisible to system scope and vice versa.
2. "Failed with result 'exit-code'" is logged at priority 4 (warning), not 3 (err). Filter accordingly — a severity-only search at `err` will miss it.
3. If a service keeps restarting, check `Restart=always` + short `RestartUSec` in the unit file before investigating the crash. The restart policy may be masking the real problem.
4. Glob patterns work in the `unit` parameter (e.g., `rclone*` matches all rclone services). Use this before manually iterating.
5. Empty journal results are common — broaden scope before concluding "no issues." Try alternate scope, broader time range, remove priority filter.

## Anti-patterns

- Don't use `search_journals` for user unit lifecycle messages (start/stop/fail) — it filters on `_SYSTEMD_UNIT` which maps to `user@1000.service`, not the actual unit. Use `get_json_entries` instead.
- Don't query the entire journal without a `since` constraint — unbounded queries are slow and produce excessive output.
- Don't assume a unit name without the suffix — `nginx` is ambiguous; `nginx.service` is precise.
- Don't rely solely on priority filters — units may use inconsistent severity levels. If a filtered query returns nothing, retry without the filter.
- Don't assume features exist based on docs alone — distro build flags (`--without`) can disable features. If a flag returns "unrecognized option", try alternatives.

## Procedures

### Service failure investigation
When a service is broken, won't start, or has failed.

1. `get_unit_status(unit)` — state, recent logs, exit code
2. `list_recent_errors(unit=unit, since="1h")` — what went wrong
3. IF exit code non-zero:
     `read_unit_file(unit)` — check ExecStart, dependencies, conditions
   IF ConditionPath/AssertPath failing:
     Check if the required path exists
   IF dependency failed:
     `list_dependencies(unit)` — which dependency is the problem?
     Recurse: investigate the failed dependency
4. `get_unit_properties(unit)` — check resource limits (MemoryMax, CPUQuota), restart policy
5. IF service keeps restarting:
     Check RestartUSec (too short = restart loop)
     `list_recent_errors` with broader time range — find the actual crash reason
6. VERIFY: `check_active(unit)` returns active/running
7. CROSS-DOMAIN: If service failed due to disk/I/O → `linux-block-device-rules.md`; if due to network → `linux-network-rules.md`

### System-wide failure survey
When investigating what's failing across the system.

1. `list_failed_units` with `user=False` — system scope
2. `list_failed_units` with `user=True` — user scope
3. For each failed unit: `get_unit_status` for details
4. Prioritize: services the user depends on first, then supporting services
5. VERIFY: `list_failed_units` returns empty (both scopes)

### Log investigation
When searching for specific events or errors in logs.

1. `search_journals(unit=unit, since="1h")` — recent entries
2. IF empty:
     Try `since="1d"`
     Remove priority filter
     Try alternate scope (user/system)
     Try `get_json_entries` (more reliable for user units)
3. IF need precise timestamps or field data:
     `get_json_entries` — structured JSON output
4. IF correlating across services:
     `search_journals(grep="pattern", since="timerange")` without unit filter
5. VERIFY: Found relevant log entries that explain the issue

### Timer investigation
When a timer isn't firing or firing incorrectly.

1. `list_timers` — check next trigger time and last trigger
2. `check_enabled(timer)` — is the timer enabled?
3. `get_unit_relationships(timer)` — does it activate the right service?
4. IF never triggered:
     Check calendar spec in unit file: `read_unit_file(timer)`
   IF triggered but service fails:
     → "Service failure investigation" for the activated service
5. VERIFY: `list_timers` shows reasonable next trigger time

### Boot issue investigation
When the system had boot problems or the user reports slow boot.

1. `get_boot_log` — boot messages for the current or previous boot
2. `list_failed_units` — anything fail during boot?
3. `get_kernel_log` — hardware-level issues during boot
4. IF slow boot:
     Suggest: `systemd-analyze blame` via Bash for per-unit boot timing
5. VERIFY: No failed units, boot log shows clean startup

## Tools

| Goal | Tool |
|------|------|
| Is it running? | `check_active` |
| Is it enabled at boot? | `check_enabled` |
| What's broken? | `list_failed_units` |
| Full status + recent logs | `get_unit_status` |
| Read the unit file | `read_unit_file` |
| Specific properties | `get_unit_properties` |
| What depends on it? | `list_dependencies(reverse=True)` |
| What does it need? | `list_dependencies` |
| Full relationship map | `get_unit_relationships` |
| All timers and schedules | `list_timers` |
| Find available units | `list_units` (systemctl server) |
| Search logs | `search_journals` |
| Recent errors only | `list_recent_errors` |
| Structured log data | `get_json_entries` |
| Find units with logs | `list_units` (journalctl server) |
| Boot messages | `get_boot_log` |
| Kernel messages | `get_kernel_log` |
| Journal disk usage | `check_disk_usage` |

## Query Strategy

1. Start with a specific unit + short time range + severity filter. Broaden only if results are empty.
2. Always specify `since` — never query the entire journal.
3. Use `priority` to filter noise: `err` for errors, `warning` for warnings+.
4. Use `grep` for specific patterns rather than scanning all output.
5. Use `max_lines` to keep output manageable (default 100 is usually enough).
6. Use `get_json_entries` instead of `search_journals` when you need precise field data, user unit lifecycle messages, or cross-service timestamp correlation.
7. Be suspicious of empty results — cross-check with a different tool or broader query before concluding "no issues."

## Safety

### Privilege

Most systemd tools work without privilege for read operations.

| Operation | Privilege needed |
|-----------|-----------------|
| Read unit status, properties, logs | None |
| Start/stop/restart system units | Root or polkit |
| Enable/disable system units | Root or polkit |
| Mask/unmask units | Root |
| Edit unit overrides (`systemctl edit`) | Root |
| User unit lifecycle (`--user`) | None (own session) |

Stuart auto-escalates via polkit when configured.

### High-risk operations

- Masking a unit (`systemctl mask`) prevents it from starting by any means — including dependencies. State the effect, confirm before proceeding.
- Daemon-reload (`systemctl daemon-reload`) is safe but re-reads all unit files — mention if done after editing a unit file.
- Restarting core services (NetworkManager, systemd-resolved, sshd) may disconnect the user. Batch instructions for offline-aware sequences.

### Cross-references

- If a service failed due to disk full or I/O errors → `linux-block-device-rules.md` "Disk full investigation"
- If a service can't bind to a port → `linux-network-rules.md` "Port and service investigation"
- If a service is consuming excessive resources → `linux-performance-rules.md` "Resource consumer identification"
- If container-related units (docker, podman) → `container-runtime-rules.md`
- If libvirtd or VM-related units → `linux-virtual-rules.md`

## Quirks

- **`search_journals` and user units**: The unit filter uses `_SYSTEMD_UNIT` which maps to `user@1000.service` (the manager), not the actual unit. Use `get_json_entries` for user unit lifecycle messages.
- **`since` format**: Use journalctl-native formats: `"2 days ago"`, `"yesterday"`, `"2026-03-01"`. `get_json_entries` is more reliable with native formats.
- **Priority levels for failures**: "Failed with result 'exit-code'" is PRIORITY 4 (warning). "Failed to start [unit]" is PRIORITY 3 (err). Filter accordingly.
- **Severity inconsistency**: Units may use incorrect severity levels. Don't rely solely on priority filters.
- **Version-dependent features**: journal namespaces (v245+), soft-reboot (v254+), run0 (v256+). Check version from `tool_info()`.
- **Distro build flags**: Distributions may compile systemd with `--without` flags that disable documented features.

## Domain Deep Knowledge

This domain's deep knowledge is covered by the Domain Model and Procedures above.

Additional version awareness:
- Check systemd version from `tool_info()` before assuming flag availability
- Feature availability varies significantly between systemd versions (v239 on RHEL 8 vs v256+ on Fedora 43)
- When a flag returns "unrecognized option", the build may differ from upstream docs — try alternatives
