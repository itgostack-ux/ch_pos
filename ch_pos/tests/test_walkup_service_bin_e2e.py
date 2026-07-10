"""
End-to-End tests for the "walk-up bin visibility" of non-stock
warranty / VAS service SKUs and the ``get_external_imei_plan_for_item``
mapper used to auto-open the IMEI prompt when such an item is added
directly from the item bin.

Guards two ch_pos changes:

  1. ``ch_pos.api.search.pos_item_search`` — the in-stock CASE was
     widened with a first arm ``WHEN i.is_stock_item = 0 THEN 1`` so
     service SKUs (which never have Bin rows) always qualify for the
     walk-up bin in "sale" usage_context.

  2. ``ch_pos.api.search.get_external_imei_plan_for_item`` — new
     whitelisted mapper. Returns the CH Warranty Plan (with
     ``is_sellable=1`` AND ``allow_external_device=1``) that a given
     Item is the ``service_item`` for, else ``{}``. The POS frontend
     uses this to auto-open the IMEI prompt when an external-device
     plan's SKU is added directly.

Run:
    bench --site erpnext.local execute \\
        ch_pos.tests.test_walkup_service_bin_e2e.run_all
"""

from __future__ import annotations

import frappe

from ch_pos.api.search import (
    get_external_imei_plan_for_item,
    pos_item_search,
)

# ─── Counters ──────────────────────────────────────────────────────────
PASS = 0
FAIL = 0
results: list[tuple[str, str, str]] = []

TAG = "CH-WALKUP-BIN-E2E"


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


def _skip(test_id: str, detail: str = "") -> None:
    results.append(("SKIP", test_id, detail))
    print(f"  ⚪ SKIP {test_id}: {detail}")


# ─── Fixtures ──────────────────────────────────────────────────────────

def _first_pos_profile() -> str | None:
    """Any usable POS Profile on the bench."""
    return frappe.db.get_value("POS Profile", {"disabled": 0}, "name")


def _make_service_item(code: str) -> str:
    """Create (or reuse) a non-stock, sellable, Active-lifecycle service
    Item. Deliberately mirrors the shape of real warranty / VAS SKUs
    that are provisioned but never inventoried."""
    if frappe.db.exists("Item", code):
        return code
    item = frappe.new_doc("Item")
    item.item_code = code
    item.item_name = code
    item.item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
    item.stock_uom = "Nos"
    item.is_stock_item = 0
    item.is_sales_item = 1
    item.has_variants = 0
    item.has_serial_no = 0
    item.disabled = 0
    # ``ch_serial_kind`` is a Custom Field with reqd=1 on this bench —
    # UOM = quantity-only tracking, the correct classification for
    # non-stock service SKUs.
    if frappe.db.has_column("Item", "ch_serial_kind"):
        item.ch_serial_kind = "UOM"
    if frappe.db.has_column("Item", "ch_lifecycle_status"):
        item.ch_lifecycle_status = "Active"
    item.insert(ignore_permissions=True)
    frappe.db.commit()
    return code


def _seed_plan(
    plan_name: str,
    *,
    service_item: str,
    is_sellable: int = 1,
    allow_external_device: int = 0,
    external_device_item: str | None = None,
    plan_type: str = "Value Added Service",
) -> str:
    """Create a CH Warranty Plan for the tests."""
    doc = frappe.new_doc("CH Warranty Plan")
    doc.plan_name = plan_name
    doc.plan_type = plan_type
    doc.status = "Active"
    doc.duration_months = 12
    doc.pricing_mode = "Fixed"
    doc.price = 99.0
    doc.service_item = service_item
    doc.max_claims = 1
    doc.claims_per_year = 0
    doc.deductible_amount = 0
    doc.coverage_scope = "Screen Only"
    doc.fulfillment_type = "Digital Activation"
    doc.is_sellable = is_sellable
    doc.allow_external_device = allow_external_device
    if allow_external_device and external_device_item:
        doc.external_device_item = external_device_item
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def _purge() -> None:
    for wp in frappe.get_all(
        "CH Warranty Plan",
        filters={"plan_name": ["like", f"{TAG}%"]},
        pluck="name",
    ):
        try:
            frappe.delete_doc("CH Warranty Plan", wp, force=True, ignore_permissions=True)
        except Exception:
            pass
    for code in (
        f"{TAG}-EXTERNAL-DEVICE",
        f"{TAG}-SERVICE-EXTERNAL",
        f"{TAG}-SERVICE-INSTORE",
        f"{TAG}-SERVICE-GOV",
        f"{TAG}-UNRELATED-SVC",
    ):
        if frappe.db.exists("Item", code):
            try:
                frappe.delete_doc("Item", code, force=True, ignore_permissions=True)
            except Exception:
                pass
    frappe.db.commit()


# ─── Tests ─────────────────────────────────────────────────────────────

def test_w01_non_stock_service_item_visible_in_walkup_bin(ctx: dict) -> None:
    """A non-stock service SKU (no Bin row) must surface in
    pos_item_search under usage_context='sale' with a warehouse — the
    walk-up bin path."""
    pos_profile = ctx.get("pos_profile")
    if not pos_profile:
        _skip("W01", "no POS Profile on bench")
        return

    service_item = ctx["service_external"]
    res = pos_item_search(
        pos_profile=pos_profile,
        search_term=service_item,
        filters={},
        usage_context="sale",
    )
    items = (res or {}).get("items", []) if isinstance(res, dict) else []
    hit = next((i for i in items if i.get("item_code") == service_item), None)
    if not hit:
        _fail(
            "W01",
            f"non-stock service SKU {service_item!r} did NOT surface — "
            f"in-stock gate is still blocking it",
        )
        return
    _pass("W01", f"non-stock service SKU {service_item} surfaced (walk-up visible)")


def test_w02_non_stock_flag_on_returned_payload(ctx: dict) -> None:
    """The payload must expose is_stock_item so the frontend can
    bypass its own out-of-stock guard for service SKUs."""
    pos_profile = ctx.get("pos_profile")
    if not pos_profile:
        _skip("W02", "no POS Profile on bench")
        return

    service_item = ctx["service_external"]
    res = pos_item_search(
        pos_profile=pos_profile,
        search_term=service_item,
        filters={},
        usage_context="sale",
    )
    items = (res or {}).get("items", []) if isinstance(res, dict) else []
    hit = next((i for i in items if i.get("item_code") == service_item), None)
    if not hit:
        _skip("W02", "row not returned — W01 covers the surfacing failure")
        return
    if int(hit.get("is_stock_item") or 0) != 0:
        _fail("W02", f"expected is_stock_item=0, got {hit.get('is_stock_item')!r}")
        return
    _pass("W02", "payload carries is_stock_item=0 (front-end guard can honour it)")


def test_w03_stock_item_still_needs_bin_qty(ctx: dict) -> None:
    """Regression: a stock-tracked item with zero Bin qty must still
    be filtered out — this fix must NOT widen visibility for real
    inventory."""
    pos_profile = ctx.get("pos_profile")
    if not pos_profile:
        _skip("W03", "no POS Profile on bench")
        return

    # Find a stock item with no Bin rows anywhere.
    row = frappe.db.sql(
        """
        SELECT i.name FROM `tabItem` i
        WHERE i.disabled = 0 AND i.is_sales_item = 1
          AND i.has_variants = 0 AND i.is_stock_item = 1
          AND NOT EXISTS (
            SELECT 1 FROM `tabBin` b
              WHERE b.item_code = i.name AND b.actual_qty > 0
          )
        LIMIT 1
        """,
        as_dict=True,
    )
    if not row:
        _skip("W03", "no zero-bin stock item on bench to test")
        return

    item_code = row[0].name
    res = pos_item_search(
        pos_profile=pos_profile,
        search_term=item_code,
        filters={},
        usage_context="sale",
    )
    items = (res or {}).get("items", []) if isinstance(res, dict) else []
    hit = next((i for i in items if i.get("item_code") == item_code), None)
    if hit and (hit.get("stock_qty") or 0) <= 0 and not hit.get("nearby_stores"):
        _fail(
            "W03",
            f"stock item {item_code} with zero Bin qty leaked without any nearby stock — "
            f"regression: gate widened beyond non-stock items",
        )
        return
    _pass("W03", "stock items with zero Bin qty are still gated correctly")


def test_w04_mapper_returns_external_device_plan(ctx: dict) -> None:
    """get_external_imei_plan_for_item returns the plan when the item
    is the service_item for a sellable + allow_external_device=1
    plan."""
    plan = get_external_imei_plan_for_item(ctx["service_external"])
    if not plan or plan.get("name") != ctx["plan_external"]:
        _fail(
            "W04",
            f"expected mapper to return {ctx['plan_external']!r}, got {plan!r}",
        )
        return
    if int(plan.get("allow_external_device") or 0) != 1:
        _fail("W04", f"mapped plan missing allow_external_device flag: {plan!r}")
        return
    if not plan.get("external_device_item"):
        _fail("W04", f"mapped plan missing external_device_item: {plan!r}")
        return
    _pass(
        "W04",
        f"mapper returned {plan['name']} with allow_external_device=1 and "
        f"external_device_item={plan['external_device_item']}",
    )


def test_w05_mapper_ignores_governance_only_plan(ctx: dict) -> None:
    """A plan with is_sellable=0 must NOT auto-open the IMEI prompt
    even if it has allow_external_device=1."""
    plan = get_external_imei_plan_for_item(ctx["service_gov"])
    if plan:
        _fail(
            "W05",
            f"governance-only plan leaked through mapper: {plan!r}",
        )
        return
    _pass("W05", "is_sellable=0 plan correctly ignored by mapper")


def test_w06_mapper_ignores_in_store_only_plan(ctx: dict) -> None:
    """A plan with allow_external_device=0 must NOT trigger the IMEI
    prompt — those flows use the standard 'Add VAS' selector against
    an in-store device."""
    plan = get_external_imei_plan_for_item(ctx["service_instore"])
    if plan:
        _fail(
            "W06",
            f"in-store-only plan leaked through mapper: {plan!r}",
        )
        return
    _pass("W06", "allow_external_device=0 plan correctly ignored by mapper")


def test_w07_mapper_ignores_unrelated_item(ctx: dict) -> None:
    """An Item that is not the service_item of any plan yields {}."""
    plan = get_external_imei_plan_for_item(ctx["unrelated_svc"])
    if plan:
        _fail("W07", f"unrelated item wrongly mapped to plan: {plan!r}")
        return
    _pass("W07", "unrelated service item correctly returns {}")


def test_w08_mapper_handles_empty_input(_ctx: dict) -> None:
    """Empty / None input must not crash — returns {}."""
    for value in ("", None):
        got = get_external_imei_plan_for_item(value)
        if got:
            _fail("W08", f"mapper returned {got!r} for input {value!r}")
            return
    _pass("W08", "empty/None inputs return {} without error")


# ─── Runner ────────────────────────────────────────────────────────────

def run_all() -> None:
    print("\n============================================================")
    print(" CH POS — Walk-up Service Bin Visibility + IMEI-Prompt E2E")
    print("============================================================\n")

    # ``frappe.flags.in_test`` disables governance's completeness check
    # (governance.validate_completeness) which would otherwise reject
    # our stub service Items for missing gst_hsn_code / ch_sub_category —
    # fields irrelevant to this bin-visibility contract.
    prev_in_test = frappe.flags.in_test
    frappe.flags.in_test = True
    frappe.flags.in_qa_seed = True
    try:
        _run_all_inner()
    finally:
        frappe.flags.in_qa_seed = False
        frappe.flags.in_test = prev_in_test


def _run_all_inner() -> None:
    _purge()

    # ── Seed items and plans ──
    external_device = _make_service_item(f"{TAG}-EXTERNAL-DEVICE")
    service_external = _make_service_item(f"{TAG}-SERVICE-EXTERNAL")
    service_instore = _make_service_item(f"{TAG}-SERVICE-INSTORE")
    service_gov = _make_service_item(f"{TAG}-SERVICE-GOV")
    unrelated_svc = _make_service_item(f"{TAG}-UNRELATED-SVC")

    plan_external = _seed_plan(
        f"{TAG} External IMEI Plan",
        service_item=service_external,
        is_sellable=1,
        allow_external_device=1,
        external_device_item=external_device,
    )
    plan_instore = _seed_plan(
        f"{TAG} In-Store Only Plan",
        service_item=service_instore,
        is_sellable=1,
        allow_external_device=0,
    )
    plan_gov = _seed_plan(
        f"{TAG} Governance-Only External Plan",
        service_item=service_gov,
        is_sellable=0,
        allow_external_device=1,
        external_device_item=external_device,
    )

    ctx = {
        "pos_profile": _first_pos_profile(),
        "external_device": external_device,
        "service_external": service_external,
        "service_instore": service_instore,
        "service_gov": service_gov,
        "unrelated_svc": unrelated_svc,
        "plan_external": plan_external,
        "plan_instore": plan_instore,
        "plan_gov": plan_gov,
    }

    tests = [
        test_w01_non_stock_service_item_visible_in_walkup_bin,
        test_w02_non_stock_flag_on_returned_payload,
        test_w03_stock_item_still_needs_bin_qty,
        test_w04_mapper_returns_external_device_plan,
        test_w05_mapper_ignores_governance_only_plan,
        test_w06_mapper_ignores_in_store_only_plan,
        test_w07_mapper_ignores_unrelated_item,
        test_w08_mapper_handles_empty_input,
    ]

    print("─── Test Run ────────────────────────────────────────────")
    for t in tests:
        try:
            t(ctx)
        except Exception as e:  # pragma: no cover — keep going on failure
            _fail(t.__name__, f"exception: {e!r}")

    _purge()

    print("\n─── Summary ─────────────────────────────────────────────")
    total = PASS + FAIL
    print(f"  Passed: {PASS} / {total}")
    print(f"  Failed: {FAIL}")
    if FAIL:
        for status, name, detail in results:
            if status == "FAIL":
                print(f"    ❌ {name}: {detail}")
    print("─────────────────────────────────────────────────────────\n")
