---
name: linux-block-device
description: >
    Domain knowledge for block device, storage stack, and disk health
    administration using the block-device MCP server. Load BEFORE using
    any block-device MCP tool directly. Covers the full storage hierarchy:
    physical devices, partitions, LVM/RAID/DM, filesystems, and mounts.
---

# Block Devices & Storage

## Session Start

1. Call `tool_info()` to see which storage commands are available
2. Note missing tools — some (smartctl, nvme, cryptsetup) require package installation
3. If the system uses LVM, RAID, btrfs, or LUKS, note that for query strategy

## Common Tasks

### "Disk is full"

1. `check_disk_usage` — which filesystems are full or near-full?
2. `list_mounts` — check mount options, find the device behind the mount point
3. For btrfs: subvolume usage may differ from `df` — check with Bash `btrfs filesystem usage`

### "What disks do I have?"

1. `list_devices(columns="NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL")` — full overview
2. `identify_device` — UUIDs, labels, partition types
3. `read_sysfs(device, "queue/rotational")` — SSD (0) vs HDD (1)

### "Is my disk healthy?"

1. `tool_info` — check if smartctl/nvme are available
2. If available: `smart_health` (Phase 2) — otherwise suggest `sudo smartctl -a /dev/sdX` via Bash
3. `get_device_messages` — kernel messages for I/O errors, bad sectors, resets

### "Mount isn't working"

1. `list_mounts` — is it currently mounted? With what options?
2. `identify_device` — does the UUID match fstab?
3. `get_device_messages` — I/O errors or filesystem corruption messages
4. Check systemd mount units: use systemctl MCP `list_units(type="mount")`

## Storage Stack Mental Model

```
Physical device → Partition table → Partition →
  [LUKS encryption] → [RAID array] → [LVM PV → VG → LV] →
  Filesystem → Mount point
```

Not all layers are present on every system. Start from the top (`list_devices`) and work down through whatever layers exist.

## Tool Selection

| Goal | Tool |
|------|------|
| What devices exist? | `list_devices` |
| Device UUIDs, labels, types? | `identify_device` |
| Where is everything mounted? | `list_mounts` |
| How full are filesystems? | `check_disk_usage` |
| Low-level device property? | `read_sysfs` |
| Kernel messages for a device? | `get_device_messages` |
| Is the disk healthy? | `smart_health` (Phase 2) |
| LVM layout? | `list_lvm` (Phase 3) |
| RAID status? | `raid_status` (Phase 3) |
| Filesystem metadata? | `fs_info` (Phase 4) |
| Systemd mount units? | Use systemctl MCP: `list_units(type="mount")` |

## Query Strategy

### Top-down approach

1. `list_devices` — get the full block device tree
2. `check_disk_usage` — which filesystems are full or near-full?
3. Drill into specific devices based on findings

### Device identification

- `list_devices(columns="NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL")` for a comprehensive overview
- `identify_device` when you need UUIDs or PARTUUIDs (e.g. matching fstab entries)
- `read_sysfs(device, "queue/rotational")` to distinguish SSD (0) from HDD (1)

### Mount troubleshooting

- `list_mounts` shows current mount tree with options
- Systemd mount units (via systemctl MCP) may override or supplement fstab
- When a mount fails: check `get_device_messages` for I/O errors, `identify_device` for UUID matches

### Efficient queries

- Always specify a device when you know which one you're investigating
- Use `columns` parameter in `list_devices` to get exactly the info you need
- Use `read_sysfs` for specific low-level attributes rather than parsing lsblk output

## Privilege Escalation

Some storage tools need elevated privileges. Stuart auto-escalates via polkit when configured.

| Command | When | Without polkit |
|---------|------|---------------|
| smartctl, nvme | Always needs root | Suggest `sudo` to user |
| blkid -p | Probe mode only | Normal blkid works unprivileged |
| dmesg | If kernel.dmesg_restrict=1 | Auto-retry with polkit, else suggest `sudo` |
| cryptsetup | LUKS header reads | Suggest `sudo` to user |
| dmidecode | Always needs root | Suggest `sudo` to user |

If a tool reports "Permission denied", the error message includes the exact `sudo` command to run manually. For automatic escalation, see PRIVILEGES.md.

## Known Quirks

- **lsblk columns vary by version**: Use `read_manual("lsblk", "COLUMNS")` to see available columns on this system
- **blkid without sudo may use cached data**: Pass `probe=True` for fresh reads (may need sudo)
- **NVMe vs SATA naming**: NVMe devices appear as `/dev/nvme*`, SATA as `/dev/sd*`. Use `list_devices` to see the full picture.
- **btrfs needs mount points**: Most btrfs commands take a mount point path, not `/dev/...`. Use `list_mounts` to find the mount point first.
- **Device mapper names**: DM devices appear as `/dev/dm-N` in lsblk but are mapped to names under `/dev/mapper/`. Use `identify_device` or `list_devices(filesystem=True)` to see both.
