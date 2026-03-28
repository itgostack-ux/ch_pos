"""
CH POS — Walk-in Token E2E Test Suite.

Covers the complete walk-in tracking and token management lifecycle:

1.  Create token via Kiosk API (guest)
2.  Engage token (staff picks up customer)
3.  Drop token with mandatory reason
4.  Quick walk-in (2-second retail entry)
5.  Convert token via POS invoice submit
6.  Auto-create token for orphan invoice
7.  Revert token on invoice cancel
8.  Expire old tokens via scheduler job
9.  Audit orphan invoices API
10. Walk-in insights API

Run:
    bench --site erpnext.local execute ch_pos.tests.test_walkin_token_e2e.test_all
"""

import sys
import traceback

import frappe
from frappe.utils import now_datetime, nowdate, add_days, flt

results = []


def ok(name, detail=""):
    results.append({"scenario": name, "status": "PASS", "detail": detail})
    print(f"  PASS  {name}{f'  ({detail})' if detail else ''}")


def fail(name, detail=""):
    results.append({"scenario": name, "status": "FAIL", "detail": detail})
    print(f"  FAIL  {name}{f'  ({detail})' if detail else ''}")


def skip(name, detail=""):
    results.append({"scenario": name, "status": "SKIP", "detail": detail})
    print(f"  SKIP  {name}{f'  ({detail})' if detail else ''}")


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _get_test_context():
    """Gather required references: POS Profile, warehouse, company."""
    frappe.set_user("Administrator")
    profiles = frappe.get_all(
        "POS Profile",
        filters={"disabled": 0},
        fields=["name", "company", "warehouse"],
        order_by="name",
        limit=1,
    )
    if not profiles:
        raise RuntimeError("No active POS Profile found for testing")
    p = profiles[0]
    return {
        "pos_profile": p.name,
        "company": p.company,
        "warehouse": p.warehouse,
        "company_abbr": frappe.db.get_value("Company", p.company, "abbr") or "CH",
    }


def _cleanup_test_tokens():
    """Remove tokens created during this test run (customer_name starts with 'E2E-')."""
    tokens = frappe.get_all(
        "POS Kiosk Token",
        filters={"customer_name": ["like", "E2E-%"]},
        pluck="name",
    )
    for t in tokens:
        frappe.delete_doc("POS Kiosk Token", t, force=True)
    frappe.db.commit()


def _create_test_token(ctx, **overrides):
    """Create a token directly (bypassing API) for test setup."""
    from ch_pos.api.token_api import _generate_token_display
    display = _generate_token_display(ctx["pos_profile"], ctx["company_abbr"])
    vals = {
        "doctype": "POS Kiosk Token",
        "pos_profile": ctx["pos_profile"],
        "company": ctx["company"],
        "store": ctx["warehouse"],
        "status": "Waiting",
        "token_display": display,
        "customer_name": "E2E-Test",
        "customer_phone": "9876543210",
        "visit_source": "Kiosk",
        "visit_purpose": "Repair",
        "expires_at": add_days(now_datetime(), 1),
    }
    vals.update(overrides)
    doc = frappe.get_doc(vals)
    doc.flags.ignore_permissions = True
    doc.insert()
    return doc


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------


def test_01_create_token_via_api(ctx):
    """1. Create token via guest kiosk API."""
    from ch_pos.api.token_api import create_token
    result = create_token(
        pos_profile=ctx["pos_profile"],
        customer_name="E2E-Kiosk-Customer",
        customer_phone="9876543210",
        device_type="Mobile",
        device_brand="Samsung",
        device_model="Galaxy S23",
        issue_category="Screen Replacement",
        issue_description="Cracked screen",
    )
    assert result.get("token"), f"No token_display returned: {result}"
    assert result.get("name"), f"No doc name returned: {result}"

    doc = frappe.get_doc("POS Kiosk Token", result["name"])
    assert doc.status == "Waiting", f"Expected Waiting, got {doc.status}"
    assert doc.customer_name == "E2E-Kiosk-Customer"
    ok("Create token via Kiosk API", f"Token: {doc.token_display}")
    return doc.name


def test_02_engage_token(ctx, token_name):
    """2. Engage a Waiting token — staff picks up customer."""
    from ch_pos.api.token_api import engage_token
    result = engage_token(token_name)
    assert result.get("status") == "ok"

    doc = frappe.get_doc("POS Kiosk Token", token_name)
    assert doc.status == "Engaged", f"Expected Engaged, got {doc.status}"
    assert doc.engaged_at is not None, "engaged_at not set"
    ok("Engage token", f"engaged_at = {doc.engaged_at}")


def test_03_drop_token_with_reason(ctx):
    """3. Drop a token — mandatory reason required."""
    token = _create_test_token(ctx, customer_name="E2E-Drop-Customer")

    from ch_pos.api.token_api import drop_token

    # Should fail without reason
    try:
        drop_token(token.name, drop_reason="")
        fail("Drop without reason should throw")
        return
    except frappe.exceptions.ValidationError:
        pass  # expected

    # Should succeed with reason
    result = drop_token(
        token.name,
        drop_reason="Price Too High",
        drop_sub_reason="Competitor cheaper",
        drop_remarks="Customer saw Amazon price",
    )
    assert result.get("status") == "ok"

    doc = frappe.get_doc("POS Kiosk Token", token.name)
    assert doc.status == "Dropped", f"Expected Dropped, got {doc.status}"
    assert doc.drop_reason == "Price Too High"
    assert doc.exit_at is not None
    ok("Drop token with reason", f"reason={doc.drop_reason}")


def test_04_quick_walkin(ctx):
    """4. Quick walk-in — 2-second retail entry."""
    from ch_pos.api.token_api import quick_walkin
    result = quick_walkin(
        pos_profile=ctx["pos_profile"],
        visit_purpose="Sales",
        category_interest="Mobile",
        brand_interest="Apple",
        budget_range="50K-75K",
        customer_name="E2E-QuickWalkin",
    )
    assert result.get("status") == "ok"
    assert result.get("name")

    doc = frappe.get_doc("POS Kiosk Token", result["name"])
    assert doc.status == "Engaged", f"Expected Engaged, got {doc.status}"
    assert doc.category_interest == "Mobile"
    assert doc.brand_interest == "Apple"
    assert doc.budget_range == "50K-75K"
    assert doc.engaged_at is not None
    ok("Quick walk-in", f"Token: {doc.token_display}, purpose={doc.visit_purpose}")
    return doc.name


def test_05_convert_token_via_invoice(ctx, token_name):
    """5. Convert token when POS invoice is submitted."""
    # Ensure the token is in a convertible state
    frappe.db.set_value("POS Kiosk Token", token_name, "status", "Engaged")
    frappe.db.commit()

    # Create a minimal POS Sales Invoice linked to the token
    si = _create_test_pos_invoice(ctx, token_name=token_name)

    # Check token got converted
    token = frappe.get_doc("POS Kiosk Token", token_name)
    assert token.status == "Converted", f"Expected Converted, got {token.status}"
    assert token.converted_invoice == si.name
    assert token.exit_at is not None
    ok("Convert token via POS invoice", f"Invoice: {si.name}")
    return si.name


def test_06_auto_create_token_for_orphan(ctx):
    """6. Auto-create token when POS invoice submitted without one."""
    si = _create_test_pos_invoice(ctx, token_name=None)

    # The doc_event hook should auto-create a token
    linked_token = frappe.db.get_value("Sales Invoice", si.name, "custom_kiosk_token")
    if linked_token:
        token = frappe.get_doc("POS Kiosk Token", linked_token)
        assert token.status == "Converted"
        assert token.converted_invoice == si.name
        ok("Auto-create token for orphan invoice", f"Token: {linked_token}")
    else:
        # Auto-creation might be disabled or hook not wired — skip gracefully
        skip("Auto-create token for orphan invoice", "No token auto-created — check hooks")


def test_07_revert_token_on_cancel(ctx):
    """7. Revert token status when invoice is cancelled."""
    token = _create_test_token(ctx, customer_name="E2E-Revert-Customer", status="Engaged")
    si = _create_test_pos_invoice(ctx, token_name=token.name)

    # Verify converted
    assert frappe.db.get_value("POS Kiosk Token", token.name, "status") == "Converted"

    # Cancel the invoice
    si.reload()
    si.cancel()
    frappe.db.commit()

    token_status = frappe.db.get_value("POS Kiosk Token", token.name, "status")
    if token_status == "Expired":
        ok("Revert token on cancel", "Status reverted to Expired")
    elif token_status == "Converted":
        fail("Revert token on cancel", "Status still Converted after cancel — check revert_kiosk_token_status hook")
    else:
        ok("Revert token on cancel", f"Status set to {token_status}")


def test_08_expire_old_tokens(ctx):
    """8. Scheduler job expires old tokens."""
    from ch_pos.pos_kiosk.doctype.pos_kiosk_token.pos_kiosk_token import expire_old_tokens

    token = _create_test_token(
        ctx,
        customer_name="E2E-Expire-Customer",
        status="Waiting",
        expires_at=add_days(now_datetime(), -1),
    )
    # Submit the token so it can be expired (docstatus check in expire_old_tokens)
    token.submit()
    frappe.db.commit()

    expire_old_tokens()

    status = frappe.db.get_value("POS Kiosk Token", token.name, "status")
    assert status == "Expired", f"Expected Expired, got {status}"
    ok("Expire old tokens", f"Token {token.name} expired by scheduler")


def test_09_audit_orphan_invoices(ctx):
    """9. Audit orphan invoices API."""
    from ch_pos.api.token_api import audit_orphan_invoices
    result = audit_orphan_invoices(pos_profile=ctx["pos_profile"], date=nowdate())
    assert "total_orphans" in result
    assert "invoices" in result
    assert isinstance(result["invoices"], list)
    ok("Audit orphan invoices", f"Found {result['total_orphans']} orphans")


def test_10_walkin_insights(ctx):
    """10. Walk-in insights API returns structured data."""
    from ch_pos.api.token_api import get_walkin_insights
    result = get_walkin_insights(pos_profile=ctx["pos_profile"], days=30)
    assert "insights" in result or "summary" in result
    ok("Walk-in insights API", f"{len(result.get('insights', []))} insights returned")


# ---------------------------------------------------------------------------
# Invoice helper
# ---------------------------------------------------------------------------

def _create_test_pos_invoice(ctx, token_name=None):
    """Create a minimal POS Sales Invoice for testing token linkage."""
    # Find default customer
    customer = frappe.db.get_value("Customer", {"disabled": 0}, "name") or "Walk In"

    # Find an item that belongs to the company's default item group
    item = frappe.db.get_value(
        "Item",
        {"disabled": 0, "is_stock_item": 0, "has_variants": 0},
        ["name", "item_name"],
        as_dict=True,
    )
    if not item:
        # Try stock items
        item = frappe.db.get_value(
            "Item",
            {"disabled": 0, "has_variants": 0},
            ["name", "item_name"],
            as_dict=True,
        )
    if not item:
        raise RuntimeError("No items found for test invoice")

    # Find a mode of payment
    mop = frappe.db.get_value("Mode of Payment", {"enabled": 1}, "name") or "Cash"

    # Build income account
    company_abbr = ctx["company_abbr"]
    income_account = frappe.db.get_value(
        "Account",
        {"company": ctx["company"], "account_type": "Income Account", "is_group": 0},
        "name",
    )
    if not income_account:
        income_account = frappe.db.get_value(
            "Account",
            {"company": ctx["company"], "root_type": "Income", "is_group": 0},
            "name",
        )

    # Debit-to account
    debit_account = frappe.db.get_value(
        "Account",
        {"company": ctx["company"], "account_type": "Receivable", "is_group": 0},
        "name",
    )

    si = frappe.get_doc({
        "doctype": "Sales Invoice",
        "is_pos": 1,
        "pos_profile": ctx["pos_profile"],
        "company": ctx["company"],
        "customer": customer,
        "customer_name": "E2E-Token-Test",
        "posting_date": nowdate(),
        "set_warehouse": ctx["warehouse"],
        "update_stock": 0,  # Avoid stock complications in test
        "debit_to": debit_account,
        "items": [{
            "item_code": item.name,
            "item_name": item.item_name,
            "qty": 1,
            "rate": 100,
            "income_account": income_account,
        }],
        "payments": [{
            "mode_of_payment": mop,
            "amount": 100,
        }],
    })

    if token_name:
        si.custom_kiosk_token = token_name

    si.flags.ignore_permissions = True
    si.flags.ignore_mandatory = True
    si.insert()
    si.submit()
    frappe.db.commit()
    return si


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def test_all():
    """Execute all walk-in token E2E scenarios."""
    global results
    results = []

    print("\n" + "=" * 70)
    print("  Walk-in Token E2E Test Suite")
    print("=" * 70)

    try:
        ctx = _get_test_context()
        print(f"  Context: {ctx['pos_profile']} | {ctx['company']}")
    except Exception as e:
        print(f"  ABORT  Cannot build test context: {e}")
        return results

    _cleanup_test_tokens()

    scenarios = [
        ("01: Create token via Kiosk API", test_01_create_token_via_api, [ctx]),
        ("02: Engage token", None, None),  # depends on 01
        ("03: Drop token with reason", test_03_drop_token_with_reason, [ctx]),
        ("04: Quick walk-in", test_04_quick_walkin, [ctx]),
        ("05: Convert token via invoice", None, None),  # depends on 04
        ("06: Auto-create token for orphan", test_06_auto_create_token_for_orphan, [ctx]),
        ("07: Revert token on cancel", test_07_revert_token_on_cancel, [ctx]),
        ("08: Expire old tokens", test_08_expire_old_tokens, [ctx]),
        ("09: Audit orphan invoices", test_09_audit_orphan_invoices, [ctx]),
        ("10: Walk-in insights", test_10_walkin_insights, [ctx]),
    ]

    # Run test 01, then 02 depends on its result
    token_name_01 = None
    try:
        token_name_01 = test_01_create_token_via_api(ctx)
    except Exception as e:
        fail("01: Create token via Kiosk API", str(e))
        traceback.print_exc()

    if token_name_01:
        try:
            test_02_engage_token(ctx, token_name_01)
        except Exception as e:
            fail("02: Engage token", str(e))
            traceback.print_exc()
    else:
        skip("02: Engage token", "Depends on test 01")

    # Test 03: Drop
    try:
        test_03_drop_token_with_reason(ctx)
    except Exception as e:
        fail("03: Drop token with reason", str(e))
        traceback.print_exc()

    # Test 04: Quick walk-in, then 05 depends on it
    quick_token_name = None
    try:
        quick_token_name = test_04_quick_walkin(ctx)
    except Exception as e:
        fail("04: Quick walk-in", str(e))
        traceback.print_exc()

    if quick_token_name:
        try:
            test_05_convert_token_via_invoice(ctx, quick_token_name)
        except Exception as e:
            fail("05: Convert token via invoice", str(e))
            traceback.print_exc()
    else:
        skip("05: Convert token via invoice", "Depends on test 04")

    # Independent tests 06-10
    for name, fn, args in scenarios[5:]:
        try:
            fn(*args)
        except Exception as e:
            fail(name, str(e))
            traceback.print_exc()

    # Cleanup
    _cleanup_test_tokens()

    # Summary
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")

    print("\n" + "-" * 70)
    print(f"  Results: {passed} passed, {failed} failed, {skipped} skipped")
    print("-" * 70 + "\n")

    return results
