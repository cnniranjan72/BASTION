from bastion_catalog.data import CATALOG
from bastion_catalog.main import app
from fastapi.testclient import TestClient


def test_healthz_returns_ok():
    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "catalog"}


def test_list_catalog_returns_every_seeded_item():
    client = TestClient(app)
    response = client.get("/catalog")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == len(CATALOG)
    assert {item["sku"] for item in body} == {item.sku for item in CATALOG}


def test_get_catalog_item_by_sku():
    client = TestClient(app)
    response = client.get("/catalog/SSD-1TB")

    assert response.status_code == 200
    body = response.json()
    assert body["sku"] == "SSD-1TB"
    assert body["price_inr"] == 5999


def test_get_catalog_item_unknown_sku_is_404():
    client = TestClient(app)
    response = client.get("/catalog/NOT-A-REAL-SKU")

    assert response.status_code == 404
