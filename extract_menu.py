#!/usr/bin/env python3
"""Export Diet Food's SnappFood menu for the brand's own website.

Run:  python3 extract_menu.py

Writes everything to ./export/:
  menu.json           restaurant info + categories -> items -> add-on groups -> options
  images/items/       item photos at the original upload resolution, named <item id>_<n>.<ext>
  images/restaurant/  logo and cover
  raw/                the untouched API responses, a safety net for anything menu.json leaves out

Prices are in Toman. Needs only the Python standard library.

Run it while the restaurant is open: SnappFood's menu is incomplete while it's closed (an export at
04:43 got 23 of 83 items), so the script stops without writing anything when it is.
"""

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

VENDOR_CODE = "jy28j9"
APP_PARAMS = "optionalClient=SUPERAPP&client=SUPERAPP&deviceType=SUPERAPP&appVersion=6.0.0"
SOURCES = {
    "menu": f"https://apigw.snappfood.ir/menu-read-model/{VENDOR_CODE}?{APP_PARAMS}",
    "vendor_details": f"https://apigw.snappfood.ir/menu-read-model/vendor-details/{VENDOR_CODE}?{APP_PARAMS}",
    "vendor_review": f"https://apigw.snappfood.ir/menu-read-model/vendor-review/{VENDOR_CODE}?{APP_PARAMS}",
    "vendor_rewards": f"https://apigw.snappfood.ir/menu-read-model/vendor-rewards/{VENDOR_CODE}?{APP_PARAMS}",
    # SnappFood's older website API: used to cross-check the item list, and kept in raw/ for the
    # fields the newer API doesn't return (packaging price, VAT, listed phone).
    # Returned 404 from 2026-11-23, so the export runs without it (see OPTIONAL_SOURCES).
    "legacy_menu": "https://snappfood.ir/mobile/v2/restaurant/details/dynamic"
    f"?optionalClient=WEBSITE&client=WEBSITE&deviceType=WEBSITE&appVersion=8.1.1&vendorCode={VENDOR_CODE}&fetch-static-data=1",
}
# Sources the export can do without, and what is lost when one of them fails.
OPTIONAL_SOURCES = {
    "legacy_menu": "categories keep SnappFood's app order, items listed only in a deal section go under that "
    "section's name, and the missing-item cross-check is skipped",
}
WEEK = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
OUT = Path(__file__).resolve().parent / "export"
HEADERS = {"User-Agent": "diet-food-menu-export/1.0"}


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as resp:
        return resp.read()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extension(url):
    return Path(urllib.parse.urlparse(url).path).suffix or ".jpg"


def original_image_url(url):
    """SnappFood links to resized copies; the original upload sits at the same path minus the resize segment."""
    url = re.sub(r"^(https://cdn\.snappfood\.ir)/\d+x\d+/", r"\1/", url)
    return re.sub(r"/media/cache/[^/]+/", "/", url)


def save_image(url, rel_path, warnings):
    """Download the original upload if the CDN still has it, else the resized copy."""
    for candidate in dict.fromkeys([original_image_url(url), url]):
        try:
            data = fetch(candidate)
        except urllib.error.HTTPError:
            continue
        (OUT / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (OUT / rel_path).write_bytes(data)
        return {"file": rel_path.as_posix(), "source_url": candidate}
    warnings.append(f"Could not download {url}")
    return {"file": None, "source_url": url}


def build_item(product, warnings, regular_discount=None):
    variations = product["variations"]
    if len(variations) != 1:
        sys.exit(
            f"'{product['title']}' has {len(variations)} sizes, and menu.json has no field for sizes yet. "
            "Update build_item() before exporting (export/raw/ is already saved)."
        )
    v = variations[0]
    # regular_discount: the restaurant's own discount, for items whose SnappFood listing carries a temporary deal
    discount = regular_discount or v["discount"] or {"amount": 0, "ratio": 0}
    image_urls = dict.fromkeys(img["src"] for img in product["images"] + v["images"])
    return {
        "id": v["id"],
        "name": product["title"],
        "description": product["description"],
        "price": v["price"],
        "discount_percent": discount["ratio"],
        "discount_amount": discount["amount"],
        "final_price": v["price"] - discount["amount"],
        "available": v["active"] and not v["disabledUntil"],
        "badge": product["popularityBadge"] or None,
        "rating_out_of_10": product["rate"],
        "images": [
            save_image(url, Path("images", "items", f"{v['id']}_{n}{extension(url)}"), warnings)
            for n, url in enumerate(image_urls, 1)
        ],
        "addon_groups": [
            {
                "id": group["id"],
                "name": group["title"],
                "min_select": group["minCount"],
                "max_select": group["maxCount"],
                "options": [
                    {"id": t["id"], "name": t["title"], "description": t["description"], "price": t["price"]}
                    for t in group["toppings"]
                ],
            }
            for group in v["toppingGroups"]
        ],
    }


def build_restaurant(details, review, warnings):
    hours = sorted(
        (
            {"day": day, "open": group["startHour"], "close": group["stopHour"]}
            for group in details["scheduleGroups"]
            for day in group["weekdays"]
        ),
        key=lambda h: (WEEK.index(h["day"]), h["open"]),
    )
    return {
        "name": details["title"],
        "city": details["cityTitle"],
        "address": details["address"]["address"],
        "location": {"latitude": details["address"]["latitude"], "longitude": details["address"]["longitude"]},
        "opening_hours": hours,
        "cuisine_tags": details["productTypes"],
        "min_order": details["minOrder"],
        "logo": save_image(details["logo"], Path("images", "restaurant", "logo" + extension(details["logo"])), warnings),
        "cover": save_image(details["cover"], Path("images", "restaurant", "cover" + extension(details["cover"])), warnings),
        "rating": {
            "score_out_of_5": round(review["rate"] / 2, 1),
            "score_out_of_10": review["rate"],
            "ratings_count": review["rateCount"],
            "reviews_count": review["reviewCount"],
            "stars_percent": review["rateDiversity"],
        },
    }


def build_categories(menu_categories, legacy_menus, warnings):
    """The menu's own categories, plus items SnappFood lists only in one of its generated sections."""
    # The older API lists every item under its real category; its product ids are the newer API's variation ids.
    legacy = {p["id"]: (m, p) for m in legacy_menus if m["categoryId"] > 0 for p in m["products"]}
    category_order = [m["categoryId"] for m in legacy_menus]

    categories = {}
    for c in menu_categories:
        if c["id"] > 0:
            categories[c["id"]] = {"id": c["id"], "name": c["title"], "items": [build_item(p, warnings) for p in c["products"]]}

    # Negative ids are SnappFood-generated sections ("پرطرفدارها", "تخفیف‌دارها", time-limited deals). They mostly
    # repeat items from real categories, but an item in a deal can be listed only there.
    listed = {v["id"] for c in menu_categories if c["id"] > 0 for p in c["products"] for v in p["variations"]}
    for section in menu_categories:
        if section["id"] > 0:
            continue
        for product in section["products"]:
            variation_id = product["variations"][0]["id"]
            if variation_id in listed:
                continue
            listed.add(variation_id)
            if variation_id in legacy:
                legacy_menu, legacy_product = legacy[variation_id]
                category_id, category_name = legacy_menu["categoryId"], legacy_menu["category"]
                regular_discount = {"amount": legacy_product["discount"], "ratio": legacy_product["discountRatio"]}
            else:
                category_id, category_name, regular_discount = section["id"], section["title"], None
                warnings.append(f"'{product['title']}' is only in SnappFood's '{section['title']}' section; exported under that name")
            category = categories.setdefault(category_id, {"id": category_id, "name": category_name, "items": []})
            category["items"].append(build_item(product, warnings, regular_discount))
            print(f"  '{product['title']}' is only in SnappFood's '{section['title']}' section; added to '{category_name}'")

    def position(category):
        return category_order.index(category["id"]) if category["id"] in category_order else len(category_order)

    return sorted(categories.values(), key=position)


def main():
    raw, warnings = {}, []
    for name, url in SOURCES.items():
        print(f"Fetching {name}...")
        try:
            raw[name] = json.loads(fetch(url))
        except urllib.error.HTTPError as e:
            if name not in OPTIONAL_SOURCES:
                raise
            warnings.append(f"Skipped {name} ({e.code} {e.reason}); {OPTIONAL_SOURCES[name]}")
    legacy_menus = raw["legacy_menu"]["data"]["menus"] if "legacy_menu" in raw else []

    details = raw["vendor_details"]["data"]
    if not details["isOpen"]:
        hours = ", ".join(f"{g['startHour']}-{g['stopHour']} {'/'.join(g['weekdays'])}" for g in details["scheduleGroups"])
        sys.exit(
            f"{details['title']} is closed on SnappFood right now, and SnappFood leaves part of the menu out while a "
            f"restaurant is closed. Run this again during opening hours ({hours}). Nothing was written."
        )
    for name, data in raw.items():
        write_json(OUT / "raw" / f"{name}.json", data)

    categories = build_categories(raw["menu"]["data"]["menuCategories"], legacy_menus, warnings)
    menu = {
        "source": {
            "platform": "SnappFood",
            "vendor_code": VENDOR_CODE,
            "extracted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "currency": "Toman",
        "restaurant": build_restaurant(raw["vendor_details"]["data"], raw["vendor_review"]["data"], warnings),
        "categories": categories,
    }
    write_json(OUT / "menu.json", menu)

    items = [item for c in categories for item in c["items"]]
    exported_ids = {item["id"] for item in items}
    legacy = {p["id"]: p["title"] for m in legacy_menus for p in m["products"]}
    missing = [title for pid, title in legacy.items() if pid not in exported_ids]
    if missing:
        warnings.append(f"Listed by SnappFood's older API but missing from menu.json: {', '.join(missing)}")
    no_photo = [item["name"] for item in items if not item["images"]]

    print(f"\nSaved {OUT / 'menu.json'}")
    print(f"  {len(categories)} categories, {len(items)} items, {sum(len(i['addon_groups']) for i in items)} add-on groups")
    print(f"  {sum(len(i['images']) for i in items)} item photos + logo + cover in {OUT / 'images'}")
    print(f"  raw API responses in {OUT / 'raw'}")
    if no_photo:
        print(f"  {len(no_photo)} items have no photo on SnappFood: {', '.join(no_photo)}")
    for warning in warnings:
        print(f"WARNING: {warning}")


if __name__ == "__main__":
    main()
