# System Profile — Reference

Extended reference for the system-profile utility skill. Read on demand, not auto-loaded.

## YAML Schemas

### hardware.yaml

```yaml
gathered_at: "2026-03-06T14:30:00+11:00"    # ISO 8601 with timezone
gathered_by: "stuart"

cpu:
    model: "AMD Ryzen 9 7950X"               # from lscpu "Model name"
    cores: 16                                 # from lscpu "Core(s) per socket"
    threads: 32                               # from lscpu "CPU(s)"
    architecture: "x86_64"                    # from lscpu "Architecture"
    sockets: 1                                # from lscpu "Socket(s)"

memory:
    total: "62Gi"                             # from free -h "total"

disks:                                        # from lsblk, type "disk" only
    - device: "nvme0n1"
      size: "931.5G"
      type: "disk"
      transport: "nvme"                       # nvme, sata, usb — from device path or lsblk TRAN
      partitions:                             # from lsblk children
          - name: "nvme0n1p1"
            size: "600M"
            fstype: "vfat"
            mountpoint: "/boot/efi"
          - name: "nvme0n1p3"
            size: "930G"
            fstype: "btrfs"
            mountpoint: "/"

gpu:                                          # from lspci | grep -i vga; null if none
    model: "AMD Radeon RX 7900 XTX"
    driver: "amdgpu"                          # from lspci -k if available, else null

chassis:
    type: "desktop"                           # desktop, laptop, server, vm, container, unknown
    source: "hostnamectl"                     # how it was determined
```

### distro.yaml

```yaml
gathered_at: "2026-03-06T14:30:00+11:00"
gathered_by: "stuart"

os:
    name: "Fedora Linux"                      # from /etc/os-release NAME
    version: "43"                             # from /etc/os-release VERSION_ID
    pretty_name: "Fedora Linux 43 (Workstation Edition)"  # PRETTY_NAME
    id: "fedora"                              # ID
    variant: "Workstation Edition"            # VARIANT, null if absent

kernel:
    version: "6.18.12-200.fc43.x86_64"       # from uname -r
    architecture: "x86_64"                    # from uname -m

package_managers:                             # which are installed (not all will be active)
    - name: "dnf5"
      path: "/usr/bin/dnf5"
    - name: "flatpak"
      path: "/usr/bin/flatpak"

desktop:
    environment: "GNOME"                      # from $XDG_CURRENT_DESKTOP, null if headless
    display_server: "wayland"                 # wayland or x11, from $XDG_SESSION_TYPE

init_system: "systemd"                        # from ps -p 1 -o comm=
```

### toolchain.yaml

```yaml
gathered_at: "2026-03-06T14:30:00+11:00"
gathered_by: "stuart"

servers:
    journald:
        status: "ok"                          # ok, degraded (some tools missing), unavailable
        tools:
            journalctl:
                exists: true
                version: "systemd 256"
            systemd-cat:
                exists: true
                version: "systemd 256"

    network:
        status: "degraded"
        tools:
            ip:
                exists: true
                version: "ip utility, iproute2-6.11.0"
            ethtool:
                exists: false
            nmap:
                exists: false

summary:
    total_servers: 9
    ok: 7
    degraded: 2
    unavailable: 0
    missing_tools: 3
    missing_tools_list:                       # for quick reference
        - server: "network"
          tool: "ethtool"
        - server: "network"
          tool: "nmap"
```

## Detailed Gathering Commands

### Hardware

Run all in a single Bash call:

```bash
echo "===LSCPU===" && lscpu && \
echo "===FREE===" && free -h && \
echo "===LSBLK===" && lsblk -o NAME,SIZE,TYPE,FSTYPE,TRAN,MOUNTPOINTS && \
echo "===GPU===" && (lspci 2>/dev/null | grep -i 'vga\|3d\|display' || echo "no gpu detected") && \
echo "===CHASSIS===" && (cat /sys/class/dmi/id/chassis_type 2>/dev/null || echo "unknown") && \
echo "===HOSTNAMECTL===" && (hostnamectl 2>/dev/null | grep -i 'chassis\|virtualization' || echo "unknown") && \
echo "===TIMESTAMP===" && date -Iseconds
```

**Parsing guidance:**

- `lscpu`: extract "Model name", "CPU(s)", "Core(s) per socket", "Socket(s)", "Architecture"
- `free -h`: extract "total" from the "Mem:" row
- `lsblk`: filter rows where TYPE is "disk" for top-level devices; children are partitions
- `lspci`: first match is usually the primary GPU. Extract model from the description field
- `chassis_type`: integer mapping — 1=Other, 3=Desktop, 8=Portable, 9=Laptop, 10=Notebook. See DMI spec for full list. `hostnamectl` provides a human-readable string ("desktop", "laptop", "vm")
- If `lspci` is not available, GPU will be null — this is fine for the baseline profile

**Fallback commands:**

- No `lspci`: check `/sys/bus/pci/devices/*/class` for display controllers (class `0x030000`)
- No `hostnamectl`: check `systemd-detect-virt` for VM detection, fall back to chassis_type integer

**Sudo-enhanced (optional, skip if unavailable):**

- `sudo dmidecode -t system` — manufacturer, product name, serial number
- `sudo dmidecode -t baseboard` — motherboard model
- `sudo smartctl -a /dev/nvme0n1` — disk health (SMART data)

### Distro

Run all in a single Bash call:

```bash
echo "===OS_RELEASE===" && cat /etc/os-release && \
echo "===KERNEL===" && uname -r && \
echo "===ARCH===" && uname -m && \
echo "===PKG_MANAGERS===" && for cmd in dnf5 dnf apt apt-get pacman zypper flatpak snap; do command -v "$cmd" 2>/dev/null && echo "found: $cmd"; done && \
echo "===DESKTOP===" && echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-unset}" && echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-unset}" && \
echo "===INIT===" && ps -p 1 -o comm= && \
echo "===TIMESTAMP===" && date -Iseconds
```

**Parsing guidance:**

- `/etc/os-release`: key=value format. Extract NAME, VERSION_ID, PRETTY_NAME, ID, VARIANT (VARIANT may not exist)
- Package managers: `command -v` returns the path if found. Record name and path for each
- `XDG_CURRENT_DESKTOP`: may be colon-separated (e.g. `ubuntu:GNOME`). Use the last component. Note: Claude Code runs Bash in a non-interactive shell — these session-level variables may not be inherited. If unset, try `loginctl show-session $(loginctl | awk 'NR==2{print $1}') -p Desktop -p Type --value` as a fallback, or record as null.
- `XDG_SESSION_TYPE`: "wayland", "x11", or "tty". Same caveat as `XDG_CURRENT_DESKTOP`.
- `ps -p 1`: almost always "systemd" on modern Linux. Could be "init" on older or niche systems

### Toolchain

No Bash commands — call `tool_info()` on each active MCP server:

1. `mcp__plugin_stuart_journald__tool_info()`
2. `mcp__plugin_stuart_systemd__tool_info()`
3. `mcp__plugin_stuart_block-device__tool_info()` (note: hyphenated server name)
4. `mcp__plugin_stuart_syslog__tool_info()`
5. `mcp__plugin_stuart_serial-device__tool_info()`
6. `mcp__plugin_stuart_container__tool_info()`
7. `mcp__plugin_stuart_network__tool_info()`
8. `mcp__plugin_stuart_virtual__tool_info()`
9. `mcp__plugin_stuart_packages__tool_info()`

Call all in parallel where possible — they are independent.

Most servers return JSON with tool entries: `{"tool_name": {"exists": bool, "path": str|null, "version": str|null}}`.

**Exceptions:**
- **packages** server: returns a richer structure with `distro`, `package_manager`, `tools`, and `universal_formats` keys — extract the `tools` dict for tool status.
- **container** server: returns runtime detection (docker/podman presence, compose capability), not a tools dict. Parse for runtime availability and compose status instead of iterating tool entries.

## Tool Audit Procedure

1. **Enumerate active servers** — use the list above (all 9 servers from `.mcp.json`)
2. **Call `tool_info()`** on each server. If a server fails to respond (timeout, not enabled), record its status as "unavailable"
3. **Parse results** — for each server, iterate tool entries:
   - `exists: true` → tool is installed, record version
   - `exists: false` → tool is missing
4. **Determine server status**:
   - All tools exist → `ok`
   - Some tools missing → `degraded`
   - Server didn't respond → `unavailable`
5. **Aggregate missing tools** — collect all tools where `exists: false` across all servers
6. **Look up packages for missing tools** — `tool_info()` does not include package hints. For each missing tool, use `search_provider("<tool_name>")` from the packages MCP server to find the providing package. If `search_provider` is unavailable or times out, use WebSearch as a fallback. Do not hardcode tool-to-package mappings — they vary by distro and go stale.
7. **Generate install commands** — use the distro profile's package manager. Default to `sudo dnf install` for Fedora, `sudo apt install` for Debian-family
8. **Present report** as a table:

```
Server         Tool         Status     Package         Install Command
───────────────────────────────────────────────────────────────────────
block-device   smartctl     missing    smartmontools   sudo dnf install smartmontools
network        ethtool      missing    ethtool         sudo dnf install ethtool
network        nmap         missing    nmap            sudo dnf install nmap
```

## Profile Staleness Logic

| Profile | Threshold | Rationale |
|---------|-----------|-----------|
| hardware | 30 days | Hardware changes are rare — physical swap, new RAM |
| distro | 7 days | Kernel updates happen weekly on rolling distros |
| toolchain | 7 days | User may install/remove packages between sessions |

**When to suggest refresh (not force):**

- Profile age exceeds threshold AND the current task is relevant (e.g. don't suggest refreshing hardware profile for a network question)
- User reports they upgraded the OS, installed packages, or changed hardware
- A `tool_info()` call during a task shows a tool that the toolchain profile says is missing (or vice versa) — the profile is stale

**Invalidation triggers:**

- `uname -r` differs from `distro.yaml` kernel version → distro profile is stale
- Package install/remove detected (e.g. user ran `sudo dnf install ethtool`) → toolchain profile is stale
- Hardware change is rarely detectable automatically — rely on age threshold

## Future Profile Types

Reserved for future implementation (see IDEAS.md):

- **network-profile** (owner: Ben): interfaces, VPN clients installed, default connectivity topology. Distinct from live network state. Would complement the network skill for faster troubleshooting context.
- **service-profile**: running daemons, container runtime, libvirtd, databases, web servers. Things that affect troubleshooting context. Boundary with "dynamic state" needs careful definition.
