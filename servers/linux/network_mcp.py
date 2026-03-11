#!/usr/bin/env python3
"""
Stuart — Network MCP Server

Exposes network interface, routing, DNS, socket, firewall, WiFi, and
connectivity inspection as MCP tools for Claude Code. Wraps multiple
underlying Linux commands. All tools are read-only.

Usage:
    python3 network_mcp.py

Tested on:
    - Fedora 43, iproute2 6.14.0, Python 3.14

Underlying tools (phased):
    Phase 1: ip, ss, dig, resolvectl, ping
    Phase 2: firewall-cmd, nft
    Phase 3: nmcli, iw, ethtool
    Phase 4: tracepath, traceroute, nmap

Argument tier decisions (see docs/TOOL_CONVENTION.md):
    Tier 1 (exposed as params):
        ip: object (addr/link/route/neigh), device, -4/-6
        ss: -t/-u, -l, -n, state filter, port filter
        dig: domain, record type, @server
        resolvectl: (none — status only)
        ping: host, -c count, -w deadline
        firewall-cmd: --zone, --list-all, --get-active-zones
        nft: list ruleset, list tables
        nmcli: device/connection, specific device, -t (terse)
        iw: dev <iface> info/link/scan
        ethtool: interface
        tracepath: host, -m max_hops
        traceroute: host, -m max_hops
    Tier 2 (param or separate tool):
        ip -j (json) — handled internally
        ss -p (process info) — exposed as param, may need sudo
        nmcli wifi list — separate tool (list_wifi_networks)
    Tier 3 (handled internally):
        -color=never, --no-pager, --no-legend, +noall +answer (dig)
    Tier 4 (omitted):
        Any write operations:
            ip addr add/del, ip route add/del, ip link set
            nmcli con up/down/add/delete/modify
            firewall-cmd --add-*/--remove-*/--set-*
            nft add/delete/flush
        Streaming: ip monitor, ss --events
        Dangerous: nmap aggressive scans, ping flood

Network-security split point:
    Firewall tools (list_firewall_rules, check_firewall_zones) live here
    for now. If a dedicated security specialist is created, extract these
    into a separate firewall_mcp.py server.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from privilege import PrivilegeHelper
from tool_check import ToolCache

server = FastMCP(
    name="sysops-network",
    instructions=(
        "Inspect network interfaces, routing, DNS, sockets, firewall rules, "
        "WiFi, and connectivity. All tools are read-only."
    ),
)

# ── ToolCache instances ────────────────────────────────────────────

# Phase 1 — always available on Linux
_tools: dict[str, ToolCache] = {
    "ip": ToolCache("ip", "/usr/bin/ip", ["-V"], ["help"]),
    "ss": ToolCache("ss", "/usr/bin/ss", ["--version"], ["--help"]),
    "dig": ToolCache("dig", "/usr/bin/dig", ["-v"], ["-h"]),
    "resolvectl": ToolCache("resolvectl", "/usr/bin/resolvectl",
                            ["--version"], ["--help"]),
    "ping": ToolCache("ping", "/usr/bin/ping", ["-V"], ["-h"]),
}

# Phase 2+: optional tools (may not be installed/active)
_OPTIONAL_TOOLS = {
    # Phase 2 — Firewall
    "firewall-cmd": ("/usr/bin/firewall-cmd", ["--version"], ["--help"]),
    "nft": ("/usr/bin/nft", ["--version"], ["--help"]),
    # Phase 3 — NetworkManager, WiFi, NIC details
    "nmcli": ("/usr/bin/nmcli", ["--version"], ["--help"]),
    "iw": ("/usr/bin/iw", ["--version"], ["help"]),
    "ethtool": ("/usr/bin/ethtool", ["--version"], ["--help"]),
    # Phase 4 — Path tracing
    "tracepath": ("/usr/bin/tracepath", ["-V"], ["--help"]),
    "traceroute": ("/usr/bin/traceroute", ["--version"], ["--help"]),
    "nmap": ("/usr/bin/nmap", ["--version"], ["--help"]),
    # Reachability diagnostic (HTTPS checks)
    "curl": ("/usr/bin/curl", ["--version"], ["--help"]),
}

for _name, (_path, _vargs, _hargs) in _OPTIONAL_TOOLS.items():
    _tools[_name] = ToolCache(_name, _path, _vargs, _hargs)

_PACKAGE_HINTS = {
    "dig": "bind-utils",
    "nft": "nftables",
    "firewall-cmd": "firewalld",
    "nmcli": "NetworkManager",
    "iw": "iw",
    "ethtool": "ethtool",
    "traceroute": "traceroute",
    "nmap": "nmap",
    "curl": "curl",
}

_priv = PrivilegeHelper()


# ── Shared runner ──────────────────────────────────────────────────

def _run_cmd(
    tool_key: str,
    args: list[str],
    max_lines: int = 200,
    timeout: int = 30,
    privilege: str = "never",
    helper_command_id: str | None = None,
    helper_device: str | None = None,
) -> str:
    """Run a network command. Returns stdout or error message.

    Args:
        privilege: "never", "auto", or "always". See privilege.py.
        helper_command_id: Route escalation through privilege helper.
        helper_device: Device path for helper commands that need one.
    """
    info = _tools[tool_key].info()
    if not info.get("exists"):
        pkg = _PACKAGE_HINTS.get(tool_key, "")
        hint = f" Install with: sudo dnf install {pkg}" if pkg else ""
        return f"Error: {tool_key} is not installed.{hint}"

    cmd = [info["path"]] + args
    result = _priv.run_command(
        cmd, privilege=privilege, timeout=timeout,
        helper_command_id=helper_command_id,
        helper_device=helper_device,
    )

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if PrivilegeHelper.is_permission_error(result):
            return (
                f"Permission denied running {tool_key}.\n\n"
                f"{_priv.format_sudo_hint(cmd)}\n\n"
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


# ── Standard tools ─────────────────────────────────────────────────

@server.tool()
def tool_info() -> str:
    """Return version and availability for all network commands.

    Call this at the start of a session to see which tools are
    installed. Some (nmap, traceroute, ethtool) may not be present.
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
    """Read the man page for a specific network command.

    Args:
        tool: Command name, e.g. "ip", "ss", "dig", "firewall-cmd",
              "nmcli", "iw", "ethtool", "nft", "nmap", etc.
        section: Section to extract, e.g. "OPTIONS", "DESCRIPTION".
                 Leave empty for full page (truncated).
    """
    if tool not in _tools:
        return f"Unknown tool '{tool}'. Available: {', '.join(sorted(_tools.keys()))}"
    return _tools[tool].read_man(section=section)


# ── Phase 1: Core Networking ──────────────────────────────────────

@server.tool()
def list_interfaces(
    device: str = "",
    family: str = "",
    show_stats: bool = False,
    max_lines: int = 100,
) -> str:
    """List network interfaces with IP addresses.

    Args:
        device: Specific interface, e.g. "eth0", "wlan0", "enp0s3".
                Leave empty for all interfaces.
        family: Address family filter: "inet" (IPv4), "inet6" (IPv6).
                Leave empty for both.
        show_stats: Include packet/byte counters and error stats.
        max_lines: Maximum lines to return.
    """
    if show_stats:
        args = ["-s", "link", "show"]
    else:
        args = ["addr", "show"]

    if family and not show_stats:
        args = ["-f", family] + args
    if device:
        args += ["dev", device]

    return _run_cmd("ip", args, max_lines=max_lines)


@server.tool()
def list_routes(
    device: str = "",
    family: str = "",
    table: str = "",
    max_lines: int = 100,
) -> str:
    """Show the routing table. The default gateway is visible here.

    Args:
        device: Filter routes for a specific interface.
        family: "inet" (IPv4 only) or "inet6" (IPv6 only).
                Leave empty for IPv4 (default). Use "inet6" for IPv6.
        table: Routing table: "main" (default), "local", "all", or a
               custom table name/number.
        max_lines: Maximum lines to return.
    """
    if family == "inet6":
        args = ["-6", "route", "show"]
    else:
        args = ["route", "show"]

    if device:
        args += ["dev", device]
    if table:
        args += ["table", table]

    return _run_cmd("ip", args, max_lines=max_lines)


@server.tool()
def list_neighbors(
    device: str = "",
    family: str = "",
    max_lines: int = 100,
) -> str:
    """Show the ARP/neighbor discovery table (MAC-to-IP mappings).

    Reveals other devices on the local network that this host has
    communicated with recently.

    Args:
        device: Filter to a specific interface.
        family: "inet" (IPv4/ARP) or "inet6" (IPv6/NDP).
                Leave empty for both.
        max_lines: Maximum lines to return.
    """
    args = ["neigh", "show"]

    if device:
        args += ["dev", device]
    if family:
        args = ["-f", family] + args

    return _run_cmd("ip", args, max_lines=max_lines)


@server.tool()
def list_sockets(
    protocol: str = "",
    listening: bool = False,
    process: bool = False,
    state: str = "",
    port: str = "",
    max_lines: int = 100,
) -> str:
    """Show socket statistics: connections, listeners, port usage.

    Args:
        protocol: "tcp", "udp", or leave empty for both.
        listening: True to show only listening sockets (servers).
                   False to show all sockets.
        process: True to show the process owning each socket.
                 May require sudo to see other users' processes.
        state: Filter by TCP state: "established", "time-wait",
               "close-wait", "listen", "syn-sent", "syn-recv",
               "fin-wait-1", "fin-wait-2", "closing", "last-ack".
               Leave empty for all states.
        port: Filter by port number, e.g. "443", "8080", "22".
        max_lines: Maximum lines to return.
    """
    args = ["-n"]  # numeric — don't resolve names

    if protocol == "tcp":
        args.append("-t")
    elif protocol == "udp":
        args.append("-u")
    else:
        args += ["-t", "-u"]

    if listening:
        args.append("-l")
    else:
        args.append("-a")

    if process:
        args.append("-p")

    if state:
        args += ["state", state]

    if port:
        # ss filter: sport or dport matches
        args += [f"sport = :{port}", "or", f"dport = :{port}"]

    # -p flag needs root to see other users' processes
    priv = "auto" if process else "never"
    helper_id = "ss-processes" if process else None
    return _run_cmd("ss", args, max_lines=max_lines, privilege=priv,
                    helper_command_id=helper_id)


@server.tool()
def check_dns(
    domain: str,
    record_type: str = "A",
    server: str = "",
    max_lines: int = 50,
) -> str:
    """Perform a DNS lookup.

    Args:
        domain: Domain name to resolve, e.g. "google.com",
                "github.com", "10.0.0.1" (for PTR).
        record_type: DNS record type: "A", "AAAA", "MX", "NS", "TXT",
                     "SOA", "CNAME", "PTR", "SRV", "ANY".
        server: DNS server to query, e.g. "8.8.8.8", "1.1.1.1".
                Leave empty to use the system resolver.
        max_lines: Maximum lines to return.
    """
    args = ["+noall", "+answer", "+authority", "+question", "+stats"]
    args += [domain, record_type]
    if server:
        args.append(f"@{server}")

    return _run_cmd("dig", args, max_lines=max_lines)


@server.tool()
def check_resolver(
    max_lines: int = 100,
) -> str:
    """Show DNS resolver configuration: servers, search domains, DNSSEC.

    Uses resolvectl on systemd systems. Falls back to /etc/resolv.conf
    if resolvectl is unavailable.

    Args:
        max_lines: Maximum lines to return.
    """
    info = _tools["resolvectl"].info()
    if info.get("exists"):
        return _run_cmd("resolvectl", ["status"], max_lines=max_lines)

    # Fallback: read /etc/resolv.conf directly
    resolv = Path("/etc/resolv.conf")
    if not resolv.exists():
        return "No /etc/resolv.conf found and resolvectl is not available."

    try:
        content = resolv.read_text().strip()
        lines = content.split("\n")
        if len(lines) > max_lines:
            return (
                f"[Showing first {max_lines} of {len(lines)} lines from "
                f"/etc/resolv.conf]\n\n"
                + "\n".join(lines[:max_lines])
            )
        return f"[/etc/resolv.conf — resolvectl not available]\n\n{content}"
    except PermissionError:
        return "Permission denied reading /etc/resolv.conf."


@server.tool()
def check_connectivity(
    host: str,
    count: int = 4,
    deadline: int = 10,
    max_lines: int = 50,
) -> str:
    """Ping a host to test network connectivity.

    Returns latency statistics (min/avg/max/mdev) and packet loss.

    Args:
        host: Hostname or IP address to ping, e.g. "8.8.8.8",
              "google.com", "192.168.1.1".
        count: Number of ping packets to send. Default 4.
        deadline: Maximum seconds to wait for all replies. Prevents
                  indefinite blocking. Default 10.
        max_lines: Maximum lines to return.
    """
    args = ["-c", str(count), "-w", str(deadline), host]
    return _run_cmd("ping", args, max_lines=max_lines, timeout=deadline + 5)


# ── Reachability diagnostic helpers ───────────────────────────────

# Virtual interface prefixes to exclude from primary interface selection
_VIRTUAL_IFACE_PREFIXES = (
    "lo", "docker", "podman", "br-", "virbr", "veth", "cni", "flannel",
)

# VPN/tunnel interface prefixes
_VPN_IFACE_PREFIXES = (
    "tun", "tap", "wg", "ppp",          # Standard VPN/tunnel
    "nordlynx", "proton", "mullvad",     # Commercial VPN clients
    "tailscale",                          # Tailscale mesh VPN
)


def _get_network_context(timeout: int = 5) -> dict:
    """Gather network context: primary interface, gateway, DNS, VPN status.

    Returns dict with keys: primary_iface, gateway, dns_servers, vpn_active,
    vpn_iface, all_ifaces, filtered_ifaces, error.
    """
    ctx: dict = {
        "primary_iface": None,
        "gateway": None,
        "dns_servers": [],
        "vpn_active": False,
        "vpn_iface": None,
        "other_vpn_ifaces": [],
        "all_ifaces": [],
        "filtered_ifaces": [],
        "error": None,
    }

    ip_info = _tools["ip"].info()
    if not ip_info.get("exists"):
        ctx["error"] = "ip command not available"
        return ctx

    # Get default route
    try:
        result = subprocess.run(
            [ip_info["path"], "-j", "route", "show", "default"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            routes = json.loads(result.stdout)
            if routes:
                route = routes[0]
                ctx["gateway"] = route.get("gateway")
                ctx["primary_iface"] = route.get("dev")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, IndexError):
        pass

    # Check if primary interface is a VPN/tunnel
    if ctx["primary_iface"]:
        for prefix in _VPN_IFACE_PREFIXES:
            if ctx["primary_iface"].startswith(prefix):
                ctx["vpn_active"] = True
                ctx["vpn_iface"] = ctx["primary_iface"]
                break

    # Get all interfaces with state and IPs
    try:
        result = subprocess.run(
            [ip_info["path"], "-j", "addr", "show"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            ifaces = json.loads(result.stdout)
            for iface in ifaces:
                name = iface.get("ifname", "")
                state = iface.get("operstate", "UNKNOWN")
                addrs = []
                for ai in iface.get("addr_info", []):
                    if ai.get("family") == "inet":
                        addrs.append(
                            f"{ai.get('local', '')}/{ai.get('prefixlen', '')}"
                        )
                entry = {"name": name, "state": state, "addrs": addrs}

                is_virtual = any(
                    name.startswith(p) for p in _VIRTUAL_IFACE_PREFIXES
                )
                is_vpn = any(
                    name.startswith(p) for p in _VPN_IFACE_PREFIXES
                )
                if is_vpn and name != ctx.get("vpn_iface"):
                    ctx["other_vpn_ifaces"].append(entry)
                if is_virtual:
                    ctx["filtered_ifaces"].append(entry)
                else:
                    ctx["all_ifaces"].append(entry)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass

    # Get DNS servers
    resolvectl_info = _tools["resolvectl"].info()
    if resolvectl_info.get("exists"):
        try:
            result = subprocess.run(
                [resolvectl_info["path"], "status"],
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    line = line.strip()
                    if "DNS Servers:" in line or "Current DNS Server:" in line:
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            server = parts[1].strip()
                            if server and server not in ctx["dns_servers"]:
                                ctx["dns_servers"].append(server)
        except subprocess.TimeoutExpired:
            pass

    # Fallback: /etc/resolv.conf
    if not ctx["dns_servers"]:
        resolv = Path("/etc/resolv.conf")
        if resolv.exists():
            try:
                for line in resolv.read_text().split("\n"):
                    if line.strip().startswith("nameserver"):
                        server = line.split()[1]
                        if server != "127.0.0.53":
                            ctx["dns_servers"].append(server)
            except (PermissionError, IndexError):
                pass

    return ctx


def _ping_check(host: str, count: int = 2, timeout: int = 5) -> dict:
    """Run a quick ping check. Returns {pass, latency_ms, loss_pct, detail}."""
    ping_info = _tools["ping"].info()
    if not ping_info.get("exists"):
        return {"pass": None, "detail": "ping not available"}

    try:
        result = subprocess.run(
            [ping_info["path"], "-c", str(count), "-w", str(timeout), host],
            capture_output=True, text=True, timeout=timeout + 3,
        )
        output = result.stdout + result.stderr

        # Parse packet loss
        loss_match = re.search(r"(\d+)% packet loss", output)
        loss_pct = int(loss_match.group(1)) if loss_match else 100

        # Parse avg latency
        rtt_match = re.search(
            r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", output
        )
        latency_ms = float(rtt_match.group(1)) if rtt_match else None

        passed = loss_pct < 100
        detail = f"{latency_ms:.1f}ms, {loss_pct}% loss" if passed else "timeout"
        return {"pass": passed, "latency_ms": latency_ms,
                "loss_pct": loss_pct, "detail": detail}
    except subprocess.TimeoutExpired:
        return {"pass": False, "detail": "timeout"}


def _dns_check(
    domain: str, server: str = "", timeout: int = 5,
) -> dict:
    """Run a DNS lookup. Returns {pass, latency_ms, detail}."""
    dig_info = _tools["dig"].info()
    if not dig_info.get("exists"):
        return {"pass": None, "detail": "dig not available"}

    args = ["+noall", "+answer", "+stats", "+time={}".format(timeout), domain]
    if server:
        args.append(f"@{server}")

    try:
        start = time.monotonic()
        result = subprocess.run(
            [dig_info["path"]] + args,
            capture_output=True, text=True, timeout=timeout + 3,
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        output = result.stdout.strip()
        # dig returns 0 even for NXDOMAIN; check for answer lines
        has_answer = any(
            line and not line.startswith(";")
            for line in output.split("\n") if line.strip()
        )

        if has_answer:
            return {"pass": True, "latency_ms": round(elapsed_ms, 1),
                    "detail": f"{elapsed_ms:.0f}ms"}
        else:
            return {"pass": False, "detail": "no answer"}
    except subprocess.TimeoutExpired:
        return {"pass": False, "detail": "timeout"}


def _https_check(
    url: str, timeout: int = 5, tls_only: bool = False,
) -> dict:
    """Run an HTTPS/HTTP check via curl. Returns {pass, latency_ms, detail}.

    Args:
        url: URL to check.
        timeout: Max seconds.
        tls_only: If True, consider TLS handshake success as pass
            regardless of HTTP status (for API endpoints that reject
            unauthenticated requests).
    """
    curl_info = _tools["curl"].info()
    if not curl_info.get("exists"):
        return {"pass": None, "detail": "curl not installed"}

    try:
        start = time.monotonic()
        result = subprocess.run(
            [curl_info["path"], "-sS", "-o", "/dev/null", "-w",
             "%{http_code} %{ssl_verify_result}",
             "--connect-timeout", str(timeout),
             "--max-time", str(timeout), "-L", url],
            capture_output=True, text=True, timeout=timeout + 3,
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        parts = result.stdout.strip().split()
        http_code = parts[0] if parts else "000"
        ssl_ok = parts[1] == "0" if len(parts) > 1 else False

        if result.returncode == 0 and http_code.startswith(("2", "3")):
            return {"pass": True, "latency_ms": round(elapsed_ms, 1),
                    "detail": f"{elapsed_ms:.0f}ms, HTTP {http_code}"}
        elif tls_only and ssl_ok:
            # TLS handshake succeeded — endpoint is reachable
            return {"pass": True, "latency_ms": round(elapsed_ms, 1),
                    "detail": f"{elapsed_ms:.0f}ms, TLS OK "
                    f"(HTTP {http_code})"}
        else:
            stderr = result.stderr.strip() if result.stderr else ""
            detail = f"HTTP {http_code}" if http_code else stderr or "failed"
            return {"pass": False, "detail": detail}
    except subprocess.TimeoutExpired:
        return {"pass": False, "detail": "timeout"}


def _check_arp(ip: str, timeout: int = 5) -> dict:
    """Check ARP/neighbor entry. Returns {pass, mac, detail}."""
    ip_info = _tools["ip"].info()
    if not ip_info.get("exists"):
        return {"pass": None, "detail": "ip not available"}

    try:
        result = subprocess.run(
            [ip_info["path"], "-j", "neigh", "show", ip],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            entries = json.loads(result.stdout)
            for entry in entries:
                mac = entry.get("lladdr", "")
                state = entry.get("state", [])
                if mac and ("REACHABLE" in state or "STALE" in state):
                    return {"pass": True, "mac": mac,
                            "detail": mac}
                elif mac:
                    return {"pass": True, "mac": mac,
                            "detail": f"{mac} ({','.join(state)})"}
            return {"pass": False, "detail": "no entry"}
        return {"pass": False, "detail": "no entry"}
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"pass": False, "detail": "check failed"}


def _classify(results: dict) -> tuple[str, str]:
    """Classify the overall reachability state from layer results.

    Returns (classification, explanation).
    """
    def passed(layer: int) -> bool:
        return results.get(layer, {}).get("pass", False)

    def skipped(layer: int) -> bool:
        return results.get(layer, {}).get("skipped", False)

    if all(passed(i) or skipped(i) for i in range(1, 8)):
        return ("ALL_OK",
                "All layers passed. Network connectivity is fully operational.")

    if not passed(1):
        return ("LINK_DOWN",
                "Layer 1 failed — interface down, no IP, or no carrier.")

    if not passed(2):
        if results.get(2, {}).get("skipped"):
            pass  # No gateway (tunnel/cellular) — skip this classification
        else:
            return ("GATEWAY_UNREACHABLE",
                    "Layer 1 passes but Layer 2 fails — cannot reach "
                    "default gateway. Local subnet issue.")

    if not passed(3) and passed(4):
        return ("LOCAL_DNS_FAILURE",
                "Layers 1-2 pass, Layer 3 fails but Layer 4 passes. "
                "Local DNS broken, internet reachable via IP.")

    if not passed(4):
        return ("INTERNET_UNREACHABLE",
                "Can reach gateway but not the internet. "
                "ISP or upstream issue.")

    if not passed(5) and passed(4):
        return ("PUBLIC_DNS_FAILURE",
                "Internet reachable by IP (Layer 4) but public DNS "
                "servers not resolving (Layer 5).")

    if not passed(6) and not skipped(6):
        return ("HTTPS_BLOCKED",
                "ICMP and DNS work but HTTPS fails. "
                "Possible captive portal, proxy, or firewall blocking.")

    if not passed(7) and not skipped(7):
        return ("CLAUDE_API_UNREACHABLE",
                "Internet works but Claude API is unreachable. "
                "Anthropic service may be down — check status.claude.com.")

    return ("PARTIAL_FAILURE",
            "Mixed results across layers — see per-layer details.")


@server.tool()
def check_reachability(
    include_api_check: bool = True,
    custom_targets: list[str] | None = None,
    timeout_per_check: int = 5,
    max_lines: int = 200,
) -> str:
    """Run a layered reachability diagnostic from local link to internet.

    Tests connectivity bottom-up: local interface -> gateway -> DNS ->
    internet (bypassing DNS) -> internet DNS -> HTTPS -> Claude API.
    Each layer uses multiple test types to isolate failure class.

    Returns structured results per layer with pass/fail, latency,
    and a summary classification.

    Args:
        include_api_check: Also test HTTPS to api.anthropic.com
            and status.claude.com. Default True.
        custom_targets: Additional hosts/IPs to ping after the
            standard layers. e.g. ["nas.local", "10.0.0.1"]
        timeout_per_check: Max seconds per individual check. Default 5.
        max_lines: Maximum lines to return.
    """
    lines: list[str] = ["=== Reachability Diagnostic ===", ""]
    layer_results: dict[int, dict] = {}

    # Gather network context
    ctx = _get_network_context(timeout=timeout_per_check)

    # ── Layer 1: Local Link ──
    lines.append("Layer 1: Local Link")
    l1_pass = False

    if ctx["error"]:
        lines.append(f"  Error: {ctx['error']}")
    else:
        primary = ctx["primary_iface"]
        for iface in ctx["all_ifaces"]:
            is_primary = iface["name"] == primary
            label = "Primary" if is_primary else "Other"
            addrs = ", ".join(iface["addrs"]) if iface["addrs"] else "no IPv4"
            route_note = " (carries default route)" if is_primary else ""
            lines.append(
                f"  {label}: {iface['name']} — {iface['state']}, "
                f"{addrs}{route_note}"
            )
            if is_primary and iface["state"] == "UP" and iface["addrs"]:
                l1_pass = True

        if ctx["filtered_ifaces"]:
            names = ", ".join(i["name"] for i in ctx["filtered_ifaces"])
            lines.append(f"  Filtered: {names} (virtual/loopback — excluded)")

        if ctx["vpn_active"]:
            lines.append(
                f"  VPN: detected — default route via {ctx['vpn_iface']}"
            )
        elif ctx["other_vpn_ifaces"]:
            names = ", ".join(i["name"] for i in ctx["other_vpn_ifaces"])
            lines.append(
                f"  VPN: {names} present (split-tunnel — not carrying "
                f"default route)"
            )
        else:
            lines.append("  VPN: not detected on default route")

        if not primary:
            lines.append("  Warning: no default route found")

    lines.append(f"  Result: {'PASS' if l1_pass else 'FAIL'}")
    lines.append("")
    layer_results[1] = {"pass": l1_pass}

    # ── Layer 2: Default Gateway ──
    gw = ctx["gateway"]
    if gw:
        lines.append(f"Layer 2: Default Gateway ({gw})")
        ping_r = _ping_check(gw, count=2, timeout=timeout_per_check)
        lines.append(f"  Ping {gw}: "
                      f"{'PASS' if ping_r['pass'] else 'FAIL'} "
                      f"({ping_r['detail']})")

        arp_r = _check_arp(gw, timeout=timeout_per_check)
        lines.append(f"  ARP entry: "
                      f"{'PASS' if arp_r['pass'] else 'FAIL'} "
                      f"({arp_r['detail']})")

        l2_pass = bool(ping_r["pass"])
        lines.append(f"  Result: {'PASS' if l2_pass else 'FAIL'}")
        layer_results[2] = {"pass": l2_pass}
    else:
        lines.append("Layer 2: Default Gateway")
        reason = ("VPN/tunnel — no traditional gateway"
                  if ctx["vpn_active"] else "no default route")
        lines.append(f"  Skipped: {reason}")
        layer_results[2] = {"pass": False, "skipped": True}
    lines.append("")

    # ── Layer 3: Local DNS ──
    dns_servers = ctx["dns_servers"]
    dns_server = dns_servers[0] if dns_servers else ""
    lines.append(f"Layer 3: Local DNS"
                 f"{f' ({dns_server})' if dns_server else ''}")

    if dns_server:
        dns_r1 = _dns_check("google.com", server=dns_server,
                            timeout=timeout_per_check)
        lines.append(f"  Resolve \"google.com\" via {dns_server}: "
                      f"{'PASS' if dns_r1['pass'] else 'FAIL'} "
                      f"({dns_r1['detail']})")

        dns_r2 = _dns_check("cloudflare.com", server=dns_server,
                            timeout=timeout_per_check)
        lines.append(f"  Resolve \"cloudflare.com\" via {dns_server}: "
                      f"{'PASS' if dns_r2['pass'] else 'FAIL'} "
                      f"({dns_r2['detail']})")

        l3_pass = bool(dns_r1["pass"] or dns_r2["pass"])
    else:
        lines.append("  No DNS server detected — skipping")
        l3_pass = False

    lines.append(f"  Result: {'PASS' if l3_pass else 'FAIL'}")
    lines.append("")
    layer_results[3] = {"pass": l3_pass}

    # ── Layer 4: Internet (bypass DNS) ──
    lines.append("Layer 4: Internet (bypass DNS)")
    ping_r1 = _ping_check("8.8.8.8", count=2, timeout=timeout_per_check)
    lines.append(f"  Ping 8.8.8.8: "
                  f"{'PASS' if ping_r1['pass'] else 'FAIL'} "
                  f"({ping_r1['detail']})")

    ping_r2 = _ping_check("1.1.1.1", count=2, timeout=timeout_per_check)
    lines.append(f"  Ping 1.1.1.1: "
                  f"{'PASS' if ping_r2['pass'] else 'FAIL'} "
                  f"({ping_r2['detail']})")

    l4_pass = bool(ping_r1["pass"] or ping_r2["pass"])
    lines.append(f"  Result: {'PASS' if l4_pass else 'FAIL'}")
    lines.append("")
    layer_results[4] = {"pass": l4_pass}

    # ── Layer 5: Internet DNS ──
    lines.append("Layer 5: Internet DNS")
    dns_r3 = _dns_check("google.com", server="8.8.8.8",
                        timeout=timeout_per_check)
    lines.append(f"  Resolve \"google.com\" via 8.8.8.8: "
                  f"{'PASS' if dns_r3['pass'] else 'FAIL'} "
                  f"({dns_r3['detail']})")

    dns_r4 = _dns_check("google.com", server="1.1.1.1",
                        timeout=timeout_per_check)
    lines.append(f"  Resolve \"google.com\" via 1.1.1.1: "
                  f"{'PASS' if dns_r4['pass'] else 'FAIL'} "
                  f"({dns_r4['detail']})")

    l5_pass = bool(dns_r3["pass"] or dns_r4["pass"])
    lines.append(f"  Result: {'PASS' if l5_pass else 'FAIL'}")
    lines.append("")
    layer_results[5] = {"pass": l5_pass}

    # ── Layer 6: HTTPS ──
    lines.append("Layer 6: HTTP/HTTPS")
    curl_info = _tools["curl"].info()
    if curl_info.get("exists"):
        https_r1 = _https_check("http://detectportal.firefox.com/success.txt",
                                timeout=timeout_per_check)
        lines.append(
            f"  GET http://detectportal.firefox.com: "
            f"{'PASS' if https_r1['pass'] else 'FAIL'} "
            f"({https_r1['detail']})"
        )

        https_r2 = _https_check("https://1.1.1.1",
                                timeout=timeout_per_check)
        lines.append(f"  GET https://1.1.1.1: "
                      f"{'PASS' if https_r2['pass'] else 'FAIL'} "
                      f"({https_r2['detail']})")

        l6_pass = bool(https_r1["pass"] or https_r2["pass"])
        lines.append(f"  Result: {'PASS' if l6_pass else 'FAIL'}")
        layer_results[6] = {"pass": l6_pass}
    else:
        lines.append("  Skipped — curl not installed. "
                      "Install with: sudo dnf install curl")
        layer_results[6] = {"pass": False, "skipped": True}
    lines.append("")

    # ── Layer 7: Claude API ──
    if include_api_check:
        lines.append("Layer 7: Claude API")
        if curl_info.get("exists"):
            api_r1 = _https_check("https://api.anthropic.com",
                                  timeout=timeout_per_check,
                                  tls_only=True)
            lines.append(
                f"  TLS api.anthropic.com:443: "
                f"{'PASS' if api_r1['pass'] else 'FAIL'} "
                f"({api_r1['detail']})"
            )

            api_r2 = _https_check("https://status.claude.com",
                                  timeout=timeout_per_check)
            lines.append(
                f"  GET https://status.claude.com: "
                f"{'PASS' if api_r2['pass'] else 'FAIL'} "
                f"({api_r2['detail']})"
            )

            l7_pass = bool(api_r1["pass"] or api_r2["pass"])
            lines.append(f"  Result: {'PASS' if l7_pass else 'FAIL'}")
            layer_results[7] = {"pass": l7_pass}
        else:
            lines.append("  Skipped — curl not installed")
            layer_results[7] = {"pass": False, "skipped": True}
        lines.append("")

    # ── Custom targets ──
    if custom_targets:
        lines.append("Custom targets:")
        for target in custom_targets:
            r = _ping_check(target, count=2, timeout=timeout_per_check)
            lines.append(f"  Ping {target}: "
                          f"{'PASS' if r['pass'] else 'FAIL'} "
                          f"({r['detail']})")
        lines.append("")

    # ── Summary ──
    classification, explanation = _classify(layer_results)
    lines.append("=== Summary ===")
    lines.append(f"Classification: {classification}")
    lines.append(explanation)

    output = "\n".join(lines)
    out_lines = output.split("\n")
    if len(out_lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(out_lines)} lines.]\n\n"
            + "\n".join(out_lines[:max_lines])
        )
    return output


# ── Phase 2: Firewall ─────────────────────────────────────────────

@server.tool()
def list_firewall_rules(
    zone: str = "",
    max_lines: int = 200,
) -> str:
    """Inspect firewall rules. Tries firewalld first, falls back to nft.

    For firewalld: shows services, ports, rich rules, and masquerading
    for the specified zone (or default zone).
    For nft: shows the full nftables ruleset.

    Args:
        zone: Firewalld zone, e.g. "public", "home", "trusted".
              Leave empty for the default zone.
              Ignored when falling back to nft.
        max_lines: Maximum lines to return.
    """
    # Try firewalld first
    fw_info = _tools["firewall-cmd"].info()
    if fw_info.get("exists"):
        args = ["--list-all"]
        if zone:
            args += [f"--zone={zone}"]
        result = _run_cmd("firewall-cmd", args, max_lines=max_lines)
        # If firewalld is not running, fall through to nft
        if "not running" not in result.lower():
            return result

    # Fallback: raw nftables (may need root)
    nft_info = _tools["nft"].info()
    if nft_info.get("exists"):
        return _run_cmd("nft", ["list", "ruleset"], max_lines=max_lines,
                        privilege="auto", helper_command_id="nft-list")

    return (
        "No firewall inspection tool available. "
        "Install firewalld or nftables for firewall queries."
    )


@server.tool()
def check_firewall_zones(
    active_only: bool = True,
    max_lines: int = 100,
) -> str:
    """List firewall zones and their interface assignments.

    Requires firewalld. Shows which zones are active and which
    interfaces/sources are assigned to each.

    Args:
        active_only: True to show only zones with assigned interfaces.
                     False to show all configured zones with details.
        max_lines: Maximum lines to return.
    """
    fw_info = _tools["firewall-cmd"].info()
    if not fw_info.get("exists"):
        return (
            "Error: firewall-cmd is not installed. "
            "Install with: sudo dnf install firewalld"
        )

    if active_only:
        return _run_cmd(
            "firewall-cmd", ["--get-active-zones"], max_lines=max_lines
        )
    else:
        return _run_cmd(
            "firewall-cmd", ["--list-all-zones"], max_lines=max_lines
        )


# ── Phase 3: NetworkManager, WiFi, NIC Details ────────────────────

@server.tool()
def list_connections(
    device: str = "",
    active_only: bool = True,
    max_lines: int = 100,
) -> str:
    """Show NetworkManager connection profiles and device status.

    Args:
        device: Filter to a specific device, e.g. "eth0", "wlan0".
                Leave empty for all.
        active_only: True to show only active connections.
                     False to show all configured profiles.
        max_lines: Maximum lines to return.
    """
    nm_info = _tools["nmcli"].info()
    if not nm_info.get("exists"):
        return (
            "Error: nmcli is not installed. "
            "Install with: sudo dnf install NetworkManager"
        )

    # Get device status first
    dev_args = ["device", "status"]
    dev_output = _run_cmd("nmcli", dev_args, max_lines=max_lines)

    # Then connection profiles
    conn_args = ["connection", "show"]
    if active_only:
        conn_args.append("--active")
    conn_output = _run_cmd("nmcli", conn_args, max_lines=max_lines)

    output = "=== Device Status ===\n" + dev_output
    output += "\n\n=== Connection Profiles"
    if active_only:
        output += " (active only)"
    output += " ===\n" + conn_output

    if device:
        # Also get device-specific details
        detail_output = _run_cmd(
            "nmcli", ["device", "show", device], max_lines=max_lines
        )
        output += f"\n\n=== Device Details: {device} ===\n" + detail_output

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return output


@server.tool()
def list_wifi_networks(
    device: str = "",
    max_lines: int = 100,
) -> str:
    """Scan and list visible WiFi networks.

    Shows SSID, signal strength, security type, frequency, and channel.

    Args:
        device: Specific WiFi interface, e.g. "wlan0", "wlp2s0".
                Leave empty to use the first available WiFi device.
        max_lines: Maximum lines to return.
    """
    nm_info = _tools["nmcli"].info()
    if not nm_info.get("exists"):
        return (
            "Error: nmcli is not installed. "
            "Install with: sudo dnf install NetworkManager"
        )

    args = ["device", "wifi", "list"]
    if device:
        args += ["ifname", device]

    return _run_cmd("nmcli", args, max_lines=max_lines)


@server.tool()
def get_nic_details(
    interface: str,
    max_lines: int = 100,
) -> str:
    """Get NIC hardware details: speed, duplex, driver, link state.

    For WiFi interfaces, includes wireless-specific info (SSID,
    frequency, signal strength, bitrate).

    Args:
        interface: Network interface name, e.g. "eth0", "enp0s3",
                   "wlan0", "wlp2s0".
        max_lines: Maximum lines to return.
    """
    output_parts = []

    # Check if wireless interface
    wireless_path = Path(f"/sys/class/net/{interface}/wireless")
    is_wifi = wireless_path.exists()

    if is_wifi:
        # Use iw for WiFi details
        iw_info = _tools["iw"].info()
        if iw_info.get("exists"):
            info_result = _run_cmd(
                "iw", ["dev", interface, "info"], max_lines=50
            )
            output_parts.append(f"=== WiFi Info ({interface}) ===\n{info_result}")

            link_result = _run_cmd(
                "iw", ["dev", interface, "link"], max_lines=50
            )
            output_parts.append(f"\n=== WiFi Link ===\n{link_result}")

    # Use ethtool for hardware details (works for both wired and WiFi)
    eth_info = _tools["ethtool"].info()
    if eth_info.get("exists"):
        eth_result = _run_cmd(
            "ethtool", [interface], max_lines=50
        )
        output_parts.append(f"\n=== Ethtool ({interface}) ===\n{eth_result}")

        # Driver info
        drv_result = _run_cmd(
            "ethtool", ["-i", interface], max_lines=30
        )
        output_parts.append(f"\n=== Driver Info ===\n{drv_result}")

    if not output_parts:
        return (
            "No NIC inspection tools available. "
            "Install ethtool or iw for hardware details."
        )

    output = "\n".join(output_parts)
    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return output


# ── Phase 4: Path Tracing ─────────────────────────────────────────

@server.tool()
def check_path(
    host: str,
    max_hops: int = 30,
    max_lines: int = 100,
) -> str:
    """Trace the network path to a remote host.

    Prefers tracepath (no sudo needed) over traceroute.

    Args:
        host: Hostname or IP to trace to, e.g. "google.com", "8.8.8.8".
        max_hops: Maximum number of hops. Default 30.
        max_lines: Maximum lines to return.
    """
    # Prefer tracepath — doesn't need root
    tp_info = _tools["tracepath"].info()
    if tp_info.get("exists"):
        args = ["-m", str(max_hops), host]
        return _run_cmd(
            "tracepath", args, max_lines=max_lines, timeout=60
        )

    # Fallback: traceroute
    tr_info = _tools["traceroute"].info()
    if tr_info.get("exists"):
        args = ["-m", str(max_hops), host]
        return _run_cmd(
            "traceroute", args, max_lines=max_lines, timeout=60
        )

    return (
        "No path tracing tool available. "
        "Install tracepath (iputils) or traceroute."
    )


if __name__ == "__main__":
    server.run(transport="stdio")
