# Serial Device Reference

Extended reference material for USB, Thunderbolt, and serial device diagnostics. The main skill (SKILL.md) covers session start, common tasks, tool selection, and query strategies. This file contains device topology knowledge, troubleshooting tactics, driver quirks, and legacy system support.

## Device Topology Mental Model

### USB

```
Host controller (xHCI) → Root hub → [Hub(s)] → Device
```

- Each device has a **bus number** and **device number** (shown by `list_usb_devices`)
- Each device has a **VID:PID** (Vendor ID : Product ID) — the primary identifier
- Hubs create a tree: a device on a hub appears as `2-3.1` in sysfs (bus 2, port 3, hub port 1)
- Negotiated speed depends on both the device and the port it's plugged into
- USB 2.0 = 480 Mbit/s, USB 3.0 = 5 Gbit/s, USB 3.1 = 10 Gbit/s, USB 3.2 = 20 Gbit/s
- **USB3 dual-device presentation**: Every USB3 device presents two logical devices on the bus — a SuperSpeed (USB3) device and a Hi-Speed (USB2) companion. This is normal USB3 behaviour, not a sign of traffic splitting. USB2-only devices (e.g. keyboards, mice) only appear on the Hi-Speed side. When a dock's peripherals appear on two different buses, this is why — do not describe it as the dock "splitting traffic."

### Thunderbolt / USB4

```
Host controller (NHI) → Domain → [Daisy-chained devices]
```

- Managed by the `bolt` daemon, separate from USB
- Requires device **authorization** — security policy controls what connects
- Speed reported per-lane (e.g. `40 Gb/s = 2 lanes * 20 Gb/s`)
- Pre-boot: Thunderbolt is handled by firmware (BIOS/UEFI) directly via PCIe tunneling

#### Tunnelling architecture

Thunderbolt/USB4 is a **unified tunnel** over the USB-C connector. It encapsulates USB3, DisplayPort, and PCIe traffic into Thunderbolt packets on a single link. USB-C "Alternate Mode" switches the connector's pins from plain USB signalling to carry the Thunderbolt tunnel — it is NOT multiplexing different protocols side by side.

Key implications:

- **The Thunderbolt controller is a PCIe device.** It does not appear in `lsusb`. Use `list_thunderbolt_devices()` (boltctl) to see it.
- **USB devices on a TB dock appear on virtual xHCI controllers** created by the tunnel. They show up as separate USB buses in `lsusb`, not as children of a visible TB device.
- **The only USB device that identifies the dock by name** is typically a Billboard device (USB Billboard class, used for alt-mode negotiation). Everything else looks like generic hubs and peripherals.
- Do not describe TB as carrying protocols "alongside" each other — it tunnels them inside a single transport layer.

### Serial Ports

```
Hardware UART (ttyS0-3) — onboard platform ports, always enumerated
USB-serial adapter (ttyUSB0, ttyACM0) — appear when plugged in
```

- **ttyUSB** = USB-to-serial adapters (FTDI, CP210x, CH340 chipsets)
- **ttyACM** = CDC-ACM class devices (modems, Arduino, some routers)
- Different drivers, same diagnostic approach
- Only entries in `/sys/class/tty/` with a `device/` subdir are real hardware

## Drawing Device Topology (Detailed)

**Step 1 — Gather all data in one pass:**

1. `list_usb_devices(tree=True)` — bus/port topology
2. `list_usb_devices()` — human-readable product names and VID:PID
3. `list_thunderbolt_devices()` — TB devices, speeds, authorization
4. `list_serial_ports()` — if serial devices are relevant

Do not make multiple passes over the same data. Gather everything, then analyse.

**Step 2 — Classify devices:**

- **Physical (user plugs in/out)**: peripherals, external hubs, docks, adapters
- **Internal (built-in)**: laptop webcam, fingerprint reader, Bluetooth, platform UARTs
- **Uncertain**: hubs where you cannot determine if they're inside a dock or external — **flag these and ask the user before proceeding**

**Step 3 — Check available renderers:**

- `dot` (Graphviz) — proper graph layout, labelled ports, renders to PNG/SVG
- `mmdc` (Mermaid CLI) — graph rendering, needs a renderer to view
- `graph-easy` — ASCII graph rendering
- If none available, use an ASCII block diagram

Do not assume Mermaid is available. Check first with `tool_info()` or a quick `which` command.

**Step 4 — Render with labels:**

- Use human-friendly names (product strings), not bus/device IDs
- Label every connection with port type (USB-A, USB-C, USB-C/TB4) and connector
- Annotate link speeds where known (e.g. "USB 3.0, 5 Gb/s", "TB4, 40 Gb/s")
- Distinguish physical ports from logical buses
- Group internal/built-in devices separately from external/pluggable devices

## Reality Check: Drivers, Devices, and Non-Determinism

The Linux USB and serial subsystems are among the most complex and least predictable parts of the kernel. Be aware of these realities:

### The driver quality problem

Greg Kroah-Hartman (Linux USB/serial subsystem maintainer) has documented extensively that:

- **Many USB drivers are poor quality.** Vendors often write minimal drivers to pass certification, not to handle edge cases. Some are direct Windows driver ports that misuse Linux APIs.
- **Devices frequently violate their own specifications.** A device claiming USB 2.0 compliance may not handle suspend/resume, may enumerate incorrectly, or may advertise capabilities it doesn't support.
- **The device ecosystem is vast.** Thousands of VID:PID combinations exist. The kernel cannot test against all of them. Regressions happen with kernel updates.
- **Serial-over-USB is particularly fragile.** CH340, CP210x, FTDI, and PL2303 chipsets each have their own quirks. Counterfeit chips (especially FTDI and PL2303) cause driver mismatch and silent failures.

### Non-determinism to expect

- **Enumeration order is not guaranteed.** ttyUSB0 today may be ttyUSB1 tomorrow if another device enumerates first. Use udev rules with serial numbers for stable naming.
- **Hot-plug timing is unpredictable.** A device may take 50ms or 5 seconds to fully enumerate depending on hub topology, driver load time, and firmware handshake.
- **Devices enter wedged states.** A device can appear in lsusb but fail to function — the kernel sees it but the driver handshake is stuck. Power-cycling the port or device is often the only recovery.
- **Suspend/resume breaks things.** Laptop lid-close → reopen frequently leaves USB devices in an undefined state. The kernel attempts re-enumeration but some devices don't respond.
- **Hub resets cascade.** Resetting one port on a hub can disrupt other devices on the same hub. This is by design (USB spec) but surprises users.

### What this means for troubleshooting

- **Do not assume tools tell the full story.** A device appearing in `lsusb` does NOT mean it's working. A driver being bound does NOT mean communication is happening.
- **Be suspicious of "it was working before".** Kernel updates, firmware changes, and even ambient electrical conditions can change behaviour.
- **The physical layer matters.** Bad cables, underpowered hubs, and marginal connections cause symptoms that look like software issues. Always consider the physical path.
- **Counterfeit hardware is common.** Cheap USB-serial adapters frequently use counterfeit FTDI or PL2303 chips that legitimate drivers refuse or mishandle. If a known-good adapter fails, the chip may be a clone.

## Pragmatic Troubleshooting Tactics

When detailed analysis doesn't yield a clear answer, or when a quick fix is needed, these physical and software resets often resolve issues. Present these to the user as options, ordered from least to most disruptive:

### Quick resets (suggest first)

1. **Replug the device** — physically disconnect and reconnect. Sounds obvious but forces full re-enumeration and driver rebind.
2. **Try a different port** — bypasses a potentially faulty port, hub, or controller. Try a port on a different bus if possible.
3. **Try a different cable** — USB cables degrade, especially at the connector. USB 3.0 cables are more sensitive to quality than 2.0.
4. **Remove the hub** — connect directly to the host. Eliminates hub power/signalling issues from the equation.

### Software resets (suggest if replug fails)

5. **Unbind and rebind the driver** — forces the kernel to re-initialise the device without physical disconnection:
   ```
   echo "DEVICE_ID" > /sys/bus/usb/drivers/DRIVER/unbind
   echo "DEVICE_ID" > /sys/bus/usb/drivers/DRIVER/bind
   ```
   The DEVICE_ID comes from the sysfs path (e.g. `2-3:1.0`). Suggest the commands but do not execute them.

6. **Reset the USB port** — use `usbreset` (from usbutils) or write to the sysfs `authorized` attribute:
   ```
   echo 0 > /sys/bus/usb/devices/DEVICE/authorized
   echo 1 > /sys/bus/usb/devices/DEVICE/authorized
   ```
   This power-cycles the port at the electrical level.

7. **Reload the kernel module** — `modprobe -r <module> && modprobe <module>`. Useful when the driver itself is in a bad state. Find the module from `get_device_properties` (DRIVER property) or `lsmod | grep <chipset>`.

### Persistent fixes (suggest when pattern is identified)

8. **Udev rules** — for stable device naming, disabling autosuspend, setting permissions. Document the rule, do not write it.
9. **Kernel module parameters** — some drivers accept quirks via modprobe options. Check `modinfo <module>` for available parameters.
10. **Firmware updates** — some devices (especially docks, hubs, Thunderbolt controllers) have updatable firmware. Check vendor support pages.

### When to tell the user "it's the hardware"

- Consistent failures across multiple ports, cables, and systems → likely the device itself
- dmesg shows repeated "device descriptor read" errors → electrical signalling failure
- Device works on Windows but not Linux → likely a driver gap; check if a newer kernel version adds support
- Random disconnects only under load → power delivery issue (check bMaxPower vs hub capacity)

## Sudo Considerations

- **stty -F**: Requires group membership (`dialout` or `tty`), not sudo
- **lsof**: Without sudo, only shows processes owned by the current user
- **dmesg**: May require sudo if `kernel.dmesg_restrict=1`
- **udevadm info**: Generally accessible without sudo
- **lsusb -v**: Some descriptor fields may show as `(error)` without sudo
- **boltctl**: Generally accessible without sudo for listing; authorization changes require polkit

## Known Quirks

- **Bus/device numbers are not stable**: They change on reboot or reconnect. Use VID:PID or serial number for persistent identification.
- **lsusb bus:device vs sysfs path**: `Bus 001 Device 003` does NOT map to `/sys/bus/usb/devices/1-3`. The sysfs path depends on port topology. Use `get_device_properties` to find the authoritative sysfs DEVPATH.
- **ttyACM vs ttyUSB**: ACM devices use the `cdc_acm` driver; USB-serial uses chipset-specific drivers (ftdi_sio, cp210x, ch341). Both appear in `list_serial_ports`.
- **Autosuspend is common**: Many distros enable USB autosuspend by default. `control=auto` with a short delay causes intermittent disconnects on poorly-behaved devices.
- **Thunderbolt without bolt**: If `boltctl` is missing but `/sys/bus/thunderbolt/` exists, the hardware is there but the management daemon is not installed.
- **Hub speed vs device speed**: A USB 3.0 device behind a USB 2.0 hub negotiates at 480 Mbit/s. The `tree` view shows negotiated speeds per port — check the chain.
- **Platform UARTs (ttyS0-3)**: Always enumerated even if no physical hardware is connected. `list_serial_ports` includes them; udev properties will be minimal for these.
- **lsusb -v partial output without sudo**: Some fields like `bMaxPower` may show as 0 or `(error)` without root access. The flat listing (`list_usb_devices`) works fine without sudo.

## Extended Known Quirks

Chipset-specific and hardware-specific quirks.

- **PL2303 clones**: Prolific revoked support for counterfeit PL2303 chips in their official driver. The kernel `pl2303` driver may refuse to bind or produce "unsupported device" errors. Workaround: use an older kernel module or replace the adapter.
- **FTDI bricking**: FTDI's Windows driver historically bricked counterfeit FT232 chips by overwriting the USB PID to 0000. If a device shows VID:PID `0403:0000`, this happened. Linux can recover it with `ftdi_eeprom`.
- **CH340 variants**: CH340, CH340G, CH341 are related but not identical. The `ch341` kernel module handles all variants but some cheap boards have inadequate oscillators causing baud rate errors at higher speeds.
- **USB device descriptor read errors**: `"device descriptor read/64, error -71"` or similar in dmesg usually indicates an electrical problem (cable, power, connector) not a software issue. Don't debug software — check the physical path.
- **Device resets in a loop**: If dmesg shows repeated "reset high-speed USB device" messages, the device is likely in a wedged state. The kernel retries enumeration but the device firmware is stuck. Power-cycle the hub or port.

## Legacy System Support

The MCP tools assume a modern Linux stack (udev, sysfs, dbus). On older or minimal systems, some tools will degrade or be unavailable.

### What may be missing

| Component | Modern (systemd-era) | Legacy / minimal |
|-----------|---------------------|------------------|
| Device enumeration | `udevadm info`, sysfs | `cat /proc/bus/usb/devices`, `lsusb` (still works) |
| Device events | `udevadm monitor` | `dmesg` only |
| Stable naming | udev rules | Manual symlinks or devfs |
| Thunderbolt | `boltctl` | Not applicable (pre-Thunderbolt hardware) |
| Serial port config | `stty`, udev properties | `stty`, `setserial` |
| USB sysfs | `/sys/bus/usb/devices/` | `/proc/bus/usb/` (procfs USB, deprecated) |

### Fallback approaches

- If `udevadm` is missing: `lsusb` still works standalone. For device properties, fall back to reading `/proc/bus/usb/devices` or parsing `lsusb -v` output.
- If `/sys/bus/usb/` is empty or absent: the kernel may lack CONFIG_SYSFS or CONFIG_USB support. Check `uname -r` and kernel config.
- If `setserial` is needed: it's the legacy tool for configuring hardware UARTs (IRQ, I/O port). On modern systems `stty` covers the same ground, but embedded or very old systems may need it.
- For `/proc/bus/usb/devices`: each entry has fields T (topology), B (bandwidth), D (device), P (product), S (serial), C (config), I (interface). Older than sysfs but reliable.

### Kernel version awareness

- USB 3.0 (xHCI) support: kernel 2.6.31+ (2009)
- Thunderbolt: kernel 3.17+ (2014), USB4 support kernel 5.6+ (2020)
- udev merged into systemd: 2012 (before that, standalone udev)
- sysfs USB attributes: kernel 2.6.0+ (2003)
- `/proc/bus/usb/` deprecated in favour of sysfs from kernel 2.6.x onward

If the system runs a kernel older than 4.x, expect reduced tool coverage. `lsusb`, `stty`, `dmesg`, and direct `/proc/` reads should still work.
