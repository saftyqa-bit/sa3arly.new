from app.scraping.document import parse_document


def test_jsonld_product_offer():
    html = """
    <html><head><title>Phone</title>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Product",
      "name": "Apple iPhone 17 Pro Max 256GB",
      "sku": "IP17PM256",
      "brand": {"@type": "Brand", "name": "Apple"},
      "image": "https://cdn.store.example/products/phone.webp",
      "offers": {
        "@type": "Offer",
        "price": "104990",
        "priceCurrency": "EGP",
        "availability": "https://schema.org/InStock",
        "url": "/iphone-17-pro-max"
      }
    }
    </script></head><body></body></html>
    """
    doc = parse_document(html, "https://store.example/product", "text/html")
    priced = [x for x in doc.candidates if x.price]
    assert priced
    assert priced[0].price == 104990
    assert priced[0].currency == "EGP"
    assert priced[0].image_url == "https://cdn.store.example/products/phone.webp"
    assert priced[0].availability == "available"
    assert priced[0].url == "https://store.example/iphone-17-pro-max"


def test_cash_price_selector_beats_monthly_installment_value():
    html = """
    <html><body>
      <article class="product-card">
        <h2>Apple iPhone 17 Pro Max 256GB</h2>
        <a href="/iphone-17-pro-max">Open</a>
        <span itemprop="price" content="104990">104,990 EGP</span>
        <span class="price">9,850 EGP monthly</span>
      </article>
    </body></html>
    """
    doc = parse_document(html, "https://store.example/category", "text/html")
    candidates = [x for x in doc.candidates if x.title.startswith("Apple iPhone") and x.price]
    assert candidates
    assert candidates[0].price == 104990


def test_generic_price_class_separates_cash_from_monthly_cluster():
    html = """
    <html><body><article class="product-card">
      <h2>Apple iPhone 17 Pro Max 256GB</h2><a href="/phone">Open</a>
      <span class="price">104,990 EGP</span>
      <span class="price">109,990 EGP</span>
      <span class="price">9,850 EGP monthly installment</span>
    </article></body></html>
    """
    doc = parse_document(html, "https://store.example/category", "text/html")
    candidates = [x for x in doc.candidates if x.title.startswith("Apple iPhone") and x.price]
    assert candidates
    assert candidates[0].price == 104990
    assert candidates[0].old_price == 109990


def test_dynamic_direct_page_extracts_labeled_egp_near_h1():
    html = """
    <html><head><title>Unionaire INV-ARTO012 | B.TECH</title></head><body>
      <main>
        <h1>Unionaire Artify Inverter Split Air Conditioner - INV-ARTO012</h1>
        <div>EGP 18,380 Lowest in 30 days</div>
        <div>From 1,268.62/mo with mylo</div>
        <button>Add to cart</button>
        <div>Other sellers for this product 18,380 EGP</div>
      </main>
    </body></html>
    """
    doc = parse_document(html, "https://btech.com/en/p/example", "text/html")
    candidates = [candidate for candidate in doc.candidates if candidate.price]
    assert candidates
    assert candidates[0].price == 18_380
    assert candidates[0].currency == "EGP"
    assert candidates[0].source_method == "html_visible_direct"


def test_dynamic_direct_page_does_not_treat_unlabeled_monthly_amount_as_cash():
    html = """
    <html><head><title>Phone | Store</title></head><body>
      <main><h1>Example Phone 256GB</h1><div>From 1,268.62/mo</div></main>
    </body></html>
    """
    doc = parse_document(html, "https://store.example/p/example", "text/html")
    assert not any(candidate.price for candidate in doc.candidates)


def test_dynamic_direct_page_does_not_extract_price_from_alphanumeric_sku():
    html = """
    <html><head><title>Anker Soundcore Q11i - A3005 | B.TECH</title></head><body>
      <main>
        <h1>Anker Soundcore Q11i Wireless Headphones, Black - A3005</h1>
        <div>A3005 EGP</div>
      </main>
    </body></html>
    """
    doc = parse_document(
        html,
        "https://btech.com/en/p/anker-soundcore-q11i-wireless-headphones-black-a3005",
        "text/html",
    )
    assert not any(candidate.price for candidate in doc.candidates)


def test_structured_direct_price_suppresses_conflicting_visible_fallback():
    html = """
    <html><head>
      <title>LG Washing Machine F4X5RYG24</title>
      <meta property="og:title" content="LG Washing Machine F4X5RYG24">
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "LG Washing Machine F4X5RYG24",
        "sku": "0c42d0b7-7b78-4b57-b43c-8328769d1649",
        "offers": {
          "@type": "Offer",
          "price": 31899,
          "priceCurrency": "EGP",
          "url": "https://btech.com/en/p/0c42d0b7-7b78-4b57-b43c-8328769d1649"
        }
      }
      </script>
    </head><body><main>
      <h1>LG Washing Machine F4X5RYG24</h1>
      <div>Promotion starts from EGP 24</div>
    </main></body></html>
    """
    doc = parse_document(
        html,
        "https://btech.com/en/p/0c42d0b7-7b78-4b57-b43c-8328769d1649",
        "text/html",
    )

    priced = [candidate for candidate in doc.candidates if candidate.price is not None]
    assert len(priced) == 1
    assert priced[0].price == 31_899
    assert priced[0].source_method == "jsonld_offer"
    assert not any(candidate.source_method == "html_visible_direct" for candidate in doc.candidates)


def test_q11i_structured_price_suppresses_sku_derived_visible_false_price():
    url = "https://btech.com/en/p/anker-soundcore-q11i-wireless-headphones-black-a3005"
    html = f"""
    <html><head>
      <title>Anker Soundcore Q11i Wireless Headphones, Black - A3005 | B.TECH</title>
      <meta property="og:title"
            content="Anker Soundcore Q11i Wireless Headphones, Black - A3005 | B.TECH">
      <script type="application/ld+json">
      {{
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Anker Soundcore Q11i Over Ear Headphones , Black - A3005",
        "sku": "anker-soundcore-q11i-wireless-headphones-black-a3005",
        "brand": {{"@type": "Brand", "name": "Anker"}},
        "offers": {{
          "@type": "Offer",
          "price": 4176,
          "priceCurrency": "EGP",
          "availability": "https://schema.org/InStock",
          "url": "{url}"
        }}
      }}
      </script>
    </head><body><main>
      <h1>Anker Soundcore Q11i Wireless Headphones, Black - A3005</h1>
      <div>A3005 EGP</div>
    </main></body></html>
    """
    doc = parse_document(html, url, "text/html")

    priced = [candidate for candidate in doc.candidates if candidate.price is not None]
    assert len(priced) == 1
    assert priced[0].price == 4_176
    assert priced[0].availability == "available"
    assert priced[0].source_method == "jsonld_offer"
    assert not any(candidate.source_method == "html_visible_direct" for candidate in doc.candidates)


def test_structured_price_suppresses_visible_fallback_when_page_titles_differ():
    url = "https://btech.com/en/p/example-q11i"
    html = f"""
    <html><head>
      <title>Anker Soundcore Q11i Wireless Headphones, Black - A3005 | B.TECH</title>
      <script type="application/ld+json">
      {{
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Anker Soundcore Q11i Over Ear Headphones , Black - A3005",
        "offers": {{
          "@type": "Offer",
          "price": 4176,
          "priceCurrency": "EGP",
          "url": "{url}"
        }}
      }}
      </script>
    </head><body><main>
      <h1>Anker Soundcore Q11i Wireless Headphones, Black - A3005</h1>
      <div>Promotion starts from EGP 3005</div>
    </main></body></html>
    """
    doc = parse_document(html, url, "text/html")

    priced = [candidate for candidate in doc.candidates if candidate.price is not None]
    assert len(priced) == 1
    assert priced[0].price == 4_176
    assert priced[0].source_method == "jsonld_offer"
