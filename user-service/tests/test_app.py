import os
from unittest.mock import MagicMock

os.environ.setdefault("SECRET_KEY", "test-session-secret-at-least-32-characters")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-at-least-32-characters")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/user-test")
os.environ.setdefault("COOKIE_SECURE", "true")

import app as service


def client():
    service.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    service.users = MagicMock()
    service.refresh_tokens = MagicMock()
    service.revoked_tokens = MagicMock()
    return service.app.test_client()


def test_health_is_database_independent():
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json["status"] == "ok"


def test_registration_page_has_field_labels():
    response = client().get("/register")
    assert response.status_code == 200
    assert b"Confirm password" in response.data


def test_login_sets_hardened_access_and_refresh_cookies():
    test_client = client()
    service.users.find_one.return_value = {
        "email": "admin@example.com",
        "password": service.generate_password_hash("Correct-password-123!"),
        "role": "admin",
        "disabled": False,
    }
    response = test_client.post("/login", data={"email": "admin@example.com", "password": "Correct-password-123!"})
    cookies = "\n".join(response.headers.getlist("Set-Cookie"))
    assert response.status_code == 302
    assert "access_token_cookie=" in cookies
    assert "refresh_token_cookie=" in cookies
    assert "Secure" in cookies
    assert "HttpOnly" in cookies
    assert "SameSite=Lax" in cookies


def test_invalid_login_does_not_issue_token():
    test_client = client()
    service.users.find_one.return_value = None
    response = test_client.post("/login", data={"email": "nobody@example.com", "password": "Wrong-password-123!"})
    assert response.status_code == 200
    assert b"Invalid credentials" in response.data
    assert "access_token_cookie=" not in "\n".join(response.headers.getlist("Set-Cookie"))
