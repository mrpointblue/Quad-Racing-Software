from datetime import datetime
import os
import shutil
import socket
import subprocess
import time


def format_storage_size(size_bytes: int) -> str:
    size_gib = size_bytes / (1024 ** 3)
    return f"{size_gib:.1f} GB"

def get_system_storage_status() -> dict:
    usage = shutil.disk_usage("/")
    used = usage.total - usage.free

    if usage.total > 0:
        used_percent = round((used / usage.total) * 100)
    else:
        used_percent = 0

    return {
        "mount": "/",
        "total": format_storage_size(usage.total),
        "used": format_storage_size(used),
        "free": format_storage_size(usage.free),
        "used_percent": used_percent,
    }

def read_cpu_times() -> tuple[int, int]:
    with open("/proc/stat", "r", encoding="utf-8") as stat_file:
        values = stat_file.readline().split()[1:]

    cpu_values = [int(value) for value in values]
    idle = cpu_values[3] + cpu_values[4]
    total = sum(cpu_values)

    return idle, total

def get_cpu_usage_percent() -> int | None:
    try:
        idle_before, total_before = read_cpu_times()
        time.sleep(0.1)
        idle_after, total_after = read_cpu_times()
    except OSError:
        return None

    total_delta = total_after - total_before
    idle_delta = idle_after - idle_before

    if total_delta <= 0:
        return None

    return round((1 - (idle_delta / total_delta)) * 100)

def get_cpu_temperature() -> str:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as temp_file:
            raw_temperature = temp_file.read().strip()
    except OSError:
        return "-"

    if not raw_temperature:
        return "-"

    temperature_celsius = int(raw_temperature) / 1000
    return f"{temperature_celsius:.1f} °C"

def get_memory_status() -> dict:
    values = {}

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as meminfo_file:
            for line in meminfo_file:
                key, raw_value = line.split(":", 1)
                values[key] = int(raw_value.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return {
            "total": "-",
            "used": "-",
            "free": "-",
            "used_percent": "-",
        }

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = total - available

    if total > 0:
        used_percent = round((used / total) * 100)
    else:
        used_percent = 0

    return {
        "total": format_storage_size(total),
        "used": format_storage_size(used),
        "free": format_storage_size(available),
        "used_percent": used_percent,
    }

def read_text_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as file:
            return file.read().strip()
    except OSError:
        return None

def get_ip_command() -> str | None:
    ip_command = shutil.which("ip")

    if ip_command:
        return ip_command

    for path in ["/usr/sbin/ip", "/sbin/ip", "/bin/ip", "/usr/bin/ip"]:
        if os.path.exists(path):
            return path

    return None

def get_interface_ipv4_address(interface_name: str) -> str:
    ip_command = get_ip_command()

    if not ip_command:
        return "-"

    try:
        result = subprocess.run(
            [ip_command, "-o", "-4", "addr", "show", "dev", interface_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "-"

    if result.returncode != 0:
        return "-"

    for line in result.stdout.splitlines():
        parts = line.split()

        if "inet" in parts:
            inet_index = parts.index("inet")

            try:
                return parts[inet_index + 1].split("/")[0]
            except IndexError:
                return "-"

    return "-"

def get_host_ipv4_addresses() -> list[str]:
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    addresses = []

    for raw_address in result.stdout.split():
        address = raw_address.strip()

        if "." in address and not address.startswith("127."):
            addresses.append(address)

    return addresses

def get_primary_ipv4_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1)
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
    except OSError:
        return None

    if address and not address.startswith("127."):
        return address

    return None

def list_network_interfaces() -> list[str]:
    try:
        interfaces = os.listdir("/sys/class/net")
    except OSError:
        return []

    return sorted(interface for interface in interfaces if interface != "lo")

def is_wireless_interface(interface_name: str) -> bool:
    return os.path.isdir(f"/sys/class/net/{interface_name}/wireless")

def get_default_route_interface() -> str | None:
    ip_command = get_ip_command()

    if ip_command:
        try:
            result = subprocess.run(
                [ip_command, "route", "show", "default"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None

        if result and result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()

                if "dev" in parts:
                    try:
                        return parts[parts.index("dev") + 1]
                    except IndexError:
                        pass

    try:
        with open("/proc/net/route", "r", encoding="utf-8") as route_file:
            for line in route_file.readlines()[1:]:
                parts = line.split()

                if len(parts) > 2 and parts[1] == "00000000":
                    return parts[0]
    except OSError:
        return None

    return None

def get_wifi_signal_strength(interface_name: str) -> str:
    try:
        with open("/proc/net/wireless", "r", encoding="utf-8") as wireless_file:
            lines = wireless_file.readlines()
    except OSError:
        return "-"

    for line in lines:
        line = line.strip()

        if not line.startswith(f"{interface_name}:"):
            continue

        values = line.replace(".", "").split()

        try:
            link_quality = float(values[2])
        except (IndexError, ValueError):
            return "-"

        signal_percent = max(0, min(100, round((link_quality / 70) * 100)))
        return f"{signal_percent}%"

    return "-"

def get_network_interface_status(interface_name: str, label: str, wireless: bool = False) -> dict:
    mac_address = read_text_file(f"/sys/class/net/{interface_name}/address") or "-"
    operstate = read_text_file(f"/sys/class/net/{interface_name}/operstate") or "-"
    carrier = read_text_file(f"/sys/class/net/{interface_name}/carrier")
    ip_address = get_interface_ipv4_address(interface_name)

    link_connected = carrier == "1" or operstate == "up"
    connected = ip_address != "-"

    return {
        "name": interface_name,
        "label": label,
        "mac_address": mac_address,
        "ip_address": ip_address,
        "connected": connected,
        "link_connected": link_connected,
        "status": "Verbunden" if connected else "Nicht verbunden",
        "signal_strength": get_wifi_signal_strength(interface_name) if wireless else "-",
    }

def get_empty_network_interface_status(interface_name: str, label: str) -> dict:
    return {
        "name": interface_name,
        "label": label,
        "mac_address": "-",
        "ip_address": "-",
        "connected": False,
        "link_connected": False,
        "status": "Nicht verbunden",
        "signal_strength": "-",
    }

def choose_network_interface_status(
    statuses: list[dict],
    default_interface: str | None,
    fallback_name: str,
    label: str,
    wireless: bool = False,
) -> dict:
    for status in statuses:
        if status["name"] == default_interface:
            return status

    for status in statuses:
        if status["connected"]:
            return status

    for status in statuses:
        if status["name"] == fallback_name:
            return status

    if statuses:
        return statuses[0]

    return get_empty_network_interface_status(fallback_name, label)

def get_network_status() -> dict:
    interfaces = list_network_interfaces()
    default_interface = get_default_route_interface()
    ethernet_interfaces = [
        interface for interface in interfaces
        if not is_wireless_interface(interface)
    ]
    wifi_interfaces = [
        interface for interface in interfaces
        if is_wireless_interface(interface)
    ]

    ethernet_statuses = [
        get_network_interface_status(interface, "Ethernet")
        for interface in ethernet_interfaces
    ]
    wifi_statuses = [
        get_network_interface_status(interface, "WLAN", wireless=True)
        for interface in wifi_interfaces
    ]

    ethernet = choose_network_interface_status(
        statuses=ethernet_statuses,
        default_interface=default_interface,
        fallback_name="eth0",
        label="Ethernet",
    )
    wifi = choose_network_interface_status(
        statuses=wifi_statuses,
        default_interface=default_interface,
        fallback_name="wlan0",
        label="WLAN",
        wireless=True,
    )

    host_ipv4_addresses = get_host_ipv4_addresses()
    primary_ipv4_address = get_primary_ipv4_address()

    if primary_ipv4_address and primary_ipv4_address not in host_ipv4_addresses:
        host_ipv4_addresses.insert(0, primary_ipv4_address)

    if (
        default_interface == ethernet["name"]
        and ethernet["ip_address"] == "-"
        and host_ipv4_addresses
    ):
        ethernet["ip_address"] = host_ipv4_addresses[0]
        ethernet["connected"] = True
        ethernet["status"] = "Verbunden"
    elif (
        default_interface == wifi["name"]
        and wifi["ip_address"] == "-"
        and host_ipv4_addresses
    ):
        wifi["ip_address"] = host_ipv4_addresses[0]
        wifi["connected"] = True
        wifi["status"] = "Verbunden"
    elif (
        ethernet["link_connected"]
        and ethernet["ip_address"] == "-"
        and host_ipv4_addresses
    ):
        ethernet["ip_address"] = host_ipv4_addresses[0]
        ethernet["connected"] = True
        ethernet["status"] = "Verbunden"
    elif (
        wifi["link_connected"]
        and wifi["ip_address"] == "-"
        and host_ipv4_addresses
        and not ethernet["link_connected"]
    ):
        wifi["ip_address"] = host_ipv4_addresses[0]
        wifi["connected"] = True
        wifi["status"] = "Verbunden"

    if ethernet["connected"]:
        active_connection = "Ethernet"
    elif wifi["connected"]:
        active_connection = "WLAN"
    else:
        active_connection = "Nicht verbunden"

    return {
        "active_connection": active_connection,
        "ethernet": ethernet,
        "wifi": wifi,
    }

def get_system_status() -> dict:
    current_time = datetime.now()

    return {
        "current_time": current_time.strftime("%d.%m.%Y %H:%M:%S"),
        "current_time_input": current_time.strftime("%Y-%m-%dT%H:%M"),
        "storage": get_system_storage_status(),
        "cpu_usage_percent": get_cpu_usage_percent(),
        "cpu_temperature": get_cpu_temperature(),
        "memory": get_memory_status(),
        "network": get_network_status(),
    }
