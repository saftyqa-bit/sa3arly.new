from app.scraping.robots import robots_can_fetch


def test_amazon_specific_allow_overrides_broad_disallow():
    robots_text = "\n".join(
        [
            "User-agent: *",
            "Disallow: /-/",
            "Allow: /-/en/",
            "Allow: /-/en$",
        ]
    )
    url = "https://www.amazon.eg/-/en/Canon-Camera/dp/B08C68F2DX"
    assert robots_can_fetch(robots_text, "Sa3arlyPriceBot/1.0", url) is True


def test_more_specific_disallow_still_wins():
    robots_text = "\n".join(
        ["User-agent: *", "Allow: /", "Disallow: /private/"]
    )
    assert robots_can_fetch(
        robots_text, "Sa3arlyPriceBot/1.0", "https://shop.example/private/order"
    ) is False


def test_named_bot_group_does_not_apply_to_sa3arly():
    robots_text = "\n".join(
        [
            "User-agent: GPTBot",
            "Disallow: /",
            "",
            "User-agent: *",
            "Disallow: /gp/cart",
        ]
    )
    assert robots_can_fetch(
        robots_text, "Sa3arlyPriceBot/1.0", "https://shop.example/dp/B000123"
    ) is True
    assert robots_can_fetch(
        robots_text, "GPTBot", "https://shop.example/dp/B000123"
    ) is False


def test_wildcard_and_end_anchor_are_supported():
    robots_text = "\n".join(
        ["User-agent: *", "Disallow: /*?sort=", "Allow: /catalog$" ]
    )
    assert robots_can_fetch(
        robots_text, "Sa3arlyPriceBot/1.0", "https://shop.example/list?sort=price"
    ) is False
    assert robots_can_fetch(
        robots_text, "Sa3arlyPriceBot/1.0", "https://shop.example/catalog"
    ) is True
