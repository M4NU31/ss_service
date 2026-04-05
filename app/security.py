"""
URL validation and SSRF prevention.

Rules:
  - Only http / https schemes.
  - Hostname must resolve to a public IP address.
  - Private, loopback, link-local, and reserved ranges are blocked.
  - IPv6 localhost is blocked.
"""

import ipaddress
import socket
from fastapi import HTTPException, status


# ---------------------------------------------------------------------------
# Private / reserved IP ranges that must never be reached
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    # Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    # Private (RFC 1918)
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # Link-local (includes AWS/GCP/Azure metadata endpoints)
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    # Unique local (IPv6 private)
    ipaddress.ip_network("fc00::/7"),
    # CGNAT
    ipaddress.ip_network("100.64.0.0/10"),
    # Documentation / TEST-NET
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    # Unspecified / broadcast
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("255.255.255.255/32"),
]


def _is_private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _BLOCKED_NETWORKS)


def validate_url(url: str) -> None:
    """
    Raise HTTP 400 if the URL is not safe to fetch.

    This is called with the *string* form of the validated Pydantic HttpUrl,
    so scheme / format checks are already done — we only need to SSRF-proof it.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only http and https URLs are allowed.",
        )

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL must contain a valid hostname.",
        )

    # Resolve all addresses the hostname maps to and check each one.
    # getaddrinfo returns (family, type, proto, canonname, sockaddr) tuples.
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not resolve hostname: {hostname!r}",
        )

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip = sockaddr[0]
        if _is_private(ip):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL resolves to a private or reserved IP address.",
            )
