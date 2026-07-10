"""
End-to-End tests for POS ``get_vas_plans_with_rules``.

Post-merge architecture: the VAS Plan / VAS Product / VAS Attach Rule
/ VAS Claim doctypes have been folded back into their source
CH Warranty Plan / CH Warranty Claim / CH Attach Rule (see
``ch_item_master.patches.v31_merge_vas_plan_into_ch_warranty_plan``).
This test suite guards the single-surface implementation:

    ``CH Warranty Plan`` where ``status='Active' AND is_sellable=1``.

Run:
    bench --site erpnext.local execute \\
        ch_pos.tests.test_vas_plans_catalog_e2e.run_all
"""

import frappe
from frappe.utils import flt, nowdate

from ch_pos.api.pos_api import get_vas_plans_with_rules

# ─── Counters ───────────────────────────────────────────────────────
PASS = 0
FAIL = 0
results: list[tuple[str, str, str]] = []

TAG = "CH-VAS-E2E"  # naming marker for artefacts


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
#  SEEDING
# ═══════════════════════════════════════════════════════════════════

def _purge() -> None:
    """Delete every CH Warranty Plan tagged with the test marker."""
    for wp in frappe.get_all(
        "CH Warranty Plan",
        filters={"plan_name": ["like", f"{TAG}%"]},
        pluck="name",
    ):
        try:
            frappe.delete_doc("CH Warranty Plan", wp, force=True, ignore_permissions=True)
        except Exception:
            pass
    frappe.db.commit()


def _get_service_item() -> str:
    """Return any non-stock sales Item — the plan controller only needs
    the FK to resolve."""
    item = frappe.db.get_value(
        "Item",
        {"is_stock_item": 0, "is_sales_item": 1, "disabled": 0},
        "name",
    )
    if not item:
        raise RuntimeError("No non-stock sales Item available on this bench")
    return item


def _seed_plan(
    plan_name: str, *,
    plan_type: str,
    price: float = 0.0,
    is_sellable: int = 1,
    allow_external_device: int = 0,
    external_device_item: str | None = None,
    pricing_mode: str = "Fixed",
    percentage_value: float = 0.0,
    min_device_price: float = 0.0,
    max_device_price: float = 0.0,
    duration_months: int = 12,
) -> str:
    """Create a CH Warranty Plan — the single sellable-catalog surface."""
    doc = frappe.new_doc("CH Warranty Plan")
    doc.plan_name = plan_name
    doc.plan_type = plan_type
    doc.status = "Active"
    doc.duration_months = duration_months
    doc.pricing_mode = pricing_mode
    doc.price = price
    doc.percentage_value = percentage_value
    doc.service_item = _get_service_item()
    doc.max_claims = 1
    doc.claims_per_year = 0
    doc.deductible_amount = 0
    doc.coverage_scope = "Screen Only"
    doc.fulfillment_type = "Digital Activation"
    doc.is_sellable = is_sellable
    doc.min_device_price = min_device_price
    doc.max_device_price = max_device_price
    doc.allow_external_device = allow_external_device
    if allow_external_device and external_device_item:
        doc.external_device_item = external_device_item
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


# ═══════════════════════════════════════════════════════════════════
#  TESTS
# ═══════════════════════════════════════════════════════════════════

def test_v01_sellable_plan_surfaces(ctx: dict) -> None:
    """A CH Warranty Plan with is_sellable=1 must appear in the payload."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p["name"] == ctx["sellable_own"]), None)
    if not hit:
        _fail("V01", "sellable Own Warranty plan not returned")
        return
    _pass("V01", f"sellable plan {hit['name']} surfaced")


def test_v02_non_sellable_plan_hidden(ctx: dict) -> None:
    """A CH Warranty Plan with is_sellable=0 must be filtered out."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p["name"] == ctx["governance_only"]), None)
    if hit:
        _fail("V02", f"governance-only plan {hit['name']} should be hidden")
        return
    _pass("V02", "governance-only plan (is_sellable=0) correctly hidden")


def test_v03_percentage_pricing_from_device(ctx: dict) -> None:
    """Plan with pricing_mode=Percentage must compute price from cart
    device rate. 10% of ₹29,000 = ₹2,900 (matches teammate's ADLD case)."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p["name"] == ctx["percentage_plan"]), None)
    if not hit:
        _fail("V03", "percentage-priced plan not returned")
        return
    expected = round(29000.0 * 10 / 100.0, 2)
    if flt(hit["price"]) != expected:
        _fail("V03", f"expected ₹{expected} (10% of ₹29,000), got ₹{hit['price']}")
        return
    _pass("V03", f"percentage pricing resolved: 10% of ₹29,000 = ₹{hit['price']}")


def test_v04_fixed_pricing_passes_through(ctx: dict) -> None:
    """Fixed-mode plans return their stored price unchanged."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p["name"] == ctx["fixed_plan"]), None)
    if not hit:
        _fail("V04", "fixed-priced plan not returned")
        return
    if flt(hit["price"]) != 499.0:
        _fail("V04", f"expected ₹499 fixed, got ₹{hit['price']}")
        return
    _pass("V04", f"fixed price ₹{hit['price']} returned unchanged")


def test_v05_price_band_blocks_over_max(ctx: dict) -> None:
    """Plan with max_device_price=₹20,000 should be blocked for ₹29,000 cart."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p["name"] == ctx["capped_plan"]), None)
    if not hit:
        _fail("V05", "capped plan not returned")
        return
    if not hit.get("blocked"):
        _fail("V05", "capped plan should be blocked but isn't")
        return
    _pass("V05", f"capped plan blocked: {hit['blocked_reason']}")


def test_v06_price_band_open_when_no_max(ctx: dict) -> None:
    """min=0 max=0 must mean 'no cap' — plan remains available."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p["name"] == ctx["sellable_own"]), None)
    if not hit:
        _fail("V06", "uncapped plan not present")
        return
    if hit.get("blocked"):
        _fail("V06", f"uncapped plan wrongly blocked: {hit.get('blocked_reason')}")
        return
    _pass("V06", "min=0, max=0 correctly treated as unbounded")


def test_v07_protection_plan_needs_device_in_cart(ctx: dict) -> None:
    """Protection Plan without external-IMEI must block when cart is empty."""
    plans = get_vas_plans_with_rules(cart_items=[])
    hit = next((p for p in plans if p["name"] == ctx["protection_plan"]), None)
    if not hit:
        _fail("V07", "Protection Plan missing from empty-cart payload")
        return
    if not hit.get("requires_device"):
        _fail("V07", "Protection Plan should have requires_device=True")
        return
    if not hit.get("blocked"):
        _fail("V07", "Protection Plan should be blocked when cart has no device")
        return
    _pass("V07", f"Protection Plan blocked on empty cart: {hit['blocked_reason']}")


def test_v08_endpoint_smoke(ctx: dict) -> None:
    """Endpoint returns a list without crashing for a populated cart."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    if not isinstance(plans, list):
        _fail("V08", f"expected list, got {type(plans)}")
        return
    _pass("V08", f"endpoint returned {len(plans)} plans without crashing")


def test_v09_js_payload_shape_complete(ctx: dict) -> None:
    """Every field cart_service.js reads from the plan must be present."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p["name"] == ctx["sellable_own"]), None)
    if not hit:
        _fail("V09", "sample plan not returned")
        return
    required = (
        "name", "vas_plan", "plan_name", "plan_type", "service_item",
        "duration_months", "price", "brand", "allow_external_device",
        "allows_external_device", "requires_device", "blocked",
        "blocked_reason",
    )
    missing = [f for f in required if f not in hit]
    if missing:
        _fail("V09", f"payload missing fields: {missing}")
        return
    # vas_plan must be None post-merge (doctype is gone)
    if hit["vas_plan"] is not None:
        _fail("V09", f"vas_plan should be None post-merge, got {hit['vas_plan']!r}")
        return
    _pass("V09", "all JS-facing fields present; vas_plan=None (post-merge)")


def test_v10_deleted_doctypes_are_gone(_ctx: dict) -> None:
    """The four merged doctypes must not exist as DocType metadata."""
    for dt in ("VAS Plan", "VAS Product", "VAS Attach Rule", "VAS Claim"):
        if frappe.db.exists("DocType", dt):
            _fail("V10", f"DocType {dt!r} still exists after merge")
            return
    _pass("V10", "all 4 legacy doctypes removed from DocType metadata")


# ═══════════════════════════════════════════════════════════════════
#  DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════════

def _diagnostics(_ctx: dict) -> None:
    print("\n─── Diagnostics ─────────────────────────────────────────")
    import inspect
    from ch_pos.api import pos_api
    src = inspect.getsource(pos_api.get_vas_plans_with_rules)
    single_surface = "tabVAS Plan" not in src and "is_sellable" in src
    print(f"  single-surface endpoint (no VAS Plan join): {single_surface}")

    sellable = frappe.db.count(
        "CH Warranty Plan", {"status": "Active", "is_sellable": 1}
    )
    governance = frappe.db.count(
        "CH Warranty Plan", {"status": "Active", "is_sellable": 0}
    )
    print(f"  Sellable CH Warranty Plans on bench:  {sellable}")
    print(f"  Governance-only CH Warranty Plans:   {governance}")
    print("─────────────────────────────────────────────────────────\n")


# ═══════════════════════════════════════════════════════════════════
#  RUNNER
# ═══════════════════════════════════════════════════════════════════

def run_all() -> None:
    print("\n============================================================")
    print(" CH POS — VAS Catalog (post-merge, single-surface) E2E")
    print("============================================================\n")

    frappe.flags.in_qa_seed = True
    try:
        _run_all_inner()
    finally:
        frappe.flags.in_qa_seed = False


def _run_all_inner() -> None:
    _purge()

    # ── Seed CH Warranty Plans ──
    sellable_own = _seed_plan(
        f"{TAG} Sellable Own Warranty",
        plan_type="Own Warranty",
        price=999.0,
        is_sellable=1,
    )
    governance_only = _seed_plan(
        f"{TAG} Governance-only Own Warranty",
        plan_type="Own Warranty",
        price=1.0,
        is_sellable=0,   # bundled with device sale, not directly sellable
    )
    protection_plan = _seed_plan(
        f"{TAG} Protection Plan",
        plan_type="Protection Plan",
        price=1499.0,
        is_sellable=1,
    )
    capped_plan = _seed_plan(
        f"{TAG} Capped VAS",
        plan_type="Value Added Service",
        price=499.0,
        is_sellable=1,
        max_device_price=20000.0,
    )
    percentage_plan = _seed_plan(
        f"{TAG} Percentage-priced Own Warranty",
        plan_type="Own Warranty",
        price=0.0,   # ignored under Percentage mode
        pricing_mode="Percentage of Device Price",
        percentage_value=10.0,
        is_sellable=1,
    )
    fixed_plan = _seed_plan(
        f"{TAG} Fixed-priced VAS",
        plan_type="Value Added Service",
        price=499.0,
        is_sellable=1,
    )

    # Simulated cart with a ₹29,000 device.
    cart = [{
        "item_code": frappe.db.get_value(
            "Item",
            {"is_stock_item": 1, "disabled": 0},
            "name",
        ),
        "rate": 29000.0,
        "is_warranty": False,
        "is_vas": False,
    }]

    ctx = {
        "sellable_own": sellable_own,
        "governance_only": governance_only,
        "protection_plan": protection_plan,
        "capped_plan": capped_plan,
        "percentage_plan": percentage_plan,
        "fixed_plan": fixed_plan,
        "cart": cart,
    }

    _diagnostics(ctx)

    tests = [
        test_v01_sellable_plan_surfaces,
        test_v02_non_sellable_plan_hidden,
        test_v03_percentage_pricing_from_device,
        test_v04_fixed_pricing_passes_through,
        test_v05_price_band_blocks_over_max,
        test_v06_price_band_open_when_no_max,
        test_v07_protection_plan_needs_device_in_cart,
        test_v08_endpoint_smoke,
        test_v09_js_payload_shape_complete,
        test_v10_deleted_doctypes_are_gone,
    ]
    for t in tests:
        try:
            t(ctx)
        except Exception as e:  # noqa: BLE001
            _fail(t.__name__, f"crashed: {e}")

    _purge()

    print("\n============================================================")
    print(f" Summary: {PASS} passed / {FAIL} failed")
    print("============================================================\n")
    for status, tid, detail in results:
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {tid}: {detail}")

    if FAIL:
        raise AssertionError(f"{FAIL} VAS-catalog E2E test(s) failed")
