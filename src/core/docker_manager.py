"""
Docker manager for VPN proxy container orchestration.

Provides functionality to:
- Detect Docker installation and status
- Generate docker-compose.yml for VPN proxy containers
- Manage container lifecycle (start, stop, status)
- Support multiple VPN providers
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _subprocess_kwargs() -> dict:
    """Get platform-specific subprocess kwargs to hide console windows on Windows."""
    kwargs = {}
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _dict_to_yaml(data: dict, indent: int = 0) -> str:
    """Simple dict to YAML converter (avoids pyyaml dependency)."""
    lines = []
    prefix = "  " * indent

    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_dict_to_yaml(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            for item in value:
                if isinstance(item, dict):
                    # First key on same line as dash
                    first = True
                    for k, v in item.items():
                        if first:
                            lines.append(f"{prefix}  - {k}: {_format_value(v)}")
                            first = False
                        else:
                            lines.append(f"{prefix}    {k}: {_format_value(v)}")
                else:
                    lines.append(f"{prefix}  - {_format_value(item)}")
        else:
            lines.append(f"{prefix}{key}: {_format_value(value)}")

    return "\n".join(lines)


def _format_value(value) -> str:
    """Format a value for YAML output."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        # Quote strings that need it
        if any(c in value for c in ":#{}[]&*!|>'\"%@`"):
            return f'"{value}"'
        if value.lower() in ("true", "false", "null", "yes", "no"):
            return f'"{value}"'
        return value
    return str(value)


class VPNProvider(Enum):
    """Supported VPN providers for gluetun container."""
    NORDVPN = "nordvpn"
    MULLVAD = "mullvad"
    SURFSHARK = "surfshark"
    EXPRESSVPN = "expressvpn"
    PROTONVPN = "protonvpn"
    PRIVATE_INTERNET_ACCESS = "private internet access"
    WINDSCRIBE = "windscribe"
    CYBERGHOST = "cyberghost"
    CUSTOM = "custom"


@dataclass
class VPNConfig:
    """Configuration for a VPN connection."""
    provider: VPNProvider
    region: str
    vpn_type: str = "wireguard"  # wireguard or openvpn
    # Provider-specific credentials
    wireguard_private_key: Optional[str] = None
    wireguard_addresses: Optional[str] = None
    openvpn_user: Optional[str] = None
    openvpn_password: Optional[str] = None
    # Custom provider settings
    custom_config: Dict[str, str] = field(default_factory=dict)

    def to_env_dict(self) -> Dict[str, str]:
        """Convert to environment variables for gluetun container."""
        env = {
            "VPN_SERVICE_PROVIDER": self.provider.value,
            "VPN_TYPE": self.vpn_type,
            "SERVER_COUNTRIES": self.region,
        }

        if self.vpn_type == "wireguard":
            if self.wireguard_private_key:
                env["WIREGUARD_PRIVATE_KEY"] = self.wireguard_private_key
            if self.wireguard_addresses:
                env["WIREGUARD_ADDRESSES"] = self.wireguard_addresses
        else:  # openvpn
            if self.openvpn_user:
                env["OPENVPN_USER"] = self.openvpn_user
            if self.openvpn_password:
                env["OPENVPN_PASSWORD"] = self.openvpn_password

        # Add any custom config
        env.update(self.custom_config)

        return env


@dataclass
class DockerStatus:
    """Docker installation and runtime status."""
    installed: bool = False
    running: bool = False
    version: Optional[str] = None
    compose_version: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ContainerStatus:
    """Status of a proxy container."""
    name: str
    running: bool
    status: str  # "running", "exited", "created", etc.
    health: Optional[str] = None  # "healthy", "unhealthy", "starting"
    ports: List[str] = field(default_factory=list)
    ip_address: Optional[str] = None


class DockerManager:
    """
    Manages Docker containers for VPN proxy setup.

    Creates and manages gluetun VPN containers with SOCKS5 proxy
    that can be used for IP rotation.
    """

    # Base port for SOCKS5 proxies (1081, 1082, 1083, ...)
    BASE_PROXY_PORT = 1081

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Initialize Docker manager.

        Args:
            config_dir: Directory to store docker-compose.yml and configs.
                       Defaults to ~/.coomer-betterui/docker
        """
        if config_dir is None:
            config_dir = Path.home() / ".coomer-betterui" / "docker"

        self.config_dir = config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.compose_file = self.config_dir / "docker-compose.yml"

    def check_docker_status(self) -> DockerStatus:
        """
        Check if Docker is installed and running.

        Returns:
            DockerStatus with installation and runtime info
        """
        status = DockerStatus()

        # Check if docker command exists
        docker_path = shutil.which("docker")
        if not docker_path:
            status.error = "Docker not found in PATH. Install Docker Desktop: https://www.docker.com/products/docker-desktop"
            return status

        status.installed = True

        # Check Docker version and if daemon is running
        try:
            result = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=10,
                **_subprocess_kwargs(),
            )
            if result.returncode == 0:
                status.running = True
                status.version = result.stdout.strip()
            else:
                # Docker installed but daemon not running
                if "Cannot connect" in result.stderr or "error during connect" in result.stderr:
                    status.error = "Docker is installed but not running. Start Docker Desktop."
                else:
                    status.error = f"Docker error: {result.stderr.strip()}"
        except subprocess.TimeoutExpired:
            status.error = "Docker daemon not responding (timeout)"
        except Exception as e:
            status.error = f"Error checking Docker: {e}"

        # Check docker compose version
        if status.running:
            try:
                result = subprocess.run(
                    ["docker", "compose", "version", "--short"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    **_subprocess_kwargs(),
                )
                if result.returncode == 0:
                    status.compose_version = result.stdout.strip()
            except Exception:
                # Try old docker-compose command
                try:
                    result = subprocess.run(
                        ["docker-compose", "version", "--short"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        **_subprocess_kwargs(),
                    )
                    if result.returncode == 0:
                        status.compose_version = result.stdout.strip()
                except Exception:
                    pass

        return status

    def generate_compose_file(
        self,
        vpn_configs: List[VPNConfig],
        project_name: str = "coomer-vpn-proxies",
    ) -> Path:
        """
        Generate docker-compose.yml for VPN proxy containers.

        Args:
            vpn_configs: List of VPN configurations (one per proxy)
            project_name: Docker Compose project name

        Returns:
            Path to generated docker-compose.yml
        """
        services = {}

        for i, vpn_config in enumerate(vpn_configs):
            port = self.BASE_PROXY_PORT + i
            region_slug = vpn_config.region.lower().replace(" ", "-")[:10]
            vpn_service_name = f"vpn-{region_slug}-{i}"

            if vpn_config.provider == VPNProvider.EXPRESSVPN:
                # Use polkaned/expressvpn image and setup
                env_vars = {
                    "ACTIVATION_CODE": vpn_config.custom_config.get("activation_code", "<YOUR_CODE_HERE>"),
                    "SERVER": vpn_config.region.upper(),
                    "PREFERRED_PROTOCOL": vpn_config.custom_config.get("preferred_protocol", "lightway_udp"),
                }
                services[vpn_service_name] = {
                    "image": "polkaned/expressvpn:latest",
                    "container_name": f"{project_name}-{vpn_service_name}",
                    "cap_add": ["NET_ADMIN"],
                    "devices": ["/dev/net/tun:/dev/net/tun"],
                    "environment": env_vars,
                    "ports": [f"{port}:1080"],
                    "restart": "no",
                    "tty": True,
                    "command": "/bin/bash",
                    "stdin_open": True,
                    "privileged": True,
                }
                # Add socks5 proxy container
                socks_service_name = f"socks-{region_slug}-{i}"
                services[socks_service_name] = {
                    "image": "serjs/go-socks5-proxy:latest",
                    "container_name": f"{project_name}-{socks_service_name}",
                    "network_mode": f"service:{vpn_service_name}",
                    "depends_on": [vpn_service_name],
                    "environment": {
                        "PROXY_PORT": "1080",
                        "REQUIRE_AUTH": "false",
                    },
                    "restart": "no",
                    "labels": {
                        "coomer.proxy.port": str(port),
                        "coomer.proxy.region": vpn_config.region,
                        "coomer.proxy.type": "socks5",
                    },
                }
            else:
                # Default: gluetun HTTP proxy
                env_vars = vpn_config.to_env_dict()
                env_vars["HTTPPROXY"] = "on"
                services[vpn_service_name] = {
                    "image": "qmcgaw/gluetun:latest",
                    "container_name": f"{project_name}-{vpn_service_name}",
                    "cap_add": ["NET_ADMIN"],
                    "devices": ["/dev/net/tun:/dev/net/tun"],
                    "environment": env_vars,
                    "ports": [f"{port}:8888"],
                    "restart": "unless-stopped",
                    "labels": {
                        "coomer.proxy.port": str(port),
                        "coomer.proxy.region": vpn_config.region,
                        "coomer.proxy.type": "http",
                    },
                }

        compose_content = {
            "version": "3.8",
            "name": project_name,
            "services": services,
        }

        # Write compose file
        with open(self.compose_file, "w") as f:
            f.write(_dict_to_yaml(compose_content))

        logger.info(f"Generated docker-compose.yml at {self.compose_file}")
        return self.compose_file

    def generate_simple_compose(
        self,
        provider: str,
        regions: List[str],
        vpn_type: str = "wireguard",
        credentials: Dict[str, str] = None,
    ) -> Tuple[Path, List[str]]:
        """
        Generate a simple docker-compose.yml with basic settings.

        Args:
            provider: VPN provider name (e.g., "nordvpn", "mullvad")
            regions: List of regions/countries to create proxies for
            vpn_type: "wireguard" or "openvpn"
            credentials: Dict with credential keys like wireguard_private_key, etc.

        Returns:
            Tuple of (compose_file_path, list_of_proxy_urls)
        """
        credentials = credentials or {}
        vpn_configs = []

        for region in regions:
            config = VPNConfig(
                provider=VPNProvider(provider.lower()),
                region=region,
                vpn_type=vpn_type,
                wireguard_private_key=credentials.get("wireguard_private_key"),
                wireguard_addresses=credentials.get("wireguard_addresses"),
                openvpn_user=credentials.get("openvpn_user"),
                openvpn_password=credentials.get("openvpn_password"),
            )
            vpn_configs.append(config)

        compose_path = self.generate_compose_file(vpn_configs)

        # Generate proxy URLs
        proxy_urls = []
        for i in range(len(regions)):
            port = self.BASE_PROXY_PORT + i
            # Use HTTP proxy (gluetun's built-in on port 8888, mapped to our port)
            proxy_urls.append(f"http://localhost:{port}")

        return compose_path, proxy_urls

    def start_containers(self) -> Tuple[bool, str]:
        """
        Start the VPN proxy containers.

        Returns:
            Tuple of (success, message)
        """
        if not self.compose_file.exists():
            return False, "docker-compose.yml not found. Generate it first."

        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(self.compose_file), "up", "-d"],
                capture_output=True,
                text=True,
                timeout=120,  # 2 minutes timeout for pulling images
                cwd=self.config_dir,
                **_subprocess_kwargs(),
            )

            if result.returncode == 0:
                return True, "Containers started successfully"
            else:
                return False, f"Failed to start containers: {result.stderr}"

        except subprocess.TimeoutExpired:
            return False, "Timeout starting containers (images may still be downloading)"
        except Exception as e:
            return False, f"Error starting containers: {e}"

    def stop_containers(self) -> Tuple[bool, str]:
        """
        Stop the VPN proxy containers.

        Returns:
            Tuple of (success, message)
        """
        if not self.compose_file.exists():
            return False, "docker-compose.yml not found"

        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(self.compose_file), "down"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self.config_dir,
                **_subprocess_kwargs(),
            )

            if result.returncode == 0:
                return True, "Containers stopped successfully"
            else:
                return False, f"Failed to stop containers: {result.stderr}"

        except subprocess.TimeoutExpired:
            return False, "Timeout stopping containers"
        except Exception as e:
            return False, f"Error stopping containers: {e}"

    def get_container_status(self) -> List[ContainerStatus]:
        """
        Get status of all VPN proxy containers.

        Returns:
            List of ContainerStatus objects
        """
        if not self.compose_file.exists():
            return []

        try:
            result = subprocess.run(
                [
                    "docker", "compose", "-f", str(self.compose_file),
                    "ps", "--format", "json"
                ],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=self.config_dir,
                **_subprocess_kwargs(),
            )

            if result.returncode != 0:
                return []

            containers = []
            # Parse JSON output (one JSON object per line)
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    status = ContainerStatus(
                        name=data.get("Name", ""),
                        running=data.get("State", "") == "running",
                        status=data.get("State", "unknown"),
                        health=data.get("Health", None),
                        ports=self._parse_ports(data.get("Publishers", [])),
                    )
                    containers.append(status)
                except json.JSONDecodeError:
                    continue

            return containers

        except Exception as e:
            logger.warning(f"Error getting container status: {e}")
            return []

    def _parse_ports(self, publishers: List[Dict]) -> List[str]:
        """Parse port mappings from docker compose ps output."""
        ports = []
        for pub in publishers:
            if isinstance(pub, dict):
                host_port = pub.get("PublishedPort")
                container_port = pub.get("TargetPort")
                if host_port and container_port:
                    ports.append(f"{host_port}:{container_port}")
        return ports

    def get_proxy_urls(self) -> List[str]:
        """
        Get list of proxy URLs from running containers.

        Returns:
            List of proxy URLs (e.g., ["socks5://localhost:1081", ...])
        """
        containers = self.get_container_status()
        proxy_urls = []

        for container in containers:
            if container.running and "vpn-" in container.name:
                for port_mapping in container.ports:
                    if ":" in port_mapping:
                        host_port = port_mapping.split(":")[0]
                        proxy_urls.append(f"socks5://localhost:{host_port}")

        return proxy_urls

    def remove_containers(self) -> Tuple[bool, str]:
        """
        Remove all VPN proxy containers and volumes.

        Returns:
            Tuple of (success, message)
        """
        if not self.compose_file.exists():
            return True, "No containers to remove"

        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(self.compose_file), "down", "-v", "--remove-orphans"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self.config_dir,
                **_subprocess_kwargs(),
            )

            if result.returncode == 0:
                return True, "Containers removed successfully"
            else:
                return False, f"Failed to remove containers: {result.stderr}"

        except Exception as e:
            return False, f"Error removing containers: {e}"

    def get_compose_file_content(self) -> Optional[str]:
        """Get the content of the generated docker-compose.yml."""
        if self.compose_file.exists():
            return self.compose_file.read_text()
        return None


# Provider-specific configuration templates
VPN_PROVIDER_INFO = {
    "nordvpn": {
        "name": "NordVPN",
        "vpn_types": ["wireguard", "openvpn"],
        "default_type": "wireguard",
        "credentials_needed": {
            "wireguard": ["wireguard_private_key"],
            "openvpn": ["openvpn_user", "openvpn_password"],
        },
        "sample_regions": ["United States", "Germany", "Japan", "Netherlands", "United Kingdom"],
        "docs_url": "https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/nordvpn.md",
    },
    "mullvad": {
        "name": "Mullvad",
        "vpn_types": ["wireguard", "openvpn"],
        "default_type": "wireguard",
        "credentials_needed": {
            "wireguard": ["wireguard_private_key", "wireguard_addresses"],
            "openvpn": ["openvpn_user"],  # Mullvad uses account number as user
        },
        "sample_regions": ["United States", "Germany", "Sweden", "Switzerland", "Japan"],
        "docs_url": "https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/mullvad.md",
    },
    "surfshark": {
        "name": "Surfshark",
        "vpn_types": ["wireguard", "openvpn"],
        "default_type": "openvpn",
        "credentials_needed": {
            "wireguard": ["wireguard_private_key"],
            "openvpn": ["openvpn_user", "openvpn_password"],
        },
        "sample_regions": ["United States", "Germany", "France", "Australia", "Japan"],
        "docs_url": "https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/surfshark.md",
    },
    "protonvpn": {
        "name": "ProtonVPN",
        "vpn_types": ["wireguard", "openvpn"],
        "default_type": "wireguard",
        "credentials_needed": {
            "wireguard": ["wireguard_private_key"],
            "openvpn": ["openvpn_user", "openvpn_password"],
        },
        "sample_regions": ["United States", "Netherlands", "Japan", "Switzerland", "Iceland"],
        "docs_url": "https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/protonvpn.md",
    },
    "private internet access": {
        "name": "Private Internet Access",
        "vpn_types": ["wireguard", "openvpn"],
        "default_type": "openvpn",
        "credentials_needed": {
            "wireguard": ["wireguard_private_key"],
            "openvpn": ["openvpn_user", "openvpn_password"],
        },
        "sample_regions": ["United States", "Canada", "United Kingdom", "Netherlands", "Germany"],
        "docs_url": "https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/private-internet-access.md",
    },
    "windscribe": {
        "name": "Windscribe",
        "vpn_types": ["wireguard", "openvpn"],
        "default_type": "wireguard",
        "credentials_needed": {
            "wireguard": ["wireguard_private_key"],
            "openvpn": ["openvpn_user", "openvpn_password"],
        },
        "sample_regions": ["United States", "Canada", "Germany", "Hong Kong", "Japan"],
        "docs_url": "https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/windscribe.md",
    },
    "expressvpn": {
        "name": "ExpressVPN",
        "vpn_types": ["openvpn"],
        "default_type": "openvpn",
        "credentials_needed": {
            "openvpn": ["openvpn_user", "openvpn_password"],
        },
        "sample_regions": ["USA", "UK", "Germany", "Japan", "Canada", "Australia", "France", "Netherlands"],
        "docs_url": "https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/expressvpn.md",
        "note": "Use short country codes (USA, UK, Germany) not location names. See docs for full list.",
    },
}


def get_supported_providers() -> List[str]:
    """Get list of supported VPN provider names."""
    return list(VPN_PROVIDER_INFO.keys())


def get_provider_info(provider: str) -> Optional[Dict[str, Any]]:
    """Get configuration info for a VPN provider."""
    return VPN_PROVIDER_INFO.get(provider.lower())
