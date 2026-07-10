"""
End-to-End tests for POS Attach Panel warranty pricing.

Guards the three bugs fixed in the ``attach_api._get_warranty_plans``
percentage-price shipment:

  1. ``frappe.db.table_exists("tabCH Warranty Plan")`` silently returned
     False and killed the whole function.
  2. Brand filter ``["in", [brand, "", None]]`` dropped catch-all
     (NULL-brand) plans because SQL ``IN`` does not match NULL.
  3. Plans with ``pricing_mode = "Percentage of Device Price"`` carry
     ``price = 0`` on the doc — the effective price must be resolved to
     ``device_price × percentage_value / 100`` before returning to the
     client, otherwise those plans get added to the cart at ₹0.

Run:
    bench --site erpnext.local execute ch_pos.tests.test_warranty_percentage_e2e.run_all

Design notes:
- The test is idempotent: it purges any residue from a previous run
  before setup, and cleans up its own artefacts on success or failure.
- We stub / seed only what is missing on a fresh bench (service item,
  device Item, CH Item Price, warranty plans) so it works on a plain
  ``bench new-site`` install.
"""

import frappe
from frappe.utils import flt, nowdate

from ch_pos.api.attach_api import _get_warranty_plans, get_attach_offers

# ─── Counters ───────────────────────────────────────────────────────
PASS = 0
FAIL = 0
results: list[tuple[str, str, str]] = []

TAG = "CH-WP-E2E"  # marker for all artefacts we create — used for purge


def _pass(test_id: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    results.append(("PASS", test_id, detail))
    print(f"  ✅ {test_id}: {detail}" if detail else f"  ✅ {test_id}")


def _fail(test_id: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    results.append(("FAIL", test_id, detail))
    print(f"  ❌ {test_id}: {detail}" if detail else f"  ❌ {test_id}")


# ═══════════════════════════════════════════════════════════════════
#  SETUP / CLEANUP
# ═══════════════════════════════════════════════════════════════════

def _purge() -> None:
    """Remove any artefacts left over from a previous run."""
    # Warranty plans by naming pattern.
    for name in frappe.get_all(
        "CH Warranty Plan",
        filters={"plan_name": ["like", f"{TAG}%"]},
        pluck="name",
    ):
        frappe.delete_doc("CH Warranty Plan", name, force=True, ignore_permissions=True)

    frappe.db.commit()


def _get_item_group(preferred: str = "Mobiles") -> str:
    """Return a real Item Group we can hang the test device off of."""
    if frappe.db.exists("Item Group", preferred):
        return preferred
    return frappe.db.get_value("Item Group", {"is_group": 0}, "name")


def _get_brand(preferred: str = "Motorola") -> str | None:
    """Return an existing brand we can attach to the plan, if any."""
    if frappe.db.exists("Brand", preferred):
        return preferred
    return frappe.db.get_value("Brand", {}, "name")


def _get_service_item() -> str:
    """Return an existing non-stock sales item usable as ``service_item``.

    We don't create one — Item creation on this bench is gated by a
    stack of ch_item_master validators (taxonomy, MRP, serial-kind, ...)
    that are pointless to satisfy for a test fixture. Every real bench
    has at least one non-stock sales item.
    """
    existing = frappe.db.get_value(
        "Item",
        {"is_stock_item": 0, "is_sales_item": 1, "disabled": 0},
        "name",
    )
    if not existing:
        raise RuntimeError(
            "No non-stock sales item on this bench — CH Warranty Plan "
            "needs one as `service_item`. Create any non-stock item and "
            "re-run."
        )
    return existing


def _seed_device(item_group: str, brand: str | None, selling_price: float) -> str:
    """Return an item_code we can use as the test device.

    We DO NOT create Items — Item mandatory-field policy (ch_category,
    ch_sub_category, ch_serial_kind, MRP, taxonomy, etc.) is governance
    territory owned by ch_item_master and drifts frequently. Instead:

      1. Find an existing stock Item with a POS/Active CH Item Price row
         whose ``selling_price`` matches (± ₹1) and hasn't already been
         claimed by an earlier seed call.
      2. If none exists, pick any UNCLAIMED Item and CREATE or UPDATE
         its CH Item Price row to the target price. The price row itself
         is fully under our control and safe to seed.

    Reusing the same item across two seed calls would clobber the price
    set by the first call (repricing it to the second target), causing
    silent test cross-contamination. The ``_claimed`` set prevents this.
    """
    claimed = _seed_device._claimed  # type: ignore[attr-defined]

    # Try to find an item already priced at (or near) the target that
    # we haven't already claimed.
    row = frappe.db.sql(
        """
        SELECT p.item_code, p.selling_price
        FROM `tabCH Item Price` p
        JOIN `tabItem` i ON i.name = p.item_code
        WHERE p.channel = 'POS' AND p.status = 'Active'
          AND ABS(p.selling_price - %s) < 1
          AND i.is_stock_item = 1 AND i.disabled = 0
          AND i.item_group = %s
          AND p.item_code NOT IN %s
        LIMIT 1
        """,
        (selling_price, item_group, tuple(claimed) or ("__none__",)),
        as_dict=True,
    )
    if row:
        claimed.add(row[0]["item_code"])
        return row[0]["item_code"]

    # Fall back: pick any UNCLAIMED priced POS item in this group and
    # re-price it. This is why we keep ``_claimed`` — we must never
    # touch a row a previous _seed_device call is depending on.
    row = frappe.db.sql(
        """
        SELECT p.name AS price_name, p.item_code
        FROM `tabCH Item Price` p
        JOIN `tabItem` i ON i.name = p.item_code
        WHERE p.channel = 'POS' AND p.status = 'Active'
          AND i.is_stock_item = 1 AND i.disabled = 0
          AND i.item_group = %s
          AND p.item_code NOT IN %s
        LIMIT 1
        """,
        (item_group, tuple(claimed) or ("__none__",)),
        as_dict=True,
    )
    if not row:
        # Last resort: any priced POS item, ignoring group.
        row = frappe.db.sql(
            """
            SELECT p.name AS price_name, p.item_code
            FROM `tabCH Item Price` p
            JOIN `tabItem` i ON i.name = p.item_code
            WHERE p.channel = 'POS' AND p.status = 'Active'
              AND i.is_stock_item = 1 AND i.disabled = 0
              AND p.item_code NOT IN %s
            LIMIT 1
            """,
            (tuple(claimed) or ("__none__",),),
            as_dict=True,
        )
    if not row:
        raise RuntimeError(
            "Not enough unique POS/Active CH Item Price rows on this bench "
            f"to run E2E (need one per test device, already claimed: {claimed})."
        )

    # Repurpose the price row for the test.
    price_doc = frappe.get_doc("CH Item Price", row[0]["price_name"])
    original_selling = flt(price_doc.selling_price)
    original_mrp = flt(price_doc.mrp)
    price_doc.db_set("selling_price", selling_price, update_modified=False)
    price_doc.db_set("mrp", max(original_mrp, selling_price), update_modified=False)
    frappe.db.commit()

    # Remember the original so we can restore it in cleanup.
    _seed_device._restore.append(  # type: ignore[attr-defined]
        (row[0]["price_name"], original_selling, original_mrp)
    )
    claimed.add(row[0]["item_code"])
    return row[0]["item_code"]


# Bookkeeping so we can put prices back the way we found them and never
# hand the same item to two seed calls.
_seed_device._restore = []  # type: ignore[attr-defined]
_seed_device._claimed = set()  # type: ignore[attr-defined]


def _restore_prices() -> None:
    for price_name, original_selling, original_mrp in _seed_device._restore:  # type: ignore[attr-defined]
        if frappe.db.exists("CH Item Price", price_name):
            frappe.db.set_value(
                "CH Item Price", price_name,
                {"selling_price": original_selling, "mrp": original_mrp},
                update_modified=False,
            )
    _seed_device._restore.clear()  # type: ignore[attr-defined]
    _seed_device._claimed.clear()  # type: ignore[attr-defined]
    frappe.db.commit()


def _seed_plan(
    plan_name: str,
    *,
    pricing_mode: str,
    price: float = 0.0,
    percentage_value: float = 0.0,
    brand: str | None = None,
    item_groups: list[str] | None = None,
    service_item: str | None = None,
) -> str:
    """Create an Active CH Warranty Plan and return its resolved name."""
    doc = frappe.new_doc("CH Warranty Plan")
    doc.plan_name = plan_name
    doc.plan_type = "Extended Warranty"
    doc.status = "Active"
    doc.duration_months = 12
    doc.pricing_mode = pricing_mode
    doc.price = price
    doc.percentage_value = percentage_value
    doc.service_item = service_item or _get_service_item()
    if brand:
        doc.brand = brand
    if item_groups:
        for ig in item_groups:
            doc.append("applicable_item_groups", {"item_group": ig})
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


# ═══════════════════════════════════════════════════════════════════
#  TEST SCENARIOS
# ═══════════════════════════════════════════════════════════════════

def test_w01_percentage_plan_computes_price(ctx: dict) -> None:
    """10 %-of-device plan against a ₹29,000 device → ₹2,900."""
    plans = _get_warranty_plans(ctx["device"], ctx["item_group"], ctx["brand"])
    match = next((p for p in plans if p.name == ctx["plan_pct"]), None)
    if not match:
        _fail("W01", f"percentage plan not returned; got {[p.name for p in plans]}")
        return
    expected = round(ctx["device_price"] * 0.10, 2)
    if flt(match.price) != expected:
        _fail("W01", f"expected ₹{expected}, got ₹{match.price}")
        return
    _pass("W01", f"₹{ctx['device_price']} × 10% = ₹{match.price}")


def test_w02_fixed_plan_price_passthrough(ctx: dict) -> None:
    """Fixed-price plan → price returned as configured, untouched."""
    plans = _get_warranty_plans(ctx["device"], ctx["item_group"], ctx["brand"])
    match = next((p for p in plans if p.name == ctx["plan_fixed"]), None)
    if not match:
        _fail("W02", "fixed plan not returned")
        return
    if flt(match.price) != 999.0:
        _fail("W02", f"expected ₹999.0 pass-through, got ₹{match.price}")
        return
    _pass("W02", f"fixed price passed through as ₹{match.price}")


def test_w03_catchall_brand_plan_matches_any_device(ctx: dict) -> None:
    """A plan with NULL brand must return for any device brand.

    Regression guard for the ``["in", [brand, "", None]]`` filter that
    dropped every catch-all plan when a device brand was passed in.
    """
    plans = _get_warranty_plans(ctx["device"], ctx["item_group"], ctx["brand"])
    if not any(p.name == ctx["plan_catchall"] for p in plans):
        _fail(
            "W03",
            f"catch-all (NULL brand) plan filtered out for brand={ctx['brand']}",
        )
        return
    _pass("W03", "catch-all plan returned alongside branded plans")


def test_w04_brand_specific_plan_excluded_for_other_brand(ctx: dict) -> None:
    """A plan bound to Brand X must NOT return for a Brand Y device."""
    other_brand = ctx.get("other_brand")
    if not other_brand:
        _pass("W04", "SKIP — no second brand available on bench")
        return
    plans = _get_warranty_plans(ctx["device_other"], ctx["item_group"], other_brand)
    if any(p.name == ctx["plan_branded"] for p in plans):
        _fail(
            "W04",
            f"brand-bound plan ({ctx['brand']}) leaked to device on {other_brand}",
        )
        return
    _pass("W04", f"brand-bound plan correctly excluded for {other_brand}")


def test_w05_item_group_applicability_filter(ctx: dict) -> None:
    """A plan restricted to Item Group X must not show for Group Y."""
    other_group = ctx.get("other_item_group")
    if not other_group:
        _pass("W05", "SKIP — no second item group available")
        return
    plans = _get_warranty_plans(ctx["device"], other_group, ctx["brand"])
    if any(p.name == ctx["plan_group_scoped"] for p in plans):
        _fail(
            "W05",
            f"group-scoped plan leaked into item_group={other_group}",
        )
        return
    _pass("W05", f"group-scoped plan correctly hidden for {other_group}")


def test_w06_get_attach_offers_end_to_end(ctx: dict) -> None:
    """Full whitelisted entrypoint returns computed prices for the UI."""
    payload = get_attach_offers(ctx["device"])
    plans = payload.get("warranty_plans") or []
    pct = next((p for p in plans if p.name == ctx["plan_pct"]), None)
    if not pct:
        _fail("W06", f"percentage plan absent from payload; got {[p.name for p in plans]}")
        return
    expected = round(ctx["device_price"] * 0.10, 2)
    if flt(pct.price) != expected:
        _fail("W06", f"payload price ₹{pct.price} != ₹{expected}")
        return
    _pass(
        "W06",
        f"get_attach_offers payload carries computed ₹{pct.price} for percentage plan",
    )


def test_w07_teammate_scenario_45000_at_10pct(ctx: dict) -> None:
    """Reproduce the screenshot: ₹45,000 device + 10 % plan → ₹4,500."""
    plans = _get_warranty_plans(
        ctx["device_45k"], ctx["item_group"], ctx["brand"],
    )
    hit = next((p for p in plans if p.name == ctx["plan_pct"]), None)
    if not hit:
        _fail("W07", "percentage plan not returned for ₹45k device")
        return
    if flt(hit.price) != 4500.0:
        _fail("W07", f"expected ₹4,500 for 10% × ₹45,000, got ₹{hit.price}")
        return
    _pass("W07", "₹45,000 device + 10% plan resolves to ₹4,500 (screenshot scenario)")


def test_w08_js_cart_push_shape(ctx: dict) -> None:
    """Prove the API payload has every field the JS cart push consumes.

    ``cart_service.js::_show_attach_panel`` builds the cart line as::

        {
            item_code:  plan.service_item || plan.name,
            item_name:  `🛡 ${plan.plan_name} (${plan.duration_months}m)`,
            rate:       flt(plan.price),
            mrp:        flt(plan.price),
            warranty_plan: plan.name,
            ...
        }

    So the payload must expose: name, plan_name, service_item,
    duration_months, price (already computed). Assert them.
    """
    plans = _get_warranty_plans(ctx["device"], ctx["item_group"], ctx["brand"])
    hit = next((p for p in plans if p.name == ctx["plan_pct"]), None)
    if not hit:
        _fail("W08", "percentage plan not present in payload")
        return
    required = ("name", "plan_name", "service_item", "duration_months", "price")
    missing = [f for f in required if hit.get(f) in (None, "")]
    if missing:
        _fail("W08", f"payload missing fields required by JS cart push: {missing}")
        return
    # And the computed rate the JS will push to the cart:
    js_rate = flt(hit.price)
    if js_rate != round(ctx["device_price"] * 0.10, 2):
        _fail("W08", f"JS-side flt(plan.price) would yield ₹{js_rate}, expected ₹{round(ctx['device_price']*0.10,2)}")
        return
    _pass("W08", f"JS cart push shape complete; rate={js_rate}")


# ═══════════════════════════════════════════════════════════════════
#  DIAGNOSTICS  (run before assertions — helps triage manual-test fails)
# ═══════════════════════════════════════════════════════════════════

def _diagnostics(ctx: dict) -> None:
    """Print diagnostic info about the bench state.

    If a teammate runs this and one of these prerequisites is missing,
    that explains why manual testing "still fails" after the code fix.
    """
    print("\n─── Diagnostics ─────────────────────────────────────────")
    # 1. Fix landed in the source file?
    import inspect
    from ch_pos.api import attach_api
    src = inspect.getsource(attach_api._get_warranty_plans)
    has_fix_a = 'table_exists("CH Warranty Plan")' in src
    has_fix_b = "Percentage of Device Price" in src and "device_price *" in src
    print(f"  code has table_exists fix:       {has_fix_a}")
    print(f"  code has percentage compute fix: {has_fix_b}")

    # 2. Prerequisites for the test bench:
    print(f"  device item exists:  {frappe.db.exists('Item', ctx['device'])}")
    print(f"  device CH Item Price (POS/Active) selling_price = "
          f"₹{frappe.db.get_value('CH Item Price', {'item_code': ctx['device'], 'channel':'POS', 'status':'Active'}, 'selling_price')}")

    # 3. What plans are Active right now?
    active = frappe.get_all(
        "CH Warranty Plan",
        filters={"status": "Active"},
        fields=["name", "plan_name", "pricing_mode", "percentage_value", "price", "brand"],
    )
    print(f"  active plans on bench: {len(active)}")
    for p in active:
        print(f"    - {p.name}: {p.plan_name} | {p.pricing_mode} | "
              f"pct={p.percentage_value} | fixed=₹{p.price} | brand={p.brand}")
    print("─────────────────────────────────────────────────────────\n")


# ═══════════════════════════════════════════════════════════════════
#  RUNNER
# ═══════════════════════════════════════════════════════════════════

def run_all() -> None:
    print("\n============================================================")
    print(" CH POS — Warranty Percentage Pricing E2E")
    print("============================================================\n")

    # Bypass QA-only mandatory validators (MRP-on-Item, etc.) that only
    # exist to protect production catalog data.
    frappe.flags.in_qa_seed = True
    try:
        _run_all_inner()
    finally:
        frappe.flags.in_qa_seed = False


def _run_all_inner() -> None:
    # Fresh slate.
    _purge()
    # Reset the seed-device claim-set in case run_all is called twice in
    # one worker (bench console, iterative dev).
    _seed_device._claimed.clear()  # type: ignore[attr-defined]
    _seed_device._restore.clear()  # type: ignore[attr-defined]

    # Discover a real device we can use, and read its actual attributes
    # off the DB so the tests match reality rather than assumptions.
    item_group = _get_item_group("Mobiles")
    device = _seed_device(item_group, None, 29000.0)
    device_meta = frappe.db.get_value(
        "Item", device, ["item_group", "brand"], as_dict=True,
    ) or frappe._dict()
    item_group = device_meta.item_group or item_group
    brand = device_meta.brand

    # Read the resolved selling_price back off the DB — cheaper and more
    # honest than trusting our target ₹29,000.
    device_price = flt(frappe.db.get_value(
        "CH Item Price",
        {"item_code": device, "channel": "POS", "status": "Active"},
        "selling_price",
    ))

    device_45k = _seed_device(item_group, brand, 45000.0)

    other_group = frappe.db.get_value(
        "Item Group",
        {"is_group": 0, "name": ["!=", item_group]},
        "name",
    )
    other_brand = frappe.db.get_value(
        "Brand", {"name": ["!=", brand]}, "name",
    ) if brand else frappe.db.get_value("Brand", {}, "name")

    # A second-brand device only exists if we can find one already priced.
    device_other = None
    if other_brand:
        row = frappe.db.sql(
            """
            SELECT p.item_code FROM `tabCH Item Price` p
            JOIN `tabItem` i ON i.name = p.item_code
            WHERE p.channel='POS' AND p.status='Active'
              AND i.brand = %s AND i.is_stock_item=1 AND i.disabled=0
            LIMIT 1
            """,
            (other_brand,), as_dict=True,
        )
        if row:
            device_other = row[0]["item_code"]

    # Seed a family of plans covering all shapes _get_warranty_plans
    # is supposed to distinguish.
    plan_pct = _seed_plan(
        f"{TAG} Percentage 10pct",
        pricing_mode="Percentage of Device Price",
        percentage_value=10.0,
        # Note: no brand → catch-all; that's the whole point of W03.
    )
    plan_fixed = _seed_plan(
        f"{TAG} Fixed 999",
        pricing_mode="Fixed",
        price=999.0,
    )
    plan_catchall = plan_pct  # same doc — catch-all + percentage
    plan_branded = _seed_plan(
        f"{TAG} Brand Only {brand or 'X'}",
        pricing_mode="Fixed",
        price=1499.0,
        brand=brand,
    ) if brand else None
    plan_group_scoped = _seed_plan(
        f"{TAG} Group Only {item_group}",
        pricing_mode="Fixed",
        price=799.0,
        item_groups=[item_group],
    )

    ctx = {
        "device": device,
        "device_45k": device_45k,
        "device_other": device_other,
        "device_price": device_price,
        "item_group": item_group,
        "other_item_group": other_group,
        "brand": brand,
        "other_brand": other_brand,
        "plan_pct": plan_pct,
        "plan_fixed": plan_fixed,
        "plan_catchall": plan_catchall,
        "plan_branded": plan_branded,
        "plan_group_scoped": plan_group_scoped,
    }

    _diagnostics(ctx)

    tests = [
        test_w01_percentage_plan_computes_price,
        test_w02_fixed_plan_price_passthrough,
        test_w03_catchall_brand_plan_matches_any_device,
        test_w04_brand_specific_plan_excluded_for_other_brand,
        test_w05_item_group_applicability_filter,
        test_w06_get_attach_offers_end_to_end,
        test_w07_teammate_scenario_45000_at_10pct,
        test_w08_js_cart_push_shape,
    ]
    for t in tests:
        try:
            t(ctx)
        except Exception as e:  # noqa: BLE001 — surface every failure
            _fail(t.__name__, f"crashed: {e}")

    # Cleanup — even on failure so re-runs stay hermetic.
    _purge()
    _restore_prices()

    print("\n============================================================")
    print(f" Summary: {PASS} passed / {FAIL} failed")
    print("============================================================\n")
    for status, tid, detail in results:
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {tid}: {detail}")

    if FAIL:
        raise AssertionError(f"{FAIL} warranty-percentage E2E test(s) failed")
