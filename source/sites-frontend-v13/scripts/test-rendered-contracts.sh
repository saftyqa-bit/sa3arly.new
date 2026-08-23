#!/usr/bin/env bash
set -euo pipefail

# Keep every legacy SEO/catalog contract that remains semantically valid. The
# old homepage and old cash/installment wording are replaced by the phase-two
# contracts, which verify true-cost sorting, sparklines, pagination and alerts.
RETAINED='homepage exposes Organization and WebSite structured data|filter and legacy product query combinations are noindex with clean canonical|the eight main categories use the requested /c routes and crawlable metadata|the approved category order, totals, and hierarchy are rendered|legacy main-category routes permanently redirect to the new canonicals|specific product-type pages remain available below the main categories|twenty distinct product pages render public initial HTML and truthful no-price state|mapped no-price product has Product schema without Offer or AggregateOffer|one visible EGP price produces one Offer with matching displayed value|multiple visible EGP prices produce a matching AggregateOffer|brand and store pages contain real product links and self-canonicals|awaiting-mapping product remains noindex and is excluded from product sitemap|sitemap index and child maps contain only canonical public pages with real lastmod|robots.txt allows public pages, protects private surfaces, and declares all sitemaps|database, mappings, authentication-free comparison, and analytics stay intact|same-domain Open Graph and product illustration assets are valid'

node --test \
  --test-concurrency=1 \
  --test-name-pattern="^(${RETAINED})$" \
  tests/rendered-html.test.mjs

node --test \
  --test-concurrency=1 \
  tests/phase-two-rendered.test.mjs
