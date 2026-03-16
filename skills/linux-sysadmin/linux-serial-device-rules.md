---
name: linux-serial-device
description: >
    Domain knowledge for USB, Thunderbolt, and serial port device
    diagnostics using the serial-device MCP server. Load BEFORE using
    any serial-device MCP tool directly. Covers device enumeration,
    topology, power management, port settings, and kernel event analysis.
---

# USB, Thunderbolt & Serial Devices

## Guide

This file covers USB, Thunderbolt, and serial port device diagnostics.

- **Domain Model** — USB bus topology, Thunderbolt tunnelling, serial port types
- **Heuristics** — expert shortcuts: physical layer matters, tools don't tell the full story
- **Anti-patterns** — common mistakes with USB/TB/serial troubleshooting
- **Procedures** — diagnostic workflows for USB, serial adapters, TB docks, device topology
- **Tools** — goal-to-tool lookup for the serial-device MCP server
- **Query Strategy** — efficient queries, intermittent disconnect diagnosis, bandwidth analysis
- **Safety** — privilege, udev rule guidance, cross-domain pointers
- **Quirks** — bus/device instability, ttyACM vs ttyUSB, autosuspend, platform UARTs
- **Domain Deep Knowledge** → `linux-serial-device-deep-knowledge.md` for driver quality, chipset quirks, legacy systems

## Domain Model

### USB
```
Host controller (xHCI) → Root hub → [Hub(s)] → Device
```
- Each device has a **bus number**, **device number**, and **VID:PID** (primary identifier)
- Hubs create a tree: `2-3.1` in sysfs = bus 2, port 3, hub port 1
- USB 2.0 = 480 Mbit/s, USB 3.0 = 5 Gbit/s, USB 3.1 = 10 Gbit/s, USB 3.2 = 20 Gbit/s
- **USB3 dual-device**: Every USB3 device presents two logical devices (SuperSpeed + Hi-Speed companion). This is normal, not traffic splitting.

### Thunderbolt / USB4
```
Host controller (NHI) → Domain → [Daisy-chained devices]
```
- Managed by `bolt` daemon, separate from USB
- Requires device **authorization** — security policy controls connections
- **Tunnelling architecture**: TB encapsulates USB3, DisplayPort, and PCIe in a single transport. USB-C Alternate Mode switches pins to carry the tunnel — not multiplexing.
- TB controller is a PCIe device — invisible to `lsusb`. Use `list_thunderbolt_devices()`.
- USB devices on a TB dock appear on virtual xHCI controllers (separate USB buses).
- The only USB device identifying the dock by name is typically a Billboard device.

### Serial Ports
```
Hardware UART (ttyS0-3) — onboard, always enumerated
USB-serial adapter (ttyUSB0, ttyACM0) — appear when plugged in
```
- **ttyUSB** = USB-to-serial adapters (FTDI, CP210x, CH340 chipsets)
- **ttyACM** = CDC-ACM class devices (modems, Arduino, some routers)
- Only entries in `/sys/class/tty/` with a `device/` subdir are real hardware

## Heuristics

1. Tools don't tell the full story. A device in `lsusb` may not be working. A bound driver may not be communicating. Always verify actual function, not just presence.
2. The physical layer matters most. Bad cables, underpowered hubs, and marginal connections cause symptoms that look like software. Always consider the physical path.
3. Counterfeit hardware is common. Cheap USB-serial adapters with counterfeit FTDI or PL2303 chips cause driver mismatch. If a known-good adapter fails, the chip may be a clone.
4. Intermittent USB disconnects are usually autosuspend. Check `check_usb_power` — `control=auto` with a short delay is the most common cause.
5. Enumeration order is not stable. ttyUSB0 today may be ttyUSB1 tomorrow. Use udev rules with serial numbers for stable naming.

## Anti-patterns

- Don't assume a TB device name matches a USB hub — TB controllers are PCIe devices, invisible to lsusb.
- Don't describe TB as carrying protocols "alongside" each other — it tunnels them inside a single transport.
- Don't classify hubs inside docks without asking the user — USB descriptors alone cannot distinguish dock-internal from external hubs.
- Don't assume `lsusb -v` output is complete without sudo — some descriptor fields show as `(error)` without root.
- Don't debug software when dmesg shows "device descriptor read" errors — that's an electrical/physical problem.

## Procedures

### USB device not working
When a USB device isn't functioning or isn't recognized.

1. `list_usb_devices()` — is the device visible at all?
2. IF visible:
     `get_usb_device` — check descriptors
     `get_device_properties` — check driver binding
   IF NOT visible:
     `get_device_messages(pattern="usb")` — look for enumeration failures
3. `check_usb_power` on parent hub — power issue?
4. IF was working before:
     `get_device_messages(since="-3600")` — recent disconnect/error events
5. IF repeated "device descriptor read" errors:
     Physical problem — suggest different cable, port, or hub
6. VERIFY: Device visible in `list_usb_devices` and driver bound in `get_device_properties`
7. CROSS-DOMAIN: If USB network adapter → `linux-network-rules.md` for network-level diagnosis

### Serial adapter setup
When configuring a USB-serial adapter for console or device communication.

1. `list_serial_ports()` — find the adapter (ttyUSB0 or ttyACM0)
2. `get_device_properties` on the port — confirm vendor/model
3. `get_serial_settings` — check baud rate, flow control, parity
4. `check_port_lock` — if "device busy", find what holds it open
5. Common settings for Cisco console: 9600 baud, 8N1, no flow control
6. VERIFY: Port accessible and settings correct
7. CROSS-DOMAIN: If port not appearing → check USB level first (procedure above)

### Thunderbolt dock investigation
When investigating a TB dock or its connected devices.

1. `list_thunderbolt_devices()` — authorization state, speed, lanes
2. `list_usb_devices(tree=True)` — downstream USB devices on virtual buses
3. Look for a Billboard device (USB Billboard class) — carries the dock's product name
4. Do NOT assume TB device name matches a USB hub
5. VERIFY: TB device authorized, downstream devices visible on USB buses

### Device topology mapping
When user wants a visual representation of connected devices.

1. Gather all data in one pass:
   `list_usb_devices(tree=True)`, `list_usb_devices()`, `list_thunderbolt_devices()`, `list_serial_ports()`
2. Classify: physical (user plugs in), internal (built-in), uncertain (flag and ask user)
3. Check renderers: `dot` (Graphviz), `mmdc` (Mermaid), `graph-easy` (ASCII). Don't assume any is available.
4. Render with human-friendly names, port types, link speeds, physical grouping
5. VERIFY: Topology matches user's physical setup (ask to confirm uncertain classifications)

### Intermittent USB disconnect
When a device disconnects and reconnects randomly.

1. `get_device_messages(pattern="disconnect")` — look for disconnect/reconnect cycles
2. `list_usb_devices()` — identify VID:PID and bus
3. `check_usb_power` with sysfs device ID — check `control=auto` and `autosuspend_delay_ms`
4. IF autosuspend is the cause:
     Fix: udev rule setting `control=on` for that device (document, don't apply)
   IF not autosuspend:
     Try different port/cable/hub
5. VERIFY: No new disconnect events in `get_device_messages` after fix

## Tools

| Goal | Tool |
|------|------|
| List all USB devices | `list_usb_devices` |
| USB bus/port topology | `list_usb_devices(tree=True)` |
| Detailed info for one USB device | `get_usb_device` |
| Thunderbolt devices + speeds | `list_thunderbolt_devices` |
| Hardware serial ports | `list_serial_ports` |
| All properties for a /dev/ device | `get_device_properties` |
| USB power/autosuspend state | `check_usb_power` |
| Serial port baud rate + settings | `get_serial_settings` |
| What process holds a port open? | `check_port_lock` |
| Kernel messages for a device | `get_device_messages` |
| Tool versions + availability | `tool_info` |
| Man page details | `read_manual` |

## Query Strategy

1. Always specify a device filter when you know the VID:PID — avoids scanning all devices.
2. Use `get_device_properties` as the single source of truth for any specific device.
3. Start with `list_usb_devices()` for overview, then drill into specific devices.
4. For Thunderbolt: `list_thunderbolt_devices` is the only path — sysfs attributes are harder to interpret.
5. For bandwidth analysis: `list_usb_devices(tree=True)` shows which devices share a root hub/controller. Same bus = shared bandwidth.
6. Be suspicious of empty results — no USB devices may mean `lsusb` not installed (`usbutils` package); no Thunderbolt may mean `boltctl` not installed; platform UARTs (ttyS0-3) always appear even without physical hardware.

## Safety

### Privilege

| Command | When | Notes |
|---------|------|-------|
| `lsusb -v` | Some descriptor fields | Shows `(error)` without sudo |
| `lsof` | Other users' processes | Only current user without sudo |
| `dmesg` | If kernel.dmesg_restrict=1 | Auto-retry with polkit |
| `boltctl` authorize | Authorization changes | Polkit required |
| `stty -F` | Serial port settings | Needs `dialout`/`tty` group, not sudo |

### High-risk operations

- Document udev rules and fixes but do not apply them — the user decides what to write to their system
- Unbinding/rebinding drivers (`echo > /sys/bus/usb/drivers/.../unbind`) can disrupt other devices on the same hub
- Resetting USB ports (`authorized` attribute) power-cycles the electrical connection

### Cross-references

- If USB network adapter issues → `linux-network-rules.md` "Network connectivity failure"
- If USB storage device → `linux-block-device-rules.md` for filesystem/mount issues
- If device kernel messages show I/O errors → `linux-block-device-rules.md` "Disk health check"
- For USB passthrough to VMs → `linux-virtual-rules.md` "Passthrough setup"

## Quirks

- **Bus/device numbers are not stable**: Change on reboot/reconnect. Use VID:PID or serial number.
- **lsusb bus:device vs sysfs path**: `Bus 001 Device 003` ≠ `/sys/bus/usb/devices/1-3`. Use `get_device_properties` for authoritative DEVPATH.
- **ttyACM vs ttyUSB**: ACM = `cdc_acm` driver; USB-serial = chipset-specific (ftdi_sio, cp210x, ch341).
- **Autosuspend is common**: Many distros enable by default. `control=auto` with short delay causes intermittent disconnects.
- **Thunderbolt without bolt**: If `boltctl` missing but `/sys/bus/thunderbolt/` exists, hardware is there but daemon isn't installed.
- **Hub speed vs device speed**: USB 3.0 device behind USB 2.0 hub negotiates at 480 Mbit/s. Tree view shows negotiated speeds.
- **Platform UARTs (ttyS0-3)**: Always enumerated even without physical hardware. Minimal udev properties.
- **lsusb -v partial output**: Some fields like `bMaxPower` show as 0 or `(error)` without root.

## Domain Deep Knowledge → linux-serial-device-deep-knowledge.md

Read when:
- Troubleshooting hits a dead end and quick resets haven't helped
- Need chipset-specific quirks (PL2303 clones, FTDI bricking, CH340 variants)
- Working with legacy systems (pre-systemd, missing sysfs/udev)
- User asks about driver quality or non-determinism in USB subsystem
- Need detailed pragmatic troubleshooting tactics (unbind/rebind, port reset, module reload)
