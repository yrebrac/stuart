---
name: linux-serial-device
description: >
    Domain knowledge for USB, Thunderbolt, and serial port device
    diagnostics using the serial-device MCP server. Load BEFORE using
    any serial-device MCP tool directly. Covers device enumeration,
    topology, power management, port settings, and kernel event analysis.
---

# USB, Thunderbolt & Serial Devices

## Session Start

1. Call `tool_info()` to see which device commands are available
2. Note if `boltctl` is absent — Thunderbolt queries will not work (suggest `sudo dnf install bolt`)
3. Determine whether the issue involves USB, Thunderbolt, or serial ports — guides tool selection

## Common Tasks

### "USB device not working"

1. `list_usb_devices()` — is the device visible at all?
2. If visible: `get_usb_device` for descriptors, `get_device_properties` for driver binding
3. If not visible: `get_device_messages(pattern="usb")` — look for enumeration failures
4. `check_usb_power` on parent hub — power issue?
5. If it was working before: `get_device_messages(since="-3600")` for recent events

### "Serial adapter setup"

1. `list_serial_ports()` — find the adapter (ttyUSB0 or ttyACM0)
2. `get_device_properties` on the port — confirm vendor/model
3. `get_serial_settings` — check baud rate, flow control, parity
4. `check_port_lock` — if "device busy", find what holds it open
5. Common settings for Cisco console: 9600 baud, 8N1, no flow control

### "Thunderbolt dock investigation"

1. `list_thunderbolt_devices()` — authorization state, speed, lanes
2. `list_usb_devices(tree=True)` — downstream USB devices on virtual buses
3. Look for a Billboard device (USB Billboard class) — usually carries the dock's product name
4. Do NOT assume TB device name matches a USB hub — TB controllers are PCIe devices, invisible to lsusb

### "Draw my device topology"

1. Gather all data in one pass: `list_usb_devices(tree=True)`, `list_usb_devices()`, `list_thunderbolt_devices()`, `list_serial_ports()`
2. Classify devices: physical (user plugs in), internal (built-in), uncertain (flag and ask user)
3. Check renderers: `dot` (Graphviz), `mmdc` (Mermaid), `graph-easy` (ASCII), or plain ASCII
4. Render with human-friendly names, port types, link speeds, and physical grouping

**Hub classification caveat**: USB descriptors alone cannot distinguish a hub inside a dock from an external hub. Flag any hub classification as uncertain and ask the user to confirm.

For detailed device topology knowledge, troubleshooting tactics, driver quirks, and legacy system support, read REFERENCE.md in this skill directory.

## Tool Selection

| Goal | Tool |
|------|------|
| List all USB devices | `list_usb_devices` |
| Show USB bus/port topology | `list_usb_devices(tree=True)` |
| Detailed info for one USB device | `get_usb_device` |
| List Thunderbolt devices + speeds | `list_thunderbolt_devices` |
| List hardware serial ports | `list_serial_ports` |
| All properties for a /dev/ device | `get_device_properties` |
| USB power/autosuspend state | `check_usb_power` |
| Serial port baud rate + settings | `get_serial_settings` |
| What process holds a port open? | `check_port_lock` |
| Kernel messages for a device | `get_device_messages` |
| Tool versions + availability | `tool_info` |
| Man page details | `read_manual` |

## Query Strategy

### Efficient queries

- Always specify a device filter when you know the VID:PID — avoids scanning all devices
- Use `get_device_properties` as the single source of truth for any specific device — it combines udev database info with kernel attributes
- Start with `list_usb_devices()` for an overview, then drill into specific devices
- For Thunderbolt: `list_thunderbolt_devices` is the only path — sysfs attributes are harder to interpret directly

### Intermittent USB disconnect

A common cause is USB autosuspend — the kernel suspends idle devices to save power, but some handle resume poorly.

1. `get_device_messages(pattern="disconnect")` — look for disconnect/reconnect cycles
2. `list_usb_devices()` — identify the affected device's VID:PID and bus
3. `check_usb_power` with the sysfs device ID — check if `control=auto` and `autosuspend_delay_ms` is short
4. If autosuspend is the cause: the fix is a udev rule setting `control=on` for that device

### Bus topology for performance

When deciding whether devices share bandwidth:

1. `list_usb_devices(tree=True)` — the tree shows which devices share a root hub / controller
2. Devices on the same bus share bandwidth; different buses have independent bandwidth
3. USB 3.0 at 5 Gbit/s ≈ 400 MB/s theoretical, ~350 MB/s practical

### Be suspicious of empty results

- No USB devices? Check if `lsusb` is installed (`usbutils` package)
- No Thunderbolt? `boltctl` may not be installed, or hardware may not support it
- Platform UARTs (ttyS0-3) always appear even without physical hardware — udev properties will be minimal for these

## Preferences & Safety

- **Tools don't tell the full story.** A device in `lsusb` may not be working. A bound driver may not be communicating.
- **The physical layer matters.** Bad cables, underpowered hubs, and marginal connections cause symptoms that look like software issues.
- **Counterfeit hardware is common.** Cheap USB-serial adapters with counterfeit chips (FTDI, PL2303) cause driver mismatch.
- **Enumeration order is not stable.** ttyUSB0 today may be ttyUSB1 tomorrow. Use udev rules for stable naming.
- **Privilege may be needed** for: `lsof` (other users' processes), `dmesg` (if restricted), `lsusb -v` (some descriptor fields), `boltctl` authorization changes (polkit). Stuart auto-escalates via polkit when configured.
- **Document udev rules and fixes but do not apply them** — the user decides what to write to their system.
