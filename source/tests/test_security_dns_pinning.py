import socket

import pytest

from app.security import resolve_pinned_ip, validate_public_url


def test_resolve_pinned_ip_returns_the_exact_validated_address(monkeypatch):
    calls = []

    def fake_getaddrinfo(host, port, *, type):
        calls.append((host, port, type))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    host, ip = resolve_pinned_ip(
        "https://shop.example/product/1", ["shop.example"]
    )
    assert host == "shop.example"
    assert ip == "93.184.216.34"
    assert calls == [("shop.example", 443, socket.SOCK_STREAM)]


def test_private_resolution_is_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *, type: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ],
    )
    with pytest.raises(ValueError, match="forbidden address"):
        validate_public_url("https://shop.example/product/1", ["shop.example"])


def test_allowlist_rejects_suffix_confusion(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *, type: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ],
    )
    with pytest.raises(ValueError, match="outside the configured store allowlist"):
        validate_public_url("https://shop.example.attacker.test/product", ["shop.example"])
