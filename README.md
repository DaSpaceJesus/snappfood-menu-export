# SnappFood menu export

`extract_menu.py` downloads a restaurant's menu from SnappFood (categories, items, prices, add-ons, photos, opening
hours and rating) so it can be used on the restaurant's own website.

```bash
python3 extract_menu.py
```

- Needs only the Python standard library.
- Writes `export/menu.json`, `export/images/` (photos at original resolution) and `export/raw/` (untouched API responses).
- Prices are in Toman.
- Run it while the restaurant is open. SnappFood hides part of the menu while a restaurant is closed, so the script
  stops without writing anything then.
- The restaurant is set by `VENDOR_CODE` at the top of the script.

SnappFood's older website API, used to cross-check the item list, returns 404 since November 2026. The script skips it
with a warning; see `OPTIONAL_SOURCES` in the script for what that changes.
