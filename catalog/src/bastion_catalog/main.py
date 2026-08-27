"""GET /catalog — the merchant's agent-readable product list. Read-only,
no auth (a real storefront's product list is public by nature; nothing
here is a governance decision, so it has no business being behind the
same auth as interceptor's control-plane API). Port 4003, following
4001 (interceptor) / 4002 (aggregator)'s numbering.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .data import CATALOG, CATALOG_BY_SKU, CatalogItem

app = FastAPI(title="bastion-catalog", version="0.0.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "catalog"}


@app.get("/catalog")
async def list_catalog() -> list[CatalogItem]:
    return CATALOG


@app.get("/catalog/{sku}")
async def get_catalog_item(sku: str) -> CatalogItem:
    item = CATALOG_BY_SKU.get(sku)
    if item is None:
        raise HTTPException(status_code=404, detail=f"no catalog item with sku {sku!r}")
    return item
