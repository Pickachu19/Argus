import os
from unittest.mock import MagicMock

os.environ.setdefault("SECRET_KEY", "test-session-secret-at-least-32-characters")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-at-least-32-characters")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017/product-test")
os.environ.setdefault("USER_MONGO_URI", "mongodb://localhost:27017/user-test")
os.environ.setdefault("UPLOAD_FOLDER", "/tmp/secure-product-test-uploads")
os.environ.setdefault("COOKIE_SECURE", "false")

import app as service


class Cursor(list):
    def sort(self, *_args):
        return self

    def skip(self, count):
        return Cursor(self[count:])

    def limit(self, count):
        return Cursor(self[:count])


def client(rows=None):
    service.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    service.products = MagicMock()
    service.products.count_documents.return_value = len(rows or [])
    service.products.find.return_value = Cursor(rows or [])
    service.audit_logs = MagicMock()
    return service.app.test_client()


def sample_product():
    return {"_id": "507f1f77bcf86cd799439011", "name": "Keyboard", "description": "Mechanical keyboard", "price": 99.0}


def test_public_catalog_is_reachable():
    response = client([sample_product()]).get("/")
    assert response.status_code == 200
    assert b"Keyboard" in response.data


def test_public_api_returns_products():
    response = client([sample_product()]).get("/api/products")
    assert response.status_code == 200
    assert response.json["products"][0]["name"] == "Keyboard"


def test_manage_requires_authentication():
    response = client().get("/manage")
    assert response.status_code in {302, 401}


def test_health_is_database_independent():
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json["service"] == "product-service"
