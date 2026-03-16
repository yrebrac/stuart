---
name: linux-serial-device-deep-knowledge
description: >
    Extended USB/Thunderbolt/serial reference. Read on demand when
    troubleshooting hits a dead end, need chipset quirks, driver quality
    context, legacy system support, or detailed reset tactics. NOT auto-loaded.
---

# USB, Thunderbolt & Serial Devices: Deep Knowledge

Extended reference for the serial-device domain. Read when directed by the rules file.

## Contents

- [Driver Quality and Non-Determinism](#driver-quality-and-non-determinism)
- [Pragmatic Troubleshooting Tactics](#pragmatic-troubleshooting-tactics)
- [Chipset-Specific Quirks](#chipset-specific-quirks)
- [Legacy System Support](#legacy-system-support)
- [Kernel Version Awareness](#kernel-version-awareness)

## Driver Quality and Non-Determinism

Greg Kroah-Hartman (Linux USB/serial subsystem maintainer) has documented extensively:

- **Many USB drivers are poor quality.** Vendors write minimal drivers to pass certification. Some are direct Windows driver ports.
- **Devices frequently violate specs.** A USB 2.0 device may not handle suspend/resume or may advertise unsupported capabilities.
- **The device ecosystem is vast.** Thousands of VID:PID combinations. Regressions happen with kernel updates.
- **Serial-over-USB is particularly fragile.** CH340, CP210x, FTDI, PL2303 each have their own quirks.

### Non-determinism to expect

- **Enumeration order not guaranteed.** ttyUSB0 today may be ttyUSB1 tomorrow.
- **Hot-plug timing unpredictable.** 50ms to 5 seconds depending on hub topology and firmware.
- **Devices enter wedged states.** Visible in lsusb but non-functional. Power-cycling is often the only recovery.
- **Suspend/resume breaks things.** Laptop lid-close frequently leaves USB devices in undefined state.
- **Hub resets cascade.** Resetting one port can disrupt other devices on the same hub (by design).

### What this means

- A device in `lsusb` does NOT mean it's working
- "It was working before" may just be kernel/firmware/electrical change
- The physical layer matters — bad cables, underpowered hubs, marginal connections
- Counterfeit hardware is common — cheap adapters with cloned chips

## Pragmatic Troubleshooting Tactics

When detailed analysis doesn't yield a clear answer. Ordered from least to most disruptive.

### Quick resets (suggest first)

1. **Replug the device** — forces full re-enumeration and driver rebind
2. **Try a different port** — bypasses faulty port/hub/controller. Different bus if possible.
3. **Try a different cable** — USB cables degrade, especially at connectors. USB 3.0 cables more sensitive.
4. **Remove the hub** — connect directly to host. Eliminates hub power/signalling issues.

### Software resets (suggest if replug fails)

5. **Unbind and rebind the driver**:
   ```
   echo "DEVICE_ID" > /sys/bus/usb/drivers/DRIVER/unbind
   echo "DEVICE_ID" > /sys/bus/usb/drivers/DRIVER/bind
   ```
   DEVICE_ID from sysfs path (e.g. `2-3:1.0`). Document, don't execute.

6. **Reset the USB port**:
   ```
   echo 0 > /sys/bus/usb/devices/DEVICE/authorized
   echo 1 > /sys/bus/usb/devices/DEVICE/authorized
   ```
   Power-cycles the port electrically.

7. **Reload the kernel module**: `modprobe -r <module> && modprobe <module>`. Find module from `get_device_properties` (DRIVER) or `lsmod`.

### Persistent fixes (when pattern identified)

8. **Udev rules** — stable naming, disable autosuspend, permissions. Document, don't write.
9. **Kernel module parameters** — `modinfo <module>` for available quirk params.
10. **Firmware updates** — docks, hubs, TB controllers may have updatable firmware.

### When to tell the user "it's the hardware"

- Consistent failures across multiple ports, cables, systems → device itself
- Repeated "device descriptor read" errors in dmesg → electrical signalling failure
- Works on Windows but not Linux → driver gap (check newer kernel)
- Random disconnects only under load → power delivery (bMaxPower vs hub capacity)

## Chipset-Specific Quirks

- **PL2303 clones**: Prolific revoked support for counterfeits. Kernel `pl2303` driver may refuse to bind. Workaround: older kernel module or replace adapter.
- **FTDI bricking**: FTDI's Windows driver historically overwrote counterfeit FT232 PID to 0000. VID:PID `0403:0000` = bricked clone. Linux can recover with `ftdi_eeprom`.
- **CH340 variants**: CH340, CH340G, CH341 are related but not identical. `ch341` kernel module handles all, but cheap boards with inadequate oscillators cause baud rate errors at higher speeds.
- **USB device descriptor read errors**: `"device descriptor read/64, error -71"` = electrical problem. Don't debug software.
- **Device resets in a loop**: Repeated "reset high-speed USB device" = wedged firmware. Power-cycle hub or port.

## Legacy System Support

MCP tools assume modern Linux (udev, sysfs, dbus). On older/minimal systems:

| Component | Modern (systemd-era) | Legacy / minimal |
|-----------|---------------------|------------------|
| Device enumeration | `udevadm info`, sysfs | `cat /proc/bus/usb/devices`, `lsusb` |
| Device events | `udevadm monitor` | `dmesg` only |
| Stable naming | udev rules | Manual symlinks or devfs |
| Thunderbolt | `boltctl` | Not applicable |
| Serial port config | `stty`, udev properties | `stty`, `setserial` |
| USB sysfs | `/sys/bus/usb/devices/` | `/proc/bus/usb/` (deprecated) |

### Fallback approaches

- No `udevadm`: `lsusb` still works. Fall back to `/proc/bus/usb/devices` or `lsusb -v`.
- No `/sys/bus/usb/`: kernel may lack CONFIG_SYSFS or CONFIG_USB.
- `setserial`: legacy tool for hardware UARTs (IRQ, I/O port). Modern systems use `stty`.
- `/proc/bus/usb/devices`: fields T (topology), B (bandwidth), D (device), P (product), S (serial), C (config), I (interface).

## Kernel Version Awareness

- USB 3.0 (xHCI): kernel 2.6.31+ (2009)
- Thunderbolt: kernel 3.17+ (2014), USB4: kernel 5.6+ (2020)
- udev merged into systemd: 2012
- sysfs USB attributes: kernel 2.6.0+ (2003)
- `/proc/bus/usb/` deprecated from kernel 2.6.x onward

If kernel older than 4.x, expect reduced tool coverage. `lsusb`, `stty`, `dmesg`, and `/proc/` reads should still work.
