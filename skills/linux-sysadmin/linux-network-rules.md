---
name: linux-network
description: >
    Domain knowledge for network diagnostics, DNS, routing, firewall
    inspection, WiFi, and connectivity testing using the network MCP
    server. Load BEFORE using any network MCP tool directly. Covers
    interfaces, sockets, NetworkManager, and path tracing.
---

# Networking

## Guide

This file covers network diagnostics, DNS, routing, firewall, WiFi, and connectivity.

- **Domain Model** — network stack layers and helper services
- **Heuristics** — expert shortcuts: most network problems are DNS, VPNs override everything
- **Anti-patterns** — common mistakes baseline Claude makes with networking
- **Procedures** — diagnostic workflows for connectivity, DNS, WiFi, firewall, post-outage
- **Tools** — goal-to-tool lookup table for the network MCP server
- **Query Strategy** — layer isolation, efficient queries, empty result checks
- **Safety** — privilege, high-risk operations (DNS/firewall changes), cross-domain pointers
- **Quirks** — systemd-resolved stub, firewalld defaults, container bridges
- **Domain Deep Knowledge** → `linux-network-deep-knowledge.md` for VPN-DNS, protocols, WiFi RF, NTP

## Domain Model

```
Physical NIC → Link layer (MAC, speed, duplex) → IP layer (addresses, subnets) →
  Routing → Firewall (netfilter/nftables/firewalld) → Application (sockets, ports)
```

Helper services alongside the stack:
- **NetworkManager** — manages connection profiles, DHCP, WiFi, VPN. Standard on desktops.
- **systemd-networkd** — alternative network manager, common on servers. Mutually exclusive with NM.
- **systemd-resolved** — DNS stub resolver, per-interface DNS config. Runs alongside either NM or networkd.
- **wpa_supplicant** — WiFi authentication. Usually managed by NM transparently.

Key relationships:
- DNS resolution chain: Application → glibc NSS → stub resolver (127.0.0.53) → upstream DNS servers
- Firewall models: firewalld (zone-based, standard on Fedora/RHEL) → generates nftables rules → kernel netfilter
- Interface naming: predictable names based on hardware path (e.g. `enp0s3`, `wlp2s0`). Virtual: `lo`, `docker0`, `virbr0`, `wg0`, `tun0`

## Heuristics

1. 90% of "network is down" is DNS. Always test with IP first (`check_connectivity("8.8.8.8")`) to separate DNS from connectivity.
2. If a VPN is active, suspect it first. VPN clients override DNS routing, inject firewall rules, and change default routes. `check_reachability` flags active VPNs.
3. On systemd systems, `/etc/resolv.conf` lies. It points to the stub resolver (127.0.0.53), not the real upstream. Use `check_resolver` for truth.
4. A user who switched networks (e.g. mobile hotspot) without realising will invalidate all subsequent diagnostics. Always verify current network state first.
5. If ping to a hostname fails but ping to an IP works, it's DNS — not connectivity.
6. When firewall logging shows nothing, it's because `LogDenied=off` is the Fedora/RHEL default — not because traffic isn't being dropped.

## Anti-patterns

- Don't restart NetworkManager to fix DNS — it drops ALL connections and may change DHCP lease. Use `resolvectl` instead.
- Don't edit `/etc/resolv.conf` on systemd systems — it's managed by systemd-resolved and will be overwritten.
- Don't assume `ping hostname` failure = network down. Test with `ping 8.8.8.8` first.
- Don't jump to DNS troubleshooting on NXDOMAIN — the domain may genuinely not exist. Check NS and SOA records first.
- Don't assume firewall rules explain a connectivity problem without checking if the service is actually listening (`list_sockets`).
- Don't use `iptables` commands on a firewalld system — they conflict. Use `firewall-cmd` or the MCP tools.

## Procedures

### Network state verification
Before any network troubleshooting, confirm the machine is on the expected network. Do NOT skip this.

1. `list_interfaces` — which interfaces are up? Do they have IPs?
2. IF WiFi: `get_nic_details(<wifi_iface>)` — which SSID? (catches mobile hotspot, wrong network)
3. `check_resolver` — which DNS servers are configured per interface?
4. VERIFY: Interface is up, has expected IP, connected to expected network

### Network baseline capture
When about to test a fix or compare before/after.

1. `check_resolver` — full per-interface DNS servers and routing domains
2. `list_routes` — routing table
3. VPN connection status (if applicable)
4. Interface state and SSID (from verification above)

### Network connectivity failure
When a user reports network issues, can't reach a service, or you detect connectivity problems.

1. `check_reachability` — full bottom-up diagnostic in one call. Read the classification.
2. IF LINK_DOWN:
     `get_nic_details` — check driver/state
     Check physical connection
   IF DNS_FAILURE:
     → "DNS troubleshooting" procedure
   IF NO_IP:
     `list_interfaces` to confirm
     Most likely: NetworkManager reconnect needed
   IF INTERNET_UNREACHABLE:
     `list_routes` then `list_firewall_rules`
   ELSE:
     Application-level — port blocked, service down, or TLS issue
3. VERIFY: `check_reachability` again — all layers should pass
4. CROSS-DOMAIN: If outage lasted >5min, check systemd for failed services (`list_failed_units`)

### Unreachable host
When user can't reach a specific host or service.

1. `check_dns(host)` — does the name resolve?
2. IF NXDOMAIN:
     `check_dns(host, record_type="NS")` — are nameservers delegated?
     `check_dns(host, record_type="SOA")` — does the zone have authority?
     IF no NS and no SOA → domain doesn't exist. Ask user to verify spelling.
     IF NS exists but no A/AAAA → DNS misconfigured server-side, not local
   IF timeout or SERVFAIL:
     → "DNS troubleshooting" procedure
   IF resolves successfully:
     Continue below
3. `check_connectivity(host)` — basic IP reachability
4. `list_routes` — is there a route to the destination?
5. `list_firewall_rules` — is the local firewall blocking outbound?
6. `check_path(host)` — where does the path die?
7. IF ping works but service doesn't respond: above the network layer (port blocked, service down, TLS)
8. VERIFY: `check_connectivity(host)` passes
9. CROSS-DOMAIN: If the host is a container → `container-runtime-rules.md` networking section

### DNS troubleshooting
When DNS resolution is failing or returning wrong results.

1. `check_resolver` — what's the resolver config?
2. `check_dns(domain)` — does the default resolver answer?
3. `check_dns(domain, server="8.8.8.8")` — does a public resolver answer?
4. IF external works but local doesn't:
     Local resolver or upstream problem
5. IF VPN active:
     `check_resolver` — look for VPN interface with `~.` catch-all DNS domain
     `list_firewall_rules` — check for VPN-injected DNS blocks (port 53 to private IPs)
     Test DNS server directly: `dig +short <domain> @<dns_server_ip>` via Bash
     IF direct dig times out → firewall or routing, not DNS config
     → `linux-network-deep-knowledge.md` "VPN-DNS Troubleshooting" for full workflow
6. VERIFY: `check_dns(domain)` returns expected result
7. CROSS-DOMAIN: If DNS was using a local server → check that server's status via systemd MCP

### WiFi troubleshooting
When WiFi connectivity fails or is degraded.

1. `list_interfaces` — is the WiFi interface present and UP?
2. `get_nic_details(<wifi_iface>)` — associated? Signal strength?
3. `list_wifi_networks` — is the target network visible? Signal level?
4. `list_connections(device=<wifi_iface>)` — NM profiles for this network?
5. IF connected but no internet: → "Network connectivity failure" procedure
6. Common causes: driver not loaded, rfkill blocking, weak signal, wrong password in NM profile, 5GHz not supported
7. VERIFY: `get_nic_details` shows associated with good signal
8. CROSS-DOMAIN: If WiFi adapter is USB → `linux-serial-device-rules.md` for USB-level diagnosis

### Firewall audit
When checking if the firewall is too open or blocking needed traffic.

1. `check_firewall_zones` — which zones are active? Which interfaces in which zones?
2. `list_firewall_rules` — what's allowed in each active zone?
3. `list_sockets(listening=True)` — what's actually listening?
4. Cross-check: firewall allows vs actual listeners
   - Unused open ports (allowed but nothing listening) = noise, not risk
   - Listeners on ports not allowed by firewall = protected, may explain "can't connect"
5. Check: is public-facing interface in `public` zone (not `trusted`)?
6. VERIFY: Only necessary services are allowed in the public zone

### Post-outage diagnosis
When the user reports a recent outage or connectivity loss.

1. `check_reachability` — establish current state
2. Check recent network events via journald MCP:
     `search_journals(unit="NetworkManager", since="1h")` — connection up/down, DHCP
     `search_journals(identifier="kernel", grep="link", since="1h")` — interface carrier changes
3. Ask: what stopped working? One app, all apps, just Claude? Intermittent or solid?
4. IF VPN active: is the VPN itself the cause? Does problem persist with VPN off?
5. Classify: Local (L1-2), LAN (L3), ISP (L4), DNS-only, Service-specific
6. `list_interfaces(show_stats=True)` — RX/TX errors, drops for intermittent physical issues
7. VERIFY: `check_reachability` all layers pass
8. CROSS-DOMAIN: After extended outages, Claude Code may not reconnect (stale HTTP/2). User should restart Claude with `claude --continue`.

### Port and service investigation
When investigating what's listening on a port or why a service isn't reachable.

1. `list_sockets(listening=True, port="X")` — who's listening?
2. `list_sockets(listening=True, process=True)` — which process? (may need sudo)
3. `list_firewall_rules` — is the firewall allowing traffic to that port?
4. Cross-check: firewalld zone for the relevant interface permits the service?
5. VERIFY: Service listening AND firewall allowing on the correct interface/zone

## Tools

| Goal | Tool |
|------|------|
| What interfaces exist? IPs? | `list_interfaces` |
| Interface packet/error stats? | `list_interfaces(show_stats=True)` |
| Routing table / default gateway? | `list_routes` |
| ARP table / neighbor cache? | `list_neighbors` |
| What's listening on which ports? | `list_sockets(listening=True)` |
| Active TCP connections? | `list_sockets(state="established")` |
| Who owns a socket? | `list_sockets(process=True)` (may need sudo) |
| DNS lookup | `check_dns` |
| DNS resolver configuration | `check_resolver` |
| Can I reach host X? | `check_connectivity` |
| Full connectivity diagnostic? | `check_reachability` |
| Firewall rules for a zone | `list_firewall_rules` |
| Active firewall zones | `check_firewall_zones` |
| NM connection profiles | `list_connections` |
| Available WiFi networks | `list_wifi_networks` |
| NIC speed/driver/firmware | `get_nic_details` |
| Network path to remote host | `check_path` |
| Tool versions | `tool_info` |
| Man page details | `read_manual` |

## Query Strategy

1. Work bottom-up: link → IP → routing → firewall → DNS → application. Most problems are DNS, then DHCP, then link.
2. Use `list_interfaces(device=<iface>)` to focus on a specific interface rather than listing all.
3. Use `list_sockets` with filters (port, state, protocol) rather than listing everything.
4. Cross-reference firewall rules with actual listeners — unused open ports are noise, blocked listeners explain "can't connect."
5. For WiFi: `get_nic_details(<wifi_iface>)` shows association state; `list_wifi_networks` for available networks.
6. Be suspicious of empty results — no interfaces may mean NM/networkd not running; no routes may mean DHCP failed; no firewall rules may mean firewalld not running.

## Safety

### Privilege

Stuart auto-escalates via polkit when configured.

| Command | When | Alternative |
|---------|------|------------|
| `ss -p` | Process info for other users' sockets | |
| `nft list ruleset` | Direct nftables access | Use `list_firewall_rules` instead |
| `ethtool` | Some queries need root | |
| `iw dev scan` | WiFi scanning | Use `nmcli device wifi list` (no root) |
| `traceroute` ICMP mode | Needs root | `tracepath` doesn't need root |
| `nmap` most scans | Raw sockets | `-sn` may work without root |

### High-risk operations

Before proceeding: state what's changing, provide the rollback command, get user confirmation.

- **DNS resolver changes**: `resolvectl domain`, `resolvectl dns`. The `~` prefix in `resolvectl domain` is critical — without it, the domain becomes a search domain and restricts the interface to only those domains.
- **Firewall rule modifications**: `iptables`, `nft`, `firewall-cmd`. Wrong rules can lock out the user.
- **Interface state changes**: `ip link`, `nmcli`. Taking an interface down drops all connections on it.

### Cross-references

- If a network service won't start → `linux-systemd-rules.md` "Service failure investigation"
- If network performance is poor but connectivity works → `linux-performance-rules.md` for system-level bottlenecks
- If container networking issues → `container-runtime-rules.md` (bridge, host, macvlan modes)
- If USB network adapter not appearing → `linux-serial-device-rules.md` for USB-level diagnosis
- If VM networking issues → `linux-virtual-rules.md` "Networking modes" (NAT, bridged, macvtap)

## Quirks

- **systemd-resolved stub resolver**: On systemd systems, `/etc/resolv.conf` points to `127.0.0.53`. Use `check_resolver` to see actual upstream DNS servers.
- **firewalld vs raw nftables**: firewalld generates nft rules. `nft list ruleset` on a firewalld system is verbose and hard to read. Prefer `list_firewall_rules` with a zone.
- **firewalld logging defaults**: Fedora/RHEL ship with `LogDenied=off`. Dropped packets produce NO log entries unless user enables logging.
- **NetworkManager vs systemd-networkd**: Mutually exclusive. Desktop=NM, server/minimal=networkd. If `nmcli` says "not running", check networkd.
- **`ss` state filters are case-sensitive**: Use lowercase: "established", "listen", "time-wait".
- **IPv6 link-local is always present**: Every interface has `fe80::/10` even without global IPv6. Normal.
- **WiFi scan results are ephemeral**: `list_wifi_networks` is a point-in-time snapshot. Networks may appear/disappear.
- **Container bridge interfaces**: `docker0`, `podman0`, `br-*` are virtual bridges — don't confuse with physical interfaces.
- **VPN interfaces**: WireGuard (`wg0`), OpenVPN (`tun0`/`tap0`) appear in routes and may override the default gateway.
- **`dig` not installed by default on Fedora/RHEL**: Requires `bind-utils` package.
- **Predictable interface names vary by distro**: Don't hardcode — discover with `list_interfaces`.

## Domain Deep Knowledge → linux-network-deep-knowledge.md

Read when:
- VPN-DNS troubleshooting (NordVPN, WireGuard, split DNS)
- User asks "why" or "how does this work" about networking concepts
- Need WiFi RF, NTP, NAT, proxy, or tunnelling knowledge
- Captive portal / hotel WiFi issues
- Edge case not covered in Quirks or Heuristics
