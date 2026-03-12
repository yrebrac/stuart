---
name: linux-network
description: >
    Domain knowledge for network diagnostics, DNS, routing, firewall
    inspection, WiFi, and connectivity testing using the network MCP
    server. Load BEFORE using any network MCP tool directly. Covers
    interfaces, sockets, NetworkManager, and path tracing.
---

# Networking

## Session Start

1. Call `tool_info()` to see which network tools are available
2. Note missing tools — some (nmap, ethtool, iw) require package installation
3. Determine the network stack: is NetworkManager running (`nmcli` available)? Or systemd-networkd? Or manual config?
4. If investigating a specific issue, identify the relevant interface(s) first with `list_interfaces`

## Common Tasks

### Verify current network (do this first)

Before any network troubleshooting, confirm the machine is on the expected network:
1. `list_interfaces` — which interfaces are up? Do they have IPs?
2. `get_nic_details(<wifi_iface>)` — if WiFi, which SSID? (catches mobile hotspot, wrong network)
3. `check_resolver` — which DNS servers are configured per interface?

Do NOT skip this step. A user who switched networks (e.g. mobile hotspot) without realising will invalidate all subsequent diagnostics.

### Network baseline

When about to test a fix or compare before/after, capture:
- `check_resolver` — full per-interface DNS servers and routing domains
- `list_routes` — routing table
- VPN connection status (if applicable)
- Interface state and SSID (from verify step above)

This is what "baseline first" means in the network domain.

### "Network is down" / connectivity issues

Start with `check_reachability` — it runs a full bottom-up diagnostic in one call: link → gateway → local DNS → internet (ICMP) → internet DNS → HTTPS → Claude API. Each layer runs 2+ tests to isolate the problem class. The output includes a classification (e.g. `LOCAL_DNS_FAILURE`, `INTERNET_UNREACHABLE`).

If `check_reachability` is not available, or for manual follow-up:

1. `list_interfaces` — is the interface up? Does it have an IP?
2. `list_routes` — is there a default gateway?
3. `check_connectivity(<gateway_ip>)` — can we reach the gateway?
4. `check_connectivity("8.8.8.8")` — can we reach the internet? (bypasses DNS)
5. `check_dns("google.com")` — is DNS working?

### "I just had an outage" / post-outage

1. Verify current network (see above) — is the user on the expected SSID/interface?
2. `check_reachability` — establish current state
3. Ask the user: what specifically stopped working? One app, all apps, just Claude?
4. Check recent network events — see REFERENCE.md "Post-Outage Diagnosis" for journal queries
5. Check for VPN interference — `check_reachability` flags active VPNs

### "Can't reach X"

1. `check_dns(X)` — does the name resolve?
   - If **NXDOMAIN** (domain not found): the domain may not exist. Before troubleshooting DNS infrastructure, verify the domain is real:
     - `check_dns(X, record_type="NS")` — are nameservers delegated? No NS = domain isn't in DNS at all.
     - `check_dns(X, record_type="SOA")` — does the zone have authority? No SOA confirms no delegation.
     - If no NS and no SOA → tell the user the domain doesn't appear to exist. Ask them to verify spelling. Don't troubleshoot further.
     - If NS exists but no A/AAAA → domain is registered but DNS is misconfigured (missing records, wrong zone file). This is a server-side issue, not a local one.
   - If **timeout** or **SERVFAIL**: DNS infrastructure problem. Continue to "DNS issues" workflow.
   - If resolves successfully: continue below.
2. `check_connectivity(X)` — is there basic IP reachability?
3. `list_routes` — is there a route to the destination?
4. `list_firewall_rules` — is the local firewall blocking outbound?
5. `check_path(X)` — where does the path die?

If ping works but the service doesn't respond, the issue is above the network layer.

### "DNS issues"

1. `check_resolver` — what's the resolver config?
2. `check_dns(domain)` — does the default resolver answer?
3. `check_dns(domain, server="8.8.8.8")` — does a public resolver answer?
4. If external works but local doesn't → local resolver or upstream problem

If a VPN is active:
5. `check_resolver` — look for VPN interface (nordlynx, tun0, wg0) with `~.` catch-all DNS domain — this overrides all other DNS routing
6. Check firewall: `list_firewall_rules` and via Bash `sudo iptables-save | grep 'dport 53'` — VPN software may inject rules blocking DNS to private IPs
7. Test DNS server reachability directly: `dig +short <domain> @<dns_server_ip>` — bypasses systemd-resolved entirely
8. If direct dig times out → problem is firewall or routing, not DNS config. See REFERENCE.md "VPN-DNS Troubleshooting"

### "What's on port X?"

1. `list_sockets(listening=True, port="X")` — who's listening?
2. `list_sockets(listening=True, process=True)` — which process? (may need sudo)
3. `list_firewall_rules` — is the firewall allowing traffic to that port?

For detailed troubleshooting workflows (WiFi, captive portals, firewall audits, interface investigation), networking concepts, and known quirks, read REFERENCE.md in this skill directory.

## Tool Selection

| Goal | Tool |
|------|------|
| What interfaces exist? IPs? | `list_interfaces` |
| Interface packet/error stats? | `list_interfaces(show_stats=True)` |
| Routing table / default gateway? | `list_routes` |
| IPv6 routes? | `list_routes(family="inet6")` |
| ARP table / neighbor cache? | `list_neighbors` |
| What's listening on which ports? | `list_sockets(listening=True)` |
| Active TCP connections? | `list_sockets(state="established")` |
| What's using port 443? | `list_sockets(port="443")` |
| Who owns a socket? | `list_sockets(process=True)` (may need sudo) |
| DNS lookup | `check_dns` |
| DNS resolver configuration | `check_resolver` |
| Can I reach host X? | `check_connectivity` |
| Full connectivity diagnostic? | `check_reachability` |
| Firewall rules for a zone | `list_firewall_rules` |
| Active firewall zones | `check_firewall_zones` |
| All zones with full details | `check_firewall_zones(active_only=False)` |
| NM connection profiles | `list_connections` |
| Available WiFi networks | `list_wifi_networks` |
| NIC speed/driver/firmware | `get_nic_details` |
| WiFi signal/frequency/SSID | `get_nic_details(<wifi_iface>)` |
| Network path to remote host | `check_path` |
| Tool versions | `tool_info` |
| Man page details | `read_manual` |

## Query Strategy

### Layer isolation

Most network problems are DNS, followed by DHCP (no IP), followed by actual link issues. Always work bottom-up: link → IP → routing → firewall → DNS → application.

### Efficient queries

- Use `list_interfaces(device=<iface>)` to focus on a specific interface
- Use `list_sockets` with filters (port, state, protocol) rather than listing everything
- Cross-reference firewall rules with actual listeners — unused open ports are noise, blocked listeners explain "can't connect"
- For WiFi: `get_nic_details(<wifi_iface>)` shows association state and signal; `list_wifi_networks` for available networks

### Be suspicious of empty results

- No interfaces? Check if NetworkManager or systemd-networkd is running
- No routes? DHCP may have failed — check interface has an IP first
- No firewall rules? firewalld may not be running — check with `check_firewall_zones`

## Preferences & Safety

- **Layer isolation first** — don't jump to conclusions. Work through the stack systematically.
- **Don't assume DNS** — always test with IP (`8.8.8.8`) to separate DNS from connectivity
- **Privilege may be needed** for: `ss -p` (process info), `nft list ruleset`, `iw dev scan`. Stuart auto-escalates via polkit when configured. If not, error messages include manual `sudo` commands. Use `nmcli device wifi list` instead of `iw scan` where possible (no root needed). `firewall-cmd` has its own polkit policy.
- **firewalld logging is off by default** on Fedora/RHEL (`LogDenied=off`). Dropped packets produce no logs unless the user enables logging.
- **NTP issues**: Use Bash for `timedatectl` and `chronyc` — no MCP tools needed for time sync.
- **High-risk operations**: DNS resolver changes (`resolvectl domain`, `resolvectl dns`, editing `/etc/resolv.conf`), firewall rule modifications (`iptables`, `nft`, `firewall-cmd`), and interface state changes (`ip link`, `nmcli`) can make things worse. Before proceeding: state what's changing, provide the rollback command, get user confirmation. The `~` prefix in `resolvectl domain` is critical — without it, the domain becomes a search domain and restricts the interface to only those domains.
