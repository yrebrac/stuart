---
name: container-runtime
description: >
    Domain knowledge for Docker and Podman container administration using
    the container MCP server. Load BEFORE using any container MCP tool
    directly. Covers container lifecycle, images, volumes, networks,
    compose stacks, and troubleshooting.
---

# Container Runtime

## Guide

This file covers Docker and Podman container administration.

- **Domain Model** — containers, images, volumes, networks, compose, Docker vs Podman
- **Heuristics** — expert shortcuts for container troubleshooting
- **Anti-patterns** — common mistakes with container diagnostics
- **Procedures** — workflows for startup failures, crashes, networking, resource issues, compose
- **Tools** — goal-to-tool lookup for the container MCP server
- **Query Strategy** — scope-first investigation, efficient tool use
- **Safety** — privilege, rootless considerations, cross-domain pointers
- **Quirks** — rootless port binding, compose project names, stats on stopped containers
- **Domain Deep Knowledge** — Docker vs Podman differences (inline)

## Domain Model

**Containers**: Running instances of images. States: created, running, paused, exited, dead.

**Images**: Read-only templates. Layers shared between images. Identified by repository:tag or ID.

**Volumes**: Persistent data outliving containers. Named volumes (runtime-managed) vs bind mounts (host path).

**Networks**: Isolated network namespaces. Same-network containers reach each other by name. Default bridge network does NOT provide DNS between containers.

**Compose stacks**: Multi-container apps from `docker-compose.yml` / `compose.yaml`. Services, networks, volumes scoped to project.

## Heuristics

1. Container won't start? Check exit code first — it tells you the category: 0 = clean exit, 1 = app error, 137 = OOMKilled or SIGKILL, 139 = segfault, 126 = permission denied on entrypoint.
2. Port conflicts are the second most common startup failure. `list_container_ports` + `list_containers` catches this immediately.
3. Default bridge network doesn't do DNS. If containers can't find each other by name, they need a user-defined network.
4. "What's using disk?" — `check_disk_usage` gives breakdown by images, containers, volumes in one call. Orphaned volumes from removed containers are a common surprise.
5. If a container keeps restarting, check OOMKilled in `get_container_status` before reading logs — memory limits are a silent killer.

## Anti-patterns

- Don't assume containers can reach each other by name on the default bridge — they can't. User-defined networks are required for service discovery.
- Don't look for a stopped `--rm` container — it was auto-removed. Check `list_containers(all=True)` to confirm.
- Don't run `get_container_stats` on stopped containers — it only works on running ones. Use `get_container_status` for last-known state.
- Don't debug networking at the container level when the issue is the host's firewall or routing. Check both.
- Don't assume Docker and Podman compose behave identically — `podman-compose` is less mature than `docker compose` v2.

## Procedures

### Container won't start
When a container fails to start or exits immediately.

1. `get_container_status` — check State.Error, State.ExitCode, State.OOMKilled
2. `get_container_logs(tail=50)` — startup errors, missing config, permission denied
3. IF port conflict:
     `list_container_ports` + `list_containers` — which container holds the port?
4. IF OOMKilled:
     Needs more memory or has a leak — check memory limits
5. IF exit code 126:
     Permission denied on entrypoint — check image and volume mount permissions
6. Check volume mounts — missing host paths cause silent failures
7. VERIFY: Container running in `list_containers`
8. CROSS-DOMAIN: If port is already bound by a host service → `linux-network-rules.md` "Port and service investigation"

### Container keeps crashing
When a container repeatedly exits and restarts.

1. `get_container_status` — RestartCount, exit code pattern
2. `get_container_logs(tail=200)` — find the crash pattern
3. `get_container_stats` — hitting memory limits?
4. IF OOMKilled:
     Container needs more memory or has a memory leak
   IF consistent exit code:
     Application-level bug — check logs for the error
   IF intermittent:
     Resource contention, race condition, or external dependency
5. VERIFY: Container stays running after fix
6. CROSS-DOMAIN: If OOM at system level → `linux-performance-rules.md` "OOM investigation"

### Container networking issues
When containers can't communicate or external access fails.

1. `list_networks` — verify container's network exists and is correct type
2. `get_container_status` — check NetworkSettings (IP, gateway, ports)
3. `get_container_logs` — "connection refused", DNS failures
4. IF containers can't reach each other:
     Check if they're on the same user-defined network (default bridge = no DNS)
5. IF external access fails:
     Check port mappings: `list_container_ports`
     Check host firewall: use network MCP tools
6. VERIFY: Containers can communicate as expected
7. CROSS-DOMAIN: If host networking is the issue → `linux-network-rules.md`

### Resource exhaustion
When containers are consuming too many resources or disk is full.

1. `get_container_stats` — CPU, memory, network I/O for all running containers
2. `check_disk_usage` — images, containers, volumes breakdown
3. `list_container_processes` — what's consuming resources inside
4. IF disk full:
     `list_images` — large or unused images?
     `list_volumes` — orphaned volumes?
5. VERIFY: Resource usage within acceptable limits
6. CROSS-DOMAIN: If host disk is also full → `linux-block-device-rules.md`

### Compose stack investigation
When a multi-container application has issues.

1. `get_compose_status` — which services up, down, restarting
2. `get_compose_logs(service="<name>")` — logs for failing service
3. For deeper inspection: use individual container tools on the specific service container
4. IF dependency ordering issue:
     Check `depends_on` in compose file — health check conditions?
5. VERIFY: All compose services running

## Tools

| Goal | Tool |
|------|------|
| What's running? | `list_containers` |
| All containers (including stopped)? | `list_containers(all=True)` |
| Why did it exit? | `get_container_status` + `get_container_logs` |
| Health check status? | `get_container_status` |
| Resource usage? | `get_container_stats` |
| Processes inside? | `list_container_processes` |
| Port mappings? | `list_container_ports` |
| Available images? | `list_images` |
| Network topology? | `list_networks` |
| Persistent data? | `list_volumes` |
| Compose stack health? | `get_compose_status` |
| Compose service logs? | `get_compose_logs` |
| Disk usage breakdown? | `check_disk_usage` |
| Restart a container? | `restart_container` |
| Runtime version? | `tool_info` |
| CLI reference? | `read_manual` |

## Query Strategy

1. Start with `get_container_status` for a specific container — it gives the full picture.
2. Check logs with a short tail first (`tail=50`), broaden if needed.
3. Use `get_container_stats` for quick resource snapshot before diving into logs.
4. Use `list_container_ports` early — port conflicts are a common and easy-to-fix cause.
5. When a container keeps restarting, check exit code before reading logs.
6. `check_disk_usage` is the fastest way to identify disk consumption by containers.
7. Be suspicious of empty results — container may have been `--rm`'d, or compose project name may differ.

## Safety

### Privilege

- Docker: requires `docker` group membership or root
- Podman rootless: runs as current user, no extra privileges
- Podman rootful: requires root
- `restart_container` is the only lifecycle action — explain before executing

### High-risk operations

- Container removal (`docker rm`) with volumes (`-v`) deletes persistent data. Confirm first.
- Image pruning (`docker image prune -a`) removes all unused images. May require re-downloading.
- Network removal affects all containers on that network.

### Cross-references

- If port conflict with host service → `linux-network-rules.md` "Port and service investigation"
- If container disk usage filling host → `linux-block-device-rules.md` "Disk full investigation"
- If container service managed by systemd → `linux-systemd-rules.md`
- If container networking uses host bridge/macvlan → `linux-network-rules.md`
- If container resource limits causing host issues → `linux-performance-rules.md`

## Quirks

- **`docker stats` vs `podman stats`**: Column names differ slightly. Don't parse by position.
- **Rootless port binding**: Can't bind below 1024 without `net.ipv4.ip_unprivileged_port_start`.
- **Compose project names**: Derived from directory name. Multiple stacks in different dirs can collide if `project_name` not set.
- **`--rm` containers**: Auto-removed on stop. Won't appear in `list_containers(all=True)`.
- **Stats on stopped containers**: `get_container_stats` only works on running containers.
- **Rootless networking**: Slower (user-space packet translation) for both Docker and Podman.

## Domain Deep Knowledge

### Docker vs Podman differences

| Aspect | Docker | Podman |
|--------|--------|--------|
| Daemon | Persistent dockerd | Daemonless (child processes) |
| Rootless | Requires setup | Default |
| Compose | `docker compose` v2 (built-in) | `podman-compose` (separate, less mature) |
| Socket | `/var/run/docker.sock` | `/run/user/$UID/podman/podman.sock` (rootless) |
| CLI | Reference implementation | 95%+ compatible |
| Crash impact | dockerd crash stops all containers | Each container independent |

### Container networking modes

- **bridge** (default): NAT. Containers get private IPs, port mapping required. No DNS on default bridge.
- **host**: Container shares host's network namespace. Simplest but least isolated.
- **macvlan**: Container gets own MAC on physical network. Most transparent.
- **none**: No networking. Container is isolated.
- **User-defined bridge**: Like default bridge but WITH DNS resolution between containers.
