# Virtualisation Reference

Extended reference material for KVM/QEMU/libvirt virtualisation. The main skill (SKILL.md) covers session start, common tasks, tool selection, and query strategies. This file contains stack architecture, core concepts, detailed troubleshooting, networking modes, and platform comparisons.

## Virtualisation Stack Mental Model

```
Hardware (VT-x / AMD-V / IOMMU)
  └─ Linux kernel (kvm.ko + kvm_intel.ko or kvm_amd.ko)
       └─ QEMU (device emulation, process per VM)
            └─ libvirt (management daemon: libvirtd / virtqemud)
                 └─ CLI: virsh, virt-install, qemu-img
                 └─ GUI: virt-manager, cockpit-machines, GNOME Boxes
```

- **KVM**: Kernel module — turns Linux into a Type-1 hypervisor using CPU hardware extensions
- **QEMU**: Userspace emulator — handles device emulation, uses KVM for near-native CPU performance
- **libvirt**: Management layer — stable API, XML-based VM definitions, manages networking, storage, secrets
- **virsh**: CLI client for libvirt — the primary tool for VM management
- **Domains**: libvirt's term for virtual machines

## Core Concepts

**Connection URIs**: libvirt uses URIs to identify the hypervisor connection.
- `qemu:///system` — system-level VMs (default, requires `libvirt` group membership)
- `qemu:///session` — user-level VMs (rootless, limited networking)
- Most production and home-lab VMs live on `qemu:///system`

**VM states**: running, paused, shut off, crashed, pmsuspended. Check with `list_vms` or `get_vm_info`.

**Storage pools**: libvirt abstracts storage backends (directory, LVM, NFS, Ceph, iSCSI) as pools containing volumes. Default pool: `/var/lib/libvirt/images/`.

**Virtual networks**: libvirt manages virtual networks with dnsmasq for DHCP/DNS. Default network (`virbr0`): NAT mode.

**Snapshots**:
- **Internal**: Stored within the qcow2 file. Simpler to manage. Slower I/O during snapshot.
- **External**: Separate overlay qcow2 file per snapshot. More flexible, supports live snapshots. Harder to manage — reverting and deleting external snapshots has long-standing libvirt limitations.
- Backing chain depth degrades performance. More than 3-4 levels warrants consolidation (`qemu-img commit`).

**virtio**: Paravirtualised drivers for disk (`virtio-blk`, `virtio-scsi`), network (`virtio-net`), memory (`virtio-balloon`), and more. Dramatically better performance than emulated hardware (e1000, IDE). Windows guests require separate VirtIO driver installation (`virtio-win` package or ISO from Fedora).

**IOMMU / VFIO**: Hardware-assisted device passthrough. IOMMU (VT-d / AMD-Vi) groups PCI devices; all devices in a group must be passed through together. Used for GPU passthrough, high-performance NIC passthrough (SR-IOV), USB controllers.

## Detailed Troubleshooting Workflows

### VM won't start (detailed)

1. `get_vm_info(vm)` — check current state and error
2. `get_vm_xml(vm, xpath=".//os")` — check boot config (BIOS vs UEFI, boot device order)
3. `check_disk_image(path)` — verify disk image exists, is valid, backing files resolve
4. `search_journals(unit="libvirtd", since="5m")` — check libvirt logs via journald MCP
5. `check_virt_host()` — confirm KVM module is loaded

Common causes:
- Missing or corrupt disk image
- Backing file chain broken (base image moved/deleted)
- SELinux label mismatch (check `<seclabel>` in XML)
- Port conflict (SPICE/VNC port already in use)
- Insufficient host memory
- UEFI firmware not installed (`edk2-ovmf` package)

### VM performance issues (detailed)

1. `check_vm_resources(vm)` — CPU/memory allocation and current usage
2. `get_vm_xml(vm, xpath=".//cpu")` — CPU model and topology. `host-passthrough` gives best perf.
3. `get_vm_xml(vm, xpath=".//devices/disk")` — disk bus type. `virtio` is fast; `ide`/`sata` are slow emulation.
4. `get_vm_xml(vm, xpath=".//devices/interface")` — NIC model. `virtio` is fast; `e1000`/`rtl8139` are slow emulation.
5. `get_vm_xml(vm, xpath=".//memoryBacking")` — check for hugepages (better TLB performance)
6. `check_disk_image(path)` — check backing chain depth, allocation percentage

Performance tuning hierarchy:
- **Biggest wins**: virtio drivers for disk and network, host-passthrough CPU model
- **Medium wins**: hugepages, CPU pinning (`vcpupin`), cache mode tuning (cache=none for direct I/O)
- **Smaller wins**: I/O thread pinning, NUMA alignment, KSM tuning

### Snapshot issues

1. `list_snapshots(vm)` — what snapshots exist, tree structure
2. `get_snapshot_info(vm, snapshot)` — type (internal/external), state
3. `check_disk_image(path)` — check backing chain length

If the backing chain is deep (>3 levels): consolidation via `qemu-img commit` reduces the chain. This is a write operation — explain the concept but do not execute.

External snapshot limitation: `virsh snapshot-delete` and `snapshot-revert` have limited support for external snapshots. This is a long-standing libvirt issue. Internal snapshots are simpler to manage.

### Passthrough setup investigation (detailed)

1. `list_iommu_groups()` — identify the device's IOMMU group
2. Check that all devices in the same group can be passed through (or are unused)
3. `check_virt_host()` — verify IOMMU is enabled
4. Check kernel command line for `intel_iommu=on` or `amd_iommu=on`
5. For GPU passthrough: check for a second GPU (host needs its own display)

Common passthrough issues:
- Device shares IOMMU group with others — all must be passed through or unbound
- IOMMU not enabled in BIOS or kernel params
- Driver still bound on host (needs `vfio-pci` binding before VM start)
- NVIDIA "Error 43" in Windows — resolved by Hyper-V vendor ID hiding in modern QEMU/libvirt
- AMD GPU reset bug — GPU becomes unusable after VM shutdown until host reboot

## Networking Modes

- **NAT** (`virbr0`, default): VM gets private IP, host does NAT. VMs can reach outside; outside cannot reach VMs without port forwarding. Suitable for desktop VMs, isolated testing.
- **Bridged**: VM NIC connects to host bridge, gets its own IP on the physical LAN. Required for: servers that need LAN visibility, multi-VM labs that need to see each other on a real subnet.
- **Macvtap**: Direct attachment to physical NIC. Simpler than bridge setup. Caveat: host CANNOT communicate with the VM via this interface.
- **Isolated**: No external connectivity. VMs can only talk to each other and host.
- **User-mode (Slirp/Passt)**: Rootless, no host config needed. Limited: no inbound connections without explicit forwarding. Passt is the modern replacement for Slirp.

Wi-Fi interfaces cannot be bridged directly. Alternatives: macvtap, NAT with port forwarding, or a routed setup.

## Cross-Skill References

| Situation | Consult |
|-----------|---------|
| VM disk image on a block device | linux-block-device skill (`list_devices`, `check_disk_usage`) |
| libvirtd service issues | linux-systemd skill (`get_unit_status("libvirtd.service")`) |
| libvirt daemon logs | linux-systemd/journald (`search_journals(unit="libvirtd")`) |
| USB device passthrough | linux-serial-device skill (`list_usb_devices`, `get_device_properties`) |
| VM networking bridging to physical NIC | linux-network skill (`list_interfaces`, `check_connectivity`) |

## Concepts & Explanations

### VMs vs containers

| Dimension | VM | Container |
|-----------|----|-----------|
| Kernel | Own kernel | Shared with host |
| Boot time | Seconds | Milliseconds |
| Isolation | Hardware-enforced (MMU/IOMMU) | Namespace/cgroup |
| OS flexibility | Any OS | Same kernel family |
| Overhead | Low-moderate (virtio reduces it) | Very low |
| GPU access | VFIO passthrough | Limited (CDI) |
| Use when | Different OS needed, strong isolation, GUI apps, compliance | Same-kernel apps, microservices, CI/CD, density |

Lightweight VMs (Firecracker, Cloud Hypervisor, Kata Containers) blur the line — VM isolation with near-container startup times. These are primarily for cloud/serverless, not typical sysadmin desktop/server use.

### Resource management techniques

- **Memory ballooning** (`virtio-balloon`): Guest surrenders unused pages to host. Disabled when VFIO devices are attached (fixed memory addresses).
- **KSM** (Kernel Same-page Merging): Host merges identical memory pages across VMs. Saves RAM with similar guests. Configurable via `/sys/kernel/mm/ksm/`.
- **Hugepages**: 2MB or 1GB pages reduce TLB misses. Configured via `<memoryBacking><hugepages/>` in VM XML. Requires pre-allocation on host.
- **CPU pinning**: Bind vCPUs to specific host CPUs via `virsh vcpupin`. Critical for NUMA-aware workloads and latency-sensitive VMs. Also pin emulator threads (`virsh emulatorpin`).
- **CPU overcommit**: Allowed by default (scheduler handles it). Use `virsh schedinfo` to tune. Memory overcommit is riskier — can cause host OOM.

### Disk formats

- **qcow2**: QEMU native. Thin provisioning, snapshots, backing files, compression, encryption. Some write overhead vs raw.
- **raw**: No overhead, no features. Best I/O performance. Use on LVM or when snapshots are handled externally.
- **vmdk/vdi**: VMware/VirtualBox formats. QEMU can read/convert them (`qemu-img convert`). Not recommended as native format.

## Inform But Don't Tool

These are relevant but out of scope for MCP tools. Mention when contextually useful:

- **Proxmox VE**: KVM-based platform with web UI, ZFS, clustering. Popular for home labs. Has its own API (not libvirt).
- **oVirt**: Enterprise KVM management. Community-driven after Red Hat stepped back. Upstream of RHV (EOL Aug 2026).
- **virt-manager**: Full-featured GTK desktop GUI for libvirt. Recommend to users for visual VM management.
- **cockpit-machines**: Web UI plugin for libvirt management. Good for headless/remote.
- **GNOME Boxes**: Simple desktop VM manager. Good for quick single-VM use.
- **Vagrant**: Dev VM provisioning with `vagrant-libvirt` provider.
- **cloud-init**: VM instance initialisation (hostname, SSH keys, packages).
- **Terraform/OpenTofu**: Infrastructure as code with `dmacvicar/libvirt` provider.
- **Kata Containers / Firecracker**: Lightweight VM runtimes for container-level isolation.
- **VMware**: Proprietary. Post-Broadcom acquisition, pricing changed dramatically. Many organisations migrating to KVM. `qemu-img convert` handles VMDK migration.
- **VirtualBox**: Oracle. Conflicts with KVM kernel modules — cannot coexist.

## Sudo Considerations

- `virsh` with `qemu:///system`: Requires `libvirt` group membership or sudo
- `qemu-img info` on root-owned images: Needs read permission (libvirt group or sudo)
- `/dev/kvm`: Requires `kvm` group membership
- `dmesg` (if `kernel.dmesg_restrict=1`): Needs sudo
- IOMMU group device listing: Generally accessible without sudo
- `lspci`: Generally accessible without sudo

If permission denied: the tool will say so. The user may need to configure group membership or sudo access per PRIVILEGES.md.

## Known Quirks

- **Session vs system URI**: `virsh` defaults to `qemu:///session` for non-root users and `qemu:///system` for root. Most production VMs are on system. The MCP tools default to `qemu:///system`.
- **External snapshot limitations**: External snapshots cannot be easily reverted or deleted via virsh. This is a well-known, long-standing libvirt limitation. Prefer internal snapshots for basic use.
- **SELinux/sVirt labelling**: On Fedora/RHEL, each VM runs in a unique SELinux domain (`svirt_t`). Disk image label mismatches prevent VM start. Check `get_vm_xml` for `<seclabel>` and compare with file labels (`ls -Z`).
- **qcow2 backing chain**: Deep chains (many snapshots) degrade I/O performance. `check_disk_image` shows the chain. Consolidate with `qemu-img commit` when needed.
- **virsh domain names vs UUIDs**: Names are human-readable but mutable. UUIDs are immutable. Both work as the `vm` parameter in all tools.
- **CPU model compatibility**: `host-passthrough` gives best performance but prevents live migration to dissimilar hosts. `host-model` is a compromise. Visible in `get_vm_xml(xpath=".//cpu")`.
- **Windows guest clock drift**: Without `<timer name='hypervclock'>` in the `<clock>` element, Windows VMs experience time drift. Check with `get_vm_xml(xpath=".//clock")`.
- **VirtIO driver requirement**: Windows guests need the VirtIO driver ISO installed separately. Without it, VMs fall back to slow emulated hardware. Check disk/NIC bus types via `get_vm_info` or `get_vm_xml`.
- **libvirtd vs virtqemud**: Modern libvirt can run as modular daemons (`virtqemud`, `virtnetworkd`, etc.) instead of monolithic `libvirtd`. Both work with the same tools. Check which is running via systemctl MCP.
- **`virsh destroy` is not destructive to data**: Despite the name, `virsh destroy` is equivalent to pulling the power cable — it stops the VM immediately but does not delete it or its data. `virsh undefine` deletes the VM definition.
