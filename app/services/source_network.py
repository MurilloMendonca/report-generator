"""Network policy for account connectors. Private destinations require operator configuration."""
import ipaddress
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

_resolver = ThreadPoolExecutor(max_workers=4, thread_name_prefix="source-dns")
_resolver_slots = threading.BoundedSemaphore(4)
DNS_TIMEOUT_SECONDS = 5


def resolve_addresses(host, port):
    """Bound DNS latency and outstanding lookups, including a stalled resolver."""
    if not _resolver_slots.acquire(blocking=False):
        raise OSError("DNS resolver is busy")
    try:
        future = _resolver.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    except Exception:
        _resolver_slots.release()
        raise
    future.add_done_callback(lambda _future: _resolver_slots.release())
    return future.result(timeout=DNS_TIMEOUT_SECONDS)


def address_allowed(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if (str(address) == "100.100.100.200" or address.is_multicast or address.is_reserved or address.is_unspecified or address.is_link_local
            or (isinstance(address, ipaddress.IPv6Address) and (address.ipv4_mapped or address.sixtofour or address.teredo))):
        return False
    if address.is_global:
        return True
    networks = os.getenv("PDF_REPORT_SOURCE_ALLOWED_NETWORKS", "")
    try:
        return any(address in ipaddress.ip_network(network.strip()) for network in networks.split(",") if network.strip())
    except ValueError:
        return False


def resolve_host(host: str, port: int) -> tuple[str, str]:
    try:
        hostname = host.encode("idna").decode("ascii")
        addresses = resolve_addresses(hostname, port)
        if not addresses or not all(address_allowed(item[4][0]) for item in addresses):
            raise ValueError
        return hostname, addresses[0][4][0]
    except (OSError, ValueError, UnicodeError):
        raise ValueError("Destination is not public or on an operator-approved network") from None
