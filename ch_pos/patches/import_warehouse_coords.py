"""
Import GoGizmo warehouse coordinates into Warehouse doctype.

Only updates the Sellable base warehouse per store (not sub-bins like
Damaged / Reserved / Transit / Disposed / Buyback).

Run:
    bench --site yoursite.com execute \
        ch_pos.patches.import_warehouse_coords.run
"""

import re
import frappe


# ═════════════════════════════════════════════════════════════════════════════
# Coordinate data
# Key   = location name as used in your store master
# Value = (latitude, longitude)
# ═════════════════════════════════════════════════════════════════════════════

COORDINATES = {
    "Alwarthirunagar":     (13.04539157, 80.18585189),
    "Ambattur":            (13.11929631, 80.14963154),
    "Anna Nagar":          (13.08409669, 80.21813852),
    "Ashok Nagar":         (13.02967992, 80.20901662),
    "Doveton":             (13.08742684, 80.25864858),
    "Kelambakkam":         (12.78478007, 80.21950840),
    "Kilpauk":             (13.08871179, 80.24341156),
    "Kodambakkam":         (13.05365232, 80.22498608),
    "Kolathur":            (13.12137801, 80.22396032),
    "Madurai":             ( 9.926285053, 78.11839159),
    "Madurai Anna Nagar":  ( 9.920217296, 78.14893095),
    "Minjur":              (13.27859912, 80.26009021),
    "Palavakkam":          (12.95953198, 80.25591201),
    "Pallavaram":          (12.97011486, 80.14730767),
    "Paper Mills Road":    (13.11267397, 80.23765322),
    "Perambur High Road":  (13.10882685, 80.24595651),
    "Perungudi":           (12.96276179, 80.24649249),
    "Tambaram":            (12.92692241, 80.11323917),
    "Thiruvottiyur":       (13.15965923, 80.30163243),
    "Velachery":           (12.97550717, 80.22047015),
    "West Tambaram":       (12.92872100, 80.11519181),
    "Madipakkam":          (12.96606297, 80.18865458),
    "Mogappair":           (13.08215519, 80.16976999),
    "Old Washermenpet":    (13.12029646, 80.28553387),
    "Kellys":              (13.09067161, 80.24300402),
}


# ═════════════════════════════════════════════════════════════════════════════
# Normalisation helpers
# ═════════════════════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    """
    Lowercase + strip everything except letters/digits.
        "GG-KOLATHUR-Sellable - BM" → "ggkolathursellablebm"
        "Old Washermenpet"          → "oldwashermenpet"
    """
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _matches(warehouse_norm: str, location_norm: str) -> bool:
    """
    True when the location keyword is contained in the warehouse
    identifier — case- and separator-insensitive.
    """
    return location_norm in warehouse_norm


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

def run():
    print("\n" + "=" * 72)
    print("  Importing warehouse coordinates")
    print("=" * 72 + "\n")

    # Fetch every non-disabled, non-group warehouse ONCE
    all_wh = frappe.db.sql(
        """
        SELECT name, warehouse_name, ch_bin_type, company
          FROM `tabWarehouse`
         WHERE disabled = 0
           AND is_group = 0
        """,
        as_dict=True,
    )

    print(f"  Scanning {len(all_wh)} active warehouses…\n")

    updated = 0
    missing = []

    for location, (lat, lng) in COORDINATES.items():
        loc_norm = _normalize(location)

        # Find every warehouse whose name OR warehouse_name contains the
        # location keyword (normalised). This handles all naming variants:
        #   GG-KOLATHUR-Sellable - BM
        #   Gogizmo - Kolathur
        #   KOLATHUR-Damaged - BM  (will still match — filtered below)
        matches = [
            wh for wh in all_wh
            if _matches(_normalize(wh.name), loc_norm)
               or _matches(_normalize(wh.warehouse_name), loc_norm)
        ]

        # Prefer Sellable bins only — skip Damaged / Reserved / Transit /
        # Disposed / Buyback sub-bins so we set coords on the "real" one.
        sub_bin_keywords = (
            "damaged", "reserved", "transit", "disposed", "buyback",
        )
        matches = [
            wh for wh in matches
            if not any(kw in wh.name.lower() for kw in sub_bin_keywords)
        ]

        if not matches:
            print(f"  ✗ No Warehouse match: '{location}'")
            missing.append(location)
            continue

        for wh in matches:
            frappe.db.set_value(
                "Warehouse",
                wh.name,
                {
                    "custom_latitude":  lat,
                    "custom_longitude": lng,
                },
                update_modified=False,
            )
            bin_tag = f" [{wh.ch_bin_type}]" if wh.ch_bin_type else ""
            print(f"  ✓ {wh.name}{bin_tag}  →  ({lat}, {lng})")
            updated += 1

    frappe.db.commit()

    print("\n" + "=" * 72)
    print(f"  Updated: {updated} warehouse row(s)")
    print(f"  Missing: {len(missing)} location(s) with no match")
    if missing:
        print("\n  Locations without a matching Warehouse:")
        for loc in missing:
            print(f"    - {loc}")
    print("=" * 72 + "\n")


def verify():
    """
    Quick health-check — count Sellable warehouses with coords.

    Run:
        bench --site yoursite.com execute \
            ch_pos.patches.import_warehouse_coords.verify
    """
    total = frappe.db.count(
        "Warehouse", {"disabled": 0, "is_group": 0}
    )
    with_coords = frappe.db.sql(
        """
        SELECT COUNT(*)
          FROM `tabWarehouse`
         WHERE disabled = 0
           AND is_group = 0
           AND custom_latitude   IS NOT NULL
           AND custom_longitude  IS NOT NULL
           AND custom_latitude   != 0
           AND custom_longitude  != 0
        """
    )[0][0]

    print(f"\n  Total warehouses:     {total}")
    print(f"  With coordinates:     {with_coords}")
    print(f"  Coverage:             {100 * with_coords / max(total,1):.1f}%\n")

    # Sample the first few
    sample = frappe.db.sql(
        """
        SELECT name, custom_latitude, custom_longitude
          FROM `tabWarehouse`
         WHERE custom_latitude   IS NOT NULL
           AND custom_longitude  IS NOT NULL
           AND custom_latitude   != 0
         ORDER BY name
         LIMIT 5
        """,
        as_dict=True,
    )
    if sample:
        print("  Sample rows with coordinates:")
        for s in sample:
            print(f"    {s.name:<45} "
                  f"({s.custom_latitude}, {s.custom_longitude})")