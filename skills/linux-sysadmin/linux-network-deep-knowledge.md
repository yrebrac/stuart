---
name: linux-network-deep-knowledge
description: >
    Extended networking reference. Read on demand when troubleshooting
    hits a dead end, user asks conceptual questions, or you need VPN-DNS,
    WiFi, NTP, or protocol deep-dives. NOT auto-loaded.
---

# Networking: Deep Knowledge

Extended reference for the networking domain. Read when directed by the rules file's Domain Deep Knowledge section.

## Contents

- [Core Concepts](#core-concepts)
- [VPN-DNS Troubleshooting](#vpn-dns-troubleshooting)
- [NTP / Time Synchronization](#ntp--time-synchronization)
- [Captive Portals](#captive-portals)
- [OSI Model](#osi-model)
- [Protocols and Concepts](#protocols-and-concepts)
- [VPN Types](#vpn-types)
- [Firewall Models](#firewall-models)
- [WiFi and RF](#wifi-and-rf)
- [Container Networking](#container-networking)
- [Legacy Tools](#legacy-tools)
- [ISP Concepts](#isp-concepts)

## Core Concepts

**Interface naming**: Modern Linux uses predictable names based on hardware path (e.g., `enp0s3` = Ethernet, PCI bus 0, slot 3; `wlp2s0` = WiFi, PCI bus 2, slot 0). Legacy names (`eth0`, `wlan0`) still appear on some systems. Virtual interfaces include `lo` (loopback), `docker0`/`podman0` (container bridges), `virbr0` (libvirt), `wg0` (WireGuard), `tun0`/`tap0` (VPN tunnels).

**IPv4/IPv6 dual-stack**: Most modern Linux runs both. Every interface has an IPv6 link-local address (`fe80::/10`) even without global IPv6 — this is normal, not a misconfiguration.

**CIDR notation**: `/24` = 256 addresses (e.g., 192.168.1.0/24), `/32` = single host, `/16` = 65,536 addresses. Legacy "Class C" = /24, "Class B" = /16, "Class A" = /8 — obsolete but users still reference them.

**Default gateway**: The router that handles traffic to destinations not on any local subnet. Visible in `list_routes` as the route with no specific destination prefix (or `default`).

**DNS resolution chain**: Application → glibc NSS (consults `/etc/nsswitch.conf`) → stub resolver (`127.0.0.53` if systemd-resolved) → upstream DNS servers. `/etc/resolv.conf` on systemd systems typically points to the stub resolver, not the real upstream — use `check_resolver` to see actual DNS servers.

**Socket states**: `LISTEN` (server waiting), `ESTABLISHED` (active connection), `TIME-WAIT` (closed, waiting for late packets), `CLOSE-WAIT` (remote closed, local hasn't yet — potential resource leak if many accumulate).

## VPN-DNS Troubleshooting

VPN clients commonly override DNS to prevent DNS leaks. This creates conflicts with local/private DNS resolvers.

### Diagnostic sequence

1. **Capture resolver state with VPN connected**: `resolvectl status` (full output, all interfaces). Look for:
   - VPN interface (nordlynx, tun0, wg0) with `DNS Domain: ~.` — catches ALL DNS queries
   - Physical interface DNS server — is your local resolver still listed?
   - `Default Route: yes` on multiple interfaces — DNS routing conflict

2. **Test DNS server reachability directly**: `dig +short <domain> @<local_dns_ip>` — bypasses systemd-resolved. If this times out, the problem is firewall or routing, not DNS config.

3. **Check for VPN firewall rules**: `sudo iptables-save | grep -i dns` and `sudo iptables-save | grep 'dport 53'`. Some VPN clients inject OUTPUT chain DROP rules for port 53 to private IP ranges as "DNS leak prevention."

4. **Verify routing**: `ip route get <dns_server_ip>` — is the DNS server routed via the physical interface (correct for split tunnel) or the VPN interface (incorrect)?

### Common VPN-DNS patterns

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| All DNS goes through VPN | VPN sets `~.` catch-all on its interface | Set routing domains on physical interface for private zones |
| `dig @local-ip` times out | VPN firewall blocks port 53 to private IPs | Disable VPN's firewall or add iptables exception |
| Private zones fail, public works | Split DNS not configured | `resolvectl domain <phys_iface> ~<zone>` |
| DNS works after fix but resets on reconnect | VPN reconfigures DNS on each connect | NM dispatcher script to reapply settings |

### NordVPN-specific

- `deny-private-dns` iptables rules DROP all port 53 traffic to RFC1918 ranges, regardless of subnet allowlist
- `nordvpn set dns <private-ip>` is accepted but creates no iptables exception — confirmed bug (GitHub Issue #501)
- nordlynx interface gets `~.` catch-all DNS domain, overriding local DNS routing
- Workaround: `nordvpn set firewall disabled` + resolvectl routing domains via NM dispatcher script

### Split DNS with systemd-resolved

When a VPN sets `~.` on its interface, systemd-resolved routes ALL queries there. To route specific zones to your local resolver:

```
sudo resolvectl domain <phys_iface> ~myzone.local ~10.in-addr.arpa
```

The `~` prefix is critical — it marks a **routing domain**. Without `~`, it becomes a **search domain**, which restricts the interface to ONLY those domains and breaks all other DNS resolution.

### Circular dependency warning

Never set a VPN's DNS to a private IP unless that IP is reachable outside the tunnel. If the VPN needs DNS to establish the tunnel and DNS goes through the tunnel, all connectivity dies.

## NTP / Time Synchronization

NTP doesn't require dedicated MCP tools. Investigate via Bash:

- `timedatectl` — NTP sync status, timezone, system clock vs RTC
- `chronyc tracking` — detailed NTP sync info (offset, stratum, reference source)
- `chronyc sources -v` — NTP source servers with quality metrics
- `timedatectl show-timesync` — systemd-timesyncd config (if using timesyncd instead of chrony)

Common issues:
- Clock drift > 100ms — NTP may not be running or can't reach servers
- Firewall blocking UDP 123 (NTP port)
- `chronyd` vs `systemd-timesyncd` — only one should be active

## Captive Portals

Connected to hotel/airport WiFi but can't browse — captive portal login page doesn't appear.

1. Verify WiFi connection: `get_nic_details(<wifi_iface>)` — associated, has IP?
2. Try HTTP (not HTTPS) pages to trigger redirect:
   - `http://neverssl.com`, `http://captive.apple.com`, `http://1.1.1.1`
3. If DNS broken: try gateway IP directly in browser
4. NM has connectivity check feature: `nmcli general` shows connectivity state

## OSI Model

Physical (1) → Data Link (2) → Network (3) → Transport (4) → Session (5) → Presentation (6) → Application (7). In practice, relevant layers for Linux: link (2), network (3: IP), transport (4: TCP/UDP), application (7: HTTP, DNS).

## Protocols and Concepts

**ICMP**: Control protocol — ping (echo), traceroute (TTL exceeded), destination unreachable. Blocking ICMP entirely breaks path MTU discovery.

**Multicast / Broadcast**: Broadcast = all devices on subnet (IPv4 only). Multicast = specific group (224.0.0.0/4). Common multicast: mDNS (224.0.0.251), SSDP/UPnP (239.255.255.250). IPv6 uses multicast exclusively.

**NAT**: Source NAT / masquerade (home router), destination NAT / port forwarding. Linux NAT uses netfilter/nftables.

**Proxying**: Forward proxy (client knows about proxy). Reverse proxy (client thinks proxy IS the server — nginx, HAProxy, Squid).

**Tunnelling**: GRE, 6to4/6rd (IPv6 over IPv4), VXLAN (overlay for containers/VMs), IP-in-IP.

## VPN Types

- **WireGuard**: Modern, fast, kernel-native. Creates `wg0`. Config in `/etc/wireguard/`.
- **OpenVPN**: Mature, widely supported. Creates `tun0`/`tap0`. Config in `/etc/openvpn/`.
- **IPsec** (strongSwan, Libreswan): Enterprise/site-to-site. Uses ESP protocol, not TCP/UDP.
- **L2TP**: Often paired with IPsec. Less common on Linux desktops.

## Firewall Models

- **firewalld** — zone-based. Interfaces assigned to zones (public, home, trusted). Each zone defines allowed services/ports. Standard on Fedora/RHEL.
- **nftables** — kernel framework. firewalld generates nft rules. Raw nft rules are verbose but flexible.
- **iptables** — legacy interface to netfilter. Still works but nftables is the modern replacement.

## WiFi and RF

2.4GHz (longer range, more interference, channels 1/6/11 non-overlapping), 5GHz (shorter range, less interference, many channels), 6GHz (WiFi 6E). BSSID = AP MAC. SSID = network name. Channel width: 20/40/80/160 MHz — wider = faster but more interference-prone.

## Container Networking

Docker/Podman default "bridge" mode is actually NAT. `docker0`/`podman0` connects containers on a private subnet; port mappings (`-p 8080:80`) create DNAT rules. Host mode shares host's namespace entirely. Macvlan gives containers real addresses on the physical network.

## Legacy Tools

`ifconfig` → `ip addr`, `netstat` → `ss`, `route` → `ip route`, `arp` → `ip neigh`. If a user references legacy tools, translate to modern equivalents.

## ISP Concepts

Oversubscription (normal), contention ratio, looking glasses (public route servers for BGP). Traceroute is often useless for ISP issues because ICMP is deprioritised.

## Consumer vs Enterprise

Home routers combine router + switch + WiFi AP + NAT + DHCP + DNS in one device. Enterprise separates these. This skill targets the Linux host, not network infrastructure — but understanding the user's context helps.
