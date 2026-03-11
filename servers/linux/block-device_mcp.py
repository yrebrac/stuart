#!/usr/bin/env python3
"""
Stuart — Block Device MCP Server

Exposes block device, filesystem, and storage stack inspection as MCP
tools for Claude Code. Wraps multiple underlying Linux commands. All
tools are read-only.

Usage:
    python3 blockdev_mcp.py

Tested on:
    - Fedora 43, util-linux 2.41, Python 3.14

Underlying tools (phased):
    Phase 1: lsblk, blkid, findmnt, df, /sys/block/, dmesg
    Phase 2: smartctl, nvme, udevadm info
    Phase 3: pvs/vgs/lvs, mdadm, dmsetup
    Phase 4: btrfs, tune2fs, xfs_info, cryptsetup

Argument tier decisions (see docs/TOOL_CONVENTION.md):
    Tier 1 (exposed as params):
        lsblk: device, -o columns, -f, -t
        blkid: device, -p (probe)
        findmnt: mountpoint, device, -t fstype
        df: path, -T (fstype)
        dmesg: device grep pattern, --since
    Tier 2 (param or separate tool):
        lsblk -J (json) — handled internally for structured output
    Tier 3 (handled internally):
        --noheadings, --raw, --bytes, --pairs, --no-pager
    Tier 4 (omitted):
        Any write operations (mkfs, mount, umount, fdisk, parted, etc.)
        Any destructive operations
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from privilege import PrivilegeHelper
from tool_check import ToolCache

server = FastMCP(
    name="sysops-blockdev",
    instructions=(
        "Inspect block devices, filesystems, mounts, and the storage "
        "stack. All tools are read-only."
    ),
)

# ── ToolCache instances ────────────────────────────────────────────
# Phase 1 — always available on Linux
_tools: dict[str, ToolCache] = {
    "lsblk": ToolCache("lsblk", "/usr/bin/lsblk", ["--version"], ["--help"]),
    "blkid": ToolCache("blkid", "/usr/bin/blkid", ["--version"], ["--help"]),
    "findmnt": ToolCache("findmnt", "/usr/bin/findmnt", ["--version"], ["--help"]),
    "df": ToolCache("df", "/usr/bin/df", ["--version"], ["--help"]),
}

# dmesg — used for device kernel messages, may need root
_tools["dmesg"] = ToolCache("dmesg", "/usr/bin/dmesg", ["--version"], ["--help"])

# Phase 2+: optional tools (may not be installed)
_OPTIONAL_TOOLS = {
    "smartctl": ("/usr/bin/smartctl", ["--version"], ["--help"]),
    "nvme": ("/usr/bin/nvme", ["version"], ["help"]),
    "udevadm": ("/usr/bin/udevadm", ["--version"], ["--help"]),
    "pvs": ("/usr/bin/pvs", ["--version"], ["--help"]),
    "vgs": ("/usr/bin/vgs", ["--version"], ["--help"]),
    "lvs": ("/usr/bin/lvs", ["--version"], ["--help"]),
    "mdadm": ("/usr/bin/mdadm", ["--version"], ["--help"]),
    "dmsetup": ("/usr/bin/dmsetup", ["--version"], ["--help"]),
    "btrfs": ("/usr/bin/btrfs", ["--version"], ["help"]),
    "tune2fs": ("/usr/bin/tune2fs", ["-V"], ["-h"]),
    "xfs_info": ("/usr/bin/xfs_info", ["-V"], []),
    "cryptsetup": ("/usr/bin/cryptsetup", ["--version"], ["--help"]),
}

for _name, (_path, _vargs, _hargs) in _OPTIONAL_TOOLS.items():
    _tools[_name] = ToolCache(_name, _path, _vargs, _hargs)

_PACKAGE_HINTS = {
    "smartctl": "smartmontools",
    "nvme": "nvme-cli",
    "cryptsetup": "cryptsetup",
    "mdadm": "mdadm",
    "btrfs": "btrfs-progs",
    "tune2fs": "e2fsprogs",
    "xfs_info": "xfsprogs",
}

_priv = PrivilegeHelper()


# ── Shared runner ──────────────────────────────────────────────────

def _run_cmd(
    tool_key: str,
    args: list[str],
    max_lines: int = 200,
    timeout: int = 30,
    privilege: str = "never",
    helper_command_id: str | None = None,
    helper_device: str | None = None,
) -> str:
    """Run a storage command. Returns stdout or error message.

    Args:
        privilege: "never", "auto", or "always". See privilege.py.
        helper_command_id: Route escalation through privilege helper.
        helper_device: Device path for helper commands that need one.
    """
    info = _tools[tool_key].info()
    if not info.get("exists"):
        pkg = _PACKAGE_HINTS.get(tool_key, "")
        hint = f" Install with: sudo dnf install {pkg}" if pkg else ""
        return f"Error: {tool_key} is not installed.{hint}"

    cmd = [info["path"]] + args
    result = _priv.run_command(
        cmd, privilege=privilege, timeout=timeout,
        helper_command_id=helper_command_id,
        helper_device=helper_device,
    )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if PrivilegeHelper.is_permission_error(result):
            return (
                f"Permission denied running {tool_key}.\n\n"
                f"{_priv.format_sudo_hint(cmd)}\n\n"
                f"stderr: {stderr}"
            )
        # Non-zero but may have useful stdout (e.g. blkid, smartctl)
        if result.stdout.strip():
            output = result.stdout.strip()
            if stderr:
                output += f"\n\n[stderr]: {stderr}"
        else:
            return f"Error from {tool_key}: {stderr or '(no output)'}"
    else:
        output = result.stdout or result.stderr or "(no output)"

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return output.strip()


# ── Standard tools ─────────────────────────────────────────────────

@server.tool()
def tool_info() -> str:
    """Return version and availability for all storage commands.

    Call this at the start of a session to see which tools are
    installed. Some (smartctl, nvme, cryptsetup) may not be present.
    Also reports privilege escalation status (polkit).
    """
    result = {}
    for name, cache in sorted(_tools.items()):
        info = cache.info()
        result[name] = {
            "exists": info.get("exists", False),
            "path": info.get("path"),
            "version": info.get("version_raw"),
        }
    result["_privilege"] = _priv.policy_status()
    return json.dumps(result, indent=2)


@server.tool()
def read_manual(
    tool: str,
    section: str = "",
) -> str:
    """Read the man page for a specific storage command.

    Args:
        tool: Command name, e.g. "lsblk", "blkid", "findmnt", "df",
              "smartctl", "nvme", "pvs", "mdadm", "btrfs", etc.
        section: Section to extract, e.g. "OPTIONS", "DESCRIPTION".
                 Leave empty for full page (truncated).
    """
    if tool not in _tools:
        return f"Unknown tool '{tool}'. Available: {', '.join(sorted(_tools.keys()))}"
    return _tools[tool].read_man(section=section)


# ── Phase 1: Core Inspection ──────────────────────────────────────

@server.tool()
def list_devices(
    device: str = "",
    columns: str = "",
    filesystem: bool = False,
    topology: bool = False,
    max_lines: int = 100,
) -> str:
    """List block devices as a tree.

    Args:
        device: Specific device to show, e.g. "/dev/sda", "/dev/nvme0n1".
                Leave empty for all devices.
        columns: Comma-separated lsblk columns, e.g.
                 "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL".
                 Leave empty for default columns.
        filesystem: Show filesystem info (UUID, FSTYPE, LABEL, MOUNTPOINTS).
                    Shortcut for common column set.
        topology: Show topology info (alignment, min/opt I/O sizes).
                  Shortcut for topology column set.
        max_lines: Maximum lines to return.
    """
    args: list[str] = []
    if filesystem:
        args.append("-f")
    elif topology:
        args.append("-t")
    elif columns:
        args += ["-o", columns]
    if device:
        args.append(device)
    return _run_cmd("lsblk", args, max_lines=max_lines)


@server.tool()
def identify_device(
    device: str = "",
    probe: bool = False,
    max_lines: int = 100,
) -> str:
    """Identify block devices: UUID, TYPE, LABEL, PARTUUID.

    Args:
        device: Specific device, e.g. "/dev/sda1". Leave empty for all.
        probe: Use low-level superblock probing (more thorough, may
               need sudo). Default uses cached udev data.
        max_lines: Maximum lines to return.
    """
    args: list[str] = []
    if probe:
        args.append("-p")
    if device:
        args.append(device)
    # Probe mode (-p) needs root for low-level superblock access
    priv = "always" if probe else "never"
    return _run_cmd("blkid", args, max_lines=max_lines, privilege=priv,
                    helper_command_id="blkid-probe" if probe else None,
                    helper_device=device if probe else None)


@server.tool()
def list_mounts(
    mountpoint: str = "",
    device: str = "",
    fstype: str = "",
    max_lines: int = 100,
) -> str:
    """List mounted filesystems as a tree.

    Args:
        mountpoint: Filter to a specific mount point, e.g. "/home".
        device: Filter to a specific device, e.g. "/dev/sda1".
        fstype: Filter by filesystem type, e.g. "ext4", "btrfs", "tmpfs".
        max_lines: Maximum lines to return.
    """
    args: list[str] = []
    if mountpoint:
        args.append(mountpoint)
    if device:
        args += ["-S", device]
    if fstype:
        args += ["-t", fstype]
    return _run_cmd("findmnt", args, max_lines=max_lines)


@server.tool()
def check_disk_usage(
    path: str = "",
    show_fstype: bool = True,
    max_lines: int = 100,
) -> str:
    """Check filesystem disk space usage.

    Args:
        path: Show usage for the filesystem containing this path.
              Leave empty for all filesystems.
        show_fstype: Include filesystem type column (default True).
        max_lines: Maximum lines to return.
    """
    args = ["-h"]
    if show_fstype:
        args.append("-T")
    if path:
        args.append(path)
    return _run_cmd("df", args, max_lines=max_lines)


@server.tool()
def read_sysfs(
    device: str,
    attribute: str = "",
) -> str:
    """Read low-level device attributes from /sys/block/.

    Args:
        device: Block device name (no /dev/ prefix), e.g. "sda",
                "nvme0n1", "dm-0".
        attribute: Sysfs attribute file to read, e.g. "size", "ro",
                   "queue/scheduler", "queue/rotational",
                   "device/model". Leave empty to list available
                   attributes.

    Common attributes:
        size — device size in 512-byte sectors
        ro — 1 if read-only, 0 if read-write
        queue/rotational — 0 for SSD, 1 for HDD
        queue/scheduler — I/O scheduler in use
        device/model — device model string
    """
    # Validate device name: alphanumeric, dash, underscore only
    if not re.match(r"^[a-zA-Z0-9_-]+$", device):
        return f"Invalid device name: '{device}'"

    # Find the device in sysfs
    base = Path(f"/sys/block/{device}")
    if not base.exists():
        base = Path(f"/sys/class/block/{device}")
    if not base.exists():
        return f"Device '{device}' not found in /sys/block/ or /sys/class/block/"

    if not attribute:
        # List available attributes (files and dirs at top level)
        try:
            entries = sorted(p.name for p in base.iterdir())
            return f"Available attributes for {device}:\n" + "\n".join(entries)
        except PermissionError:
            return f"Permission denied listing attributes for {device}"

    # Validate attribute path
    if not re.match(r"^[a-zA-Z0-9_/.:-]+$", attribute):
        return f"Invalid attribute path: '{attribute}'"

    target = (base / attribute).resolve()
    # Safety: ensure resolved path stays within sysfs
    target_str = str(target)
    if not (
        target_str.startswith("/sys/block/")
        or target_str.startswith("/sys/class/block/")
        or target_str.startswith("/sys/devices/")
    ):
        return f"Path traversal blocked: {attribute} resolves outside /sys/"

    if target.is_dir():
        try:
            entries = sorted(p.name for p in target.iterdir())
            return f"Contents of {device}/{attribute}:\n" + "\n".join(entries)
        except PermissionError:
            return f"Permission denied reading {device}/{attribute}"

    if not target.exists():
        return f"Attribute '{attribute}' not found for device '{device}'"

    try:
        content = target.read_text().strip()
        return content if content else "(empty)"
    except PermissionError:
        return f"Permission denied reading {device}/{attribute}"
    except OSError as e:
        return f"Error reading {device}/{attribute}: {e}"


@server.tool()
def check_smart_health(
    device: str = "",
) -> str:
    """Check SMART health of disks using smartctl.

    Auto-escalates via polkit when the privilege helper is installed.

    Args:
        device: Device path (e.g. "/dev/sda", "/dev/nvme0n1").
                Leave empty to scan for all SMART-capable devices.
    """
    if not device:
        return _run_cmd("smartctl", ["--scan"], privilege="auto",
                        helper_command_id="smartctl-scan")

    if not device.startswith("/dev/"):
        return f"Invalid device path: '{device}' (must start with /dev/)"

    return _run_cmd("smartctl", ["-a", device], privilege="auto",
                    helper_command_id="smartctl-health",
                    helper_device=device)


@server.tool()
def check_nvme_health(
    device: str = "",
) -> str:
    """Check NVMe device health using nvme-cli.

    Auto-escalates via polkit when the privilege helper is installed.

    Args:
        device: NVMe device path (e.g. "/dev/nvme0n1", "/dev/nvme0").
                Leave empty to list all NVMe devices.
    """
    if not device:
        return _run_cmd("nvme", ["list"], privilege="auto",
                        helper_command_id="nvme-list")

    if not device.startswith("/dev/"):
        return f"Invalid device path: '{device}' (must start with /dev/)"

    return _run_cmd("nvme", ["smart-log", device], privilege="auto",
                    helper_command_id="nvme-smart",
                    helper_device=device)


@server.tool()
def get_device_messages(
    device: str = "",
    since: str = "",
    max_lines: int = 100,
) -> str:
    """Get kernel messages related to block devices.

    Reads dmesg output, optionally filtered to a device name.

    Args:
        device: Device name or pattern to grep for, e.g. "sda",
                "nvme", "usb". Leave empty for all storage messages.
        since: Time filter, e.g. "-600" (last 600 seconds). Requires
               dmesg support for --since. Leave empty for all messages.
        max_lines: Maximum lines to return.
    """
    args = ["--time-format=iso", "--nopager"]

    if since:
        args += ["--since", since]

    # Route through helper for privilege escalation.
    # dmesg-recent (last hour) if no since filter, dmesg-tail otherwise.
    helper_id = "dmesg-recent" if not since else "dmesg-tail"
    output = _run_cmd("dmesg", args, max_lines=10000, timeout=15,
                      privilege="auto", helper_command_id=helper_id)

    if output.startswith("Error:") or output.startswith("Permission denied"):
        return output

    # Filter to device-related messages if a device pattern given
    if device:
        pattern = device.lower()
        lines = [l for l in output.split("\n") if pattern in l.lower()]
        if not lines:
            return f"No kernel messages matching '{device}'."
        output = "\n".join(lines)

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing last {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[-max_lines:])
        )
    return output.strip() if output.strip() else "(no output)"


if __name__ == "__main__":
    server.run(transport="stdio")
