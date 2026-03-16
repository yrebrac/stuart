---
name: linux-block-device
description: >
    Domain knowledge for block device, storage stack, and disk health
    administration using the block-device MCP server. Load BEFORE using
    any block-device MCP tool directly. Covers the full storage hierarchy:
    physical devices, partitions, LVM/RAID/DM, filesystems, and mounts.
---

# Block Devices & Storage

## Guide

This file covers block device and storage stack administration.

- **Domain Model** — storage hierarchy and how layers interact
- **Heuristics** — expert shortcuts and likelihood-based triage
- **Anti-patterns** — common mistakes to avoid
- **Procedures** — diagnostic and investigation workflows with decision branching
- **Tools** — goal-to-tool lookup table for the block-device MCP server
- **Query Strategy** — rules for efficient, scoped storage queries
- **Safety** — privilege requirements, high-risk operations, cross-domain pointers
- **Quirks** — version-specific edge cases and non-obvious behavior
- **Domain Deep Knowledge** — extended concepts (inline, this domain is compact)

## Domain Model

```
Physical device → Partition table → Partition →
  [LUKS encryption] → [RAID array] → [LVM PV → VG → LV] →
  Filesystem → Mount point
```

Not all layers are present on every system. Start from the top (`list_devices`) and work down through whatever layers exist.

Key relationships:
- A physical device can have multiple partitions, each independently formatted or used as LVM PVs
- LVM adds an abstraction: Physical Volumes (PV) → Volume Groups (VG) → Logical Volumes (LV)
- Device mapper (`/dev/dm-*`) underpins LVM, LUKS, and multipath — names under `/dev/mapper/` are human-readable aliases
- Filesystems sit on top of whichever layer is "last" (partition, LV, or raw device)
- Mount points connect filesystems to the directory tree

## Heuristics

1. "Disk is full" is almost always one filesystem, not the whole disk. `check_disk_usage` identifies which mountpoint immediately — don't scan all devices.
2. If btrfs, `df` lies. Btrfs allocates in chunks; `btrfs filesystem usage <mountpoint>` is the truth. `df` can show 90% used when 50% is reclaimable.
3. When a mount fails, the UUID mismatch between fstab and the actual device is the most common cause. Check `identify_device` against fstab entries.
4. I/O errors in `dmesg` that mention sector numbers usually indicate physical media failure, not software. Check SMART data before troubleshooting software.
5. NVMe devices use `/dev/nvme*` naming with partition notation `nvme0n1p1` — don't confuse namespace (`n1`) with partition (`p1`).

## Anti-patterns

- Don't assume `/dev/sda` is the boot disk. Multi-disk systems and USB boot media can reorder devices. Use `list_mounts` to find `/` and trace back.
- Don't run `fsck` on a mounted filesystem — it causes data corruption. Always check mount state first with `list_mounts`.
- Don't rely on `lsblk` column output being consistent across versions — use the `columns` parameter to request exactly what you need.
- Don't parse `/dev/dm-N` numbers for meaning — they're assigned dynamically. Use `/dev/mapper/` names or `identify_device` for stable references.
- Don't assume `blkid` output is fresh — without `probe=True` it may use cached data from the udev database.

## Procedures

### Disk full investigation
When a user reports "disk full" or a filesystem is at/near capacity.

1. `check_disk_usage` — identify which filesystem(s) are full or near-full
2. IF single filesystem full:
     `list_mounts` — find the device behind the mount point
     IF btrfs: `btrfs filesystem usage <mountpoint>` via Bash — df is unreliable
     Suggest: `du -sh <mountpoint>/* | sort -rh | head -20` for top consumers
   IF multiple filesystems full:
     Check if they share a physical device or VG — may be a disk-level issue
3. IF LVM:
     Check VG free space — LV may be expandable without adding disks
4. VERIFY: `check_disk_usage` — confirm space reclaimed
5. CROSS-DOMAIN: If a service failed due to disk full → `linux-systemd-rules.md` "Service failure investigation"

### Device identification
When you need to understand the storage layout or identify specific devices.

1. `list_devices(columns="NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL")` — full overview
2. IF need UUIDs or labels:
     `identify_device` — UUIDs, PARTUUIDs, labels, partition types
3. IF need SSD vs HDD:
     `read_sysfs(device, "queue/rotational")` — 0=SSD, 1=HDD
4. IF device mapper present:
     `list_devices(filesystem=True)` — shows `/dev/mapper/` names alongside `dm-N`
5. VERIFY: Cross-check device count against physical expectation (user may know how many disks they have)

### Disk health check
When investigating disk reliability or user reports I/O errors.

1. `tool_info` — check if smartctl/nvme CLI is available
2. IF smartctl available:
     `smart_health` — SMART attributes, error logs, self-test results
   IF smartctl NOT available:
     Suggest: `sudo smartctl -a /dev/sdX` via Bash
3. `get_device_messages` — kernel messages for I/O errors, bad sectors, resets
4. IF errors found:
     Correlate SMART attributes with kernel messages
     Look for Reallocated_Sector_Ct, Current_Pending_Sector, Offline_Uncorrectable
5. VERIFY: No new errors appearing in `get_device_messages` after investigation
6. CROSS-DOMAIN: If I/O errors correlate with performance issues → `linux-performance-rules.md` "I/O bottleneck investigation"

### Mount troubleshooting
When a mount fails or isn't working as expected.

1. `list_mounts` — is it currently mounted? With what options?
2. IF not mounted:
     `identify_device` — does the UUID match fstab?
     IF UUID mismatch: likely fstab entry is stale (device replaced or reformatted)
   IF mounted with wrong options:
     Check fstab vs actual mount options
3. `get_device_messages` — I/O errors or filesystem corruption messages
4. IF systemd mount unit:
     Use systemctl MCP `list_units(type="mount")` and `get_unit_status`
5. VERIFY: `list_mounts` — device mounted at correct path with correct options
6. CROSS-DOMAIN: If mount involves NFS/CIFS → `linux-network-rules.md` "Network connectivity failure"

## Tools

| Goal | Tool |
|------|------|
| What devices exist? | `list_devices` |
| Device UUIDs, labels, types? | `identify_device` |
| Where is everything mounted? | `list_mounts` |
| How full are filesystems? | `check_disk_usage` |
| Low-level device property? | `read_sysfs` |
| Kernel messages for a device? | `get_device_messages` |
| Is the disk healthy? | `smart_health` |
| Systemd mount units? | Use systemctl MCP: `list_units(type="mount")` |

## Query Strategy

1. Start with `list_devices` for the full block device tree, then `check_disk_usage` for filesystem capacity. Drill into specific devices based on findings.
2. Use `columns` parameter in `list_devices` to get exactly the info you need — avoid scanning everything when you know the question.
3. Always specify a device when you know which one you're investigating.
4. Use `read_sysfs` for specific low-level attributes rather than parsing lsblk output.
5. Be suspicious of empty results — cross-check before concluding "nothing found." A missing device may be a naming issue, a different bus, or inside a container.

## Safety

### Privilege

Some storage tools need elevated privileges. Stuart auto-escalates via polkit when configured.

| Command | When | Without polkit |
|---------|------|---------------|
| smartctl, nvme | Always needs root | Suggest `sudo` to user |
| blkid -p | Probe mode only | Normal blkid works unprivileged |
| dmesg | If kernel.dmesg_restrict=1 | Auto-retry with polkit, else suggest `sudo` |
| cryptsetup | LUKS header reads | Suggest `sudo` to user |
| dmidecode | Always needs root | Suggest `sudo` to user |

If a tool reports "Permission denied", the error message includes the exact `sudo` command to run manually.

### High-risk operations

- Formatting/partitioning — always confirm device path with user, provide the exact command, do not execute
- `fsck` on mounted filesystem — causes corruption; always verify unmounted first
- LVM operations (lvextend, vgreduce) — state the change, provide rollback, get confirmation

### Cross-references

- After resolving a disk issue → `linux-systemd-rules.md` "Service failure investigation" (services may have failed due to I/O errors)
- If I/O is slow but hardware healthy → `linux-performance-rules.md` "I/O bottleneck investigation"
- If mount involves NFS/CIFS → `linux-network-rules.md` "Network connectivity failure"
- If disk image for a VM → `linux-virtual-rules.md` "VM won't start" (backing chain, image format)

## Quirks

- **lsblk columns vary by version**: Use `read_manual("lsblk", "COLUMNS")` to see available columns on this system.
- **blkid without sudo may use cached data**: Pass `probe=True` for fresh reads (may need sudo).
- **NVMe vs SATA naming**: NVMe devices appear as `/dev/nvme*`, SATA as `/dev/sd*`. Use `list_devices` to see the full picture.
- **btrfs needs mount points**: Most btrfs commands take a mount point path, not `/dev/...`. Use `list_mounts` to find the mount point first.
- **Device mapper names**: DM devices appear as `/dev/dm-N` in lsblk but are mapped to names under `/dev/mapper/`. Use `identify_device` or `list_devices(filesystem=True)` to see both.

## Domain Deep Knowledge

This domain is compact. Key concepts are covered in the Domain Model and Procedures above.

Additional concepts:
- **LUKS encryption**: Adds a layer between partition and filesystem. `cryptsetup luksDump` shows header info. The underlying device must be unlocked before the filesystem is accessible.
- **LVM snapshots**: CoW snapshots of logical volumes. Can fill up and become invalid if the original LV sees heavy writes. Check with `lvs -a`.
- **RAID levels**: RAID 0 (stripe, no redundancy), RAID 1 (mirror), RAID 5 (single parity), RAID 6 (double parity), RAID 10 (mirror+stripe). mdadm manages software RAID; check with `cat /proc/mdstat`.
- **Filesystem types**: ext4 (default, reliable), xfs (default on RHEL, good for large files), btrfs (CoW, snapshots, subvolumes), zfs (not in mainline kernel).
