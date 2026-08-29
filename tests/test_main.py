def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_serves_the_page_as_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_app_js_is_served_as_javascript(client):
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")


def test_app_css_is_served_as_css(client):
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
