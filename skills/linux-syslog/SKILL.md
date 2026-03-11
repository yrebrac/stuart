---
name: linux-syslog
description: >
    Domain knowledge for flat-file log analysis: syslog daemons (rsyslog,
    syslog-ng), /var/log/ files, and application text logs. Load BEFORE
    using any syslog MCP tool directly. Covers log discovery, searching,
    rotation, and syslog config interpretation.
---

# Syslog & Flat-File Logs

## Session Start

1. Call `tool_info()` to verify text analysis tools are available
2. Call `discover_logging()` before your first syslog query to understand the log landscape
3. Note whether journald forwards to syslog — this affects where to look

## When to Use This vs Journald

| Situation | Use |
|-----------|-----|
| Service managed by systemd | Journald first |
| System has journald + rsyslog forwarding | Journald first, syslog for retention beyond journal |
| No journald (pure syslog system) | Syslog tools |
| Application logs in /var/log/ (nginx, apache, postgres) | Syslog tools |
| Compressed/rotated historical logs | Syslog tools (`search_logs` with `include_rotated`) |
| Kernel messages | Journald (`get_kernel_log`) |

**Two-tier logging:** Many modern applications (dnf5, snapper, tuned) log to both journald and flat files at different verbosity levels. Journald gets operational status (did it run, did it succeed). Flat files get verbose debug/trace output (solver decisions, snapshot internals, cache details). When troubleshooting, check journald first for the overview, then the flat file for diagnostic detail.

**Rule:** If journald has the data, prefer it — structured queries are faster and more precise. Use syslog tools for flat files that journald does not index, or when you need the verbose detail that applications only write to their flat files.

## Common Tasks

### "Where are the logs?"

1. `discover_logging()` — what daemon, what files, what's active
2. `list_log_files()` — scan specific directories if needed
3. Cross-reference with journald — many services log to both

### "What happened recently?"

1. `read_log(file, lines=50)` — tail the relevant file
2. If you don't know which file: `discover_logging()` first, then check the most recent

### "Search for X in logs"

1. `search_logs(pattern, path)` — search a specific file
2. If no results: try `case_insensitive=True`, broaden the pattern
3. For historical data: `search_logs(pattern, path, include_rotated=True)`

### "Is my log eating disk?"

1. `check_rotation(file)` — current size, rotation state, compressed history
2. `list_log_files(directory)` — find large files

For syslog format details and distro-specific file locations, see sections below.

## Syslog Format Awareness

### RFC 3164 (BSD Syslog) — most common in flat files

```
<priority>timestamp hostname process[pid]: message
```

Example: `Mar  3 14:25:01 webserver sshd[12345]: Accepted publickey for user`

Fields: month day time hostname process PID message. **No year. No timezone.**

### RFC 5424 (IETF Syslog) — more common in network syslog

```
<priority>version timestamp hostname app-name procid msgid structured-data msg
```

Example: `<165>1 2026-03-03T14:25:01.003Z webserver sshd 12345 - - Accepted publickey`

Fields: priority, version, ISO timestamp (with year+tz), hostname, app, PID, msgid, structured-data, message.

## Tool Selection

| Goal | Tool |
|------|------|
| What logging is configured? | `discover_logging` |
| What files exist in /var/log/? | `list_log_files` |
| Read recent log entries | `read_log` (tail mode) |
| Read start of a log file | `read_log` (head mode) |
| Search for a pattern | `search_logs` |
| Search including compressed history | `search_logs` with `include_rotated=True` or glob path |
| Syslog routing rules | `get_syslog_config` |
| Rotation state for a file | `check_rotation` |
| Tool capabilities | `tool_info` |
| Man page details | `read_manual` |

## Query Strategy

### Discovery first

1. `discover_logging()` — what daemon, what files, what's recent
2. `list_log_files()` — scan specific directories if needed
3. Then query specific files

### Scope first, then broaden

1. Start with a specific file + specific pattern
2. If no results: try `case_insensitive=True`, broaden the pattern, check rotated files
3. If still nothing: use `list_log_files` to find alternative log locations
4. **Be suspicious of empty results.** The log may be in a different file or directory.

### Efficient searching

- Use `search_logs` with a pattern rather than `read_log` + manual scanning
- Use `include_rotated=True` only when you need historical data (slower)
- Use `context` parameter sparingly (1-2 lines) to see surrounding entries
- For multi-file searches, use glob patterns: `/var/log/syslog*`, `/var/log/nginx/*.log`
- Keep `max_lines` reasonable — 200 default is usually enough

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

## Troubleshooting Workflow

1. **Discover**: `discover_logging()` — what's the log landscape?
2. **Locate**: `list_log_files()` — find the relevant file(s)
3. **Recent**: `read_log(file, lines=50)` — see recent activity
4. **Search**: `search_logs(pattern, path)` — find specific events
5. **History**: `search_logs(pattern, path, include_rotated=True)` — check older logs
6. **Config**: `get_syslog_config()` — understand routing if messages are missing
7. **Rotation**: `check_rotation(file)` — check if logs were rotated away

## Sudo Considerations

These files typically require root/sudo:
- /var/log/auth.log, /var/log/secure (authentication logs)
- /var/log/btmp (failed logins — binary, use `lastb` via Bash)
- Some application logs with restrictive permissions

If permission denied: the tool will say so. Stuart auto-escalates via polkit when configured. Check whether journald has the same data (it often does, especially for rsyslog-routed files) — journald may be accessible without sudo. If the flat file contains unique data, see PRIVILEGES.md for polkit setup.

## Known Quirks

- **No year in RFC 3164 timestamps**: Lines like `Mar  3 14:25:01` have no year. Be aware of ambiguity around year boundaries when correlating with journald.
- **grep vs zgrep performance**: zgrep decompresses on the fly. Searching many .gz files is slow. Use a specific glob pattern rather than wildcards.
- **Empty rotated files**: Some logrotate configs create empty files after rotation. `check_rotation` shows sizes so you can skip empty ones.
- **Binary log files**: btmp, wtmp, lastlog contain binary data. `read_log` will return garbled output for these — use `last`, `lastb` via Bash instead.
- **Symlinked log paths**: Some distros symlink `/var/log/syslog` to other locations. The tools resolve symlinks transparently.
- **Log file encoding**: Most logs are UTF-8 or ASCII. If grep reports "binary file matches", the file may contain non-text data mixed with log entries.
- **Distro differences**: Don't assume file locations. Use `discover_logging` and `list_log_files` to find where logs actually live on this system.
