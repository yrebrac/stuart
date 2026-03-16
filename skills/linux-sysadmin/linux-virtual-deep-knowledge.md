---
name: linux-virtual-deep-knowledge
description: >
    Extended virtualisation reference. Read on demand for networking modes,
    resource management, disk formats, platform comparisons, and detailed
    concepts. NOT auto-loaded.
---

# Virtualisation: Deep Knowledge

Extended reference for the virtualisation domain. Read when directed by the rules file.

## Contents

- [Networking Modes](#networking-modes)
- [VMs vs Containers](#vms-vs-containers)
- [Resource Management](#resource-management)
- [Disk Formats](#disk-formats)
- [Snapshots In Depth](#snapshots-in-depth)
- [IOMMU and Passthrough](#iommu-and-passthrough)
- [Platform Comparisons](#platform-comparisons)

## Networking Modes

- **NAT** (`virbr0`, default): VM gets private IP, host does NAT. VMs reach outside; outside can't reach VMs without port forwarding. Suitable for desktop VMs, isolated testing.
- **Bridged**: VM NIC connects to host bridge, gets own IP on physical LAN. Required for servers needing LAN visibility, multi-VM labs on real subnets.
- **Macvtap**: Direct attachment to physical NIC. Simpler than bridge. Caveat: host CANNOT communicate with VM via this interface.
- **Isolated**: No external connectivity. VMs only talk to each other and host.
- **User-mode (Slirp/Passt)**: Rootless, no host config. Limited: no inbound without explicit forwarding. Passt is modern replacement for Slirp.

Wi-Fi interfaces cannot be bridged directly. Alternatives: macvtap, NAT with port forwarding, routed setup.

## VMs vs Containers

| Dimension | VM | Container |
|-----------|----|-----------|
| Kernel | Own kernel | Shared with host |
| Boot time | Seconds | Milliseconds |
| Isolation | Hardware-enforced (MMU/IOMMU) | Namespace/cgroup |
| OS flexibility | Any OS | Same kernel family |
| Overhead | Low-moderate (virtio reduces it) | Very low |
| GPU access | VFIO passthrough | Limited (CDI) |
| Use when | Different OS, strong isolation, GUI, compliance | Same-kernel apps, microservices, CI/CD, density |

Lightweight VMs (Firecracker, Cloud Hypervisor, Kata Containers) blur the line — VM isolation with near-container startup. Primarily cloud/serverless, not typical sysadmin use.

## Resource Management

- **Memory ballooning** (`virtio-balloon`): Guest surrenders unused pages. Disabled when VFIO devices attached.
- **KSM** (Kernel Same-page Merging): Merges identical pages across VMs. Saves RAM with similar guests. `/sys/kernel/mm/ksm/`.
- **Hugepages**: 2MB or 1GB pages reduce TLB misses. `<memoryBacking><hugepages/>` in XML. Requires pre-allocation.
- **CPU pinning**: `virsh vcpupin` binds vCPUs to host CPUs. Critical for NUMA-aware and latency-sensitive workloads. Also pin emulator threads (`virsh emulatorpin`).
- **CPU overcommit**: Allowed by default (scheduler handles it). Memory overcommit riskier — can cause host OOM.

## Disk Formats

- **qcow2**: QEMU native. Thin provisioning, snapshots, backing files, compression, encryption. Some write overhead vs raw.
- **raw**: No overhead, no features. Best I/O. Use on LVM or when snapshots handled externally.
- **vmdk/vdi**: VMware/VirtualBox formats. QEMU can read/convert (`qemu-img convert`). Not recommended as native.

## Snapshots In Depth

- **Internal**: Stored within qcow2 file. Simpler to manage. Slower I/O during snapshot.
- **External**: Separate overlay qcow2 per snapshot. More flexible, supports live snapshots. Hard to manage — revert/delete has long-standing libvirt limitations.
- Backing chain depth degrades performance. >3-4 levels warrants consolidation (`qemu-img commit`).
- For external snapshots, consolidation is manual: `qemu-img commit overlay.qcow2` merges changes into the backing file.

## IOMMU and Passthrough

Hardware-assisted device passthrough. IOMMU (VT-d / AMD-Vi) groups PCI devices; all in a group must be passed through together.

Common passthrough issues:
- Device shares IOMMU group — all must be passed through or unbound
- IOMMU not enabled in BIOS or kernel params (`intel_iommu=on` / `amd_iommu=on`)
- Driver still bound on host — needs `vfio-pci` binding before VM start
- NVIDIA "Error 43" in Windows — resolved by Hyper-V vendor ID hiding in modern QEMU
- AMD GPU reset bug — GPU unusable after VM shutdown until host reboot

## Platform Comparisons

Relevant but out of scope for MCP tools. Mention when contextually useful:

- **Proxmox VE**: KVM-based, web UI, ZFS, clustering. Popular for home labs. Has own API (not libvirt).
- **oVirt**: Enterprise KVM management. Community-driven. Upstream of RHV (EOL Aug 2026).
- **virt-manager**: Full-featured GTK GUI for libvirt. Recommend for visual VM management.
- **cockpit-machines**: Web UI plugin for libvirt. Good for headless/remote.
- **GNOME Boxes**: Simple desktop VM manager. Quick single-VM use.
- **Vagrant**: Dev VM provisioning with `vagrant-libvirt` provider.
- **cloud-init**: VM instance initialisation (hostname, SSH keys, packages).
- **Terraform/OpenTofu**: Infrastructure as code with `dmacvicar/libvirt` provider.
- **Kata Containers / Firecracker**: Lightweight VM runtimes for container-level isolation.
- **VMware**: Proprietary. Post-Broadcom, pricing changed. Many migrating to KVM. `qemu-img convert` for VMDK.
- **VirtualBox**: Oracle. Conflicts with KVM kernel modules — cannot coexist.
