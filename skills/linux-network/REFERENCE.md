# Network Reference

Extended reference material for networking domain knowledge. The main skill (SKILL.md) covers session start, common tasks, tool selection, and query strategies. This file contains background knowledge, detailed troubleshooting workflows, and known quirks.

## Network Stack Mental Model

```
Physical NIC → Link layer (MAC, speed, duplex) → IP layer (addresses, subnets) →
  Routing → Firewall (netfilter/nftables/firewalld) → Application (sockets, ports)
```

Helper layers that sit alongside this stack:
- **NetworkManager** — manages connection profiles, DHCP, WiFi, VPN. The standard on desktops/workstations.
- **systemd-networkd** — alternative network manager, common on servers and embedded. Mutually exclusive with NM.
- **systemd-resolved** — DNS stub resolver, DNSSEC, per-interface DNS config. Runs alongside either NM or networkd.
- **wpa_supplicant** — WiFi authentication (WPA/WPA2/WPA3, 802.1X). Usually managed by NetworkManager transparently.
- **dhclient / dhcpcd** — DHCP clients. NM handles DHCP internally; these are standalone alternatives.

## Core Concepts

**Interface naming**: Modern Linux uses predictable names based on hardware path (e.g., `enp0s3` = Ethernet, PCI bus 0, slot 3; `wlp2s0` = WiFi, PCI bus 2, slot 0). Legacy names (`eth0`, `wlan0`) still appear on some systems. Virtual interfaces include `lo` (loopback), `docker0`/`podman0` (container bridges), `virbr0` (libvirt), `wg0` (WireGuard), `tun0`/`tap0` (VPN tunnels).

**IPv4/IPv6 dual-stack**: Most modern Linux runs both. Every interface has an IPv6 link-local address (`fe80::/10`) even without global IPv6 — this is normal, not a misconfiguration.

**CIDR notation**: `/24` = 256 addresses (e.g., 192.168.1.0/24), `/32` = single host, `/16` = 65,536 addresses. Legacy "Class C" = /24, "Class B" = /16, "Class A" = /8 — these terms are obsolete but users still reference them.

**Default gateway**: The router that handles traffic to destinations not on any local subnet. Visible in `list_routes` as the route with no specific destination prefix (or `default`).

**DNS resolution chain**: Application → glibc NSS (consults `/etc/nsswitch.conf`) → stub resolver (`127.0.0.53` if systemd-resolved) → upstream DNS servers. `/etc/resolv.conf` on systemd systems typically points to the stub resolver, not the real upstream — use `check_resolver` to see actual DNS servers.

**Firewall models**:
- **firewalld** — zone-based. Interfaces are assigned to zones (public, home, trusted, etc.). Each zone defines allowed services/ports. Standard on Fedora/RHEL.
- **nftables** — the kernel framework. firewalld generates nft rules. Raw nft rules are harder to read but more flexible.
- **iptables** — legacy interface to netfilter. Still works but nftables is the modern replacement.

**Socket states**: `LISTEN` (server waiting for connections), `ESTABLISHED` (active connection), `TIME-WAIT` (connection closed, waiting for late packets), `CLOSE-WAIT` (remote closed, local hasn't yet — potential resource leak if many accumulate).

## Detailed Troubleshooting Workflows

### WiFi troubleshooting

1. `list_interfaces` — is the WiFi interface present and UP?
2. `get_nic_details(<wifi_iface>)` — is it associated? What's the signal strength?
3. `list_wifi_networks` — is the target network visible? What's the signal level?
4. `list_connections(device=<wifi_iface>)` — are there NM profiles for this network?
5. If connected but no internet: run the "network is down" workflow from SKILL.md

Common WiFi issues: driver not loaded (interface not present), rfkill blocking radio, weak signal, wrong password stored in NM profile, 5GHz band not supported by device, channel congestion.

### "Is my firewall too open?"

1. `check_firewall_zones` — which zones are active? Which interfaces are in which zones?
2. `list_firewall_rules` — what's allowed in each active zone?
3. Check for common issues:
   - Is the public-facing interface in the `public` zone (not `trusted` or `home`)?
   - Are only necessary services/ports open?
   - Are there any catch-all rich rules allowing broad access?
   - Is the default zone appropriate? (`firewall-cmd --get-default-zone`)
4. `list_sockets(listening=True)` — compare what's actually listening vs what the firewall allows
5. Unused open ports (firewall allows but nothing listening) are noise but not a risk. Services listening on ports not allowed by the firewall are protected but may explain "can't connect" issues.

### Captive portals / hotel WiFi

A common Linux user issue: connected to hotel/airport WiFi but can't browse. The captive portal login page doesn't appear automatically.

1. Verify WiFi connection: `get_nic_details(<wifi_iface>)` — associated, has IP?
2. Try browsing to a known HTTP (not HTTPS) page to trigger the portal redirect:
   - `http://neverssl.com` — designed for this purpose
   - `http://captive.apple.com` — Apple's detection endpoint
   - `http://1.1.1.1` — Cloudflare (sometimes works)
3. If DNS isn't working: the portal may be intercepting DNS. Try the gateway IP directly in a browser.
4. Some portals require MAC-based authentication to be renewed periodically.
5. NetworkManager has a connectivity check feature (`nmcli general` shows connectivity state).

### Investigating a specific interface

1. `list_interfaces(device=<iface>)` — IP addresses, state
2. `list_interfaces(device=<iface>, show_stats=True)` — packet counters, errors, drops
3. `get_nic_details(<iface>)` — hardware: speed, duplex, driver
4. `list_routes(device=<iface>)` — routes via this interface
5. `list_neighbors(device=<iface>)` — who else is on the same segment

### Port and service discovery (detailed)

1. `list_sockets(listening=True)` — what services are listening?
2. `list_sockets(listening=True, protocol="tcp", port="443")` — check a specific port
3. `list_sockets(listening=True, process=True)` — identify which processes own each listener (may need sudo)
4. `list_firewall_rules` — is the firewall allowing inbound traffic to that port?
5. Cross-check: is the firewalld zone for the relevant interface permitting the service?

### DNS troubleshooting (detailed)

1. `check_resolver` — what's the resolver config?
2. `check_dns(domain)` — does the default resolver answer?
3. `check_dns(domain, server="8.8.8.8")` — does a known-good public resolver answer?
4. `check_dns(domain, server="1.1.1.1")` — try a second public resolver
5. Compare results — if external works but local doesn't, the problem is the local resolver or its upstream
6. Check `check_dns(domain, record_type="NS")` — are the authoritative nameservers responding?

Common DNS issues: stale cache (restart systemd-resolved), misconfigured search domain, upstream DNS server down, DNSSEC validation failure, split-horizon DNS returning wrong answers.

### Post-Outage Diagnosis

When the user reports a recent outage or connectivity loss:

1. **Establish current state**: Run `check_reachability` to get a full-stack diagnostic. The classification tells you where the problem was (or still is).

2. **Check recent network events** via MCP tools (preferred) or journal logs:
   - NetworkManager events: `search_journals(unit="NetworkManager", since="1h")` — connection up/down, DHCP renewals, WiFi association changes
   - Kernel link state: `search_journals(identifier="kernel", grep="link", since="1h")` — interface carrier up/down
   - DNS resolver: `search_journals(unit="systemd-resolved", since="1h")` — DNS server changes, failures

3. **Ask follow-up questions**:
   - What specifically stopped working? One app, all apps, or just Claude?
   - Is the problem intermittent or was it a solid outage?
   - Did anything change? VPN connected/disconnected, WiFi reconnected, moved location?
   - Did others on the same network have issues? (helps separate local from ISP)

4. **Check for VPN interference**: `check_reachability` flags active VPNs. If a VPN is active:
   - Is the VPN itself the cause? (tunnel failed, split-tunnelling misconfigured)
   - Does the problem persist with VPN disconnected?
   - Is DNS being routed through the VPN? (common cause of "DNS works sometimes")

5. **Classify the outage**:
   - **Local** (L1-2 fail): Interface down, DHCP failure, cable/WiFi disconnect
   - **LAN** (L2 pass, L3 fail): Local DNS or DHCP server issue
   - **ISP** (L1-3 pass, L4 fail): Upstream provider issue, router failure
   - **DNS-only** (L4 pass, L3/5 fail): DNS server down, DNSSEC issue
   - **Service-specific** (L1-6 pass, L7 fail): Remote service (e.g. Claude API) outage
   - **Claude Code note**: After extended outages, Claude Code may fail to reconnect even after the network recovers (stale HTTP/2 connections). The user should restart Claude and resume with `claude --continue`.

6. **Check interface error counters**: `list_interfaces(show_stats=True)` — look for RX/TX errors, drops, or overruns that may indicate intermittent physical issues.

## Cross-Skill References

- **systemd**: Network-related systemd units and targets:
  - `network.target`, `network-online.target` — ordering targets for network-dependent services
  - `systemd-networkd.service` — alternative to NetworkManager
  - `systemd-resolved.service` — DNS resolver service
  - `hostnamectl` — hostname management (use via systemd tools)
  - Use the systemd skill's `get_unit_status`, `check_active` for service status
- **journald**: Network-related journal logs:
  - `NetworkManager` — connection events, DHCP leases, WiFi association
  - `systemd-networkd` — link state changes
  - `wpa_supplicant` — WiFi authentication events
  - `firewalld` — rule changes, zone transitions (but NOT packet logs by default — see Known Quirks)
  - Use the systemd skill's `search_journals` with grep patterns for these
- **containers**: Virtual bridge interfaces (`docker0`, `podman0`, `br-*`) appear as regular interfaces. Container networking modes:
  - **bridge** (default): NAT'd network, containers get private IPs, port mapping required. Note: Docker calls this "bridge" but it's really NAT — not the same as network bridging.
  - **host**: Container shares the host's network namespace entirely. Simplest but least isolated.
  - **macvlan**: Container gets its own MAC address on the physical network. Most transparent to the network.
  - Use the container specialist for container-specific networking queries.
- **USB/serial**: USB network adapters (USB-Ethernet, USB-WiFi dongles) appear as network interfaces. If a USB NIC isn't showing up, the issue may be at the USB level, not the network level.

## NTP / Time Synchronization

NTP is a network protocol but doesn't require dedicated MCP tools. Investigate time sync issues via Bash:

- `timedatectl` — shows NTP sync status, timezone, system clock vs RTC
- `chronyc tracking` — detailed NTP sync info (offset, stratum, reference source)
- `chronyc sources -v` — NTP source servers with quality metrics
- `timedatectl show-timesync` — systemd-timesyncd config (if using timesyncd instead of chrony)

Common issues:
- Clock drift > 100ms — NTP may not be running or can't reach servers
- Firewall blocking UDP 123 (NTP port)
- `chronyd` vs `systemd-timesyncd` — only one should be active

## Privilege Escalation

Stuart auto-escalates via polkit when configured. Without polkit, these commands may need root:
- `ss -p` (process info) — needs sudo to see processes owned by other users
- `nft list ruleset` — may need sudo depending on nftables permissions
- `ethtool` — some queries need root (register dumps, driver flash info)
- `iw dev <iface> scan` — WiFi scanning usually needs root. Use `nmcli device wifi list` instead (NM does the scan with its own privileges)
- `traceroute` — some modes (ICMP) need root. `tracepath` doesn't need root.
- `nmap` — most scans need root for raw sockets. `-sn` (ping scan) may work without root.

See PRIVILEGES.md for polkit setup.

## Known Quirks

- **Predictable interface names vary by distro**: Fedora/RHEL use `enp*`/`wlp*`. Some distros still use `eth0`/`wlan0`. Don't hardcode names — discover with `list_interfaces`.
- **systemd-resolved stub resolver**: On systemd systems, `/etc/resolv.conf` typically points to `127.0.0.53`. This is the stub resolver, not the real DNS server. Use `check_resolver` to see actual upstream DNS servers.
- **firewalld vs raw nftables**: firewalld generates nft rules. The raw `nft list ruleset` on a firewalld system shows these generated rules — they're verbose and hard to read. Prefer `list_firewall_rules` with a zone, which uses firewalld's abstraction.
- **firewalld logging defaults**: Fedora/RHEL ship with `LogDenied=off`. This means dropped packets produce NO log entries. Users who expect firewall logs must enable logging via `firewall-cmd --set-log-denied=all` (or per-rule rich rule logging). This is a config decision, not something the tool can change.
- **NetworkManager vs systemd-networkd**: Mutually exclusive. Desktop systems typically run NM. Server/minimal installs may use networkd or manual config. If `nmcli` reports "NetworkManager is not running", check for networkd or manual /etc/sysconfig/network-scripts/.
- **`ss` state filters are case-sensitive**: Use lowercase: "established", "listen", "time-wait". Not "ESTABLISHED".
- **IPv6 link-local is always present**: Every interface has an `fe80::/10` address even without global IPv6 connectivity. This is normal.
- **WiFi scan results are ephemeral**: `list_wifi_networks` shows a point-in-time snapshot. Networks may appear/disappear between scans. Signal strength varies with position and interference.
- **Container bridge interfaces**: `docker0`, `podman0`, `br-*` are virtual bridges created by container runtimes. They have IPs and routes but aren't physical connectivity. Don't confuse them with real interfaces when troubleshooting.
- **VPN interfaces**: WireGuard (`wg0`), OpenVPN (`tun0`/`tap0`) appear in `list_interfaces` and `list_routes`. VPN routes may override the default gateway, redirecting all traffic through the VPN.
- **`dig` not installed by default**: On Fedora/RHEL, `dig` requires the `bind-utils` package. If missing, the `check_dns` tool will report this and suggest installation.
- **`ping` to hostnames uses DNS**: If DNS is broken, `ping hostname` fails but `ping IP` works. This distinguishes DNS issues from connectivity issues.

## OSI Model

Physical (1) → Data Link (2) → Network (3) → Transport (4) → Session (5) → Presentation (6) → Application (7). In practice, the relevant layers for Linux networking are: link (2: Ethernet, WiFi), network (3: IP), transport (4: TCP/UDP), and application (7: HTTP, DNS, etc.).

## Multicast / Broadcast

Broadcast = all devices on a subnet (e.g., 192.168.1.255). Multicast = specific group of subscribers (224.0.0.0/4). Common multicast: mDNS (224.0.0.251), SSDP/UPnP (239.255.255.250). Broadcast is IPv4-only; IPv6 uses multicast exclusively.

## ICMP

Control protocol — ping (echo request/reply), traceroute (TTL exceeded), destination unreachable, redirect. Blocking ICMP entirely breaks path MTU discovery and makes troubleshooting harder.

## VPN Types

- **WireGuard**: Modern, fast, kernel-native. Creates `wg0` interface. Config in `/etc/wireguard/`.
- **OpenVPN**: Mature, widely supported. Creates `tun0`/`tap0`. Config in `/etc/openvpn/`.
- **IPsec** (strongSwan, Libreswan): Enterprise/site-to-site. Complex config. Uses ESP protocol, not TCP/UDP.
- **L2TP**: Often paired with IPsec. Less common on Linux desktops.

## VPN-DNS Troubleshooting

VPN clients commonly override DNS to prevent DNS leaks. This creates conflicts with local/private DNS resolvers.

### Diagnostic sequence

1. **Capture resolver state with VPN connected**: `resolvectl status` (full output, all interfaces). This is the single most important diagnostic for VPN-DNS issues. Look for:
   - VPN interface (nordlynx, tun0, wg0) with `DNS Domain: ~.` — this catches ALL DNS queries
   - Physical interface DNS server — is your local resolver still listed?
   - `Default Route: yes` on multiple interfaces — DNS routing conflict

2. **Test DNS server reachability directly**: `dig +short <domain> @<local_dns_ip>` — bypasses systemd-resolved. If this times out, the problem is firewall or routing, not DNS config.

3. **Check for VPN firewall rules**: `sudo iptables-save | grep -i dns` and `sudo iptables-save | grep 'dport 53'`. Some VPN clients inject OUTPUT chain DROP rules for port 53 to private IP ranges as "DNS leak prevention."

4. **Verify routing**: `ip route get <dns_server_ip>` — is the DNS server routed via the physical interface (correct for split tunnel) or the VPN interface (incorrect)?

### Common VPN-DNS patterns

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| All DNS goes through VPN | VPN sets `~.` catch-all routing domain on its interface | Set routing domains on physical interface for private zones |
| `dig @local-ip` times out | VPN firewall blocks port 53 to private IPs | Disable VPN's firewall or add iptables exception |
| Private zones fail, public works | Split DNS not configured | `resolvectl domain <phys_iface> ~<zone>` |
| DNS works after manual fix but resets on reconnect | VPN reconfigures DNS on each connect | NetworkManager dispatcher script to reapply settings |

### NordVPN-specific

NordVPN (Linux CLI, NordLynx/WireGuard) has known DNS issues:
- `deny-private-dns` iptables rules DROP all port 53 traffic to RFC1918 ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), regardless of subnet allowlist
- `nordvpn set dns <private-ip>` is accepted but creates no iptables exception — confirmed bug (GitHub Issue #501, open since v3.17.4)
- nordlynx interface gets `~.` catch-all DNS domain, overriding local DNS routing
- Workaround: `nordvpn set firewall disabled` (removes iptables rules; system firewall is independent) + resolvectl routing domains via NetworkManager dispatcher script

### Split DNS with systemd-resolved

When a VPN sets `~.` on its interface, systemd-resolved routes ALL queries there. To route specific zones to your local resolver:

```
sudo resolvectl domain <phys_iface> ~myzone.local ~10.in-addr.arpa
```

The `~` prefix is critical — it marks a **routing domain** (queries for this zone go to this interface's DNS server). Without `~`, it becomes a **search domain**, which restricts the interface to ONLY those domains and breaks all other DNS resolution.

### Circular dependency warning

Never set a VPN's DNS to a private IP unless that IP is reachable outside the tunnel. If the VPN needs DNS to establish the tunnel and DNS is configured to go through the tunnel, all connectivity dies. Before changing VPN DNS settings: verify the DNS server's subnet is in the VPN allowlist AND reachable on the physical interface.

## Tunnelling

GRE (generic routing encapsulation — IP-in-IP), 6to4 / 6rd (IPv6 over IPv4), VXLAN (overlay networking for containers/VMs), IP-in-IP.

## NAT

Source NAT (masquerade — your home router does this), destination NAT (port forwarding — external port → internal host:port). Linux NAT uses netfilter/nftables.

## Proxying

Forward proxy (client→proxy→server, client knows about proxy). Reverse proxy (client→proxy→server, client thinks proxy IS the server). nginx, HAProxy, Squid are common Linux proxies. Reverse proxying is fundamental to web hosting.

## Legacy Tools

`ifconfig` (replaced by `ip addr`), `netstat` (replaced by `ss`), `route` (replaced by `ip route`), `arp` (replaced by `ip neigh`). If a user references these, translate to modern equivalents.

## Container Networking Details

Docker/Podman default "bridge" mode is actually NAT. The `docker0`/`podman0` bridge connects containers on a private subnet; port mappings (`-p 8080:80`) create DNAT rules. Host mode bypasses this entirely. Macvlan gives containers real addresses on the physical network.

## Consumer vs Enterprise

Home routers combine router + switch + WiFi AP + NAT + DHCP + DNS in one device. Enterprise separates these functions. This skill targets the Linux host, not the network infrastructure — but understanding the user's network context helps troubleshooting.

## RF and WiFi

2.4GHz (longer range, more interference, channels 1/6/11 non-overlapping), 5GHz (shorter range, less interference, many more channels), 6GHz (WiFi 6E, newest). BSSID = access point MAC address. SSID = network name. Roaming = moving between APs with the same SSID. Channel width: 20/40/80/160 MHz — wider = faster but more prone to interference.

## ISP Concepts

Oversubscription (ISP sells more bandwidth than they have upstream — normal), contention ratio, looking glasses (public route servers for checking BGP from the ISP's perspective). Traceroute is often useless for diagnosing ISP issues because ICMP is deprioritised.
