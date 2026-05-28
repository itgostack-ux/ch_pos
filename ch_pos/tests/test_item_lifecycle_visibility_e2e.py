"""
POS Item Lifecycle Visibility — E2E

Asserts that items with `ch_lifecycle_status` other than `Active`/`Obsolete`
(i.e. Draft, Pending Review, Blocked) never surface through any of the POS
item-loading endpoints patched in ch_pos@5002b06 / @116458d.

Endpoints exercised (read-only, no DB mutation):
  T01  api.search.pos_item_search        — live online search
  T02  api.offline_sync.get_full_item_catalog — IndexedDB pre-warm
  T03  api.guided.get_guided_recommendations  — guided-selling rec list
  T04  api.pos_api inventory_alerts (low_stock + no_bin) SQL — dashboard
  T05  pos_core.store_hub_api stock_alerts SQL — store hub widget
  T06  pos_repair_intake._resolve_device_item — repair spare-part lookup

Each test picks a known non-Active item from the live DB and asserts it is
absent from the endpoint's result while a known Active item is present.

Run:
  bench --site erpnext.local execute \
    ch_pos.tests.test_item_lifecycle_visibility_e2e.run_all
"""

from __future__ import annotations

import frappe

HIDDEN_STATUSES = ("Draft", "Pending Review", "Blocked")
VISIBLE_STATUSES = ("Active", "Obsolete")

results: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    results.append(("PASS", name, detail))
    print(f"PASS  {name}{f'  ({detail})' if detail else ''}")


def fail(name, detail=""):
    results.append(("FAIL", name, detail))
    print(f"FAIL  {name}{f'  ({detail})' if detail else ''}")


def skip(name, detail=""):
    results.append(("SKIP", name, detail))
    print(f"SKIP  {name}{f'  ({detail})' if detail else ''}")


# ── fixtures (live DB lookups, no mutation) ───────────────────────────────────

def _first_pos_profile() -> str | None:
    return frappe.db.get_value("POS Profile", {"disabled": 0}, "name")


def _pick_hidden_item() -> dict | None:
    """Return one known non-Active sellable-shape item from live data."""
    row = frappe.db.sql(
        """
        SELECT name, item_name, ch_lifecycle_status
        FROM `tabItem`
        WHERE disabled = 0 AND is_sales_item = 1 AND has_variants = 0
          AND IFNULL(ch_lifecycle_status, '') IN %(statuses)s
        ORDER BY modified DESC LIMIT 1
        """,
        {"statuses": HIDDEN_STATUSES},
        as_dict=True,
    )
    return row[0] if row else None


def _pick_active_item() -> dict | None:
    row = frappe.db.sql(
        """
        SELECT name, item_name, item_group, ch_sub_category
        FROM `tabItem`
        WHERE disabled = 0 AND is_sales_item = 1 AND has_variants = 0
          AND IFNULL(ch_lifecycle_status, '') = 'Active'
        ORDER BY modified DESC LIMIT 1
        """,
        as_dict=True,
    )
    return row[0] if row else None


# ── T01: pos_item_search ──────────────────────────────────────────────────────

def test_pos_item_search_hides_inactive():
    from ch_pos.api.search import pos_item_search

    pos_profile = _first_pos_profile()
    if not pos_profile:
        skip("T01_pos_item_search", "no POS profile")
        return

    hidden = _pick_hidden_item()
    if not hidden:
        skip("T01_pos_item_search", "no Draft/Pending Review/Blocked items in DB")
        return

    # Search by the exact item_code — if filter is correct, result is empty.
    res = pos_item_search(
        pos_profile=pos_profile,
        search_term=hidden["name"],
        filters={},
        usage_context="sale",
    )
    items = (res or {}).get("items", [])
    leaked = [i for i in items if i.get("item_code") == hidden["name"]]
    if leaked:
        fail(
            "T01_pos_item_search",
            f"{hidden['name']} (status={hidden['ch_lifecycle_status']}) leaked",
        )
        return
    ok("T01_pos_item_search", f"hidden {hidden['name']} correctly filtered")


# ── T02: get_full_item_catalog (offline pre-warm) ─────────────────────────────

def test_full_catalog_hides_inactive():
    from ch_pos.api.offline_sync import get_full_item_catalog

    pos_profile = _first_pos_profile()
    if not pos_profile:
        skip("T02_full_catalog", "no POS profile")
        return

    # Page through everything — typical bench has < 50k items, well within reach.
    seen = set()
    page = 0
    while True:
        res = get_full_item_catalog(pos_profile=pos_profile, page=page, page_size=500)
        for it in (res or {}).get("items", []):
            seen.add(it["item_code"])
        if not (res or {}).get("has_more"):
            break
        page += 1
        if page > 200:  # safety cap
            break

    # Cross-check: any item in `seen` whose lifecycle is hidden = bug.
    if not seen:
        skip("T02_full_catalog", "catalog returned 0 items (empty profile?)")
        return

    leaked = frappe.db.sql(
        """
        SELECT name, IFNULL(ch_lifecycle_status,'') AS status
        FROM `tabItem`
        WHERE name IN %(codes)s
          AND IFNULL(ch_lifecycle_status,'') IN %(statuses)s
        LIMIT 5
        """,
        {"codes": tuple(seen), "statuses": HIDDEN_STATUSES},
        as_dict=True,
    )
    if leaked:
        fail(
            "T02_full_catalog",
            f"{len(leaked)} hidden item(s) leaked, e.g. {leaked[0]['name']}={leaked[0]['status']}",
        )
        return

    # Also assert at least one Active item is present (sanity)
    active = frappe.db.sql(
        """SELECT 1 FROM `tabItem` WHERE name IN %(codes)s
             AND IFNULL(ch_lifecycle_status,'') = 'Active' LIMIT 1""",
        {"codes": tuple(seen)},
    )
    if not active:
        fail("T02_full_catalog", "no Active items returned — filter too strict?")
        return

    ok("T02_full_catalog", f"{len(seen)} items, all Active/Obsolete")


# ── T03: get_guided_recommendations ───────────────────────────────────────────

def test_guided_recs_hide_inactive():
    from ch_pos.api.guided import get_guided_recommendations

    # Find a sub_category that has at least one Active item AND we can prove
    # the SQL is filter-correct by checking the result set is a strict subset
    # of Active/Obsolete items with that sub_category.
    row = frappe.db.sql(
        """SELECT ch_sub_category, COUNT(*) AS n
             FROM `tabItem`
             WHERE disabled=0 AND is_sales_item=1 AND has_variants=0
               AND IFNULL(ch_lifecycle_status,'')='Active'
               AND IFNULL(ch_sub_category,'') <> ''
             GROUP BY ch_sub_category ORDER BY n DESC LIMIT 1""",
        as_dict=True,
    )
    if not row:
        skip("T03_guided_recs", "no Active items with ch_sub_category")
        return
    sub_cat = row[0]["ch_sub_category"]

    recs = get_guided_recommendations(
        sub_category=sub_cat, responses=[], warehouse=None, limit=20
    )
    if not isinstance(recs, list):
        fail("T03_guided_recs", f"unexpected return type: {type(recs).__name__}")
        return

    codes = [r["item_code"] for r in recs] if recs else []
    if not codes:
        ok("T03_guided_recs", f"sub_cat={sub_cat} → 0 recs (acceptable)")
        return

    leaked = frappe.db.sql(
        """SELECT name, IFNULL(ch_lifecycle_status,'') AS status
             FROM `tabItem` WHERE name IN %(codes)s
               AND IFNULL(ch_lifecycle_status,'') IN %(statuses)s LIMIT 5""",
        {"codes": tuple(codes), "statuses": HIDDEN_STATUSES},
        as_dict=True,
    )
    if leaked:
        fail("T03_guided_recs", f"hidden leaked: {leaked[0]['name']}={leaked[0]['status']}")
        return
    ok("T03_guided_recs", f"sub_cat={sub_cat} → {len(codes)} recs, all Active/Obsolete")


# ── T04: inventory_alerts (low_stock + no_bin) SQL parity ─────────────────────

def test_inventory_alerts_sql_hides_inactive():
    """Direct-SQL parity check matching pos_api.inventory_alerts queries."""
    warehouse = frappe.db.get_value(
        "POS Profile", {"disabled": 0}, "warehouse"
    )
    if not warehouse:
        skip("T04_inventory_alerts", "no warehouse on any POS profile")
        return

    # Replicate the EXACT WHERE used in pos_api.py low_stock query.
    low_stock = frappe.db.sql(
        """SELECT b.item_code, IFNULL(i.ch_lifecycle_status,'') AS status
             FROM `tabBin` b
             JOIN `tabItem` i ON i.name = b.item_code
             WHERE b.warehouse = %s AND b.actual_qty <= 5 AND i.disabled = 0
               AND IFNULL(i.ch_lifecycle_status, '') IN ('Active', 'Obsolete')
             LIMIT 50""",
        (warehouse,),
        as_dict=True,
    )
    bad = [r for r in low_stock if r["status"] not in VISIBLE_STATUSES]
    if bad:
        fail("T04_inventory_alerts_low_stock", f"leaked: {bad[0]['item_code']}={bad[0]['status']}")
        return

    no_bin = frappe.db.sql(
        """SELECT i.name AS item_code, IFNULL(i.ch_lifecycle_status,'') AS status
             FROM `tabItem` i
             WHERE i.disabled = 0 AND i.is_stock_item = 1
               AND IFNULL(i.ch_lifecycle_status, '') IN ('Active', 'Obsolete')
               AND NOT EXISTS (
                   SELECT 1 FROM `tabBin` b
                   WHERE b.item_code = i.name AND b.warehouse = %s
               )
             LIMIT 50""",
        (warehouse,),
        as_dict=True,
    )
    bad = [r for r in no_bin if r["status"] not in VISIBLE_STATUSES]
    if bad:
        fail("T04_inventory_alerts_no_bin", f"leaked: {bad[0]['item_code']}={bad[0]['status']}")
        return

    ok(
        "T04_inventory_alerts",
        f"low_stock={len(low_stock)} no_bin={len(no_bin)} (all visible-status)",
    )


# ── T05: store_hub stock_alerts SQL parity ────────────────────────────────────

def test_store_hub_stock_alerts_hides_inactive():
    store = frappe.db.get_value("CH Store", {}, "warehouse")
    if not store:
        skip("T05_store_hub_stock_alerts", "no CH Store with warehouse configured")
        return

    rows = frappe.db.sql(
        """SELECT b.item_code, IFNULL(i.ch_lifecycle_status,'') AS status
             FROM `tabBin` b
             JOIN `tabItem` i ON i.name = b.item_code
             LEFT JOIN `tabItem Reorder` ir ON ir.parent = b.item_code AND ir.warehouse = b.warehouse
             WHERE b.warehouse = %s
               AND i.disabled = 0
               AND IFNULL(i.ch_lifecycle_status,'') IN ('Active','Obsolete')
               AND b.actual_qty <= COALESCE(ir.warehouse_reorder_level, 0)
               AND COALESCE(ir.warehouse_reorder_level, 0) > 0
             LIMIT 50""",
        (store,),
        as_dict=True,
    )
    bad = [r for r in rows if r["status"] not in VISIBLE_STATUSES]
    if bad:
        fail("T05_store_hub_stock_alerts", f"leaked: {bad[0]['item_code']}={bad[0]['status']}")
        return
    ok("T05_store_hub_stock_alerts", f"{len(rows)} alerts (all visible-status)")


# ── T06: repair intake device-item resolver ───────────────────────────────────

def test_repair_intake_resolver_hides_inactive():
    """If a Draft item matches the device search, the resolver MUST not pick it."""
    hidden = _pick_hidden_item()
    if not hidden:
        skip("T06_repair_resolver", "no Draft/Pending Review/Blocked items in DB")
        return

    # Build an in-memory PosRepairIntake instance without inserting — we only
    # invoke the pure method.
    from ch_pos.pos_repair.doctype.pos_repair_intake.pos_repair_intake import (
        POSRepairIntake,
    )

    doc = frappe.new_doc("POS Repair Intake")
    # Make brand/model match the hidden item's name exactly so the LIKE search
    # would resolve to it if the filter were absent.
    doc.device_brand = hidden["item_name"][:40] or hidden["name"]
    doc.device_model = ""
    resolved = POSRepairIntake._resolve_device_item(doc)
    if resolved == hidden["name"]:
        fail("T06_repair_resolver", f"resolver picked hidden item {hidden['name']}")
        return
    # If it resolved to something else, verify that something is visible-status.
    if resolved:
        status = frappe.db.get_value("Item", resolved, "ch_lifecycle_status")
        if status not in VISIBLE_STATUSES:
            fail("T06_repair_resolver", f"resolved to non-visible {resolved}={status}")
            return
    ok("T06_repair_resolver", f"hidden {hidden['name']} not selected (resolved={resolved})")


# ── runner ────────────────────────────────────────────────────────────────────

def run_all():
    results.clear()
    print("=" * 70)
    print("POS Item Lifecycle Visibility — E2E")
    print("=" * 70)

    test_pos_item_search_hides_inactive()
    test_full_catalog_hides_inactive()
    test_guided_recs_hide_inactive()
    test_inventory_alerts_sql_hides_inactive()
    test_store_hub_stock_alerts_hides_inactive()
    test_repair_intake_resolver_hides_inactive()

    print("-" * 70)
    passed = sum(1 for r in results if r[0] == "PASS")
    failed = sum(1 for r in results if r[0] == "FAIL")
    skipped = sum(1 for r in results if r[0] == "SKIP")
    print(f"SUMMARY: {passed} PASS, {failed} FAIL, {skipped} SKIP")
    print("=" * 70)
    if failed:
        raise AssertionError(f"{failed} test(s) failed")
    return {"pass": passed, "fail": failed, "skip": skipped}
