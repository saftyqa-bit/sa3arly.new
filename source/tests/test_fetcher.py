from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.schemas import FetchResult
from app.scraping.fetcher import FetchError, parse_retry_after
from app.scraping.secure_fetcher import SafeFetcher
from app.settings import Settings


def test_http_403_uses_browser_fallback(monkeypatch: pytest.MonkeyPatch):
    fetcher = SafeFetcher()
    fetcher.settings = Settings(
        _env_file=None,
        enable_browser_fallback=True,
    )
    browser_result = FetchResult(
        url="https://shop.example/product",
        final_url="https://shop.example/product",
        status_code=200,
        body="<html>price</html>",
        used_browser=True,
    )

    def blocked_http(*args, **kwargs):
        raise FetchError("HTTP 403 from store", code="blocked", status_code=403)

    monkeypatch.setattr(fetcher, "fetch_http", blocked_http)
    monkeypatch.setattr(fetcher, "fetch_browser", lambda *args, **kwargs: browser_result)

    result = fetcher.fetch(
        "https://shop.example/product",
        allowed_hosts=["shop.example"],
        respect_robots=True,
        browser_required=False,
    )

    assert result.used_browser is True


def test_retry_after_supports_delta_seconds_and_http_dates():
    now = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
    assert parse_retry_after("120", now=now) == 120
    retry_at = (now + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(retry_at, now=now) == 90
    assert parse_retry_after("7200", now=now, max_seconds=3600) == 3600
    assert parse_retry_after("not-a-delay", now=now) is None


def test_http2_remote_protocol_error_retries_once_with_http1(
    monkeypatch: pytest.MonkeyPatch,
):
    protocols: list[bool] = []

    class Response:
        status_code = 200
        headers = {"content-type": "text/html"}
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield b"<html>price 100</html>"

    class Client:
        def __init__(self, *args, http2: bool, **kwargs):
            self.http2 = http2
            protocols.append(http2)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, *, headers, extensions):
            assert method == "GET"
            assert headers["Host"] == "shop.example"
            assert extensions["sni_hostname"] == "shop.example"
            if self.http2:
                raise httpx.RemoteProtocolError("HTTP/2 StreamReset")
            return Response()

    monkeypatch.setattr(
        "app.scraping.fetcher.validate_public_url",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "app.scraping.secure_fetcher.resolve_pinned_ip",
        lambda *args: ("shop.example", "93.184.216.34"),
    )
    monkeypatch.setattr("app.scraping.secure_fetcher.httpx.Client", Client)
    fetcher = SafeFetcher()
    fetcher.settings = Settings(_env_file=None, respect_robots_txt=False)

    result = fetcher.fetch_http(
        "https://shop.example/product",
        allowed_hosts=["shop.example"],
        respect_robots=False,
    )

    assert result.status_code == 200
    assert protocols == [True, False]

def test_http2_typeerror_retries_once_with_http1(
    monkeypatch: pytest.MonkeyPatch,
):
    protocols: list[bool] = []

    class Response:
        status_code = 200
        headers = {"content-type": "text/html"}
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield b"<html>price 100</html>"

    class Client:
        def __init__(self, *args, http2: bool, **kwargs):
            self.http2 = http2
            protocols.append(http2)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url, *, headers, extensions):
            assert method == "GET"
            assert headers["Host"] == "shop.example"
            assert extensions["sni_hostname"] == "shop.example"
            if self.http2:
                raise TypeError("incompatible HTTP/2 transport state")
            return Response()

    monkeypatch.setattr(
        "app.scraping.fetcher.validate_public_url",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "app.scraping.secure_fetcher.resolve_pinned_ip",
        lambda *args: ("shop.example", "93.184.216.34"),
    )
    monkeypatch.setattr("app.scraping.secure_fetcher.httpx.Client", Client)
    fetcher = SafeFetcher()
    fetcher.settings = Settings(_env_file=None, respect_robots_txt=False)

    result = fetcher.fetch_http(
        "https://shop.example/product",
        allowed_hosts=["shop.example"],
        respect_robots=False,
    )

    assert result.status_code == 200
    assert protocols == [True, False]


def test_url_allowlist_rejection_is_a_typed_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
):
    def reject_url(*args, **kwargs):
        raise ValueError(
            "Host redirect.example is outside the configured store allowlist"
        )

    monkeypatch.setattr("app.scraping.fetcher.validate_public_url", reject_url)
    fetcher = SafeFetcher()
    fetcher.settings = Settings(_env_file=None, respect_robots_txt=False)

    with pytest.raises(FetchError) as error:
        fetcher.fetch_http(
            "https://shop.example/product",
            allowed_hosts=["shop.example"],
            respect_robots=False,
        )

    assert error.value.code == "host_not_allowed"
    assert "redirect.example" not in str(error.value)


def test_dns_rejection_is_a_typed_network_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.scraping.fetcher.validate_public_url",
        lambda *args: (_ for _ in ()).throw(socket.gaierror("DNS unavailable")),
    )
    fetcher = SafeFetcher()
    fetcher.settings = Settings(_env_file=None, respect_robots_txt=False)

    with pytest.raises(FetchError) as error:
        fetcher.fetch_http(
            "https://shop.example/product",
            allowed_hosts=["shop.example"],
            respect_robots=False,
        )

    assert error.value.code == "network_error"


def test_pinned_redirect_allowlist_rejection_is_a_typed_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "app.scraping.fetcher.validate_public_url",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "app.scraping.secure_fetcher.resolve_pinned_ip",
        lambda *args: (_ for _ in ()).throw(
            ValueError("Host other.example is outside the configured store allowlist")
        ),
    )
    fetcher = SafeFetcher()
    fetcher.settings = Settings(_env_file=None, respect_robots_txt=False)

    with pytest.raises(FetchError) as exc_info:
        fetcher.fetch_http(
            "https://shop.example/product",
            allowed_hosts=["shop.example"],
            respect_robots=False,
        )

    assert exc_info.value.code == "host_not_allowed"
    assert "other.example" not in str(exc_info.value)


def test_robots_request_uses_the_hardened_http_fetch(monkeypatch: pytest.MonkeyPatch):
    fetcher = SafeFetcher()
    fetcher.settings = Settings(_env_file=None, respect_robots_txt=True)
    calls: list[tuple[str, list[str], bool]] = []

    def fake_fetch_http(url, *, allowed_hosts, respect_robots, cache_headers=None):
        calls.append((url, allowed_hosts, respect_robots))
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            body="User-agent: *\nDisallow: /private",
        )

    monkeypatch.setattr(fetcher, "fetch_http", fake_fetch_http)

    assert fetcher._robots_allowed(
        "https://shop.example/product",
        ["shop.example"],
    ) is True
    assert calls == [
        ("https://shop.example/robots.txt", ["shop.example"], False)
    ]
