from app.scraping.installments import extract_installment_plans


def test_arabic_installment_extraction():
    text = (
        "قسط مع Valu ابتداء من 9,850 جنيه شهريا لمدة 12 شهر "
        "بدون فوائد ومصاريف إدارية 1,200 جنيه."
    )
    plans = extract_installment_plans(
        text,
        source_url="https://store.example/phone",
        cash_price=104990,
    )
    assert plans
    plan = plans[0]
    assert plan.provider_name == "Valu"
    assert plan.months == 12
    assert plan.periodic_payment == 9850
    assert plan.admin_fees == 1200
    assert plan.interest_free is True
    assert plan.starting_from_only is True


def test_interest_is_unknown_when_page_does_not_state_it():
    plans = extract_installment_plans(
        "قسط شهري 2,500 جنيه لمدة 12 شهر",
        source_url="https://store.example/item",
        cash_price=25000,
    )
    assert plans
    assert plans[0].interest_free is None


def test_custom_provider_alias():
    plans = extract_installment_plans(
        "قسط شهري 2500 جنيه لمدة 12 شهر مع My Finance",
        source_url="https://example.com/product",
        cash_price=25000,
        provider_aliases={"my finance": {"name": "My Finance Egypt", "type": "consumer_finance"}},
    )
    assert plans
    assert plans[0].provider_name == "My Finance Egypt"
    assert plans[0].provider_type == "consumer_finance"


def test_warranty_duration_is_not_an_installment_plan():
    plans = extract_installment_plans(
        (
            "Apple iPhone 17 Pro EGP 93,777 Lowest in 30 days. "
            "Warranty: 24 months warranty. Sold by vxstore."
        ),
        source_url=(
            "https://btech.com/en/p/"
            "apple-iphone-17-pro-256gb-12gb-ram-5g-cosmic-orange"
        ),
        cash_price=93777,
    )
    assert plans == []


def test_real_english_monthly_plan_still_extracts_duration_and_payment():
    plans = extract_installment_plans(
        "Pay in 12 months with Valu installments. Monthly payment: 8,500 EGP.",
        source_url="https://store.example/phone",
        cash_price=93777,
    )
    assert plans
    plan = plans[0]
    assert plan.provider_name == "Valu"
    assert plan.months == 12
    assert plan.periodic_payment == 8500
