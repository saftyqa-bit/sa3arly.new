from app.main import app, bot_policy


def test_bot_policy_is_public_and_matches_hourly_schedule():
    response = bot_policy()

    assert response.status_code == 200
    assert response.media_type == "text/html"
    assert "/bot" in {route.path for route in app.routes if hasattr(route, "path")}
    body = response.body.decode("utf-8")
    assert "سياسة روبوت سعرلي" in body
    assert "مرة كل ساعة" in body
    assert "مرة كل 12 ساعة" not in body
    assert "مرة كل 3 ساعات" not in body
