---
name: linux-virtual
description: >
    Domain knowledge for KVM/QEMU/libvirt virtualisation using the
    virtual MCP server. Load BEFORE using any virtual MCP tool directly.
    Covers VM inspection, storage pools, virtual networks, snapshots,
    host capabilities, IOMMU/passthrough, and troubleshooting.
---

# Virtualisation (KVM / QEMU / libvirt)

## Session Start

1. Call `tool_info()` to verify virsh and qemu-img are available
2. Call `check_virt_host()` to confirm KVM is loaded and check host capabilities
3. Note libvirt and QEMU versions — feature availability varies significantly

## Common Tasks

### "VM won't start"

1. `get_vm_info(vm)` — check current state and error
2. `check_disk_image(path)` — verify disk image exists, is valid, backing files resolve
3. `search_journals(unit="libvirtd", since="5m")` — check libvirt logs via journald MCP
4. `check_virt_host()` — confirm KVM module is loaded

Common causes: missing/corrupt disk image, broken backing chain, SELinux label mismatch, port conflict (SPICE/VNC), insufficient host memory, UEFI firmware not installed (`edk2-ovmf`).

### "VM is slow"

1. `check_vm_resources(vm)` — CPU/memory allocation and usage
2. `get_vm_xml(vm, xpath=".//devices/disk")` — disk bus type. `virtio` is fast; `ide`/`sata` are emulated.
3. `get_vm_xml(vm, xpath=".//devices/interface")` — NIC model. `virtio` is fast; `e1000`/`rtl8139` are emulated.
4. `get_vm_xml(vm, xpath=".//cpu")` — CPU model. `host-passthrough` gives best performance.
5. `check_disk_image(path)` — backing chain depth degrades I/O

Performance tuning hierarchy: virtio drivers (biggest win) → host-passthrough CPU → hugepages → CPU pinning → cache mode tuning.

### "Set up GPU/device passthrough"

1. `list_iommu_groups()` — identify the device's IOMMU group
2. Check that all devices in the same group can be passed through (or are unused)
3. `check_virt_host()` — verify IOMMU is enabled
4. Check kernel command line for `intel_iommu=on` or `amd_iommu=on`

Common issues: device shares IOMMU group, IOMMU not enabled in BIOS/kernel, driver still bound on host, NVIDIA "Error 43" (Hyper-V vendor ID hiding), AMD GPU reset bug.

### "What VMs do I have?"

1. `list_vms(all=True)` — all VMs with state
2. `get_vm_info(vm)` — overview for a specific VM
3. `list_storage_pools` — where disk images live
4. `list_vm_networks` — virtual network configuration

For detailed virtualisation concepts, networking modes, snapshot management, and platform comparisons, read REFERENCE.md in this skill directory.

## Tool Selection

| Goal | Tool |
|------|------|
| What VMs exist? | `list_vms(all=True)` |
| VM state, CPU, memory, disks, NICs? | `get_vm_info` |
| Raw XML configuration? | `get_vm_xml` |
| Specific config section (CPU, disk, NIC)? | `get_vm_xml(xpath=".//cpu")` |
| CPU/memory usage? | `check_vm_resources` |
| What snapshots exist? | `list_snapshots` |
| Snapshot details? | `get_snapshot_info` |
| Storage pool capacity and volumes? | `list_storage_pools(pool="name")` |
| Disk image format, size, backing chain? | `check_disk_image` |
| Virtual network config (bridge, DHCP)? | `list_vm_networks(network="name")` |
| Is KVM available on this host? | `check_virt_host` |
| IOMMU groups for passthrough? | `list_iommu_groups` |
| Start a VM | `start_vm` |
| Stop a VM | `stop_vm` (graceful) or `stop_vm(force=True)` |
| libvirtd service status? | Use systemctl MCP: `get_unit_status("libvirtd.service")` |
| libvirt daemon logs? | Use journald MCP: `search_journals(unit="libvirtd")` |
| Tool versions and availability? | `tool_info` |

## Query Strategy

### Top-down approach

1. `check_virt_host()` — is this a KVM host? What capabilities?
2. `list_vms(all=True)` — what VMs exist?
3. `get_vm_info(vm)` — overview for a specific VM
4. Drill into specific aspects: snapshots, storage, networking, XML config

### Efficient queries

- Always specify the VM name when investigating a specific VM
- Use `get_vm_xml(xpath="...")` to extract specific config sections rather than parsing full XML
- Common XPath targets: `.//devices/disk`, `.//devices/interface`, `.//cpu`, `.//os`, `.//features`, `.//memoryBacking`, `.//devices/graphics`
- Use `check_disk_image` for disk-level queries rather than parsing VM XML for disk details
- Cross-reference with journald MCP for libvirt daemon logs

### Be suspicious of empty results

If `list_vms` shows nothing, check:
- Is libvirtd running? (`get_unit_status("libvirtd.service")` via systemctl MCP)
- Are you on the right URI? System VMs won't appear in session, and vice versa
- Permissions: is the user in the `libvirt` group?

## Preferences & Safety

- **Read-only by default** — use `get_vm_info`, `get_vm_xml`, `check_*` tools for investigation. `start_vm`/`stop_vm` are the only lifecycle actions.
- **`virsh destroy` is NOT destructive to data** — it's equivalent to pulling the power cable. `virsh undefine` deletes the VM definition.
- **Sudo may be needed** for: `virsh` with `qemu:///system` (requires `libvirt` group), `qemu-img info` on root-owned images, `/dev/kvm` (requires `kvm` group), `dmesg` (if restricted). Stuart auto-escalates via polkit when configured.
- **Snapshot management is complex** — external snapshots cannot be easily reverted/deleted via virsh (long-standing libvirt limitation). Prefer internal snapshots for basic use. Explain consolidation via `qemu-img commit` but do not execute.
- **Windows guests need VirtIO drivers** — without the `virtio-win` ISO, Windows VMs fall back to slow emulated hardware.
