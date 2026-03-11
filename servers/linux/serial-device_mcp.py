#!/usr/bin/env python3
"""
Stuart — Serial Device MCP Server

Exposes USB, Thunderbolt, and serial port device inspection as MCP
tools for Claude Code. Wraps multiple underlying Linux commands. All
tools are read-only.

Usage:
    python3 serial-device_mcp.py

Tested on:
    - Fedora 43, usbutils 018, systemd 258, Python 3.14

Underlying tools (phased):
    Phase 1: lsusb, udevadm, stty, lsof, boltctl, dmesg, /sys/
    Phase 2: lspci (host controller enumeration), lshw, usb-devices

Argument tier decisions (see docs/TOOL_CONVENTION.md):
    Tier 1 (exposed as params):
        lsusb: -t (tree), -d VID:PID (filter), -s BUS:DEV, -v (verbose)
        udevadm info: --query=all, --name=<node>
        stty: -F <tty>, -a
        lsof: <path>
        boltctl: list
        dmesg: --since, pattern filter
    Tier 2 (param or separate tool):
        lsusb -v — separate tool (get_usb_device) due to distinct output
    Tier 3 (handled internally):
        --no-pager, --nopager, output formatting
    Tier 4 (omitted):
        Any write operations (udevadm trigger, boltctl authorize, etc.)
        Any interactive operations (minicom, screen, tio)
        udevadm monitor (streaming — not suited to MCP request/response)
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
    name="serial-device",
    instructions=(
        "Inspect USB, Thunderbolt, and serial port devices. "
        "Covers device enumeration, topology, power management, "
        "port settings, and kernel events. All tools are read-only."
    ),
)

# ── ToolCache instances ────────────────────────────────────────────
# Phase 1 — core tools, expected on any Linux system
_tools: dict[str, ToolCache] = {
    "lsusb": ToolCache("lsusb", "/usr/bin/lsusb", ["--version"], ["--help"]),
    "udevadm": ToolCache("udevadm", "/usr/bin/udevadm", ["--version"], ["--help"]),
    "stty": ToolCache("stty", "/usr/bin/stty", ["--version"], ["--help"]),
    "lsof": ToolCache("lsof", "/usr/bin/lsof", ["-v"], ["-h"]),
    "dmesg": ToolCache("dmesg", "/usr/bin/dmesg", ["--version"], ["--help"]),
}

# Phase 1 optional — may not be installed on non-Thunderbolt systems
_OPTIONAL_TOOLS = {
    "boltctl": ("/usr/bin/boltctl", ["--version"], ["--help"]),
    "lspci": ("/usr/bin/lspci", ["--version"], ["--help"]),
}

for _name, (_path, _vargs, _hargs) in _OPTIONAL_TOOLS.items():
    _tools[_name] = ToolCache(_name, _path, _vargs, _hargs)

_PACKAGE_HINTS = {
    "boltctl": "bolt",
    "lspci": "pciutils",
    "lshw": "lshw",
}

# Default dmesg filter patterns for device-related messages
_DEVICE_PATTERNS = ["usb", "tty", "serial", "thunderbolt", "xhci", "ehci", "uhci"]

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
    """Run a device command. Returns stdout or error message.

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
        # Non-zero but may have useful stdout (e.g. lsof with no matches)
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
    """Return version and availability for all serial/USB commands.

    Call this at the start of a session to see which tools are
    installed. boltctl may not be present on non-Thunderbolt systems.
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
    """Read the man page for a specific device command.

    Args:
        tool: Command name, e.g. "lsusb", "udevadm", "boltctl",
              "stty", "lsof".
        section: Section to extract, e.g. "OPTIONS", "DESCRIPTION".
                 Leave empty for full page (truncated).
    """
    if tool not in _tools:
        return f"Unknown tool '{tool}'. Available: {', '.join(sorted(_tools.keys()))}"
    return _tools[tool].read_man(section=section)


# ── Phase 1: USB Inspection ───────────────────────────────────────

@server.tool()
def list_usb_devices(
    tree: bool = False,
    device_filter: str = "",
    max_lines: int = 100,
) -> str:
    """List USB devices. Flat listing or topology tree.

    Args:
        tree: Show hierarchical topology with hub/port structure and
              negotiated speeds. Useful for bus layout analysis.
        device_filter: Filter by vendor:product ID, e.g. "0e41:4248".
                       Only works in flat mode (ignored when tree=True).
        max_lines: Maximum lines to return.
    """
    args: list[str] = []
    if tree:
        args.append("-t")
    elif device_filter:
        args += ["-d", device_filter]
    return _run_cmd("lsusb", args, max_lines=max_lines)


@server.tool()
def get_usb_device(
    vid_pid: str = "",
    bus_device: str = "",
) -> str:
    """Get detailed info for a specific USB device.

    Provide exactly one of vid_pid or bus_device.

    Args:
        vid_pid: Vendor:Product ID, e.g. "0e41:4248". Format: XXXX:XXXX.
        bus_device: Bus and device number, e.g. "003:010". Format: NNN:NNN.
    """
    if not vid_pid and not bus_device:
        return "Error: provide either vid_pid (e.g. '0e41:4248') or bus_device (e.g. '003:010')."
    if vid_pid and bus_device:
        return "Error: provide only one of vid_pid or bus_device, not both."

    args = ["-v"]
    if vid_pid:
        if not re.match(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$", vid_pid):
            return f"Invalid vid_pid format: '{vid_pid}'. Expected XXXX:XXXX (hex)."
        args += ["-d", vid_pid]
    else:
        if not re.match(r"^[0-9]{1,3}:[0-9]{1,3}$", bus_device):
            return f"Invalid bus_device format: '{bus_device}'. Expected NNN:NNN."
        args += ["-s", bus_device]

    return _run_cmd("lsusb", args, max_lines=200)


# ── Phase 1: Thunderbolt Inspection ───────────────────────────────

@server.tool()
def list_thunderbolt_devices(
    max_lines: int = 100,
) -> str:
    """List Thunderbolt/USB4 devices with authorization and speed info.

    Shows device name, type, UUID, authorization state, connection
    speed (lanes and Gb/s), and security policy.

    Args:
        max_lines: Maximum lines to return.
    """
    return _run_cmd("boltctl", ["list"], max_lines=max_lines)


# ── Phase 1: Serial Port Inspection ──────────────────────────────

@server.tool()
def list_serial_ports(
    max_lines: int = 100,
) -> str:
    """List hardware serial ports with device metadata.

    Enumerates real hardware ports only (filters out virtual consoles
    and pseudo-ttys). Includes USB-serial adapters (ttyUSB*, ttyACM*),
    and platform UARTs (ttyS*). Enriches with udev properties where
    available.

    Args:
        max_lines: Maximum lines to return.
    """
    tty_base = Path("/sys/class/tty")
    if not tty_base.exists():
        return "Error: /sys/class/tty not found."

    ports = []
    for entry in sorted(tty_base.iterdir()):
        # Only real hardware has a device/ subdir
        if not (entry / "device").exists():
            continue
        ports.append(entry.name)

    if not ports:
        return (
            "No hardware serial ports found.\n\n"
            "This means no USB-serial adapters (ttyUSB*, ttyACM*) are "
            "connected and no platform UARTs (ttyS*) were detected."
        )

    lines = []
    udevadm_info = _tools["udevadm"].info()
    udevadm_path = udevadm_info.get("path") if udevadm_info.get("exists") else None

    for port in ports:
        dev_path = f"/dev/{port}"
        line = f"{dev_path}"

        # Enrich with udevadm properties if available
        if udevadm_path:
            try:
                result = subprocess.run(
                    [udevadm_path, "info", "-q", "property", "-n", dev_path],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    props = {}
                    for prop_line in result.stdout.strip().split("\n"):
                        if "=" in prop_line:
                            k, v = prop_line.split("=", 1)
                            props[k] = v

                    details = []
                    if props.get("ID_BUS"):
                        details.append(f"bus={props['ID_BUS']}")
                    if props.get("ID_VENDOR_FROM_DATABASE") or props.get("ID_VENDOR"):
                        vendor = props.get("ID_VENDOR_FROM_DATABASE", props.get("ID_VENDOR", ""))
                        details.append(f"vendor={vendor}")
                    if props.get("ID_MODEL_FROM_DATABASE") or props.get("ID_MODEL"):
                        model = props.get("ID_MODEL_FROM_DATABASE", props.get("ID_MODEL", ""))
                        details.append(f"model={model}")
                    if props.get("ID_SERIAL_SHORT") or props.get("ID_SERIAL"):
                        serial = props.get("ID_SERIAL_SHORT", props.get("ID_SERIAL", ""))
                        details.append(f"serial={serial}")
                    if props.get("ID_USB_DRIVER"):
                        details.append(f"driver={props['ID_USB_DRIVER']}")

                    if details:
                        line += f"  [{', '.join(details)}]"
                    elif port.startswith("ttyS"):
                        line += "  [platform UART]"
            except (subprocess.TimeoutExpired, OSError):
                pass

        lines.append(line)

    output = f"Hardware serial ports ({len(ports)} found):\n\n" + "\n".join(lines)
    out_lines = output.split("\n")
    if len(out_lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(out_lines)} lines.]\n\n"
            + "\n".join(out_lines[:max_lines])
        )
    return output


# ── Phase 1: Device Properties ────────────────────────────────────

@server.tool()
def get_device_properties(
    device_node: str,
    max_lines: int = 100,
) -> str:
    """Get all udev properties for a device node.

    Returns vendor, model, serial, bus type, driver, and other
    attributes from the udev database. Works for any /dev/ device.

    Args:
        device_node: Device path, e.g. "/dev/ttyUSB0",
                     "/dev/bus/usb/001/005", "/dev/ttyACM0".
        max_lines: Maximum lines to return.
    """
    if not re.match(r"^/dev/[a-zA-Z0-9/_.-]+$", device_node):
        return f"Invalid device path: '{device_node}'. Must start with /dev/."

    return _run_cmd(
        "udevadm",
        ["info", "--query=all", f"--name={device_node}"],
        max_lines=max_lines,
    )


# ── Phase 1: USB Power Management ────────────────────────────────

@server.tool()
def check_usb_power(
    device: str,
) -> str:
    """Check USB power management / autosuspend state for a device.

    Reads runtime power attributes from sysfs. Autosuspend is a
    common cause of intermittent USB disconnects.

    Args:
        device: USB device sysfs ID, e.g. "2-3", "1-1.2", "3-4.1".
                Find this from lsusb (Bus NNN) and the port topology,
                or from get_device_properties (DEVPATH property).

    Returns:
        Power control mode, autosuspend delay, runtime status, and
        whether runtime PM is enabled.
    """
    # Validate: bus-port format (digits, dashes, dots)
    if not re.match(r"^[0-9]+-[0-9]+(\.[0-9]+)*$", device):
        return (
            f"Invalid USB device ID: '{device}'. "
            f"Expected format like '2-3' or '1-1.2' (bus-port path).\n\n"
            f"Find the device ID from list_usb_devices(tree=True) output "
            f"or from get_device_properties() DEVPATH."
        )

    base = Path(f"/sys/bus/usb/devices/{device}")
    if not base.exists():
        return f"USB device '{device}' not found in /sys/bus/usb/devices/"

    power_dir = base / "power"
    if not power_dir.exists():
        return f"No power attributes found for USB device '{device}'."

    attrs = [
        ("control", "Power control mode (auto=autosuspend, on=always-on)"),
        ("autosuspend_delay_ms", "Delay before autosuspend (milliseconds)"),
        ("runtime_status", "Current power state (active, suspended, etc.)"),
        ("runtime_enabled", "Runtime PM enabled (enabled, disabled)"),
    ]

    lines = [f"USB power management for device {device}:\n"]
    for attr_name, desc in attrs:
        attr_file = power_dir / attr_name
        if attr_file.exists():
            try:
                value = attr_file.read_text().strip()
                lines.append(f"  {attr_name}: {value}  ({desc})")
            except (PermissionError, OSError) as e:
                lines.append(f"  {attr_name}: [error: {e}]")
        else:
            lines.append(f"  {attr_name}: [not available]")

    # Also read product/manufacturer if available for context
    for extra in ["product", "manufacturer", "idVendor", "idProduct", "speed"]:
        extra_file = base / extra
        if extra_file.exists():
            try:
                value = extra_file.read_text().strip()
                lines.append(f"  {extra}: {value}")
            except (PermissionError, OSError):
                pass

    return "\n".join(lines)


# ── Phase 1: Serial Port Settings ────────────────────────────────

@server.tool()
def get_serial_settings(
    tty: str,
) -> str:
    """Get runtime serial port settings (baud rate, flow control, etc).

    Args:
        tty: Device path, e.g. "/dev/ttyUSB0", "/dev/ttyS0",
             "/dev/ttyACM0".

    Note: requires read access to the device. The user typically
    needs to be in the 'dialout' or 'tty' group.
    """
    if not re.match(r"^/dev/tty[A-Za-z0-9]+$", tty):
        return f"Invalid tty path: '{tty}'. Expected /dev/ttyXXX format."

    return _run_cmd("stty", ["-F", tty, "-a"])


# ── Phase 1: Port Lock Check ─────────────────────────────────────

@server.tool()
def check_port_lock(
    path: str,
) -> str:
    """Check which processes have a device node open.

    Useful for diagnosing "device busy" errors on serial ports.

    Args:
        path: Device path, e.g. "/dev/ttyUSB0", "/dev/ttyACM0".

    Note: without sudo, only shows processes owned by the current
    user. Run with sudo to see all processes.
    """
    if not re.match(r"^/dev/[a-zA-Z0-9/_.-]+$", path):
        return f"Invalid device path: '{path}'. Must start with /dev/."

    info = _tools["lsof"].info()
    if not info.get("exists"):
        return "Error: lsof is not installed."

    try:
        result = subprocess.run(
            [info["path"], path],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return "Error: lsof timed out."

    if result.returncode != 0:
        # lsof returns 1 when no matches found — this is normal
        stderr = (result.stderr or "").strip()
        if not result.stdout.strip():
            return f"(port {path} is not held open by any process)"
        return result.stdout.strip()

    output = result.stdout.strip()
    return output if output else f"(port {path} is not held open by any process)"


# ── Phase 1: Device Kernel Messages ──────────────────────────────

@server.tool()
def get_device_messages(
    pattern: str = "",
    since: str = "",
    max_lines: int = 100,
) -> str:
    """Get kernel messages related to USB, Thunderbolt, or serial devices.

    When no pattern is given, filters to device-related messages
    (usb, tty, serial, thunderbolt, xhci, ehci).

    Args:
        pattern: Device name or keyword to filter for, e.g. "ttyUSB0",
                 "disconnect", "usb 2-3". Leave empty for all device
                 messages.
        since: Time filter, e.g. "-600" (last 600 seconds). Leave
               empty for all messages.
        max_lines: Maximum lines to return (shows most recent).
    """
    args = ["--time-format=iso", "--nopager"]

    if since:
        args += ["--since", since]

    # Use a large max_lines for the raw fetch — we filter client-side
    helper_id = "dmesg-recent" if not since else "dmesg-tail"
    output = _run_cmd("dmesg", args, max_lines=10000, timeout=15,
                      privilege="auto", helper_command_id=helper_id)

    if output.startswith("Error:") or output.startswith("Permission denied"):
        return output

    # Filter to device-related messages
    if pattern:
        match_patterns = [pattern.lower()]
    else:
        match_patterns = _DEVICE_PATTERNS

    lines = [
        l for l in output.split("\n")
        if any(p in l.lower() for p in match_patterns)
    ]

    if not lines:
        if pattern:
            return f"No kernel messages matching '{pattern}'."
        return "No device-related kernel messages found."

    # Show most recent lines (tail) when truncating
    if len(lines) > max_lines:
        return (
            f"[Showing last {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[-max_lines:])
        )
    return "\n".join(lines)


if __name__ == "__main__":
    server.run(transport="stdio")
