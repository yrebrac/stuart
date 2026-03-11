#!/usr/bin/env python3
"""
Stuart — journald MCP Server

Exposes systemd journal querying as MCP tools for Claude Code.
Runs journalctl locally. All tools are read-only.

Usage:
    python3 journalctl_mcp.py

Tested on:
    - Fedora 43, systemd 258, Python 3.14

Argument tier decisions (see docs/TOOL_CONVENTION.md):
    Tier 1 (exposed as params): -u, --since, -p, --grep, -n, -b, -k
    Tier 2 (exposed as param or separate tool): --user, -o json (get_json_entries tool)
    Tier 3 (handled internally): --no-pager
    Tier 4 (omitted): --follow, --flush, --rotate, --vacuum-*, --cursor,
        --after-cursor, --merge, --directory, --file, --root, --image,
        --namespace, --header, --field (except list_units), --list-boots,
        --update-catalog, --new-id128, --setup-keys, --verify,
        --sync, --relinquish-var
"""

import sys
import subprocess
import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_check import ToolCache

server = FastMCP(
    name="sysops-journalctl",
    instructions="Query systemd journals. All tools are read-only."
)

_tool = ToolCache(
    tool_name="journalctl",
    tool_path="/usr/bin/journalctl",
    version_args=["--version"],
    help_args=["--help"],
)


def _run_journalctl(args: list[str], max_lines: int = 200, user: bool = False) -> str:
    """Run journalctl with the given arguments. Returns stdout or stderr.

    Output is truncated to max_lines to keep token usage reasonable.
    Always uses --no-pager to prevent blocking.
    """
    cmd = ["/usr/bin/journalctl", "--no-pager"]

    if user:
        cmd.append("--user")

    cmd += args

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
    except subprocess.TimeoutExpired:
        return "Error: journalctl timed out after 30 seconds. Try a narrower query."

    if result.returncode != 0:
        stderr = result.stderr or ""
        if "No journal files were found" in stderr or "Permission denied" in stderr:
            scope = "user session" if user else "system"
            return (
                f"Cannot access {scope} journals. "
                f"{'Is the user session running?' if user else 'User may need systemd-journal group membership.'}\n\n"
                f"stderr: {stderr.strip()}"
            )

    output = result.stdout or ""
    stderr = result.stderr or ""

    # Non-zero exit with no stdout — something went wrong beyond the
    # permission checks above (e.g. no matching entries for --grep)
    if result.returncode != 0 and not output.strip():
        if stderr.strip():
            return f"No matching entries.\n\n[stderr]: {stderr.strip()}"
        return "(no matching entries)"

    if not output.strip():
        return "(no matching entries)"

    # Truncate if needed, keeping the most recent lines (tail)
    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing last {max_lines} of {len(lines)} lines. "
            f"Narrow your search for full results.]\n\n"
            + "\n".join(lines[-max_lines:])
        )

    return output


@server.tool()
def tool_info() -> str:
    """Return version and availability for journalctl.

    Call this at the start of a session to see which version of
    systemd/journalctl is available.
    """
    info = _tool.info()
    result = {
        "journalctl": {
            "exists": info.get("exists", False),
            "path": info.get("path"),
            "version": info.get("version_raw"),
        }
    }
    return json.dumps(result, indent=2)


@server.tool()
def read_manual(
    section: str = ""
) -> str:
    """Read the journalctl man page, optionally extracting a specific section.

    Use this as a last resort when tool_info() help text doesn't answer
    your question about available options or behavior.

    Args:
        section: Section to extract, e.g. "OPTIONS", "DESCRIPTION",
                 "EXIT STATUS", "ENVIRONMENT". Leave empty for full
                 page (truncated to 200 lines).
    """
    return _tool.read_man(section=section)


@server.tool()
def search_journals(
    unit: str = "",
    since: str = "24h",
    priority: str = "",
    grep: str = "",
    max_lines: int = 100,
    user: bool = False
) -> str:
    """Search systemd journals.

    IMPORTANT: For user units (user=True), the -u filter matches on
    _SYSTEMD_UNIT which is "user@1000.service" (the manager), NOT the
    actual unit name. This means lifecycle messages like "Failed to start"
    or "Started" will NOT appear. Use get_json_entries instead for user
    unit lifecycle events — it returns the correct entries.

    Args:
        unit: Systemd unit name, e.g. "nginx.service", "sshd.service".
              Include the suffix (.service, .timer, etc.) for precision.
              Glob patterns supported, e.g. "rclone*".
              Leave empty to search all units.
        since: How far back to search, e.g. "1h", "6h", "24h", "2d",
               "2025-01-15", "today", "yesterday".
        priority: Minimum severity filter. One of: emerg, alert, crit,
                  err, warning, notice, info, debug. Leave empty for all.
                  Note: "Failed with result" is priority 4 (warning),
                  "Failed to start" is priority 3 (err).
        grep: Pattern to search for in log messages.
        max_lines: Maximum lines to return (default 100). Keeps token usage low.
        user: True for user session journals (user services like rclone,
              syncthing). False for system journals (default).
              See IMPORTANT note above about user unit limitations.
    """
    args = []

    if unit:
        args += ["-u", unit]
    if since:
        args += ["--since", f"{since} ago" if since[0].isdigit() else since]
    if priority:
        args += ["-p", priority]
    if grep:
        args += ["--grep", grep]

    return _run_journalctl(args, max_lines, user=user)


@server.tool()
def list_recent_errors(
    since: str = "24h",
    unit: str = "",
    max_lines: int = 100,
    user: bool = False
) -> str:
    """Get recent error and critical journals (priority err and above).

    Note: "Failed with result" messages are priority 4 (warning), so they
    won't appear here. Use search_journals with priority="warning" or
    get_json_entries to find those.

    Args:
        since: How far back to look, e.g. "1h", "6h", "24h".
        unit: Limit to a specific unit (glob patterns supported), or
              leave empty for all. Include the suffix for precision.
        max_lines: Maximum lines to return.
        user: True for user session journals (user services like rclone,
              syncthing). False for system journals (default).
              Same user unit limitation as search_journals applies.
    """
    args = ["-p", "err", "--since", f"{since} ago" if since[0].isdigit() else since]

    if unit:
        args += ["-u", unit]

    return _run_journalctl(args, max_lines, user=user)


@server.tool()
def list_units(
    user: bool = False
) -> str:
    """List all systemd units that have journal entries.
    Useful for discovering valid unit names before searching.

    Use this to validate unit names — don't guess. If you're unsure
    of a unit name, list first, then search.

    Args:
        user: True for user session units, False for system units.
              For user units, lists USER_UNIT field values (the actual
              unit names like rclone_bisync.service, not the manager).
    """
    field = "USER_UNIT" if user else "_SYSTEMD_UNIT"
    args = [f"--field={field}"]
    return _run_journalctl(args, max_lines=500, user=user)


@server.tool()
def get_boot_log(
    boot: str = "0",
    priority: str = "",
    max_lines: int = 100,
    user: bool = False
) -> str:
    """Get journal entries for a specific boot.

    Args:
        boot: Boot ID or offset. "0" = current boot, "-1" = previous boot.
        priority: Minimum severity filter (e.g. "err", "warning").
        max_lines: Maximum lines to return.
        user: True for user session journals (user services like rclone,
              syncthing). False for system journals (default).
    """
    args = ["-b", boot]

    if priority:
        args += ["-p", priority]

    return _run_journalctl(args, max_lines, user=user)


@server.tool()
def get_kernel_log(
    since: str = "1h",
    priority: str = "",
    max_lines: int = 100,
) -> str:
    """Get kernel messages (dmesg equivalent from journal).

    Kernel messages are always system-level — no user/system scope distinction.

    Args:
        since: How far back to look, e.g. "1h", "6h", "24h", "2d".
        priority: Minimum severity filter (e.g. "err", "warning").
        max_lines: Maximum lines to return.
    """
    args = ["-k", "--since", f"{since} ago" if since[0].isdigit() else since]

    if priority:
        args += ["-p", priority]

    return _run_journalctl(args, max_lines)


@server.tool()
def check_disk_usage() -> str:
    """Show how much disk space the journal is consuming."""
    return _run_journalctl(["--disk-usage"], max_lines=10)


@server.tool()
def get_json_entries(
    unit: str = "",
    since: str = "1h",
    grep: str = "",
    max_entries: int = 100,
    user: bool = False
) -> str:
    """Get journal entries as structured JSON. Useful when precise field
    data is needed (timestamps, PIDs, exact priority values).

    Preferred over search_journals for user units (user=True) — correctly
    returns lifecycle messages (start/stop/fail) that search_journals
    misses due to the _SYSTEMD_UNIT field mapping issue.

    Args:
        unit: Systemd unit name (glob patterns supported). Leave empty for all.
        since: How far back to search, e.g. "1h", "6h", "24h", "2d",
               "2025-01-15", "today", "yesterday".
        grep: Pattern to search for.
        max_entries: Maximum number of entries to return.
        user: True for user session journals (user services like rclone,
              syncthing). False for system journals (default).
    """
    args = ["-o", "json", f"-n{max_entries}"]

    if unit:
        args += ["-u", unit]
    if since:
        args += ["--since", f"{since} ago" if since[0].isdigit() else since]
    if grep:
        args += ["--grep", grep]

    return _run_journalctl(args, max_lines=max_entries * 5, user=user)


if __name__ == "__main__":
    server.run(transport="stdio")
