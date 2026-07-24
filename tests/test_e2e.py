import os
import urllib3

import pytest
import requests
from bs4 import BeautifulSoup

pytestmark = pytest.mark.integration
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
BASE_URL = os.getenv("E2E_BASE_URL", "https://localhost")


def csrf(html):
    return BeautifulSoup(html, "html.parser").find("input", {"name": "csrf_token"})["value"]


def login(email, password):
    session = requests.Session()
    page = session.get(f"{BASE_URL}/user/login", verify=False, timeout=10)
    response = session.post(
        f"{BASE_URL}/user/login",
        data={"csrf_token": csrf(page.text), "email": email, "password": password},
        verify=False,
        allow_redirects=False,
        timeout=10,
    )
    assert response.status_code == 302
    return session


def test_register_login_rbac_and_product_crud():
    email = "integration-user@example.com"
    password = "Integration-user-123!"
    user = requests.Session()
    page = user.get(f"{BASE_URL}/user/register", verify=False, timeout=10)
    response = user.post(f"{BASE_URL}/user/register", data={
        "csrf_token": csrf(page.text), "email": email, "password": password, "confirm_password": password,
    }, verify=False, allow_redirects=False, timeout=10)
    assert response.status_code == 302

    user = login(email, password)
    assert user.get(f"{BASE_URL}/product/manage", verify=False, allow_redirects=False, timeout=10).status_code == 302

    admin = login(os.getenv("ADMIN_EMAIL", "admin@example.com"), os.environ["ADMIN_PASSWORD"])
    page = admin.get(f"{BASE_URL}/product/add", verify=False, timeout=10)
    response = admin.post(f"{BASE_URL}/product/add", data={
        "csrf_token": csrf(page.text),
        "csrf_access_token": admin.cookies["csrf_access_token"],
        "name": "Integration Product", "description": "Created by the end-to-end test", "price": "12.50",
    }, verify=False, allow_redirects=False, timeout=10)
    assert response.status_code == 302

    manage = admin.get(f"{BASE_URL}/product/manage", verify=False, timeout=10)
    soup = BeautifulSoup(manage.text, "html.parser")
    edit_path = soup.find("a", string="Edit")["href"]
    edit_page = admin.get(f"{BASE_URL}{edit_path}", verify=False, timeout=10)
    response = admin.post(f"{BASE_URL}{edit_path}", data={
        "csrf_token": csrf(edit_page.text),
        "csrf_access_token": admin.cookies["csrf_access_token"],
        "name": "Updated Integration Product", "description": "Updated by the test", "price": "14.50",
    }, verify=False, allow_redirects=False, timeout=10)
    assert response.status_code == 302

    manage = admin.get(f"{BASE_URL}/product/manage", verify=False, timeout=10)
    soup = BeautifulSoup(manage.text, "html.parser")
    delete_path = soup.select_one("button.js-delete")["data-delete-url"]
    response = admin.post(f"{BASE_URL}{delete_path}", data={
        "csrf_token": csrf(manage.text), "csrf_access_token": admin.cookies["csrf_access_token"],
    }, verify=False, allow_redirects=False, timeout=10)
    assert response.status_code == 302
