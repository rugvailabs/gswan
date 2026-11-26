import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "test.db"))


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_and_get_item(client):
    r = client.post("/items", json={"name": "first"})
    assert r.status_code == 201
    item = r.json()
    assert item["id"].startswith("item_") and item["name"] == "first" and "createdAt" in item
    assert client.get(f"/items/{item['id']}").json() == item
    assert client.get("/items/item_missing").status_code == 404


def test_list_is_paginated(client):
    for i in range(5):
        client.post("/items", json={"name": f"n{i}"})
    page = client.get("/items", params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 5 and page["limit"] == 2 and page["offset"] == 1
    assert [x["name"] for x in page["items"]] == ["n1", "n2"]


def test_rejects_bad_input(client):
    assert client.post("/items", json={"name": ""}).status_code == 422
    assert client.get("/items", params={"limit": 0}).status_code == 422
