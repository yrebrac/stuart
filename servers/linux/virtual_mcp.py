#!/usr/bin/env python3
"""
Stuart — Virtual Machine MCP Server

Exposes KVM/QEMU/libvirt virtualisation inspection and basic lifecycle
operations as MCP tools for Claude Code. Wraps virsh, qemu-img, and
host capability checks via sysfs/procfs.

Usage:
    python3 virtual_mcp.py

Tested on:
    - Fedora 43, libvirt 11.3.0, QEMU 10.0.0, Python 3.14

Underlying tools:
    Core: virsh (libvirt-client), qemu-img (qemu-img)
    Optional: virt-install (virt-install)
    System checks: /dev/kvm, /proc/cpuinfo, /sys/module/kvm*,
                   /sys/kernel/iommu_groups/

Argument tier decisions (see docs/TOOL_CONVENTION.md):
    Tier 1 (exposed as params):
        virsh: domain name, --all, state filter, pool name, network name
        qemu-img: image path
    Tier 2 (param or separate tool):
        virsh dumpxml with XPath extraction (via Python xml.etree)
        virsh shutdown vs virsh destroy (force param)
    Tier 3 (handled internally):
        --connect URI, output formatting
    Tier 4 (omitted):
        virsh create, virsh undefine, virsh define, virsh edit,
        virsh migrate, virsh managedsave,
        virsh snapshot-create, virsh snapshot-delete, virsh snapshot-revert,
        virsh pool-create, virsh pool-delete,
        virsh net-create, virsh net-destroy, virsh net-undefine,
        virsh vol-create, virsh vol-delete,
        virsh attach-*, virsh detach-*,
        qemu-img create, qemu-img convert, qemu-img resize,
        qemu-img snapshot (create/delete/apply),
        virt-install (provisioning)
"""

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_check import ToolCache

server = FastMCP(
    name="sysops-virtual",
    instructions=(
        "Inspect and manage KVM/QEMU/libvirt virtual machines, storage "
        "pools, virtual networks, snapshots, and host virtualisation "
        "capabilities. Most tools are read-only. Basic VM lifecycle "
        "operations (start, stop) are available."
    ),
)

# ── ToolCache instances ────────────────────────────────────────────
# Core — expected on any KVM host
_tools: dict[str, ToolCache] = {
    "virsh": ToolCache("virsh", "/usr/bin/virsh", ["--version"], ["--help"]),
    "qemu-img": ToolCache("qemu-img", "/usr/bin/qemu-img", ["--version"], ["--help"]),
}

# Optional tools (may not be installed)
_OPTIONAL_TOOLS = {
    "virt-install": ("/usr/bin/virt-install", ["--version"], ["--help"]),
}

for _name, (_path, _vargs, _hargs) in _OPTIONAL_TOOLS.items():
    _tools[_name] = ToolCache(_name, _path, _vargs, _hargs)

_PACKAGE_HINTS = {
    "virsh": "libvirt-client",
    "qemu-img": "qemu-img",
    "virt-install": "virt-install",
}

# Default libvirt connection URI
_DEFAULT_URI = "qemu:///system"

# Valid name pattern for VM domains, pools, networks
_NAME_RE = re.compile(r"^[a-zA-Z0-9._:/-]+$")


# ── Shared runners ────────────────────────────────────────────────

def _run_cmd(
    tool_key: str,
    args: list[str],
    max_lines: int = 200,
    timeout: int = 30,
) -> str:
    """Run a command via ToolCache. Returns stdout or error message."""
    info = _tools[tool_key].info()
    if not info.get("exists"):
        pkg = _PACKAGE_HINTS.get(tool_key, "")
        hint = f" Install with: sudo dnf install {pkg}" if pkg else ""
        return f"Error: {tool_key} is not installed.{hint}"

    cmd = [info["path"]] + args

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"Error: {tool_key} timed out after {timeout} seconds."

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "Permission denied" in stderr or "Operation not permitted" in stderr:
            return (
                f"Permission denied running {tool_key}. "
                f"This command may require membership in the 'libvirt' group "
                f"or sudo privileges.\n\nstderr: {stderr}"
            )
        # Failed to connect to libvirt — common setup issue
        if "Failed to connect" in stderr or "failed to connect" in stderr:
            return (
                f"Cannot connect to libvirt. Is libvirtd running?\n"
                f"Try: sudo systemctl start libvirtd\n\n"
                f"stderr: {stderr}"
            )
        # Non-zero but may have useful stdout
        if result.stdout.strip():
            output = result.stdout.strip()
            if stderr:
                output += f"\n\n[stderr]: {stderr}"
        else:
            return f"Error from {tool_key}: {stderr or '(no output)'}"
    else:
        output = result.stdout or result.stderr or "(no output)"

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return output.strip()


def _run_virsh(
    args: list[str],
    uri: str = "",
    max_lines: int = 200,
    timeout: int = 30,
) -> str:
    """Run a virsh command with connection URI."""
    connect_uri = uri or _DEFAULT_URI
    full_args = ["-c", connect_uri] + args
    return _run_cmd("virsh", full_args, max_lines=max_lines, timeout=timeout)


def _validate_name(name: str, kind: str = "name") -> str | None:
    """Validate a VM/pool/network name. Returns error string or None."""
    if not name:
        return f"Error: {kind} is required."
    if not _NAME_RE.match(name):
        return f"Invalid {kind}: '{name}'"
    return None


# ── Standard tools ─────────────────────────────────────────────────

@server.tool()
def tool_info() -> str:
    """Return version and availability for virtualisation commands.

    Call this at the start of a session to see which tools are
    installed. virsh and qemu-img are expected on any KVM host.
    """
    result = {}
    for name, cache in sorted(_tools.items()):
        info = cache.info()
        result[name] = {
            "exists": info.get("exists", False),
            "path": info.get("path"),
            "version": info.get("version_raw"),
        }
    return json.dumps(result, indent=2)


@server.tool()
def read_manual(
    tool: str,
    section: str = "",
) -> str:
    """Read the man page for a virtualisation command.

    Args:
        tool: Command name, e.g. "virsh", "qemu-img", "virt-install".
        section: Section to extract, e.g. "OPTIONS", "DESCRIPTION".
                 Leave empty for full page (truncated).
    """
    if tool not in _tools:
        return f"Unknown tool '{tool}'. Available: {', '.join(sorted(_tools.keys()))}"
    return _tools[tool].read_man(section=section)


# ── Host Inspection ────────────────────────────────────────────────

@server.tool()
def check_virt_host() -> str:
    """Check host virtualisation capabilities.

    Checks KVM module, CPU flags, IOMMU status, and libvirt/QEMU
    versions. Call at session start to understand the host.
    """
    results: list[str] = []

    # /dev/kvm
    kvm_dev = Path("/dev/kvm")
    if kvm_dev.exists():
        results.append("KVM device: /dev/kvm exists")
        # Check permissions
        if os.access(str(kvm_dev), os.R_OK | os.W_OK):
            results.append("KVM access: read/write OK")
        else:
            results.append(
                "KVM access: DENIED — add user to 'kvm' group"
            )
    else:
        results.append("KVM device: /dev/kvm NOT FOUND — KVM may not be available")

    # CPU virtualisation flags
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        if "vmx" in cpuinfo:
            results.append("CPU flags: vmx (Intel VT-x)")
        elif "svm" in cpuinfo:
            results.append("CPU flags: svm (AMD-V)")
        else:
            results.append(
                "CPU flags: no vmx/svm found — hardware virtualisation "
                "may not be supported or enabled in BIOS"
            )
    except OSError:
        results.append("CPU flags: could not read /proc/cpuinfo")

    # KVM kernel modules
    kvm_base = Path("/sys/module/kvm")
    if kvm_base.exists():
        results.append("KVM module: loaded")
        # Check for intel or amd specific module
        for variant in ["kvm_intel", "kvm_amd"]:
            mod_path = Path(f"/sys/module/{variant}")
            if mod_path.exists():
                # Check nested virt support
                nested = mod_path / "parameters" / "nested"
                if nested.exists():
                    try:
                        val = nested.read_text().strip()
                        results.append(f"  {variant}: loaded (nested={val})")
                    except OSError:
                        results.append(f"  {variant}: loaded")
                else:
                    results.append(f"  {variant}: loaded")
    else:
        results.append("KVM module: NOT loaded")

    # IOMMU
    iommu_dir = Path("/sys/kernel/iommu_groups")
    if iommu_dir.exists():
        try:
            groups = list(iommu_dir.iterdir())
            results.append(f"IOMMU: {len(groups)} groups found")
        except OSError:
            results.append("IOMMU: directory exists but could not read")
    else:
        results.append(
            "IOMMU: not available (check BIOS for VT-d/AMD-Vi, "
            "kernel param intel_iommu=on or amd_iommu=on)"
        )

    # libvirt / QEMU versions via virsh
    virsh_info = _tools["virsh"].info()
    if virsh_info.get("exists"):
        version_output = _run_virsh(["version", "--daemon"], timeout=10)
        if not version_output.startswith("Error"):
            results.append(f"\n{version_output}")
        else:
            # Try without --daemon in case libvirtd is not running
            results.append(f"virsh: installed ({virsh_info.get('version_raw', 'unknown version')})")
            results.append(f"libvirtd: may not be running ({version_output})")
    else:
        results.append("virsh: NOT installed")

    qemu_info = _tools["qemu-img"].info()
    if qemu_info.get("exists"):
        results.append(f"qemu-img: {qemu_info.get('version_raw', 'installed')}")
    else:
        results.append("qemu-img: NOT installed")

    return "\n".join(results)


@server.tool()
def list_iommu_groups(
    max_lines: int = 200,
) -> str:
    """List IOMMU groups and their devices for passthrough planning.

    Each IOMMU group contains PCI devices that must be passed through
    together. Requires IOMMU enabled in BIOS and kernel.

    Args:
        max_lines: Maximum lines to return.
    """
    iommu_dir = Path("/sys/kernel/iommu_groups")
    if not iommu_dir.exists():
        return (
            "IOMMU groups not available.\n"
            "Enable in BIOS (VT-d or AMD-Vi) and add kernel parameter: "
            "intel_iommu=on or amd_iommu=on"
        )

    lines: list[str] = []

    try:
        groups = sorted(iommu_dir.iterdir(), key=lambda p: int(p.name))
    except (OSError, ValueError):
        return "Error reading /sys/kernel/iommu_groups/"

    for group in groups:
        devices_dir = group / "devices"
        if not devices_dir.exists():
            continue

        try:
            devices = sorted(devices_dir.iterdir())
        except OSError:
            continue

        for device in devices:
            # Get PCI device description via lspci-style info
            dev_name = device.name  # e.g. 0000:01:00.0
            desc = ""

            # Try to read device class and label from sysfs
            try:
                vendor_path = device / "vendor"
                device_path = device / "device"
                class_path = device / "class"

                if class_path.exists():
                    class_code = class_path.read_text().strip()
                else:
                    class_code = ""

                # Try lspci for human-readable description
                lspci = subprocess.run(
                    ["lspci", "-s", dev_name, "-nn"],
                    capture_output=True, text=True, timeout=5,
                )
                if lspci.returncode == 0 and lspci.stdout.strip():
                    desc = lspci.stdout.strip()
                else:
                    desc = dev_name
            except (OSError, subprocess.TimeoutExpired):
                desc = dev_name

            lines.append(f"Group {group.name:>3}: {desc}")

    if not lines:
        return "No devices found in IOMMU groups."

    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} entries.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return "\n".join(lines)


# ── VM Inspection ──────────────────────────────────────────────────

@server.tool()
def list_vms(
    all: bool = True,
    state: str = "",
    max_lines: int = 100,
) -> str:
    """List virtual machines (libvirt domains).

    Args:
        all: Include shut-off and inactive VMs (default True).
        state: Filter by state: "running", "paused", "shutoff",
               "crashed", "other". Leave empty for all states.
        max_lines: Maximum lines to return.
    """
    args = ["list"]
    if all:
        args.append("--all")
    if state:
        args.append(f"--state-{state}")
    return _run_virsh(args, max_lines=max_lines)


@server.tool()
def get_vm_info(
    vm: str,
) -> str:
    """Get combined detail for a VM: state, resources, disks, and NICs.

    Runs dominfo, domblklist, and domiflist for a comprehensive
    overview in one call.

    Args:
        vm: VM (domain) name or UUID.
    """
    err = _validate_name(vm, "VM name")
    if err:
        return err

    sections: list[str] = []

    # dominfo — state, UUID, CPU, memory
    info = _run_virsh(["dominfo", vm], timeout=15)
    sections.append(f"=== Domain Info ===\n{info}")

    # domblklist — attached disks
    blklist = _run_virsh(["domblklist", vm, "--details"], timeout=15)
    sections.append(f"\n=== Block Devices ===\n{blklist}")

    # domiflist — network interfaces
    iflist = _run_virsh(["domiflist", vm], timeout=15)
    sections.append(f"\n=== Network Interfaces ===\n{iflist}")

    return "\n".join(sections)


@server.tool()
def get_vm_xml(
    vm: str,
    xpath: str = "",
    max_lines: int = 200,
) -> str:
    """Get the libvirt XML definition for a VM, optionally extracting
    specific elements via XPath.

    Args:
        vm: VM (domain) name or UUID.
        xpath: XPath expression to extract, e.g. ".//devices/disk",
               ".//cpu", ".//os", ".//devices/interface",
               ".//memoryBacking", ".//features".
               Leave empty for full XML.
        max_lines: Maximum lines to return.
    """
    err = _validate_name(vm, "VM name")
    if err:
        return err

    raw = _run_virsh(["dumpxml", vm], max_lines=5000, timeout=15)
    if raw.startswith("Error"):
        return raw

    if not xpath:
        # Return full XML, truncated
        lines = raw.split("\n")
        if len(lines) > max_lines:
            return (
                f"[Showing first {max_lines} of {len(lines)} lines. "
                f"Use xpath to extract specific sections.]\n\n"
                + "\n".join(lines[:max_lines])
            )
        return raw

    # Parse and extract via XPath
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return f"XML parse error: {e}\n\nRaw output:\n{raw[:500]}"

    matches = root.findall(xpath)
    if not matches:
        return f"No elements matched xpath '{xpath}'."

    parts: list[str] = []
    for elem in matches:
        parts.append(ET.tostring(elem, encoding="unicode"))

    output = "\n".join(parts)
    lines = output.split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return output


@server.tool()
def check_vm_resources(
    vm: str,
) -> str:
    """Check CPU and memory allocation and usage for a VM.

    Args:
        vm: VM (domain) name or UUID.
    """
    err = _validate_name(vm, "VM name")
    if err:
        return err

    sections: list[str] = []

    # vCPU info
    vcpu = _run_virsh(["vcpuinfo", vm], timeout=15)
    sections.append(f"=== vCPU Info ===\n{vcpu}")

    # Memory stats (only works on running VMs with balloon driver)
    memstat = _run_virsh(["dommemstat", vm], timeout=15)
    sections.append(f"\n=== Memory Stats ===\n{memstat}")

    # Domain stats (comprehensive)
    domstats = _run_virsh(["domstats", vm], timeout=15)
    sections.append(f"\n=== Domain Stats ===\n{domstats}")

    return "\n".join(sections)


# ── Snapshots ──────────────────────────────────────────────────────

@server.tool()
def list_snapshots(
    vm: str,
    max_lines: int = 100,
) -> str:
    """List snapshots for a VM.

    Args:
        vm: VM (domain) name or UUID.
        max_lines: Maximum lines to return.
    """
    err = _validate_name(vm, "VM name")
    if err:
        return err

    return _run_virsh(
        ["snapshot-list", vm, "--tree"],
        max_lines=max_lines,
    )


@server.tool()
def get_snapshot_info(
    vm: str,
    snapshot: str,
) -> str:
    """Get details for a specific VM snapshot.

    Args:
        vm: VM (domain) name or UUID.
        snapshot: Snapshot name.
    """
    err = _validate_name(vm, "VM name")
    if err:
        return err
    err = _validate_name(snapshot, "snapshot name")
    if err:
        return err

    sections: list[str] = []

    # Snapshot info
    info = _run_virsh(
        ["snapshot-info", vm, "--snapshotname", snapshot],
        timeout=15,
    )
    sections.append(f"=== Snapshot Info ===\n{info}")

    # Snapshot XML (truncated)
    xml_out = _run_virsh(
        ["snapshot-dumpxml", vm, snapshot],
        max_lines=100,
        timeout=15,
    )
    sections.append(f"\n=== Snapshot XML ===\n{xml_out}")

    return "\n".join(sections)


# ── Storage ────────────────────────────────────────────────────────

@server.tool()
def list_storage_pools(
    pool: str = "",
    max_lines: int = 100,
) -> str:
    """List libvirt storage pools, or get detail for a named pool.

    When pool is specified, returns pool info and its volumes.
    When pool is empty, lists all pools with status.

    Args:
        pool: Pool name for detailed info. Leave empty to list all.
        max_lines: Maximum lines to return.
    """
    if not pool:
        return _run_virsh(
            ["pool-list", "--all", "--details"],
            max_lines=max_lines,
        )

    err = _validate_name(pool, "pool name")
    if err:
        return err

    sections: list[str] = []

    info = _run_virsh(["pool-info", pool], timeout=15)
    sections.append(f"=== Pool Info ===\n{info}")

    vols = _run_virsh(
        ["vol-list", pool, "--details"],
        max_lines=max_lines,
        timeout=15,
    )
    sections.append(f"\n=== Volumes ===\n{vols}")

    return "\n".join(sections)


@server.tool()
def check_disk_image(
    path: str,
) -> str:
    """Inspect a disk image: format, sizes, backing chain.

    Shows virtual size, actual disk usage, format (qcow2/raw/etc.),
    and backing file chain for thin-provisioned images.

    Args:
        path: Path to disk image, e.g.
              "/var/lib/libvirt/images/myvm.qcow2".
    """
    if not path:
        return "Error: path is required."

    # Basic path validation — must be absolute
    if not path.startswith("/"):
        return "Error: path must be absolute (start with /)."

    # Get image info as JSON for structured parsing
    raw = _run_cmd(
        "qemu-img",
        ["info", "--output=json", path],
        max_lines=500,
        timeout=30,
    )
    if raw.startswith("Error"):
        return raw

    # Try to parse JSON for a cleaner presentation
    try:
        data = json.loads(raw)
        lines: list[str] = []
        lines.append(f"File: {data.get('filename', path)}")
        lines.append(f"Format: {data.get('format', 'unknown')}")

        virtual = data.get("virtual-size", 0)
        actual = data.get("actual-size", 0)
        lines.append(f"Virtual size: {_human_size(virtual)}")
        lines.append(f"Actual size: {_human_size(actual)}")

        if virtual > 0 and actual > 0:
            pct = (actual / virtual) * 100
            lines.append(f"Allocation: {pct:.1f}%")

        if data.get("backing-filename"):
            lines.append(f"Backing file: {data['backing-filename']}")
        if data.get("backing-filename-format"):
            lines.append(f"Backing format: {data['backing-filename-format']}")

        # Format-specific info
        fmt_specific = data.get("format-specific", {})
        if fmt_specific.get("type") == "qcow2":
            qcow2_data = fmt_specific.get("data", {})
            if "compat" in qcow2_data:
                lines.append(f"qcow2 compat: {qcow2_data['compat']}")
            if qcow2_data.get("lazy-refcounts"):
                lines.append("Lazy refcounts: enabled")
            if qcow2_data.get("extended-l2"):
                lines.append("Extended L2: enabled")
            encrypt = qcow2_data.get("encrypt", {})
            if encrypt:
                lines.append(f"Encryption: {encrypt.get('format', 'yes')}")

        return "\n".join(lines)

    except (json.JSONDecodeError, KeyError):
        # Fall back to raw output
        return raw


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable size string."""
    if size_bytes == 0:
        return "0 B"
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PiB"


# ── Networking ─────────────────────────────────────────────────────

@server.tool()
def list_vm_networks(
    network: str = "",
    max_lines: int = 100,
) -> str:
    """List libvirt virtual networks, or get detail for a named network.

    When network is specified, returns network info and XML config
    (shows bridge name, DHCP range, NAT settings).
    When network is empty, lists all networks with status.

    Args:
        network: Network name for detailed info. Leave empty to list all.
        max_lines: Maximum lines to return.
    """
    if not network:
        return _run_virsh(
            ["net-list", "--all"],
            max_lines=max_lines,
        )

    err = _validate_name(network, "network name")
    if err:
        return err

    sections: list[str] = []

    info = _run_virsh(["net-info", network], timeout=15)
    sections.append(f"=== Network Info ===\n{info}")

    # XML shows bridge, DHCP, NAT, forwarding config
    xml_out = _run_virsh(
        ["net-dumpxml", network],
        max_lines=max_lines,
        timeout=15,
    )
    sections.append(f"\n=== Network XML ===\n{xml_out}")

    return "\n".join(sections)


# ── Lifecycle ──────────────────────────────────────────────────────

@server.tool()
def start_vm(
    vm: str,
) -> str:
    """Start a shut-off virtual machine.

    Args:
        vm: VM (domain) name or UUID.
    """
    err = _validate_name(vm, "VM name")
    if err:
        return err

    return _run_virsh(["start", vm], timeout=30)


@server.tool()
def stop_vm(
    vm: str,
    force: bool = False,
) -> str:
    """Stop a running virtual machine.

    By default sends an ACPI shutdown signal (graceful). The guest
    OS must cooperate. Use force=True for immediate power-off.

    Args:
        vm: VM (domain) name or UUID.
        force: If True, force immediate power-off (virsh destroy).
               If False (default), graceful ACPI shutdown.
    """
    err = _validate_name(vm, "VM name")
    if err:
        return err

    if force:
        return _run_virsh(["destroy", vm], timeout=15)
    else:
        return _run_virsh(["shutdown", vm], timeout=15)


if __name__ == "__main__":
    server.run(transport="stdio")
