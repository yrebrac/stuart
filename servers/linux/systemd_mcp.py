#!/usr/bin/env python3
"""
Stuart — systemd MCP Server

Exposes systemd unit inspection as MCP tools for Claude Code.
All tools are strictly read-only. No start/stop/restart/enable/disable.

Supports both system and user units.

Usage:
    python3 systemctl_mcp.py

Tested on:
    - Fedora 43, systemd 258, Python 3.14

Argument tier decisions (see docs/TOOL_CONVENTION.md):
    Tier 1 (exposed as params): unit name, --type, --state, --property
    Tier 2 (exposed as param or separate tool): --user, --reverse,
        --failed (list_failed_units tool), --all (list_units/list_timers)
    Tier 3 (handled internally): --no-pager, --no-legend (where appropriate)
    Tier 4 (omitted): start, stop, restart, enable, disable, mask, unmask,
        daemon-reload, reset-failed, edit, set-property, kill, freeze, thaw,
        isolate, switch-root, reboot, poweroff, halt, suspend, hibernate,
        --host, --machine, --recursive, --plain (internal), --output,
        --timestamp, --lines, --quiet, --wait, --no-block, --no-wall,
        --force, --now, --preset-mode, --root, --runtime, --global
"""

import sys
import subprocess
import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_check import ToolCache

server = FastMCP(
    name="sysops-systemctl",
    instructions=(
        "Inspect systemd units, services, timers, mounts, and their relationships. "
        "All tools are read-only. Use 'user=True' for user session units (e.g. rclone, "
        "syncthing, pipewire) and 'user=False' for system units (e.g. sshd, nginx, docker)."
    )
)

_tool = ToolCache(
    tool_name="systemctl",
    tool_path="/usr/bin/systemctl",
    version_args=["--version"],
    help_args=["--help"],
)


def _run_systemctl(args: list[str], user: bool = False, max_lines: int = 500) -> str:
    """Run systemctl with given arguments. Returns stdout or stderr.

    Args:
        args: Arguments to pass to systemctl.
        user: If True, run as --user (session units). If False, system units.
        max_lines: Truncate output to this many lines.
    """
    cmd = ["/usr/bin/systemctl"]
    if user:
        cmd.append("--user")
    cmd += ["--no-pager"] + args

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15
        )
    except subprocess.TimeoutExpired:
        return "Error: systemctl timed out after 15 seconds."

    output = result.stdout or ""
    stderr = result.stderr or ""

    # Handle common permission errors clearly
    if result.returncode != 0:
        if "Access denied" in stderr or "Permission denied" in stderr:
            scope = "user session" if user else "system"
            return (
                f"Permission denied accessing {scope} units. "
                f"{'Ensure the user session is running (loginctl).' if user else 'User may need to be in the systemd-journal or wheel group.'}\n\n"
                f"stderr: {stderr.strip()}"
            )
        if "not found" in stderr.lower() or "could not be found" in stderr.lower():
            scope = "--user" if user else "system"
            return (
                f"Unit not found in {scope} scope. "
                f"{'Try user=False for system units.' if user else 'Try user=True for user session units.'}\n\n"
                f"stderr: {stderr.strip()}"
            )
        # Other errors: return both
        if stderr:
            output = output + "\n[stderr]: " + stderr.strip()

    if not output.strip():
        return "(no output)"

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[:max_lines])
        )

    return output


@server.tool()
def tool_info() -> str:
    """Return version and availability for systemctl.

    Call this at the start of a session to see which version of
    systemd/systemctl is available.
    """
    info = _tool.info()
    result = {
        "systemctl": {
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
    """Read the systemctl man page, optionally extracting a specific section.

    Use this as a last resort when tool_info() help text doesn't answer
    your question about available options or behavior.

    Args:
        section: Section to extract, e.g. "COMMANDS", "OPTIONS",
                 "UNIT COMMANDS", "UNIT FILE COMMANDS". Leave empty
                 for full page (truncated to 200 lines).
    """
    return _tool.read_man(section=section)


@server.tool()
def list_units(
    type: str = "",
    state: str = "",
    user: bool = False
) -> str:
    """List systemd units, optionally filtered by type and state.

    Args:
        type: Unit type filter: service, timer, mount, socket, target, path,
              scope, slice, swap. Leave empty for all.
        state: State filter: active, inactive, failed, running, dead, waiting,
               enabled, disabled. Leave empty for all.
        user: True for user session units, False for system units.
    """
    args = ["list-units", "--all"]

    if type:
        args += [f"--type={type}"]
    if state:
        args += [f"--state={state}"]

    return _run_systemctl(args, user=user)


@server.tool()
def get_unit_status(
    unit: str,
    user: bool = False
) -> str:
    """Get the full status of a systemd unit. Shows active state, recent
    journal entries, PID, memory usage, trigger info, and more.

    Args:
        unit: Unit name, e.g. "nginx.service", "rclone_bisync.timer".
              Include the suffix (.service, .timer, .mount, etc.).
        user: True for user session units, False for system units.
    """
    return _run_systemctl(["status", unit], user=user)


@server.tool()
def read_unit_file(
    unit: str,
    user: bool = False
) -> str:
    """Show the full unit file contents. Reveals ExecStart, dependencies,
    timers, environment variables, logging config, and all other directives.
    Equivalent to reading the unit file directly but works without
    filesystem access.

    Args:
        unit: Unit name with suffix, e.g. "nginx.service".
        user: True for user session units, False for system units.
    """
    return _run_systemctl(["cat", unit], user=user)


@server.tool()
def get_unit_properties(
    unit: str,
    properties: str = "",
    user: bool = False
) -> str:
    """Show machine-readable properties of a unit. Returns all properties
    by default, or specific ones if listed.

    Common useful properties:
    - ActiveState, SubState, LoadState — current state
    - ExecStart, ExecStartPre, ExecStartPost — what it runs
    - TriggeredBy — what timer/path/socket activates this unit
    - Triggers — what this unit activates (for timers)
    - Requires, Wants, After, Before — dependency ordering
    - WantedBy, RequiredBy — reverse dependencies
    - Type — service type (simple, oneshot, forking, notify)
    - Restart, RestartUSec — restart policy
    - LogDirectory, StandardOutput, StandardError — where output goes
    - BindsTo, PartOf — strong dependencies
    - ConditionPath*, AssertPath* — activation conditions
    - FragmentPath — location of the unit file on disk
    - MemoryCurrent, CPUUsageNSec — resource usage

    Args:
        unit: Unit name with suffix.
        properties: Comma-separated property names, e.g.
                    "TriggeredBy,ExecStart,Type,Restart".
                    Leave empty for all properties.
        user: True for user session units, False for system units.
    """
    args = ["show", unit]
    if properties:
        args += [f"--property={properties}"]

    return _run_systemctl(args, user=user)


@server.tool()
def list_timers(
    user: bool = False
) -> str:
    """List all active timers with their next/last trigger times and
    the service they activate.

    Args:
        user: True for user session timers, False for system timers.
    """
    return _run_systemctl(["list-timers", "--all"], user=user)


@server.tool()
def list_dependencies(
    unit: str,
    reverse: bool = False,
    user: bool = False
) -> str:
    """Show dependency tree for a unit. Helps understand what a unit
    needs (forward) or what depends on it (reverse).

    Args:
        unit: Unit name with suffix.
        reverse: If True, show what depends on this unit instead of
                 what it depends on.
        user: True for user session units, False for system units.
    """
    args = ["list-dependencies", unit]
    if reverse:
        args.append("--reverse")

    return _run_systemctl(args, user=user)


@server.tool()
def check_active(
    unit: str,
    user: bool = False
) -> str:
    """Quick check: is a unit active? Returns 'active', 'inactive',
    'failed', etc. Faster than full status.

    Args:
        unit: Unit name with suffix.
        user: True for user session units, False for system units.
    """
    return _run_systemctl(["is-active", unit], user=user)


@server.tool()
def check_enabled(
    unit: str,
    user: bool = False
) -> str:
    """Quick check: is a unit enabled at boot? Returns 'enabled',
    'disabled', 'static', 'masked', etc.

    Args:
        unit: Unit name with suffix.
        user: True for user session units, False for system units.
    """
    return _run_systemctl(["is-enabled", unit], user=user)


@server.tool()
def list_failed_units(
    user: bool = False
) -> str:
    """List all units in failed state. Quick way to spot problems.

    Args:
        user: True for user session units, False for system units.
    """
    return _run_systemctl(["list-units", "--failed"], user=user)


@server.tool()
def get_unit_relationships(
    unit: str,
    user: bool = False
) -> str:
    """Get a comprehensive relationship summary for a unit. Shows what
    triggers it, what it triggers, what it requires, and what requires it.
    Useful for understanding how services, timers, mounts, and sockets
    connect together.

    Args:
        unit: Unit name with suffix.
        user: True for user session units, False for system units.
    """
    props = (
        "TriggeredBy,Triggers,"
        "Requires,RequiredBy,"
        "Wants,WantedBy,"
        "BindsTo,BoundBy,"
        "PartOf,"
        "Before,After,"
        "Conflicts,"
        "Type,FragmentPath"
    )

    return _run_systemctl(["show", unit, f"--property={props}"], user=user)


if __name__ == "__main__":
    server.run(transport="stdio")
