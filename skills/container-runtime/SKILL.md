---
name: container-runtime
description: >
    Domain knowledge for Docker and Podman container administration using
    the container MCP server. Load BEFORE using any container MCP tool
    directly. Covers container lifecycle, images, volumes, networks,
    compose stacks, and troubleshooting.
---

# Container Runtime

## Session Start

Before investigating container issues, establish what you're working with:

1. Call `tool_info()` to detect which runtime is available (docker, podman, or both)
2. Note the runtime and compose versions
3. If podman is in use, be aware of rootless vs rootful differences (see Quirks)

## Core Concepts

**Containers**: Running instances of images. Identified by name or ID. States: created, running, paused, exited, dead.

**Images**: Read-only templates. Identified by repository:tag or ID. Layers are shared between images to save disk.

**Volumes**: Persistent data stores that outlive containers. Named volumes are managed by the runtime; bind mounts map host paths directly.

**Networks**: Isolated network namespaces. Containers on the same network can reach each other by name. Default bridge network does not provide DNS resolution between containers.

**Compose stacks**: Multi-container applications defined in `docker-compose.yml` / `compose.yaml`. Services, networks, and volumes are scoped to the project.

## Common Tasks

### "What's running?"

1. `list_containers` — running containers with status
2. `get_container_stats` — quick resource snapshot (CPU, memory, net I/O)
3. For compose stacks: `get_compose_status` shows all services at once

### "Container won't start / keeps crashing"

1. `get_container_status` — check ExitCode, OOMKilled, Error, RestartCount
2. `get_container_logs(tail=50)` — look for startup errors
3. `list_container_ports` + `list_containers` — port conflict?
4. If OOMKilled: needs more memory or has a leak

### "What's using all my disk?"

1. `check_disk_usage` — breakdown by images, containers, volumes
2. `list_images` — identify large or unused images
3. `list_volumes` — orphaned volumes from removed containers

### "Is my container up to date?"

1. `list_images` — check image tags and creation dates
2. Compare with upstream — use Bash with `skopeo inspect` or `podman pull --dry-run` if available
3. Note: no MCP tool for remote digest comparison yet (see IDEAS)

## Tool Selection

| Goal | Tool |
|------|------|
| What's running? | `list_containers` |
| All containers (including stopped)? | `list_containers(all=True)` |
| Why did a container exit? | `get_container_status` + `get_container_logs` |
| Is a container healthy? | `get_container_status` (check health check) |
| Resource usage? | `get_container_stats` |
| What's running inside? | `list_container_processes` |
| Port mappings? | `list_container_ports` |
| Available images? | `list_images` |
| Network topology? | `list_networks` |
| Persistent data? | `list_volumes` |
| Compose stack health? | `get_compose_status` |
| Compose service logs? | `get_compose_logs` |
| Disk usage by containers? | `check_disk_usage` |
| Restart a crashed service? | `restart_container` |
| Runtime version? | `tool_info` |
| Runtime CLI reference? | `read_manual` |

## Query Strategy

### Scope first, then broaden

1. Start with a specific container — `get_container_status` gives the full picture
2. Check logs with a short tail — `get_container_logs(tail=50)` for recent events
3. If the issue isn't clear, broaden: check stats, check networking, check the compose stack

### Efficient investigation

- Use `get_container_stats` for a quick resource snapshot before diving into logs
- Use `list_container_ports` to identify port conflicts — a common cause of startup failures
- When a container keeps restarting, check the exit code in `get_container_status` before reading logs
- `check_disk_usage` is the quickest way to identify if images/volumes are consuming disk

## Troubleshooting Workflow

### Container won't start

1. `get_container_status` — check State.Error, State.ExitCode, State.OOMKilled
2. `get_container_logs` — look for startup errors, missing config, permission denied
3. `list_container_ports` + `list_containers` — check for port conflicts
4. Check volume mounts in inspect output — missing host paths cause silent failures

### Container keeps restarting

1. `get_container_status` — check RestartCount and restart policy
2. `get_container_logs(tail=200)` — look for the crash pattern
3. `get_container_stats` — check if it's hitting memory limits (OOMKilled)
4. If OOMKilled: the container needs more memory or has a leak

### Networking issues

1. `list_networks` — verify the container's network exists
2. `get_container_status` — check NetworkSettings (IP, gateway, ports)
3. `get_container_logs` — look for "connection refused", DNS resolution failures
4. Containers on the default bridge network cannot resolve each other by name — they need a user-defined network

### Resource exhaustion

1. `get_container_stats` — CPU, memory, network I/O for all running containers
2. `check_disk_usage` — images, containers, volumes consuming disk
3. `list_container_processes` — what's consuming resources inside the container

### Compose stack issues

1. `get_compose_status` — which services are up, which are down or restarting
2. `get_compose_logs(service="<name>")` — logs for the failing service
3. Individual container tools for deeper inspection of a specific service

## Docker vs Podman Differences

- **Daemon**: Docker runs a persistent daemon (dockerd). Podman is daemonless — each container is a child process. If dockerd crashes, all containers stop.
- **Rootless**: Podman is rootless by default. Docker requires group membership or rootless mode setup.
- **Compose**: Docker uses `docker compose` (v2 plugin, built-in). Podman uses `podman-compose` (separate package, less mature).
- **Socket**: Docker exposes `/var/run/docker.sock`. Podman uses `/run/user/$UID/podman/podman.sock` in rootless mode.
- **Networking**: Rootless container networking is slower (user-space packet translation) for both runtimes.
- **CLI**: 95%+ compatible. Most `docker` commands work with `podman` unchanged.

## Known Quirks

- **`docker stats` vs `podman stats`**: Column names and formatting differ slightly. Don't parse output by position — use the content.
- **Rootless port binding**: Rootless containers cannot bind to ports below 1024 without extra config (`net.ipv4.ip_unprivileged_port_start`).
- **Compose project names**: Derived from the directory name by default. Multiple compose stacks in different directories with the same structure can collide if `project_name` isn't set explicitly.
- **Container not found after restart**: If a container was created with `--rm`, it's automatically removed when stopped. Check `list_containers(all=True)` to confirm.
- **Stats on stopped containers**: `get_container_stats` only works on running containers. For stopped containers, use `get_container_status` to see last-known state.
