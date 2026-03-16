---
name: linux-virtual
description: >
    Domain knowledge for KVM/QEMU/libvirt virtualisation using the
    virtual MCP server. Load BEFORE using any virtual MCP tool directly.
    Covers VM inspection, storage pools, virtual networks, snapshots,
    host capabilities, IOMMU/passthrough, and troubleshooting.
---

# Virtualisation (KVM / QEMU / libvirt)

## Guide

This file covers KVM/QEMU/libvirt virtualisation administration.

- **Domain Model** — virtualisation stack layers and how they interact
- **Heuristics** — expert shortcuts for VM troubleshooting
- **Anti-patterns** — common mistakes with libvirt/QEMU
- **Procedures** — diagnostic workflows for VM startup, performance, snapshots, passthrough
- **Tools** — goal-to-tool lookup for the virtual MCP server
- **Query Strategy** — top-down approach, efficient XML queries
- **Safety** — privilege, high-risk operations (snapshots, passthrough), cross-domain pointers
- **Quirks** — session vs system URI, external snapshots, SELinux, virsh destroy
- **Domain Deep Knowledge** → `linux-virtual-deep-knowledge.md` for networking modes, concepts, platform comparisons

## Domain Model

```
Hardware (VT-x / AMD-V / IOMMU)
  └─ Linux kernel (kvm.ko + kvm_intel.ko or kvm_amd.ko)
       └─ QEMU (device emulation, process per VM)
            └─ libvirt (management daemon: libvirtd / virtqemud)
                 └─ CLI: virsh, virt-install, qemu-img
```

- **KVM**: Kernel module — turns Linux into a Type-1 hypervisor using CPU hardware extensions
- **QEMU**: Userspace emulator — device emulation, uses KVM for near-native CPU performance
- **libvirt**: Management layer — stable API, XML-based VM definitions, networking, storage, secrets
- **virsh**: CLI client for libvirt — primary tool for VM management
- **Domains**: libvirt's term for virtual machines

Key concepts:
- **Connection URIs**: `qemu:///system` (production VMs, requires `libvirt` group) vs `qemu:///session` (rootless, limited)
- **Storage pools**: Abstracted backends (directory, LVM, NFS, Ceph). Default: `/var/lib/libvirt/images/`
- **Virtual networks**: libvirt manages with dnsmasq for DHCP/DNS. Default: `virbr0` (NAT)
- **virtio**: Paravirtualised drivers for disk, network, memory. Dramatically better than emulated hardware.

## Heuristics

1. "VM won't start" is most often: missing/corrupt disk image, broken backing chain, SELinux label mismatch, or UEFI firmware not installed. Check in that order.
2. VM performance problems: check virtio first. Non-virtio disk (`ide`/`sata`) or NIC (`e1000`/`rtl8139`) is the single biggest performance killer.
3. Performance tuning hierarchy: virtio drivers (biggest win) → host-passthrough CPU → hugepages → CPU pinning → cache mode tuning. Don't tune the small things until the big ones are right.
4. External snapshots are a trap. They can't be easily reverted/deleted via virsh (long-standing libvirt limitation). Internal snapshots are simpler for basic use.
5. Deep qcow2 backing chains (>3-4 levels) degrade I/O. If `check_disk_image` shows a deep chain, suggest consolidation.

## Anti-patterns

- Don't confuse `virsh destroy` with data destruction — it's equivalent to pulling the power cable (stops VM, doesn't delete it). `virsh undefine` deletes the VM definition.
- Don't assume `virsh` defaults to system — for non-root users it defaults to `qemu:///session`. Most VMs are on `qemu:///system`.
- Don't try live migration with `host-passthrough` CPU — it prevents migration to dissimilar hosts.
- Don't create external snapshots for basic use — they're hard to manage. Use internal snapshots.
- Don't recommend Windows VM optimizations without checking VirtIO driver installation — without `virtio-win`, Windows falls back to slow emulated hardware.

## Procedures

### VM won't start
When a VM fails to start or gets stuck in a transitional state.

1. `get_vm_info(vm)` — current state and error
2. `check_disk_image(path)` — disk image exists, valid, backing files resolve
3. IF image missing or corrupt:
     Check storage pool: `list_storage_pools`
   IF backing chain broken:
     `check_disk_image` shows where the chain breaks — base image moved/deleted
4. `search_journals(unit="libvirtd", since="5m")` via journald MCP — libvirt logs
5. `check_virt_host()` — KVM module loaded?
6. IF UEFI boot: check `edk2-ovmf` package installed
7. IF SELinux: check `get_vm_xml(xpath=".//seclabel")` — label mismatch?
8. VERIFY: `get_vm_info` shows running state
9. CROSS-DOMAIN: If disk image on failing storage → `linux-block-device-rules.md`

### VM performance investigation
When a VM is slow or resource-constrained.

1. `check_vm_resources(vm)` — CPU/memory allocation and usage
2. `get_vm_xml(vm, xpath=".//devices/disk")` — disk bus type
3. IF not virtio disk:
     This is likely the biggest performance issue — suggest virtio migration
4. `get_vm_xml(vm, xpath=".//devices/interface")` — NIC model
5. IF not virtio NIC:
     Suggest virtio-net migration
6. `get_vm_xml(vm, xpath=".//cpu")` — CPU model
7. IF not host-passthrough:
     Suggest change (unless live migration is needed)
8. `check_disk_image(path)` — backing chain depth?
9. VERIFY: Key performance indicators (virtio, host-passthrough) are in place
10. CROSS-DOMAIN: If host system is also slow → `linux-performance-rules.md`

### Snapshot management
When investigating or managing VM snapshots.

1. `list_snapshots(vm)` — what exists, tree structure
2. `get_snapshot_info(vm, snapshot)` — type (internal/external), state
3. `check_disk_image(path)` — backing chain length
4. IF chain deep (>3 levels):
     Suggest consolidation via `qemu-img commit` — explain but don't execute
5. IF external snapshot issues:
     Explain libvirt's limited support for external snapshot revert/delete
     Suggest internal snapshots for future use
6. VERIFY: Snapshot tree is manageable, chain depth is reasonable

### Passthrough setup investigation
When setting up GPU or device passthrough.

1. `list_iommu_groups()` — identify device's IOMMU group
2. Check all devices in same group can be passed through (or are unused)
3. `check_virt_host()` — IOMMU enabled?
4. Check kernel command line for `intel_iommu=on` or `amd_iommu=on`
5. IF GPU passthrough:
     Verify host has a second GPU for its own display
     IF NVIDIA: may need Hyper-V vendor ID hiding (modern QEMU handles this)
     IF AMD: warn about GPU reset bug (unusable after VM shutdown until host reboot)
6. VERIFY: IOMMU groups are clean, device can be isolated

### VM inventory survey
When the user wants an overview of their virtualisation setup.

1. `list_vms(all=True)` — all VMs with state
2. `list_storage_pools` — where disk images live
3. `list_vm_networks` — virtual network configuration
4. `check_virt_host()` — host capabilities
5. VERIFY: Inventory matches user's expectations

## Tools

| Goal | Tool |
|------|------|
| What VMs exist? | `list_vms(all=True)` |
| VM state, CPU, memory, disks, NICs? | `get_vm_info` |
| Raw XML configuration? | `get_vm_xml` |
| Specific config section? | `get_vm_xml(xpath=".//cpu")` |
| CPU/memory usage? | `check_vm_resources` |
| What snapshots exist? | `list_snapshots` |
| Snapshot details? | `get_snapshot_info` |
| Storage pool capacity? | `list_storage_pools(pool="name")` |
| Disk image format, backing chain? | `check_disk_image` |
| Virtual network config? | `list_vm_networks(network="name")` |
| Is KVM available? | `check_virt_host` |
| IOMMU groups? | `list_iommu_groups` |
| Start a VM | `start_vm` |
| Stop a VM | `stop_vm` (graceful) or `stop_vm(force=True)` |
| libvirtd status? | Use systemctl MCP: `get_unit_status("libvirtd.service")` |
| libvirt logs? | Use journald MCP: `search_journals(unit="libvirtd")` |
| Tool versions? | `tool_info` |

## Query Strategy

1. Start with `check_virt_host()` for host capabilities, then `list_vms(all=True)` for inventory, then drill into specific VMs.
2. Use `get_vm_xml(xpath="...")` to extract specific config sections — don't parse full XML. Common targets: `.//devices/disk`, `.//devices/interface`, `.//cpu`, `.//os`, `.//memoryBacking`, `.//devices/graphics`.
3. Use `check_disk_image` for disk queries rather than parsing VM XML.
4. Cross-reference with journald MCP for libvirt daemon logs.
5. Be suspicious of empty results — if `list_vms` shows nothing, check: is libvirtd running? Right URI? User in `libvirt` group?

## Safety

### Privilege

| Resource | Requirement |
|----------|------------|
| `virsh` with `qemu:///system` | `libvirt` group or sudo |
| `qemu-img info` on root-owned images | Read permission (libvirt group or sudo) |
| `/dev/kvm` | `kvm` group |
| IOMMU group listing | Generally no sudo needed |

### High-risk operations

- **`virsh destroy`**: Safe for data (just stops VM) but the name is misleading — always explain.
- **`virsh undefine`**: Actually deletes the VM definition. Confirm before suggesting.
- **Snapshot consolidation** (`qemu-img commit`): Write operation that modifies disk images. Explain but don't execute.
- **Passthrough device binding** (vfio-pci): Unbinds host driver. Can affect host display if GPU.

### Cross-references

- VM disk image on block device → `linux-block-device-rules.md` "Device identification"
- libvirtd service issues → `linux-systemd-rules.md` "Service failure investigation"
- USB device passthrough → `linux-serial-device-rules.md` for device identification
- VM networking to physical NIC → `linux-network-rules.md` for bridge/connectivity issues
- VM consuming host resources → `linux-performance-rules.md`

## Quirks

- **Session vs system URI**: `virsh` defaults to `qemu:///session` for non-root, `qemu:///system` for root. MCP tools default to `qemu:///system`.
- **External snapshot limitations**: Can't easily revert/delete via virsh. Long-standing libvirt limitation.
- **SELinux/sVirt labelling**: On Fedora/RHEL, each VM runs in unique SELinux domain (`svirt_t`). Label mismatches prevent start.
- **qcow2 backing chain**: Deep chains degrade I/O. `check_disk_image` shows chain depth.
- **Domain names vs UUIDs**: Names are mutable, UUIDs are immutable. Both work as the `vm` parameter.
- **CPU model compatibility**: `host-passthrough` = best performance but prevents live migration.
- **Windows guest clock drift**: Needs `<timer name='hypervclock'>` in `<clock>` element.
- **VirtIO driver requirement**: Windows guests need separate `virtio-win` ISO.
- **libvirtd vs virtqemud**: Modern libvirt can run as modular daemons. Both work with same tools.
- **`virsh destroy` naming**: Not destructive to data — equivalent to pulling the power cable.

## Domain Deep Knowledge → linux-virtual-deep-knowledge.md

Read when:
- User asks about networking modes (NAT, bridged, macvtap, isolated)
- Need VM vs container comparison
- Resource management details (ballooning, KSM, hugepages, CPU pinning)
- Disk format comparison (qcow2 vs raw vs vmdk)
- Platform comparisons (Proxmox, oVirt, virt-manager, Vagrant)
- Passthrough deep-dive beyond the procedure
