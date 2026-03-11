---
name: linux-systemd
description: >
    Domain knowledge for systemd administration using journalctl and systemctl
    MCP tools. Load BEFORE using any systemd/journald MCP tool directly.
    Covers service troubleshooting, log analysis, dependency inspection,
    and query strategy.
---

# systemd

## Session Start

Before investigating systemd issues, establish what you're working with:

1. Call `tool_info()` on both the journalctl and systemctl servers
2. Note the systemd version — feature availability varies significantly between versions
3. If the version has changed since your last session, be cautious about assuming flag behavior

## Core Concepts

**Unit types**: service, timer, socket, mount, target, path, scope, slice. Always include the suffix (e.g., `nginx.service`, `backup.timer`).

**Scopes**: System units (`user=False`) run as root. User units (`user=True`) run under a user session (e.g., rclone, syncthing, pipewire). When a unit isn't found in one scope, try the other.

**State model**: Units have three state layers:
- **LoadState**: loaded, not-found, masked, error
- **ActiveState**: active, inactive, failed, activating, deactivating
- **SubState**: running, dead, waiting, exited, failed (varies by unit type)

**Unit definitions and directives**: When investigating, understand the unit's configuration:
- type: service, timer, socket, etc.
- targets
- dependencies: Wants, WantedBy, etc.
- triggers: timer→service, socket→service, etc.

**Additional logging**: Look for logging locations outside systemd journals (application logs, syslog files, etc.). Use this information to tailor your journal searches and improve your effectiveness.

## Common Tasks

### "Service X is broken"

1. `get_unit_status(unit)` — state, recent logs, exit code
2. `list_recent_errors(unit=unit, since="1h")` — what went wrong
3. `read_unit_file(unit)` — check ExecStart, dependencies, conditions
4. `list_dependencies(unit)` — is something it needs failed?

### "What's failing?"

1. `list_failed_units` — both `user=True` and `user=False`
2. `get_unit_status` on each failed unit for details

### "Show me logs for X"

1. `search_journals(unit=unit, since="1h")` — recent entries
2. If empty: try `since="1d"`, remove priority filter, try alternate scope
3. For user units: use `get_json_entries` instead (see Known Quirks)

### "Why isn't my timer running?"

1. `list_timers` — check next trigger time and last trigger
2. `check_enabled(timer)` — is the timer enabled?
3. `get_unit_relationships(timer)` — does it activate the right service?

## Tool Selection

Pick the right tool for the task:

| Goal | Tool |
|------|------|
| Is it running? | `check_active` |
| Is it enabled at boot? | `check_enabled` |
| What's broken? | `list_failed_units` |
| Full status + recent logs | `get_unit_status` |
| Read the unit file | `read_unit_file` |
| Specific properties | `get_unit_properties` |
| What depends on it? | `list_dependencies (reverse=True)` |
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

### Scope first, then broaden

1. Start with a specific unit + short time range + severity filter
2. If results are empty, try: alternate scope (user/system), broader time range, remove severity filter, different grep pattern
3. **Be suspicious of empty results.** Cross-check with a different tool or broader query before concluding "no issues."

### Efficient log queries

- Always specify `since` or other time constraint options to limit output — never query the entire journal
- Use `priority` to filter noise: `err` for errors, `warning` for warnings+
- Use `grep` for specific patterns rather than scanning all output
- Use `max_lines` to keep output manageable (default 100 is usually enough)
- Glob patterns work in the `unit` parameter (e.g., `rclone*` matches all rclone services)

### When to use get_json_entries

Use `get_json_entries` instead of `search_journals` when:
- You need precise field data (exact timestamps, PIDs, priority values)
- You're investigating **user unit lifecycle messages** (start/stop/fail) — `search_journals` filters on `_SYSTEMD_UNIT` which maps to `user@1000.service` for user units, not the actual unit name. `get_json_entries` correctly returns these entries.
- You need to correlate events across services by timestamp

## Troubleshooting Workflow

When investigating a problem, follow this decision tree:

1. **Survey**: `list_failed_units` (both `user=True` and `user=False`) — anything broken?
2. **Status**: `get_unit_status <unit>` — what state is it in? What do recent logs say?
3. **Errors**: `list_recent_errors` scoped to the unit — what went wrong?
4. **Config**: `read_unit_file` — is the unit file correct? Check ExecStart, dependencies, conditions
5. **Dependencies**: `list_dependencies` — is something it needs failed or missing?
6. **Broader logs**: `search_journals` without unit filter, use `grep` for related patterns
7. **Properties**: `get_unit_properties` — check resource limits (MemoryMax, CPUQuota), restart policy (Restart, RestartUSec), conditions (ConditionPath*)
8. **Relationships**: `get_unit_relationships` — what triggers it? What does it conflict with?

### Common patterns

- **Service keeps restarting**: Check `get_unit_properties` for Restart=always + short RestartUSec. Check `list_recent_errors` for the crash reason.
- **Timer not firing**: Check `list_timers` for next trigger time. Check `get_unit_relationships` to confirm the timer activates the right service. Check `check_enabled` on the timer.
- **Service won't start**: Check `read_unit_file` for ConditionPath/AssertPath that might be failing. Check dependencies with `list_dependencies`.
- **"Unit not found"**: Try the other scope (user/system). Check exact unit name with `list_units`.

## Version-Aware Guidance

- Check the version from `tool_info()` before assuming flag availability
- systemd features vary significantly: journal namespaces (v245+), soft-reboot (v254+), run0 (v256+)
- **Distro build flags**: distributions may compile systemd with `--without` flags that disable features. Help and man pages may still document these features. If a flag returns "unrecognized option", try alternatives — don't assume the docs are wrong, the build may differ.

## Known Quirks

- **`search_journals` and user units**: The unit filter uses `_SYSTEMD_UNIT` which maps to `user@1000.service` (the manager), not the actual unit. For user unit lifecycle messages ("Failed to start", "Failed with result"), use `get_json_entries` instead.
- **`since` format**: Use journalctl-native formats: `"2 days ago"`, `"yesterday"`, `"2026-03-01"`. The MCP tool handles conversion from shorthand like `"2d"` but `get_json_entries` is more reliable with native formats.
- **Priority levels for failures**: "Failed with result 'exit-code'" is PRIORITY 4 (warning). "Failed to start [unit]" is PRIORITY 3 (err). Filter accordingly.
- **Severity inconsistency**: Units may be inconsistent or even incorrect with their use of the severity flag. Don't rely solely on priority filters — if a severity-filtered query returns nothing, try without the filter.
