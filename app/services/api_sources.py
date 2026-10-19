"""Import a bounded snapshot from a public HTTPS JSON endpoint.

Resolve once and connect to the validated IP while retaining hostname TLS
verification. No redirects, proxies, cookies, or caller-controlled headers.
"""

import http.client
import ipaddress
import json
import os
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException

from app.utils.tabular import parse_json
from app.services.source_network import resolve_addresses

MAX_API_BYTES = int(os.getenv("PDF_REPORT_MAX_API_BYTES", str(10 * 1024 * 1024)))
API_TIMEOUT_SECONDS = 20


def public_api_target(url: str, *, address_validator=None) -> tuple[str, str, str]:
    if not url or len(url) > 4096 or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise HTTPException(400, "Enter a valid public HTTPS API URL")
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").encode("idna").decode("ascii")
        if (parsed.scheme != "https" or not hostname or parsed.port not in {None, 443}
                or parsed.username is not None or parsed.password is not None or parsed.fragment):
            raise ValueError
        addresses = resolve_addresses(hostname, 443)
        if not addresses:
            raise ValueError
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if address_validator is not None:
                if not address_validator(str(ip)):
                    raise ValueError
                continue
            if not ip.is_global or ip.is_multicast or ip.is_reserved or (isinstance(ip, ipaddress.IPv6Address) and (
                ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None
            )):
                raise ValueError
    except (ValueError, UnicodeError, OSError) as exc:
        raise HTTPException(400, "API URLs must use public HTTPS on port 443; private network addresses are blocked") from exc
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    try:
        target.encode("ascii")
    except UnicodeError as exc:
        raise HTTPException(400, "Percent-encode non-ASCII characters in the API URL") from exc
    return hostname, target, addresses[0][4][0]


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str):
        super().__init__(hostname, timeout=API_TIMEOUT_SECONDS, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Numeric sockaddr: the host cannot resolve to a private address on a second lookup.
        family = socket.AF_INET6 if ":" in self.address else socket.AF_INET
        raw_socket = socket.socket(family, socket.SOCK_STREAM)
        deadline = time.monotonic() + self.timeout
        try:
            raw_socket.settimeout(self.timeout)
            raw_socket.connect((self.address, 443))
            raw_socket.settimeout(max(0.001, deadline - time.monotonic()))
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def select_records(payload, pointer: str):
    if not pointer:
        return payload
    if not pointer.startswith("/") or len(pointer) > 1024:
        raise HTTPException(400, "Records path must be a JSON Pointer such as /data/items")
    try:
        for part in pointer[1:].split("/"):
            # JSON Pointer escaping is deliberately strict.
            if "~" in part.replace("~0", "").replace("~1", ""):
                raise ValueError
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(payload, list):
                if not key.isascii() or not key.isdecimal() or (len(key) > 1 and key.startswith("0")):
                    raise ValueError
                payload = payload[int(key)]
            else:
                payload = payload[key]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(400, "Records path does not exist in the API response") from exc
    return payload


def fetch_json_snapshot(url: str, records_path: str = "", *, auth_headers=None, address_validator=None,
                        timeout_seconds=None, max_bytes=None) -> tuple[bytes, str]:
    hostname, target, address = public_api_target(url, address_validator=address_validator)
    connection = PinnedHTTPSConnection(hostname, address)
    timeout = min(API_TIMEOUT_SECONDS, timeout_seconds) if timeout_seconds is not None else API_TIMEOUT_SECONDS
    byte_limit = min(MAX_API_BYTES, max_bytes) if max_bytes is not None else MAX_API_BYTES
    connection.timeout = max(0.001, timeout)
    deadline = time.monotonic() + timeout
    abort_timer = None
    try:
        connection.connect()
        transport_socket = connection.sock

        def abort_request():
            # Also interrupt slow response headers and chunk framing, whose reads
            # can otherwise continually reset a socket's inactivity timeout.
            try:
                transport_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        abort_timer = threading.Timer(remaining, abort_request)
        abort_timer.daemon = True
        abort_timer.start()
        connection.request("GET", target, headers={
            "Accept": "application/json", "Accept-Encoding": "identity",
            "User-Agent": "ReportGenerator/1.0",
            **(auth_headers or {}),
        })
        transport_socket.settimeout(max(0.001, deadline - time.monotonic()))
        response = connection.getresponse()
        if response.status != 200:
            raise HTTPException(400, f"API returned HTTP {response.status}; redirects are not followed")
        media_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json" and not (media_type.startswith("application/") and media_type.endswith("+json")):
            raise HTTPException(400, "API must return an application/json response")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise HTTPException(400, "API must return an uncompressed JSON response")
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            # Socket timeouts and the deadline also bound slow streaming responses.
            transport_socket.settimeout(remaining)
            chunk = response.read1(min(65536, byte_limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > byte_limit:
                raise HTTPException(413, "API response exceeds the configured size limit")
            chunks.append(chunk)
        body = b"".join(chunks)
        payload = parse_json(body.decode("utf-8-sig"))
        selected = select_records(payload, records_path)
        if records_path:
            body = json.dumps(selected, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return body, hostname
    except HTTPException:
        raise
    except (TimeoutError, OSError, http.client.HTTPException) as exc:
        raise HTTPException(400, "Could not fetch the API response within the time limit") from exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise HTTPException(400, "API response is not valid UTF-8 JSON") from exc
    finally:
        if abort_timer:
            abort_timer.cancel()
        connection.close()
