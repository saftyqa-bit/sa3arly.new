from __future__ import annotations

import ipaddress
import secrets
import socket
from urllib.parse import urlparse

from fastapi import Header, HTTPException, status

from app.settings import get_settings

# Historical hardcoded default (pre-fix). Rejected outright even if an
# operator's env still sets it explicitly, so this known-public string can
# never authenticate a request.
_INSECURE_DEFAULT_TOKEN = "change-this-local-token"


def require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    settings = get_settings()
    # Production worker is private at Cloud Run IAM level and Cloud Tasks/
    # Scheduler never send this header, so an empty token is the documented
    # no-op case there. Publicly reachable modes must set a real token -
    # enforced at startup in main.py.
    if not settings.internal_token:
        return
    if (
        settings.internal_token == _INSECURE_DEFAULT_TOKEN
        or not x_internal_token
        or not secrets.compare_digest(x_internal_token, settings.internal_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )


def _is_forbidden_ip(ip_text: str) -> bool:
    ip = ipaddress.ip_address(ip_text)
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _validate_and_resolve(
    url: str,
    allowed_hosts: list[str] | None,
) -> tuple[str, list[str]]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    if not parsed.hostname:
        raise ValueError("URL has no hostname")

    host = parsed.hostname.lower().rstrip(".")
    normalized_allowed = {
        value.lower().removeprefix("www.").rstrip(".")
        for value in (allowed_hosts or [])
        if value
    }
    host_without_www = host.removeprefix("www.")
    if normalized_allowed and not any(
        host_without_www == allowed
        or host_without_www.endswith("." + allowed)
        for allowed in normalized_allowed
    ):
        raise ValueError(f"Host {host} is outside the configured store allowlist")

    addresses = socket.getaddrinfo(
        host,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        type=socket.SOCK_STREAM,
    )
    if not addresses:
        raise ValueError("Hostname did not resolve")
    ips = []
    for result in addresses:
        ip_text = result[4][0]
        if _is_forbidden_ip(ip_text):
            raise ValueError(f"URL resolves to forbidden address {ip_text}")
        ips.append(ip_text)
    return host, ips


def validate_public_url(
    url: str,
    allowed_hosts: list[str] | None = None,
) -> str:
    _validate_and_resolve(url, allowed_hosts)
    return url


def resolve_pinned_ip(
    url: str,
    allowed_hosts: list[str] | None = None,
) -> tuple[str, str]:
    """Validate a URL and return the exact public IP that was approved.

    A normal validation lookup followed by an independent connection lookup
    leaves a DNS-rebinding window: the hostname can resolve publicly during
    validation and privately at connection time. Callers use this pinned IP
    for the socket connection while preserving the original Host header/SNI.
    """
    host, ips = _validate_and_resolve(url, allowed_hosts)
    return host, ips[0]
