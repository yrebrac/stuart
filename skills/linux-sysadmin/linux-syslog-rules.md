---
name: linux-syslog
description: >
    Domain knowledge for flat-file log analysis: syslog daemons (rsyslog,
    syslog-ng), /var/log/ files, and application text logs. Load BEFORE
    using any syslog MCP tool directly. Covers log discovery, searching,
    rotation, and syslog config interpretation.
---

# Syslog & Flat-File Logs

## Guide

This file covers flat-file log analysis — syslog daemons, /var/log/ files, and application logs.

- **Domain Model** — when to use syslog vs journald, two-tier logging
- **Heuristics** — expert shortcuts for log investigation
- **Anti-patterns** — common mistakes with flat-file log analysis
- **Procedures** — log discovery, searching, rotation investigation
- **Tools** — goal-to-tool lookup for the syslog MCP server
- **Query Strategy** — discovery-first approach, efficient searching
- **Safety** — privilege requirements, cross-domain pointers
- **Quirks** — RFC 3164 timestamps, binary logs, distro differences
- **Domain Deep Knowledge** — syslog format details, common file locations (inline)

## Domain Model

**When to use syslog vs journald:**

| Situation | Use |
|-----------|-----|
| Service managed by systemd | Journald first |
| journald + rsyslog forwarding | Journald first, syslog for retention beyond journal |
| No journald (pure syslog system) | Syslog tools |
| Application logs in /var/log/ (nginx, apache, postgres) | Syslog tools |
| Compressed/rotated historical logs | Syslog tools (`search_logs` with `include_rotated`) |
| Kernel messages | Journald (`get_kernel_log`) |

**Two-tier logging:** Many modern applications (dnf5, snapper, tuned) log to both journald and flat files at different verbosity levels. Journald gets operational status. Flat files get verbose debug/trace output. When troubleshooting, check journald first for the overview, then the flat file for diagnostic detail.

**Rule:** If journald has the data, prefer it — structured queries are faster and more precise. Use syslog tools for flat files journald does not index, or when you need verbose detail only written to flat files.

## Heuristics

1. If journald has the data, use journald. Syslog tools are for what journald doesn't have — flat files with application-specific verbose output.
2. "Where are the logs?" should always start with `discover_logging()` — don't guess file locations, they vary by distro.
3. When searching compressed rotated logs, use a specific glob pattern rather than wildcards — `search_logs` with `include_rotated=True` across many .gz files is slow.
4. If a log file seems empty or missing, check if logrotate just ran — `check_rotation` shows timing and file sizes.

## Anti-patterns

- Don't assume log file locations across distros. `/var/log/syslog` (Debian) vs `/var/log/messages` (RHEL). Use `discover_logging` and `list_log_files`.
- Don't `read_log` binary log files (btmp, wtmp, lastlog) — they contain binary data. Use `last`, `lastb` via Bash.
- Don't scan entire log files with `read_log` when you can `search_logs` with a pattern.
- Don't ignore the journald side — many rsyslog-routed files have the same data in journald, often more easily accessible.

## Procedures

### Log landscape discovery
When starting any log investigation or the user asks "where are the logs?"

1. IF you already know the logging stack from a prior discovery or profile → skip to "Event search"
2. `discover_logging()` — what daemon, what files, what's active
3. `list_log_files()` — scan specific directories if needed
4. Cross-reference with journald — many services log to both
5. VERIFY: You know the logging setup (daemon, files, forwarding config)

### Event search
When searching for specific events or patterns in logs.

1. `search_logs(pattern, path)` — search a specific file
2. IF no results:
     Try `case_insensitive=True`
     Broaden the pattern
     Check rotated files: `search_logs(pattern, path, include_rotated=True)`
   IF still nothing:
     `list_log_files` — find alternative log locations
     Check if the data is in journald instead
3. VERIFY: Found the relevant log entries or confirmed they don't exist in any location

### Log rotation investigation
When a log file is growing too large or logs seem to be missing.

1. `check_rotation(file)` — current size, rotation state, compressed history
2. `list_log_files(directory)` — find large files
3. IF log growing too fast:
     `search_logs` with recent timestamps — identify what's generating volume
   IF logs seem missing:
     Check if logrotate recently ran (timing in `check_rotation`)
     Check rotated/compressed files with `include_rotated=True`
4. VERIFY: Rotation is configured and working, or identified why it isn't

### Syslog config investigation
When messages are going to the wrong file or not appearing where expected.

1. `get_syslog_config()` — understand routing rules
2. Match the facility.severity to the routing rules — where should messages go?
3. IF messages missing:
     Check if the facility/severity is being filtered out
     Check if journald is the primary destination and syslog forwarding is off
4. VERIFY: Messages route to the expected destination

## Tools

| Goal | Tool |
|------|------|
| What logging is configured? | `discover_logging` |
| What files exist in /var/log/? | `list_log_files` |
| Read recent log entries | `read_log` (tail mode) |
| Read start of a log file | `read_log` (head mode) |
| Search for a pattern | `search_logs` |
| Search including compressed history | `search_logs` with `include_rotated=True` |
| Syslog routing rules | `get_syslog_config` |
| Rotation state for a file | `check_rotation` |
| Tool capabilities | `tool_info` |
| Man page details | `read_manual` |

## Query Strategy

1. Always run `discover_logging()` before your first syslog query — understand the log landscape.
2. Start with a specific file + specific pattern. Broaden scope only if no results.
3. Use `search_logs` with a pattern rather than `read_log` + manual scanning.
4. Use `include_rotated=True` only when you need historical data (slower).
5. Use `context` parameter sparingly (1-2 lines) to see surrounding entries.
6. For multi-file searches, use glob patterns: `/var/log/syslog*`, `/var/log/nginx/*.log`.
7. Be suspicious of empty results — the log may be in a different file, directory, or in journald.

## Safety

### Privilege

These files typically require root/sudo:
- `/var/log/auth.log`, `/var/log/secure` (authentication logs)
- `/var/log/btmp` (failed logins — binary, use `lastb`)
- Some application logs with restrictive permissions

Stuart auto-escalates via polkit when configured. Check if journald has the same data — it often does, especially for rsyslog-routed files.

### Cross-references

- For systemd service logs → `linux-systemd-rules.md` "Log investigation" (journald is primary)
- If log volume is causing disk issues → `linux-block-device-rules.md` "Disk full investigation"
- If auth/security logs show suspicious activity → investigate with journald MCP for correlated events

## Quirks

- **No year in RFC 3164 timestamps**: Lines like `Mar  3 14:25:01` have no year. Ambiguous around year boundaries.
- **grep vs zgrep performance**: zgrep decompresses on the fly. Many .gz files is slow. Use specific glob patterns.
- **Empty rotated files**: Some logrotate configs create empty files after rotation. `check_rotation` shows sizes.
- **Binary log files**: btmp, wtmp, lastlog are binary. `read_log` returns garbled output — use `last`, `lastb` via Bash.
- **Symlinked log paths**: Some distros symlink `/var/log/syslog`. Tools resolve symlinks transparently.
- **Log file encoding**: Most logs are UTF-8/ASCII. "Binary file matches" from grep may indicate mixed binary/text data.
- **Distro differences**: Don't assume file locations. Use `discover_logging` and `list_log_files`.

## Domain Deep Knowledge

### Syslog formats

**RFC 3164 (BSD Syslog)** — most common in flat files:
```
<priority>timestamp hostname process[pid]: message
```
Example: `Mar  3 14:25:01 webserver sshd[12345]: Accepted publickey for user`
Fields: month day time hostname process PID message. **No year. No timezone.**

**RFC 5424 (IETF Syslog)** — more common in network syslog:
```
<priority>version timestamp hostname app-name procid msgid structured-data msg
```
Example: `<165>1 2026-03-03T14:25:01.003Z webserver sshd 12345 - - Accepted publickey`
Fields: priority, version, ISO timestamp (with year+tz), hostname, app, PID, msgid, structured-data, message.

### Common log file locations

| Log | Debian/Ubuntu | RHEL/Fedora |
|-----|--------------|-------------|
| System syslog | /var/log/syslog | /var/log/messages |
| Auth/security | /var/log/auth.log | /var/log/secure |
| Cron | /var/log/syslog (cron facility) | /var/log/cron |
| Mail | /var/log/mail.log | /var/log/maillog |

| Application | Typical path |
|-------------|-------------|
| Nginx | /var/log/nginx/access.log, /var/log/nginx/error.log |
| Apache | /var/log/httpd/ or /var/log/apache2/ |
| PostgreSQL | /var/log/postgresql/ |
| MySQL/MariaDB | /var/log/mysql/ or /var/log/mariadb/ |
