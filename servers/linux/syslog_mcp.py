#!/usr/bin/env python3
"""
Stuart -- Syslog MCP Server

Exposes flat-file log analysis as MCP tools for Claude Code.
Wraps GNU text tools for searching, reading, and analysing log files
in /var/log/ and elsewhere. Covers syslog daemons (rsyslog, syslog-ng)
and general application text logs. All tools are read-only.

Usage:
    python3 syslog_mcp.py

Tested on:
    - Fedora 43, Python 3.14

Underlying tools:
    grep, zgrep, zcat, tail, head, wc, find, file, stat

Argument tier decisions (see docs/TOOL_CONVENTION.md):
    Tier 1 (exposed as params):
        file path, search pattern, max_lines, case-insensitive,
        include-rotated, tail/head line count, context lines
    Tier 2 (param or separate tool):
        mode (head/tail), recursive directory scan
    Tier 3 (handled internally):
        -n (line numbers), -H (filenames in multi-file grep),
        --no-filename (single-file grep), -printf format
    Tier 4 (omitted):
        --follow, -r (unbounded recursive grep),
        any write/delete/truncate operations

Scope exclusions:
    - Kernel logging (covered by journald and block-device servers)
    - Centralized/remote logging (future separate skill)
    - SQL-based log queries
    - Binary log files (wtmp, btmp, lastlog — use last/lastb via Bash)
"""

import glob as globmod
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_check import ToolCache

server = FastMCP(
    name="sysops-syslog",
    instructions=(
        "Analyse flat-file logs: syslog daemons, /var/log/ files, "
        "and application text logs. All tools are read-only."
    ),
)

# -- ToolCache instances ──────────────────────────────────────────
# All expected on standard Linux installations.
_tools: dict[str, ToolCache] = {
    "grep": ToolCache("grep", "/usr/bin/grep", ["--version"], ["--help"]),
    "zgrep": ToolCache("zgrep", "/usr/bin/zgrep", ["--version"], ["--help"]),
    "zcat": ToolCache("zcat", "/usr/bin/zcat", ["--version"], ["--help"]),
    "tail": ToolCache("tail", "/usr/bin/tail", ["--version"], ["--help"]),
    "head": ToolCache("head", "/usr/bin/head", ["--version"], ["--help"]),
    "wc": ToolCache("wc", "/usr/bin/wc", ["--version"], ["--help"]),
    "find": ToolCache("find", "/usr/bin/find", ["--version"], ["--help"]),
    "file": ToolCache("file", "/usr/bin/file", ["--version"], ["--help"]),
    "stat": ToolCache("stat", "/usr/bin/stat", ["--version"], ["--help"]),
}

_PACKAGE_HINTS: dict[str, str] = {
    "zgrep": "gzip",
    "zcat": "gzip",
}


# -- Shared helpers ───────────────────────────────────────────────

def _run_cmd(
    cmd: list[str],
    max_lines: int = 200,
    timeout: int = 30,
) -> str:
    """Run a command. Returns stdout or error message.

    Output is truncated to max_lines (tail) to keep token usage
    reasonable.
    """
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return (
            f"Error: command timed out after {timeout} seconds. "
            "Try narrowing the query."
        )
    except FileNotFoundError:
        return f"Error: command not found: {cmd[0]}"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "Permission denied" in stderr or "Operation not permitted" in stderr:
            return (
                f"Permission denied. This file may require sudo.\n\n"
                f"stderr: {stderr}"
            )
        # grep returns 1 for "no matches" — not an error
        if result.returncode == 1 and not stderr:
            return "(no matches)"
        if result.stdout.strip():
            output = result.stdout.strip()
            if stderr:
                output += f"\n\n[stderr]: {stderr}"
        else:
            return f"Error: {stderr or '(no output)'}"
    else:
        output = result.stdout or result.stderr or "(no output)"

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing last {max_lines} of {len(lines)} lines. "
            f"Narrow your search for full results.]\n\n"
            + "\n".join(lines[-max_lines:])
        )
    return output.strip()


def _run_pipeline(
    cmd1: list[str],
    cmd2: list[str],
    max_lines: int = 200,
    timeout: int = 30,
) -> str:
    """Run two commands piped together (cmd1 | cmd2).

    Avoids shell=True for security. Used for zcat | tail/head.
    """
    try:
        p1 = subprocess.Popen(
            cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        p2 = subprocess.Popen(
            cmd2, stdin=p1.stdout, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        p1.stdout.close()
        stdout, stderr = p2.communicate(timeout=timeout)
        p1.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p1.kill()
        p2.kill()
        return f"Error: pipeline timed out after {timeout} seconds."
    except FileNotFoundError as e:
        return f"Error: command not found: {e.filename}"

    output = stdout.decode("utf-8", errors="replace")
    if not output.strip():
        err = stderr.decode("utf-8", errors="replace").strip()
        if err:
            return f"Error: {err}"
        return "(no output)"

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing last {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[-max_lines:])
        )
    return output.strip()


def _validate_path(path: str) -> str | None:
    """Validate a log file path. Returns error message or None if valid."""
    p = Path(path)
    try:
        resolved = p.resolve()
    except (OSError, ValueError):
        return f"Invalid path: {path}"

    if not resolved.exists():
        return f"File not found: {path}"

    if resolved.is_dir():
        return f"Path is a directory, not a file: {path}"

    if not resolved.is_file():
        return f"Not a regular file: {path}"

    return None


def _human_size(nbytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ("B", "K", "M", "G", "T"):
        if nbytes < 1024:
            if unit == "B":
                return f"{nbytes}{unit}"
            return f"{nbytes:.1f}{unit}"
        nbytes /= 1024
    return f"{nbytes:.1f}P"


# -- Standard tools ───────────────────────────────────────────────

@server.tool()
def tool_info() -> str:
    """Return version and availability for all text analysis tools.

    Call this at the start of a syslog investigation to verify
    which tools are available on this system.
    """
    result = {}
    for name, cache in sorted(_tools.items()):
        info = cache.info()
        result[name] = {
            "exists": info.get("exists", False),
            "path": info.get("path"),
            "version": info.get("version_raw"),
        }
    return json.dumps(result, indent=2)


@server.tool()
def read_manual(
    tool: str,
    section: str = "",
) -> str:
    """Read the man page for a specific text analysis tool.

    Args:
        tool: Command name, e.g. "grep", "zgrep", "tail", "head",
              "find", "stat", "wc", "file", "zcat".
        section: Section to extract, e.g. "OPTIONS", "DESCRIPTION".
                 Leave empty for full page (truncated).
    """
    if tool not in _tools:
        return (
            f"Unknown tool '{tool}'. "
            f"Available: {', '.join(sorted(_tools.keys()))}"
        )
    return _tools[tool].read_man(section=section)


# -- Discovery ────────────────────────────────────────────────────

@server.tool()
def discover_logging(max_lines: int = 100) -> str:
    """Detect syslog daemon, scan /var/log/, and report the log landscape.

    Returns the active syslog daemon (rsyslog, syslog-ng, or none),
    whether journald forwards to syslog, and a summary of /var/log/
    contents sorted by most recently modified.

    Call this before your first syslog query to understand what
    logging is configured on this system.

    Args:
        max_lines: Maximum log files to list in the /var/log/ scan.
    """
    sections = []

    # 1. Detect syslog daemon
    sections.append(f"## Syslog Daemon\n{_detect_syslog_daemon()}")

    # 2. Check journald forwarding
    sections.append(f"## Journald Forwarding\n{_check_journald_forwarding()}")

    # 3. Scan /var/log/
    sections.append(f"## /var/log/ Contents\n{_scan_var_log(max_lines)}")

    return "\n\n".join(sections)


def _detect_syslog_daemon() -> str:
    """Detect which syslog daemon is running."""
    for daemon, unit in [
        ("rsyslog", "rsyslog.service"),
        ("syslog-ng", "syslog-ng.service"),
    ]:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip() == "active":
                # Get version
                ver = ""
                try:
                    v = subprocess.run(
                        ["systemctl", "show", unit,
                         "--property=Description"],
                        capture_output=True, text=True, timeout=5
                    )
                    ver = v.stdout.strip()
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
                return f"{daemon} is active ({unit})\n{ver}".strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    # Fallback: check for syslog process
    try:
        result = subprocess.run(
            ["pgrep", "-la", "syslog"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return f"Syslog process found:\n{result.stdout.strip()}"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return "No syslog daemon detected. System may use journald only."


def _check_journald_forwarding() -> str:
    """Check if journald is configured to forward to syslog.

    Checks admin override (/etc/) first, then vendor default
    (/usr/lib/). Admin override takes precedence.
    """
    conf_paths = [
        Path("/etc/systemd/journald.conf"),
        Path("/usr/lib/systemd/journald.conf"),
    ]

    for conf_path in conf_paths:
        if not conf_path.exists():
            continue
        try:
            content = conf_path.read_text()
        except PermissionError:
            continue

        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("ForwardToSyslog="):
                val = stripped.split("=", 1)[1].strip().lower()
                source = f"({conf_path})"
                if val == "yes":
                    return f"ForwardToSyslog=yes — journald forwards to syslog. {source}"
                elif val == "no":
                    return f"ForwardToSyslog=no — journald does NOT forward. {source}"
                else:
                    return f"ForwardToSyslog={val} {source}"

        # File found but no ForwardToSyslog line — check next file
        # only if this was the admin override
        if conf_path == conf_paths[0]:
            continue
        break

    # Check if journald is running at all
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "systemd-journald"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip() == "active":
            return "ForwardToSyslog not set (default: no in modern systemd). journald is active."
        return "journald does not appear to be running."
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "ForwardToSyslog not set. Could not check journald status."


def _scan_var_log(max_lines: int) -> str:
    """Scan /var/log/ and return summary sorted by mtime."""
    cmd = [
        "/usr/bin/find", "/var/log",
        "-maxdepth", "2",
        "-type", "f",
        "-printf", "%T@ %s %p\n",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
    except subprocess.TimeoutExpired:
        return "Scan timed out."
    except FileNotFoundError:
        return "find command not found."

    if result.returncode != 0 and not result.stdout.strip():
        return f"Error scanning /var/log/: {result.stderr.strip()}"

    entries = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(" ", 2)
        if len(parts) == 3:
            try:
                mtime = float(parts[0])
                size = int(parts[1])
                path = parts[2]
                entries.append((mtime, size, path))
            except (ValueError, IndexError):
                continue

    entries.sort(reverse=True)  # Most recent first

    lines = []
    for mtime, size, path in entries[:max_lines]:
        dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        size_h = _human_size(size)
        lines.append(f"{dt}  {size_h:>8}  {path}")

    total = len(entries)
    shown = min(total, max_lines)
    header = f"Found {total} log files (showing {shown} most recent):\n"
    return header + "\n".join(lines)


# -- Log file listing ─────────────────────────────────────────────

@server.tool()
def list_log_files(
    path: str = "/var/log",
    recursive: bool = False,
    max_files: int = 50,
) -> str:
    """List log files in a directory with size, modification time, and type.

    Args:
        path: Directory to scan. Default "/var/log".
        recursive: Scan subdirectories. Default False (top-level only).
        max_files: Maximum files to return, sorted by most recently
                   modified.
    """
    p = Path(path)
    if not p.is_dir():
        return f"Not a directory: {path}"

    depth = "999" if recursive else "1"
    cmd = [
        "/usr/bin/find", str(p),
        "-maxdepth", depth,
        "-type", "f",
        "-printf", "%T@ %s %p\n",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
    except subprocess.TimeoutExpired:
        return f"Scan of {path} timed out."
    except FileNotFoundError:
        return "find command not found."

    if result.returncode != 0 and not result.stdout.strip():
        return f"Error scanning {path}: {result.stderr.strip()}"

    entries = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(" ", 2)
        if len(parts) == 3:
            try:
                entries.append((float(parts[0]), int(parts[1]), parts[2]))
            except ValueError:
                continue

    entries.sort(reverse=True)

    lines = []
    for mtime, size, filepath in entries[:max_files]:
        dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        size_h = _human_size(size)
        suffix = ""
        if filepath.endswith(".gz"):
            suffix = " [compressed]"
        elif filepath.endswith(".journal") or filepath.endswith(".db"):
            suffix = " [binary]"
        lines.append(f"{dt}  {size_h:>8}  {filepath}{suffix}")

    return (
        f"Files in {path} ({len(entries)} total, showing {len(lines)}):\n"
        + "\n".join(lines)
    )


# -- Log reading ──────────────────────────────────────────────────

@server.tool()
def read_log(
    file: str,
    lines: int = 100,
    mode: str = "tail",
    max_lines: int = 200,
) -> str:
    """Read entries from a log file. Supports plain text and .gz files.

    Args:
        file: Path to the log file, e.g. "/var/log/syslog",
              "/var/log/nginx/access.log.1.gz".
        lines: Number of lines to read (default 100).
        mode: "tail" (default) for most recent lines, "head" for
              first lines.
        max_lines: Maximum lines to return after processing.
    """
    err = _validate_path(file)
    if err:
        return err

    if mode not in ("tail", "head"):
        return "Invalid mode. Use 'tail' or 'head'."

    if file.endswith(".gz"):
        # Pipe: zcat file | tail/head -n N
        return _run_pipeline(
            ["/usr/bin/zcat", file],
            [f"/usr/bin/{mode}", f"-n{lines}"],
            max_lines=max_lines,
        )
    else:
        cmd = [f"/usr/bin/{mode}", f"-n{lines}", file]
        return _run_cmd(cmd, max_lines=max_lines)


# -- Log searching ────────────────────────────────────────────────

@server.tool()
def search_logs(
    pattern: str,
    path: str,
    case_insensitive: bool = False,
    include_rotated: bool = False,
    context: int = 0,
    max_lines: int = 200,
) -> str:
    """Search log files for a pattern using grep/zgrep.

    Args:
        pattern: Regex pattern to search for, e.g. "error",
                 "Failed password", "OOM killer".
        path: File path or glob pattern. Single file:
              "/var/log/syslog". Glob: "/var/log/syslog*"
              (includes rotated/compressed). Glob:
              "/var/log/nginx/*.log".
        case_insensitive: Ignore case in pattern matching.
        include_rotated: When path is a single file (not a glob),
                         also search rotated variants (.1, .2.gz,
                         etc.). Ignored if path is already a glob.
        context: Lines of context around each match (0 = match only).
        max_lines: Maximum lines to return.
    """
    is_glob = any(c in path for c in "*?[")

    if is_glob:
        files = sorted(globmod.glob(path))
        if not files:
            return f"No files match pattern: {path}"
    else:
        err = _validate_path(path)
        if err:
            return err

        if include_rotated:
            rotated = sorted(globmod.glob(f"{path}.*"))
            files = [path] + rotated
        else:
            files = [path]

    # Separate compressed and plain files
    gz_files = [f for f in files if f.endswith(".gz")]
    plain_files = [f for f in files if not f.endswith(".gz")]

    outputs = []

    # Search plain files with grep
    if plain_files:
        cmd = ["/usr/bin/grep", "-n"]
        if case_insensitive:
            cmd.append("-i")
        if context > 0:
            cmd.append(f"-C{context}")
        if len(plain_files) > 1 or gz_files:
            cmd.append("-H")
        cmd += ["--", pattern] + plain_files
        out = _run_cmd(cmd, max_lines=max_lines)
        if out != "(no matches)":
            outputs.append(out)

    # Search compressed files with zgrep
    if gz_files:
        cmd = ["/usr/bin/zgrep", "-n"]
        if case_insensitive:
            cmd.append("-i")
        if context > 0:
            cmd.append(f"-C{context}")
        if len(gz_files) > 1 or plain_files:
            cmd.append("-H")
        cmd += ["--", pattern] + gz_files
        out = _run_cmd(cmd, max_lines=max_lines)
        if out != "(no matches)":
            outputs.append(out)

    if not outputs:
        file_desc = path
        if not is_glob and include_rotated and len(files) > 1:
            file_desc = f"{path} (+ {len(files) - 1} rotated)"
        return f"No matches for '{pattern}' in {file_desc}"

    combined = "\n".join(outputs)
    out_lines = combined.strip().split("\n")
    if len(out_lines) > max_lines:
        return (
            f"[Showing {max_lines} of {len(out_lines)} matching lines.]\n\n"
            + "\n".join(out_lines[:max_lines])
        )
    return combined.strip()


# -- Syslog configuration ────────────────────────────────────────

@server.tool()
def get_syslog_config(max_lines: int = 200) -> str:
    """Parse and summarise syslog daemon routing rules.

    Detects rsyslog or syslog-ng and reads their main config file.
    Shows which facilities/priorities route to which log files.
    """
    # Try rsyslog first (most common)
    rsyslog_conf = Path("/etc/rsyslog.conf")
    if rsyslog_conf.exists():
        return _parse_rsyslog_config(rsyslog_conf, max_lines)

    # Try syslog-ng
    for syslog_ng_path in [
        "/etc/syslog-ng/syslog-ng.conf",
        "/etc/syslog-ng.conf",
    ]:
        conf = Path(syslog_ng_path)
        if conf.exists():
            return _parse_syslog_ng_config(conf, max_lines)

    return (
        "No syslog configuration found. "
        "Checked /etc/rsyslog.conf, /etc/syslog-ng/syslog-ng.conf"
    )


def _parse_rsyslog_config(conf_path: Path, max_lines: int) -> str:
    """Read and summarise rsyslog.conf."""
    try:
        content = conf_path.read_text()
    except PermissionError:
        return f"Permission denied reading {conf_path}"

    lines = [f"# rsyslog config: {conf_path}", ""]

    # Extract meaningful lines: rules, includes, modules, templates
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Facility.priority rules, RainerScript directives, legacy config
        if (re.match(r"^[a-z*]+\.", stripped)
                or stripped.startswith("$IncludeConfig")
                or stripped.startswith("include(")
                or stripped.startswith("module(")
                or stripped.startswith("input(")
                or stripped.startswith("template(")
                or stripped.startswith("action(")
                or stripped.startswith("if ")
                or stripped.startswith("*.") ):
            lines.append(stripped)

    if len(lines) <= 2:
        # No rules parsed — return raw content as fallback
        lines = [f"# rsyslog config: {conf_path} (raw)", ""]
        lines.extend(content.strip().split("\n"))

    result = "\n".join(lines)
    out_lines = result.split("\n")
    if len(out_lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(out_lines)} lines.]\n\n"
            + "\n".join(out_lines[:max_lines])
        )
    return result


def _parse_syslog_ng_config(conf_path: Path, max_lines: int) -> str:
    """Read syslog-ng.conf."""
    try:
        content = conf_path.read_text()
    except PermissionError:
        return f"Permission denied reading {conf_path}"

    lines = [f"# syslog-ng config: {conf_path}", ""]
    lines.extend(content.strip().split("\n"))

    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return "\n".join(lines)


# -- Log rotation ─────────────────────────────────────────────────

@server.tool()
def check_rotation(file: str) -> str:
    """Check log rotation state for a specific log file.

    Reports current file size, rotated variants found, total size,
    and logrotate configuration if available.

    Args:
        file: Path to the log file, e.g. "/var/log/syslog",
              "/var/log/nginx/access.log".
    """
    p = Path(file)
    if not p.exists():
        return f"File not found: {file}"

    sections = []

    # Current file info
    try:
        st = p.stat()
        mtime = datetime.fromtimestamp(st.st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        sections.append(
            f"Current file: {file}\n"
            f"  Size: {_human_size(st.st_size)}\n"
            f"  Modified: {mtime}"
        )
        current_size = st.st_size
    except PermissionError:
        sections.append(f"Permission denied reading {file}")
        current_size = 0

    # Find rotated variants
    rotated = sorted(globmod.glob(f"{file}.*"))
    if rotated:
        rot_lines = []
        total_size = current_size
        for rf in rotated:
            try:
                rstat = Path(rf).stat()
                total_size += rstat.st_size
                rot_lines.append(f"  {rf} ({_human_size(rstat.st_size)})")
            except (PermissionError, OSError):
                rot_lines.append(f"  {rf} (unreadable)")
        sections.append(
            f"Rotated variants ({len(rotated)}):\n"
            + "\n".join(rot_lines) + "\n"
            + f"Total size (including current): {_human_size(total_size)}"
        )
    else:
        sections.append("No rotated variants found.")

    # Check logrotate config
    logrotate_info = _find_logrotate_config(file)
    if logrotate_info:
        sections.append(f"Logrotate config:\n{logrotate_info}")

    return "\n\n".join(sections)


def _find_logrotate_config(file: str) -> str | None:
    """Search logrotate config for rules matching this file."""
    configs = []

    logrotate_conf = Path("/etc/logrotate.conf")
    if logrotate_conf.exists():
        configs.append(str(logrotate_conf))

    logrotate_d = Path("/etc/logrotate.d")
    if logrotate_d.is_dir():
        try:
            configs.extend(
                str(p) for p in logrotate_d.iterdir() if p.is_file()
            )
        except PermissionError:
            pass

    if not configs:
        return None

    cmd = ["/usr/bin/grep", "-l", file] + configs
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            matching = result.stdout.strip().split("\n")
            sections = []
            for mf in matching[:3]:  # Cap at 3 config files
                try:
                    content = Path(mf).read_text()
                    sections.append(f"  [{mf}]\n  {content.strip()}")
                except PermissionError:
                    sections.append(f"  [{mf}] (permission denied)")
            return "\n".join(sections)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


if __name__ == "__main__":
    server.run(transport="stdio")
