"""Scraping and extraction components.

Import and patch the transport class at package initialization so every
existing `from app.scraping.fetcher import SafeFetcher` call receives the
security-hardened subclass without duplicating or replacing the legacy module.
"""

from app.scraping import fetcher as _fetcher
from app.scraping.secure_fetcher import SafeFetcher as _SecureSafeFetcher

_fetcher.SafeFetcher = _SecureSafeFetcher
