import inspect

from app.scraping.fetcher import SafeFetcher


def test_fetcher_uses_the_hardened_transport():
    assert SafeFetcher.__module__ == "app.scraping.secure_fetcher"
    robots_source = inspect.getsource(SafeFetcher._robots_allowed)
    request_source = inspect.getsource(SafeFetcher._fetch_http_once)
    assert "robots_can_fetch" in robots_source
    assert "self.fetch_http(" in robots_source
    assert "resolve_pinned_ip" in request_source
    assert "with client.stream(" in request_source
    assert '"Host"' in request_source
    assert '"sni_hostname"' in request_source
    assert "client.stream(\"GET\", current)" not in request_source
