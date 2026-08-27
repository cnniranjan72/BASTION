"""Static catalog for a fictional small D2C electronics-accessories merchant
on Razorpay — Track 01's "make a merchant transactable by an AI buyer" needs
something for a buyer agent to actually buy. A JSON file/dict, not a real
database table: this is a buildathon-demo storefront, not a claim that
BASTION now includes merchant/inventory management. `price_inr` is in whole
rupees (display units) — `main.py`'s purchase flow is the thing that
converts to paise for Razorpay's Orders API, not this module.
"""

from __future__ import annotations

from pydantic import BaseModel


class CatalogItem(BaseModel):
    sku: str
    name: str
    price_inr: int
    description: str
    stock: int


CATALOG: list[CatalogItem] = [
    CatalogItem(
        sku="EARBUDS-PRO",
        name="Wireless Earbuds Pro",
        price_inr=1499,
        description="Bluetooth 5.3 earbuds, active noise cancellation, 24h case battery.",
        stock=42,
    ),
    CatalogItem(
        sku="CHARGER-65W",
        name="USB-C Fast Charger 65W",
        price_inr=899,
        description="GaN fast charger, one USB-C PD port, compact travel size.",
        stock=120,
    ),
    CatalogItem(
        sku="SLEEVE-14",
        name="Laptop Sleeve 14-inch",
        price_inr=649,
        description="Padded neoprene sleeve, fits most 14-inch laptops.",
        stock=75,
    ),
    CatalogItem(
        sku="KEYBOARD-TKL",
        name="Mechanical Keyboard (TKL)",
        price_inr=3299,
        description="Tenkeyless mechanical keyboard, hot-swappable switches, USB-C.",
        stock=18,
    ),
    CatalogItem(
        sku="SSD-1TB",
        name="Portable SSD 1TB",
        price_inr=5999,
        description="USB 3.2 portable SSD, up to 1050MB/s read.",
        stock=9,
    ),
    CatalogItem(
        sku="MOUSE-ERGO",
        name="Ergonomic Mouse",
        price_inr=799,
        description="Vertical ergonomic mouse, wireless, adjustable DPI.",
        stock=60,
    ),
    CatalogItem(
        sku="WEBCAM-1080P",
        name="Webcam 1080p",
        price_inr=1999,
        description="1080p/30fps USB webcam with built-in privacy shutter.",
        stock=33,
    ),
    CatalogItem(
        sku="POWERBANK-20K",
        name="Power Bank 20000mAh",
        price_inr=1299,
        description="20000mAh power bank, 20W PD fast charging, dual USB-A + USB-C.",
        stock=55,
    ),
]

CATALOG_BY_SKU: dict[str, CatalogItem] = {item.sku: item for item in CATALOG}
