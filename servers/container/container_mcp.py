#!/usr/bin/env python3
"""
Stuart — Container MCP Server

Exposes Docker and Podman container inspection and lifecycle operations
as MCP tools for Claude Code. Auto-detects available runtime(s).

Supports: docker, podman, docker compose (v2 plugin), podman-compose.

Usage:
    python3 container_mcp.py

Tested on:
    - Fedora 43, Podman 5.7.1, Python 3.14

Argument tier decisions (see docs/TOOL_CONVENTION.md):
    Tier 1 (exposed as params):
        container name/id, --all, --filter, --tail, --since,
        service name, project dir, timeout
    Tier 2 (param or separate tool):
        compose vs direct, stats vs inspect, port mappings
    Tier 3 (handled internally):
        --no-trunc, --format, --no-stream
    Tier 4 (omitted):
        rm, rmi, prune, build, push, pull, create, exec, attach,
        commit, export, import, cp, rename, pause, unpause, update,
        wait, events, save, load, tag, login, logout, swarm, stack,
        secret, config, plugin, trust, manifest, buildx,
        compose up, compose down, compose build, compose create
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# TODO: move ToolCache to a shared location (e.g. stuart/servers/shared/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "linux"))
from tool_check import ToolCache

server = FastMCP(
    name="sysops-container",
    instructions=(
        "Inspect and manage Docker/Podman containers, images, volumes, "
        "networks, and compose stacks. Includes read-only inspection "
        "and basic lifecycle operations (stop, start, restart)."
    ),
)


# ── Runtime detection ─────────────────────────────────────────────

def _detect_runtime() -> tuple[str | None, str | None]:
    """Detect container runtime. Returns (runtime_path, compose_cmd).

    Prefers docker over podman when both are present.
    compose_cmd is None if no compose capability found.
    """
    docker = shutil.which("docker")
    podman = shutil.which("podman")

    runtime = None
    compose = None

    if docker:
        runtime = docker
        # Check for docker compose v2 plugin (subcommand, not standalone)
        try:
            result = subprocess.run(
                [docker, "compose", "version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                compose = "docker-compose-plugin"
        except (subprocess.TimeoutExpired, OSError):
            pass

    if runtime is None and podman:
        runtime = podman
        pc = shutil.which("podman-compose")
        if pc:
            compose = "podman-compose"

    # If docker is runtime but has no compose, check for podman-compose
    if runtime == docker and compose is None:
        pc = shutil.which("podman-compose")
        if pc:
            compose = "podman-compose"

    return (runtime, compose)


_runtime, _compose_type = _detect_runtime()
_runtime_name = Path(_runtime).name if _runtime else None

# ToolCache for version/help discovery
_tools: dict[str, ToolCache] = {}
if shutil.which("docker"):
    _tools["docker"] = ToolCache("docker", shutil.which("docker"), ["--version"], ["--help"])
if shutil.which("podman"):
    _tools["podman"] = ToolCache("podman", shutil.which("podman"), ["--version"], ["--help"])
if shutil.which("podman-compose"):
    _tools["podman-compose"] = ToolCache(
        "podman-compose", shutil.which("podman-compose"), ["--version"], ["--help"]
    )


# ── Shared runner ─────────────────────────────────────────────────

def _run(
    args: list[str],
    max_lines: int = 200,
    timeout: int = 30,
) -> str:
    """Run a container runtime command. Returns stdout or error message."""
    if _runtime is None:
        return "Error: No container runtime detected. Install docker or podman."

    cmd = [_runtime] + args

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Error: {_runtime_name} timed out after {timeout} seconds."

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "Permission denied" in stderr or "permission denied" in stderr:
            return (
                f"Permission denied running {_runtime_name}. "
                f"Check group membership or use rootless mode.\n\n"
                f"stderr: {stderr}"
            )
        if "No such container" in stderr or "no such container" in stderr:
            return f"Container not found.\n\nstderr: {stderr}"
        if "No such image" in stderr or "no such image" in stderr:
            return f"Image not found.\n\nstderr: {stderr}"
        # Non-zero but may have useful stdout
        if result.stdout.strip():
            output = result.stdout.strip()
            if stderr:
                output += f"\n\n[stderr]: {stderr}"
        else:
            return f"Error from {_runtime_name}: {stderr or '(no output)'}"
    else:
        output = result.stdout or result.stderr or "(no output)"

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return output.strip()


def _run_compose(
    args: list[str],
    project_dir: str = ".",
    max_lines: int = 200,
    timeout: int = 30,
) -> str:
    """Run a compose command. Returns stdout or error message."""
    if _compose_type is None:
        return "Error: No compose capability detected. Install docker compose or podman-compose."

    if _compose_type == "docker-compose-plugin":
        cmd = [_runtime, "compose"] + args
    else:
        compose_path = shutil.which("podman-compose")
        if compose_path is None:
            return "Error: podman-compose not found in PATH."
        cmd = [compose_path] + args

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=project_dir if project_dir != "." else None,
        )
    except subprocess.TimeoutExpired:
        return f"Error: compose timed out after {timeout} seconds."
    except FileNotFoundError:
        return f"Error: project directory '{project_dir}' not found."

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if result.stdout.strip():
            output = result.stdout.strip()
            if stderr:
                output += f"\n\n[stderr]: {stderr}"
        else:
            return f"Error from compose: {stderr or '(no output)'}"
    else:
        output = result.stdout or result.stderr or "(no output)"

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return output.strip()


# ── Info tools ────────────────────────────────────────────────────

@server.tool()
def tool_info() -> str:
    """Return container runtime detection results and versions.

    Call this at the start of a session to see which runtime is
    available (docker, podman, or both) and which compose capability
    is detected.
    """
    result = {
        "active_runtime": _runtime_name,
        "active_runtime_path": _runtime,
        "compose_type": _compose_type,
    }
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
    tool: str = "",
    section: str = "",
) -> str:
    """Read the man page for a container runtime command.

    Use this as a last resort when tool_info() help text doesn't answer
    your question about available options or behavior.

    Args:
        tool: Command name: "docker", "podman", or "podman-compose".
              Leave empty to use the active runtime.
        section: Section to extract, e.g. "OPTIONS", "DESCRIPTION",
                 "COMMANDS". Leave empty for full page (truncated
                 to 200 lines).
    """
    if not tool:
        tool = _runtime_name or "docker"
    if tool not in _tools:
        available = ", ".join(sorted(_tools.keys())) or "(none detected)"
        return f"Unknown tool '{tool}'. Available: {available}"
    return _tools[tool].read_man(section=section)


# ── Read-only tools ──────────────────────────────────────────────

@server.tool()
def list_containers(
    all: bool = False,
    filter: str = "",
    max_lines: int = 100,
) -> str:
    """List containers. By default shows only running containers.

    Args:
        all: Include stopped/exited containers.
        filter: Docker/Podman filter expression, e.g.
                "status=exited", "name=myapp", "label=env=prod".
        max_lines: Maximum lines to return.
    """
    args = ["ps"]
    if all:
        args.append("--all")
    if filter:
        args += ["--filter", filter]
    return _run(args, max_lines=max_lines)


@server.tool()
def get_container_status(
    container: str,
) -> str:
    """Get detailed status of a container via inspect. Returns state,
    config, network settings, mounts, health check results, and more.

    Args:
        container: Container name or ID.
    """
    return _run(["inspect", container], max_lines=500)


@server.tool()
def get_container_logs(
    container: str,
    tail: int = 100,
    since: str = "",
    max_lines: int = 500,
) -> str:
    """Fetch container logs.

    Args:
        container: Container name or ID.
        tail: Number of lines from the end to show. Default 100.
        since: Show logs since timestamp, e.g. "2024-01-01T00:00:00",
               "10m" (last 10 minutes), "1h" (last hour).
        max_lines: Maximum lines to return.
    """
    args = ["logs", "--tail", str(tail)]
    if since:
        args += ["--since", since]
    args.append(container)
    return _run(args, max_lines=max_lines)


@server.tool()
def get_container_stats(
    container: str = "",
    max_lines: int = 100,
) -> str:
    """Show resource usage statistics (CPU, memory, network I/O, disk I/O).

    Returns a snapshot (non-streaming). Shows all running containers
    if no container is specified.

    Args:
        container: Container name or ID. Leave empty for all running.
        max_lines: Maximum lines to return.
    """
    args = ["stats", "--no-stream"]
    if container:
        args.append(container)
    return _run(args, max_lines=max_lines)


@server.tool()
def list_container_processes(
    container: str,
) -> str:
    """Show processes running inside a container. Equivalent to 'top'.

    Args:
        container: Container name or ID.
    """
    return _run(["top", container])


@server.tool()
def list_images(
    all: bool = False,
    max_lines: int = 100,
) -> str:
    """List container images with repository, tag, size, and creation date.

    Args:
        all: Include intermediate (dangling) images.
        max_lines: Maximum lines to return.
    """
    args = ["images"]
    if all:
        args.append("--all")
    return _run(args, max_lines=max_lines)


@server.tool()
def list_networks(
    max_lines: int = 100,
) -> str:
    """List container networks.

    Args:
        max_lines: Maximum lines to return.
    """
    return _run(["network", "ls"], max_lines=max_lines)


@server.tool()
def list_volumes(
    max_lines: int = 100,
) -> str:
    """List container volumes.

    Args:
        max_lines: Maximum lines to return.
    """
    return _run(["volume", "ls"], max_lines=max_lines)


@server.tool()
def list_container_ports(
    container: str,
) -> str:
    """Show port mappings for a container.

    Args:
        container: Container name or ID.
    """
    return _run(["port", container])


@server.tool()
def check_disk_usage(
) -> str:
    """Show disk usage summary: images, containers, volumes, and build cache.

    Equivalent to 'docker system df' / 'podman system df'.
    """
    return _run(["system", "df"])


# ── Compose tools ────────────────────────────────────────────────

@server.tool()
def get_compose_status(
    project_dir: str = ".",
    max_lines: int = 100,
) -> str:
    """Show status of a compose stack's services.

    Args:
        project_dir: Path to directory containing docker-compose.yml
                     or compose.yaml. Defaults to current directory.
        max_lines: Maximum lines to return.
    """
    return _run_compose(["ps"], project_dir=project_dir, max_lines=max_lines)


@server.tool()
def get_compose_logs(
    project_dir: str = ".",
    service: str = "",
    tail: int = 100,
    max_lines: int = 500,
) -> str:
    """Fetch logs from a compose stack or specific service.

    Args:
        project_dir: Path to directory containing docker-compose.yml
                     or compose.yaml. Defaults to current directory.
        service: Specific service name. Leave empty for all services.
        tail: Number of lines from the end per service.
        max_lines: Maximum lines to return.
    """
    args = ["logs", "--tail", str(tail)]
    if service:
        args.append(service)
    return _run_compose(args, project_dir=project_dir, max_lines=max_lines)


# ── Lifecycle tools ──────────────────────────────────────────────

@server.tool()
def stop_container(
    container: str,
    timeout: int = 10,
) -> str:
    """Stop a running container.

    Args:
        container: Container name or ID.
        timeout: Seconds to wait before forcefully killing. Default 10.
    """
    return _run(["stop", "--time", str(timeout), container], timeout=timeout + 15)


@server.tool()
def start_container(
    container: str,
) -> str:
    """Start a stopped container.

    Args:
        container: Container name or ID.
    """
    return _run(["start", container])


@server.tool()
def restart_container(
    container: str,
    timeout: int = 10,
) -> str:
    """Restart a container (stop + start).

    Args:
        container: Container name or ID.
        timeout: Seconds to wait for stop before forcefully killing. Default 10.
    """
    return _run(["restart", "--time", str(timeout), container], timeout=timeout + 15)


if __name__ == "__main__":
    server.run(transport="stdio")
