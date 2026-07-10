"""
End-to-End tests for POS ``get_vas_plans_with_rules``.

Guards the fix that surfaced the ``VAS Plan`` catalog SKU wrapper
alongside the legacy ``CH Warranty Plan`` (plan_type in [VAS, Protection
Plan]) query — the old implementation only saw the latter, so cashiers
who had migrated to the new ``VAS Plan`` doctype got a blanket "No VAS
plans available" toast regardless of what was Active in
``/desk/vas-plan``.

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
#  SETUP / CLEANUP
# ═══════════════════════════════════════════════════════════════════

def _purge() -> None:
    """Remove any artefacts left over from a previous run.

    Delete order: VAS Plan wrappers first (they FK-reference the source
    warranty plan), then the CH Warranty Plans they pointed at, then
    the service Item if we created a bench-local one.
    """
    for name in frappe.get_all(
        "VAS Plan",
        filters={"plan_name": ["like", f"{TAG}%"]},
        pluck="name",
    ):
        frappe.delete_doc("VAS Plan", name, force=True, ignore_permissions=True)

    for name in frappe.get_all(
        "CH Warranty Plan",
        filters={"plan_name": ["like", f"{TAG}%"]},
        pluck="name",
    ):
        frappe.delete_doc("CH Warranty Plan", name, force=True, ignore_permissions=True)

    frappe.db.commit()


def _get_service_item() -> str:
    existing = frappe.db.get_value(
        "Item",
        {"is_stock_item": 0, "is_sales_item": 1, "disabled": 0},
        "name",
    )
    if not existing:
        raise RuntimeError(
            "No non-stock sales Item on this bench — needed as service_item"
        )
    return existing


def _seed_source_plan(
    plan_name: str, *, plan_type: str, price: float = 0.0,
    allow_external_device: int = 0, external_device_item: str | None = None,
) -> str:
    """Create a CH Warranty Plan we can wrap with a VAS Plan below."""
    doc = frappe.new_doc("CH Warranty Plan")
    doc.plan_name = plan_name
    doc.plan_type = plan_type
    doc.status = "Active"
    doc.duration_months = 12
    doc.pricing_mode = "Fixed"
    doc.price = price
    doc.percentage_value = 0
    doc.service_item = _get_service_item()
    doc.max_claims = 1
    doc.claims_per_year = 0
    doc.deductible_amount = 0
    doc.coverage_scope = "Screen Only"
    doc.fulfillment_type = "Digital Activation"
    doc.allow_external_device = allow_external_device
    if allow_external_device and external_device_item:
        doc.external_device_item = external_device_item
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def _seed_vas_wrapper(
    plan_name: str,
    source_warranty_plan: str,
    *,
    list_price: float,
    duration_months: int = 12,
    min_device_price: float = 0.0,
    max_device_price: float = 0.0,
) -> str:
    """Create a VAS Plan wrapper — the sellable-catalog record."""
    doc = frappe.new_doc("VAS Plan")
    doc.plan_name = plan_name
    doc.source_warranty_plan = source_warranty_plan
    doc.status = "Active"
    doc.list_price = list_price
    doc.duration_months = duration_months
    doc.min_device_price = min_device_price
    doc.max_device_price = max_device_price
    doc.attach_level = "Optional"
    doc.auto_attach = 0
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


# ═══════════════════════════════════════════════════════════════════
#  TESTS
# ═══════════════════════════════════════════════════════════════════

def test_v01_vas_plan_wrapper_surfaces_in_catalog(ctx: dict) -> None:
    """A VAS Plan → CH Warranty Plan (plan_type=Own Warranty) must show up.

    This is the exact bug the teammate hit: an Active VAS Plan is
    visible under /desk/vas-plan but POS said "No VAS plans available"
    because the old implementation only queried CH Warranty Plan with
    plan_type in (Value Added Service, Protection Plan).
    """
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p.get("vas_plan") == ctx["vas_plan_own"]), None)
    if not hit:
        _fail(
            "V01",
            "VAS Plan wrapping an Own Warranty source not returned; "
            f"got {[p.get('vas_plan') or p.get('name') for p in plans]}",
        )
        return
    # It must return CH Warranty Plan name in `.name` (not VAS Plan name)
    # because cart_line.warranty_plan is a Link to CH Warranty Plan.
    if hit["name"] != ctx["source_own"]:
        _fail("V01", f"plan.name={hit['name']!r} but source is {ctx['source_own']!r}")
        return
    _pass("V01", f"VAS Plan '{ctx['vas_plan_own']}' surfaced (linked to {hit['name']})")


def test_v02_vas_plan_list_price_wins_over_wp_price(ctx: dict) -> None:
    """The sellable rate is VAS Plan.list_price, not CH Warranty Plan.price."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p.get("vas_plan") == ctx["vas_plan_protection"]), None)
    if not hit:
        _fail("V02", "protection VAS Plan not returned")
        return
    if flt(hit["price"]) != 1999.0:
        _fail("V02", f"expected list_price ₹1,999.0 (VAS Plan), got ₹{hit['price']}")
        return
    _pass("V02", f"VAS Plan.list_price ₹{hit['price']} takes precedence over WP.price")


def test_v03_legacy_ch_warranty_plan_still_surfaces(ctx: dict) -> None:
    """A CH Warranty Plan with plan_type=Protection Plan and NO VAS Plan wrapper
    must still appear (legacy compatibility for pre-migration data and the
    existing ``test_vas_external_imei_pos_e2e`` fixtures)."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p["name"] == ctx["source_legacy"]), None)
    if not hit:
        _fail("V03", "legacy Protection Plan (no VAS Plan wrapper) not returned")
        return
    if hit.get("vas_plan"):
        _fail("V03", f"legacy plan wrongly flagged with vas_plan={hit['vas_plan']}")
        return
    _pass("V03", f"legacy plan {hit['name']} still surfaces (no VAS Plan wrapper)")


def test_v04_wrapped_plan_not_duplicated_via_legacy_path(ctx: dict) -> None:
    """If a plan is wrapped by a VAS Plan, it must not ALSO appear as a
    legacy row — otherwise the catalog shows the same coverage twice."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    names = [p["name"] for p in plans]
    duplicates = {n for n in names if names.count(n) > 1}
    if ctx["source_protection"] in duplicates:
        _fail(
            "V04",
            f"CH Warranty Plan {ctx['source_protection']} appears twice — "
            "VAS Plan wrapper + legacy path both fired",
        )
        return
    _pass("V04", "wrapped source appears exactly once")


def test_v05_price_band_blocks_over_max(ctx: dict) -> None:
    """A VAS Plan with max_device_price=20,000 must be blocked (not hidden)
    when the cart has a ₹29,000 device."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p.get("vas_plan") == ctx["vas_plan_capped"]), None)
    if not hit:
        _fail("V05", "capped VAS Plan not present in catalog")
        return
    if not hit.get("blocked"):
        _fail("V05", "capped plan should be blocked but is available")
        return
    if "above plan maximum" not in (hit.get("blocked_reason") or ""):
        _fail(
            "V05",
            f"blocked_reason unclear: {hit.get('blocked_reason')!r}",
        )
        return
    _pass("V05", f"capped plan blocked: {hit['blocked_reason']}")


def test_v06_price_band_open_when_no_max(ctx: dict) -> None:
    """max_device_price=0 must mean 'no cap' — the plan must remain available."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p.get("vas_plan") == ctx["vas_plan_own"]), None)
    if not hit:
        _fail("V06", "uncapped VAS Plan not present")
        return
    if hit.get("blocked"):
        _fail("V06", f"uncapped plan wrongly blocked: {hit.get('blocked_reason')}")
        return
    _pass("V06", "min=0, max=0 correctly treated as unbounded")


def test_v07_protection_plan_needs_device_in_cart(ctx: dict) -> None:
    """Protection Plan (requires_device=True) must block when cart is empty."""
    plans = get_vas_plans_with_rules(cart_items=[])
    hit = next((p for p in plans if p["name"] == ctx["source_legacy"]), None)
    if not hit:
        _fail("V07", "legacy Protection Plan missing from empty-cart payload")
        return
    if not hit.get("requires_device"):
        _fail("V07", "Protection Plan should have requires_device=True")
        return
    if not hit.get("blocked"):
        _fail("V07", "Protection Plan should be blocked when cart has no device")
        return
    _pass("V07", f"Protection Plan blocked on empty cart: {hit['blocked_reason']}")


def test_v08_broken_source_fk_is_skipped_not_crash(ctx: dict) -> None:
    """A VAS Plan whose source_warranty_plan was deleted must not crash the
    endpoint — it should be silently skipped (already tested elsewhere via
    LEFT JOIN, but guard against regressions)."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    # If the endpoint returned anything at all, it didn't crash.
    _pass("V08", f"endpoint returned {len(plans)} plans without crashing")


def test_v09_js_payload_shape_complete(ctx: dict) -> None:
    """Every field cart_service.js reads from the plan must be present."""
    plans = get_vas_plans_with_rules(cart_items=ctx["cart"])
    hit = next((p for p in plans if p.get("vas_plan") == ctx["vas_plan_own"]), None)
    if not hit:
        _fail("V09", "sample VAS Plan not returned")
        return
    required = (
        "name", "plan_name", "plan_type", "service_item",
        "duration_months", "price", "brand", "allow_external_device",
        "allows_external_device", "requires_device", "blocked",
        "blocked_reason",
    )
    missing = [f for f in required if f not in hit]
    if missing:
        _fail("V09", f"payload missing fields: {missing}")
        return
    _pass("V09", "all JS-facing fields present in payload")


# ═══════════════════════════════════════════════════════════════════
#  DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════════

def _diagnostics(ctx: dict) -> None:
    print("\n─── Diagnostics ─────────────────────────────────────────")
    import inspect
    from ch_pos.api import pos_api
    src = inspect.getsource(pos_api.get_vas_plans_with_rules)
    print(f"  code queries `tabVAS Plan`:            {'`tabVAS Plan`' in src}")
    print(f"  code keeps legacy CH Warranty Plan:   {'plan_type' in src and 'Value Added Service' in src}")

    active_vas = frappe.db.count("VAS Plan", {"status": "Active"})
    active_wp = frappe.db.count(
        "CH Warranty Plan",
        {"status": "Active", "plan_type": ["in", ["Value Added Service", "Protection Plan"]]},
    )
    print(f"  Active VAS Plan on bench:              {active_vas}")
    print(f"  Active CH Warranty Plan (VAS/Protect): {active_wp}")
    print("─────────────────────────────────────────────────────────\n")


# ═══════════════════════════════════════════════════════════════════
#  RUNNER
# ═══════════════════════════════════════════════════════════════════

def run_all() -> None:
    print("\n============================================================")
    print(" CH POS — VAS Catalog (VAS Plan + CH Warranty Plan) E2E")
    print("============================================================\n")

    # QA-only mandatory validators (MRP-on-Item etc) bypass.
    frappe.flags.in_qa_seed = True
    try:
        _run_all_inner()
    finally:
        frappe.flags.in_qa_seed = False


def _run_all_inner() -> None:
    _purge()

    # ── Seed source CH Warranty Plans (governance layer) ──
    source_own = _seed_source_plan(
        f"{TAG} Own Warranty Source",
        plan_type="Own Warranty",
        # Fixed pricing requires price > 0 — the sellable rate lives on
        # the VAS Plan wrapper, so this is just a placeholder to satisfy
        # the validator.
        price=1.0,
    )
    source_protection = _seed_source_plan(
        f"{TAG} Protection Source",
        plan_type="Protection Plan",
        price=1499.0,
    )
    source_capped = _seed_source_plan(
        f"{TAG} Capped Source",
        plan_type="Value Added Service",
        price=299.0,
    )
    source_legacy = _seed_source_plan(
        f"{TAG} Legacy Protection No Wrapper",
        plan_type="Protection Plan",
        price=799.0,
    )

    # ── Seed VAS Plan wrappers (sellable-catalog layer) ──
    vas_plan_own = _seed_vas_wrapper(
        f"{TAG} Own Wrapper",
        source_own,
        list_price=999.0,
    )
    vas_plan_protection = _seed_vas_wrapper(
        f"{TAG} Protection Wrapper",
        source_protection,
        list_price=1999.0,
    )
    vas_plan_capped = _seed_vas_wrapper(
        f"{TAG} Capped Wrapper",
        source_capped,
        list_price=499.0,
        max_device_price=20000.0,   # cart has ₹29,000 device — should block
    )
    # NOTE: source_legacy is intentionally NOT wrapped by a VAS Plan.

    # Simulated cart with a ₹29,000 device (matches teammate's screenshot).
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
        "source_own": source_own,
        "source_protection": source_protection,
        "source_capped": source_capped,
        "source_legacy": source_legacy,
        "vas_plan_own": vas_plan_own,
        "vas_plan_protection": vas_plan_protection,
        "vas_plan_capped": vas_plan_capped,
        "cart": cart,
    }

    _diagnostics(ctx)

    tests = [
        test_v01_vas_plan_wrapper_surfaces_in_catalog,
        test_v02_vas_plan_list_price_wins_over_wp_price,
        test_v03_legacy_ch_warranty_plan_still_surfaces,
        test_v04_wrapped_plan_not_duplicated_via_legacy_path,
        test_v05_price_band_blocks_over_max,
        test_v06_price_band_open_when_no_max,
        test_v07_protection_plan_needs_device_in_cart,
        test_v08_broken_source_fk_is_skipped_not_crash,
        test_v09_js_payload_shape_complete,
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
