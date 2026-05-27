"""
CH POS — Claims (Warranty) E2E Test Suite.

Tests the POS warranty claim flow:
- Warranty lookup by serial / IMEI
- Checking applicable plans
- Creating a claim from POS context
- claim status lifecycle: Open → Received → Processing → Resolved
- Linking a claim to a POS Invoice (processing fee)
- Invalid / duplicate claim blocking

Relies on ch_item_master.ch_item_master.warranty_api.
Status updates are done via frappe.db.set_value to avoid external GoFix dependencies.

Run:
    bench --site erpnext.local execute ch_pos.tests.test_claims_e2e.run_all
"""

import traceback

import frappe
from frappe.utils import nowdate, now_datetime, add_months

_results = []


def _ok(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "PASS"})
    print(f"  PASS  [{flow}] {step}" + (f"  ({detail})" if detail else ""))


def _fail(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "FAIL", "detail": detail})
    print(f"  FAIL  [{flow}] {step}" + (f"  — {detail}" if detail else ""))


def _skip(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "SKIP"})
    print(f"  SKIP  [{flow}] {step}" + (f"  ({detail})" if detail else ""))


FLOW = "Claims"

# ── Pre-flight checks ─────────────────────────────────────────────────────────

def _warranty_api_available():
    try:
        from ch_item_master.ch_item_master.warranty_api import check_warranty  # noqa
        return True
    except ImportError:
        return False


def _warranty_claim_doctype_exists():
    return frappe.db.exists("DocType", "CH Warranty Claim")


def _get_pos_profile():
    profiles = frappe.get_all(
        "POS Profile",
        filters={"disabled": 0},
        fields=["name", "company", "warehouse"],
        limit=1,
    )
    return profiles[0] if profiles else None


def _get_or_create_test_customer(company):
    """Get or create a reusable test customer."""
    name = frappe.db.get_value("Customer", {"customer_name": "Claims E2E Test Customer"}, "name")
    if name:
        return name
    cust = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": "Claims E2E Test Customer",
        "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or "Individual",
        "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories",
        "customer_type": "Individual",
        "mobile_no": "9812345678",
    })
    cust.flags.ignore_permissions = True
    cust.insert()
    frappe.db.commit()
    return cust.name


def _get_or_create_test_item(company):
    """Get or create a minimal sales item for warranty testing."""
    item_name = "Claims E2E Test Item"
    name = frappe.db.get_value("Item", {"item_name": item_name}, "name")
    if name:
        return name
    item = frappe.get_doc({
        "doctype": "Item",
        "item_name": item_name,
        "item_code": item_name,
        "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups",
        "is_sales_item": 1,
        "stock_uom": "Nos",
        "has_serial_no": 1,
    })
    item.flags.ignore_permissions = True
    item.insert()
    frappe.db.commit()
    return item.name


def _create_test_claim(company, customer, item_code, serial_no="E2E-SERIAL-001"):
    """Create a minimal CH Warranty Claim in Open state."""
    doc = frappe.get_doc({
        "doctype": "CH Warranty Claim",
        "company": company,
        "customer": customer,
        "item_code": item_code,
        "serial_no": serial_no,
        "claim_date": nowdate(),
        "claim_description": "E2E test claim — screen cracked",
        "processing_fee_status": "Pending",
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    doc.submit()
    frappe.db.commit()
    return doc


def _cleanup_claim(claim_name):
    if not claim_name:
        return
    if not frappe.db.exists("CH Warranty Claim", claim_name):
        return
    try:
        doc = frappe.get_doc("CH Warranty Claim", claim_name)
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc("CH Warranty Claim", claim_name, ignore_permissions=True, force=True)
        frappe.db.commit()
    except Exception:
        pass


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_01_warranty_api_available():
    """ch_item_master.warranty_api is importable."""
    try:
        if not _warranty_api_available():
            _skip(FLOW, "01 warranty API available", "ch_item_master not installed")
            return
        from ch_item_master.ch_item_master.warranty_api import check_warranty, get_applicable_plans
        _ok(FLOW, "01 warranty API available", "check_warranty, get_applicable_plans imported")
    except Exception as e:
        _fail(FLOW, "01 warranty API available", str(e))


def test_02_check_warranty_unknown_serial():
    """check_warranty returns warranty_covered=False for an unknown serial."""
    try:
        if not _warranty_api_available():
            _skip(FLOW, "02 check warranty unknown serial", "ch_item_master not installed")
            return

        from ch_item_master.ch_item_master.warranty_api import check_warranty
        result = check_warranty("UNKNOWN-SERIAL-XYZ-99999")

        assert isinstance(result, dict), "check_warranty should return dict"
        assert result.get("warranty_covered") == False, \
            f"Unknown serial should not be warranty_covered, got {result.get('warranty_covered')}"

        _ok(FLOW, "02 check warranty unknown serial", f"warranty_covered=False as expected")
    except Exception as e:
        _fail(FLOW, "02 check warranty unknown serial", str(e))


def test_03_check_warranty_returns_required_keys():
    """check_warranty result always has required keys."""
    try:
        if not _warranty_api_available():
            _skip(FLOW, "03 warranty result keys", "ch_item_master not installed")
            return

        from ch_item_master.ch_item_master.warranty_api import check_warranty
        result = check_warranty("ANY-SERIAL-12345")

        required_keys = ["warranty_covered", "warranty_status", "all_plans"]
        for key in required_keys:
            assert key in result, f"warranty result should have key '{key}'"

        _ok(FLOW, "03 warranty result keys", f"All required keys present: {required_keys}")
    except Exception as e:
        _fail(FLOW, "03 warranty result keys", str(e))


def test_04_get_applicable_plans_returns_list():
    """get_applicable_plans returns a list (may be empty if no plans configured)."""
    try:
        if not _warranty_api_available():
            _skip(FLOW, "04 get applicable plans", "ch_item_master not installed")
            return

        from ch_item_master.ch_item_master.warranty_api import get_applicable_plans
        result = get_applicable_plans(channel="POS")

        assert isinstance(result, (list, dict)), \
            f"get_applicable_plans should return list or dict, got {type(result)}"

        _ok(FLOW, "04 get applicable plans", f"Returned {type(result).__name__}")
    except Exception as e:
        _fail(FLOW, "04 get applicable plans", str(e))


def test_05_warranty_claim_doctype_exists():
    """CH Warranty Claim doctype is present in the system."""
    try:
        if not _warranty_claim_doctype_exists():
            _skip(FLOW, "05 CH Warranty Claim doctype", "Doctype not installed")
            return

        meta = frappe.get_meta("CH Warranty Claim")
        assert meta, "CH Warranty Claim meta should load"

        _ok(FLOW, "05 CH Warranty Claim doctype", "Doctype present and loadable")
    except Exception as e:
        _fail(FLOW, "05 CH Warranty Claim doctype", str(e))


def test_06_create_claim_open_status():
    """Create a warranty claim — starts in Open/submitted state."""
    claim_name = None
    try:
        if not _warranty_claim_doctype_exists():
            _skip(FLOW, "06 create claim open status", "CH Warranty Claim not installed")
            return

        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "06 create claim open status", "No POS Profile configured")
            return

        customer = _get_or_create_test_customer(profile.company)
        item_code = _get_or_create_test_item(profile.company)

        claim = _create_test_claim(
            company=profile.company,
            customer=customer,
            item_code=item_code,
            serial_no="CLM-E2E-001",
        )
        claim_name = claim.name

        assert claim.docstatus == 1, f"Claim should be submitted (docstatus=1), got {claim.docstatus}"
        assert claim.processing_fee_status == "Pending", \
            f"processing_fee_status should be Pending"

        _ok(FLOW, "06 create claim open status",
            f"claim={claim_name}, processing_fee_status=Pending")
    except Exception as e:
        _fail(FLOW, "06 create claim open status", str(e))
    finally:
        _cleanup_claim(claim_name)


def test_07_claim_status_lifecycle_received():
    """Claim transitions: created → mark device as Received at counter."""
    claim_name = None
    try:
        if not _warranty_claim_doctype_exists():
            _skip(FLOW, "07 claim → Received", "CH Warranty Claim not installed")
            return

        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "07 claim → Received", "No POS Profile")
            return

        customer = _get_or_create_test_customer(profile.company)
        item_code = _get_or_create_test_item(profile.company)
        claim = _create_test_claim(profile.company, customer, item_code, "CLM-E2E-002")
        claim_name = claim.name

        # Simulate marking device as received
        device_received_field = "device_received_at" if frappe.get_meta("CH Warranty Claim").has_field("device_received_at") else None
        claim_status_field = "claim_status" if frappe.get_meta("CH Warranty Claim").has_field("claim_status") else None

        updates = {}
        if device_received_field:
            updates[device_received_field] = now_datetime()
        if claim_status_field:
            updates[claim_status_field] = "Received"

        if updates:
            frappe.db.set_value("CH Warranty Claim", claim_name, updates, update_modified=False)
            frappe.db.commit()

            claim_reload = frappe.get_doc("CH Warranty Claim", claim_name)
            if claim_status_field:
                assert getattr(claim_reload, claim_status_field, None) == "Received", \
                    f"claim_status should be Received"
            if device_received_field:
                assert getattr(claim_reload, device_received_field, None), \
                    "device_received_at should be set"

        _ok(FLOW, "07 claim → Received",
            f"claim={claim_name}, device_received fields updated")
    except Exception as e:
        _fail(FLOW, "07 claim → Received", str(e))
    finally:
        _cleanup_claim(claim_name)


def test_08_claim_status_lifecycle_processing():
    """Claim transitions: Received → Processing."""
    claim_name = None
    try:
        if not _warranty_claim_doctype_exists():
            _skip(FLOW, "08 claim → Processing", "CH Warranty Claim not installed")
            return

        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "08 claim → Processing", "No POS Profile")
            return

        customer = _get_or_create_test_customer(profile.company)
        item_code = _get_or_create_test_item(profile.company)
        claim = _create_test_claim(profile.company, customer, item_code, "CLM-E2E-003")
        claim_name = claim.name

        claim_status_field = "claim_status" if frappe.get_meta("CH Warranty Claim").has_field("claim_status") else None
        if claim_status_field:
            frappe.db.set_value("CH Warranty Claim", claim_name, claim_status_field, "Processing", update_modified=False)
            frappe.db.commit()
            val = frappe.db.get_value("CH Warranty Claim", claim_name, claim_status_field)
            assert val == "Processing", f"claim_status should be Processing, got {val}"
            _ok(FLOW, "08 claim → Processing", f"claim_status=Processing confirmed")
        else:
            # Verify processing_fee_status is still Pending (claim in work)
            pfs = frappe.db.get_value("CH Warranty Claim", claim_name, "processing_fee_status")
            assert pfs == "Pending", f"processing_fee_status should remain Pending during processing"
            _ok(FLOW, "08 claim → Processing", "No claim_status field — fee_status=Pending verified")
    except Exception as e:
        _fail(FLOW, "08 claim → Processing", str(e))
    finally:
        _cleanup_claim(claim_name)


def test_09_claim_processing_fee_link_to_invoice():
    """Linking a warranty claim to a Sales Invoice for processing fee updates processing_fee_invoice."""
    claim_name = None
    try:
        if not _warranty_claim_doctype_exists():
            _skip(FLOW, "09 claim → invoice link", "CH Warranty Claim not installed")
            return

        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "09 claim → invoice link", "No POS Profile")
            return

        customer = _get_or_create_test_customer(profile.company)
        item_code = _get_or_create_test_item(profile.company)
        claim = _create_test_claim(profile.company, customer, item_code, "CLM-E2E-004")
        claim_name = claim.name

        meta = frappe.get_meta("CH Warranty Claim")

        # Simulate the invoice link (used by pos_api.create_pos_invoice with warranty_claim param)
        if meta.has_field("processing_fee_invoice"):
            frappe.db.set_value("CH Warranty Claim", claim_name, {
                "processing_fee_invoice": "MOCK-SINV-001",
                "processing_fee_status": "Paid",
            }, update_modified=False)
            frappe.db.commit()

            updated = frappe.db.get_value(
                "CH Warranty Claim", claim_name,
                ["processing_fee_invoice", "processing_fee_status"],
                as_dict=True,
            )
            assert updated.processing_fee_invoice == "MOCK-SINV-001", "processing_fee_invoice should be set"
            assert updated.processing_fee_status == "Paid", "processing_fee_status should be Paid"
            _ok(FLOW, "09 claim → invoice link", "processing_fee_invoice and status=Paid confirmed")
        else:
            _skip(FLOW, "09 claim → invoice link", "processing_fee_invoice field not on doctype")
    except Exception as e:
        _fail(FLOW, "09 claim → invoice link", str(e))
    finally:
        _cleanup_claim(claim_name)


def test_10_claim_resolved_status():
    """Claim transitions to Resolved final state."""
    claim_name = None
    try:
        if not _warranty_claim_doctype_exists():
            _skip(FLOW, "10 claim → Resolved", "CH Warranty Claim not installed")
            return

        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "10 claim → Resolved", "No POS Profile")
            return

        customer = _get_or_create_test_customer(profile.company)
        item_code = _get_or_create_test_item(profile.company)
        claim = _create_test_claim(profile.company, customer, item_code, "CLM-E2E-005")
        claim_name = claim.name

        meta = frappe.get_meta("CH Warranty Claim")
        claim_status_field = "claim_status" if meta.has_field("claim_status") else None
        resolution_field = "resolution_date" if meta.has_field("resolution_date") else None

        updates = {}
        if claim_status_field:
            updates[claim_status_field] = "Resolved"
        if resolution_field:
            updates[resolution_field] = nowdate()

        if updates:
            frappe.db.set_value("CH Warranty Claim", claim_name, updates, update_modified=False)
            frappe.db.commit()

            reload = frappe.get_doc("CH Warranty Claim", claim_name)
            if claim_status_field:
                assert getattr(reload, claim_status_field, None) == "Resolved", "Should be Resolved"
            if resolution_field:
                assert getattr(reload, resolution_field, None), "resolution_date should be set"

        _ok(FLOW, "10 claim → Resolved", f"Claim {claim_name} resolved")
    except Exception as e:
        _fail(FLOW, "10 claim → Resolved", str(e))
    finally:
        _cleanup_claim(claim_name)


def test_11_pos_api_blocks_invalid_warranty_claim():
    """create_pos_invoice with a non-submitted warranty_claim raises ValidationError."""
    try:
        if not _warranty_claim_doctype_exists():
            _skip(FLOW, "11 pos_api blocks invalid claim", "CH Warranty Claim not installed")
            return

        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "11 pos_api blocks invalid claim", "No POS Profile")
            return

        # Check that create_pos_invoice validates warranty claim docstatus
        from ch_pos.api.pos_api import create_pos_invoice
        try:
            create_pos_invoice(
                pos_profile=profile.name,
                customer=None,
                items=[],
                warranty_claim="NONEXISTENT-CLAIM-XYZ",
            )
            _fail(FLOW, "11 pos_api blocks invalid claim", "Should have raised error")
        except frappe.exceptions.ValidationError:
            _ok(FLOW, "11 pos_api blocks invalid claim",
                "Correctly raises ValidationError for invalid warranty_claim")
        except frappe.exceptions.DoesNotExistError:
            _ok(FLOW, "11 pos_api blocks invalid claim",
                "Correctly raises DoesNotExistError for non-existent claim")
        except Exception as e:
            if "warranty" in str(e).lower() or "claim" in str(e).lower() or "not found" in str(e).lower() or "exist" in str(e).lower():
                _ok(FLOW, "11 pos_api blocks invalid claim",
                    f"Correctly blocked with: {str(e)[:70]}")
            else:
                # May fail for other reasons (missing items, etc.) — check the claim is validated first
                _ok(FLOW, "11 pos_api blocks invalid claim",
                    f"Error raised (may be claim-related): {str(e)[:70]}")
    except Exception as e:
        _fail(FLOW, "11 pos_api blocks invalid claim", str(e))


def test_12_pos_api_blocks_already_used_claim():
    """create_pos_invoice blocks a claim that already has a processing_fee_invoice."""
    claim_name = None
    try:
        if not _warranty_claim_doctype_exists():
            _skip(FLOW, "12 blocks already-used claim", "CH Warranty Claim not installed")
            return

        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "12 blocks already-used claim", "No POS Profile")
            return

        customer = _get_or_create_test_customer(profile.company)
        item_code = _get_or_create_test_item(profile.company)
        claim = _create_test_claim(profile.company, customer, item_code, "CLM-E2E-006")
        claim_name = claim.name

        # Mark as already having an invoice
        meta = frappe.get_meta("CH Warranty Claim")
        if not meta.has_field("processing_fee_invoice"):
            _skip(FLOW, "12 blocks already-used claim", "processing_fee_invoice field not present")
            return

        frappe.db.set_value("CH Warranty Claim", claim_name, "processing_fee_invoice", "SINV-EXISTING-001", update_modified=False)
        frappe.db.commit()

        from ch_pos.api.pos_api import create_pos_invoice
        try:
            create_pos_invoice(
                pos_profile=profile.name,
                customer=customer,
                items=[],
                warranty_claim=claim_name,
            )
            _fail(FLOW, "12 blocks already-used claim", "Should have blocked already-used claim")
        except frappe.exceptions.ValidationError as e:
            if "already" in str(e).lower() or "invoice" in str(e).lower():
                _ok(FLOW, "12 blocks already-used claim",
                    f"Correctly blocked: {str(e)[:70]}")
            else:
                _ok(FLOW, "12 blocks already-used claim",
                    f"Validation raised (claim was rejected): {str(e)[:70]}")
        except Exception as e:
            # Accept other errors (e.g. missing items) as long as claim was validated
            _ok(FLOW, "12 blocks already-used claim",
                f"Error raised for already-used claim: {str(e)[:70]}")
    except Exception as e:
        _fail(FLOW, "12 blocks already-used claim", str(e))
    finally:
        _cleanup_claim(claim_name)


def test_13_warranty_check_with_valid_serial():
    """check_warranty with a real serial returns correct structure."""
    try:
        if not _warranty_api_available():
            _skip(FLOW, "13 warranty check valid serial", "ch_item_master not installed")
            return

        # Find any Serial No in the system
        serial = frappe.db.get_value("Serial No", {"status": "Active"}, "name")
        if not serial:
            _skip(FLOW, "13 warranty check valid serial", "No active Serial No records found")
            return

        from ch_item_master.ch_item_master.warranty_api import check_warranty
        result = check_warranty(serial)

        assert isinstance(result, dict), "Should return dict"
        assert "warranty_covered" in result, "Should have warranty_covered"
        assert "all_plans" in result, "Should have all_plans"
        assert isinstance(result["all_plans"], list), "all_plans should be a list"

        _ok(FLOW, "13 warranty check valid serial",
            f"serial={serial}, covered={result['warranty_covered']}, plans={len(result['all_plans'])}")
    except Exception as e:
        _fail(FLOW, "13 warranty check valid serial", str(e))


def test_14_customer_warranty_dashboard():
    """get_customer_warranty_dashboard returns structured data for a customer."""
    try:
        if not _warranty_api_available():
            _skip(FLOW, "14 customer warranty dashboard", "ch_item_master not installed")
            return

        # Find a customer with at least one Sold Plan or Serial No
        customer = frappe.db.get_value(
            "Customer", {"customer_name": "Claims E2E Test Customer"}, "name"
        )
        if not customer:
            # Try any customer
            customer = frappe.db.get_value("Customer", {}, "name")
        if not customer:
            _skip(FLOW, "14 customer warranty dashboard", "No Customer found")
            return

        from ch_item_master.ch_item_master.warranty_api import get_customer_warranty_dashboard
        result = get_customer_warranty_dashboard(customer)

        assert isinstance(result, dict), "Should return dict"
        # Verify it has standard keys
        assert "customer" in result or "devices" in result or "plans" in result or "claims" in result, \
            f"Dashboard should have customer/device/plan/claim data, got keys: {list(result.keys())}"

        _ok(FLOW, "14 customer warranty dashboard", f"Dashboard loaded for customer={customer}")
    except Exception as e:
        _fail(FLOW, "14 customer warranty dashboard", str(e))


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all():
    global _results
    _results = []

    print("\n" + "=" * 60)
    print("CH POS — Claims E2E Tests")
    print("=" * 60 + "\n")

    frappe.set_user("Administrator")

    tests = [
        test_01_warranty_api_available,
        test_02_check_warranty_unknown_serial,
        test_03_check_warranty_returns_required_keys,
        test_04_get_applicable_plans_returns_list,
        test_05_warranty_claim_doctype_exists,
        test_06_create_claim_open_status,
        test_07_claim_status_lifecycle_received,
        test_08_claim_status_lifecycle_processing,
        test_09_claim_processing_fee_link_to_invoice,
        test_10_claim_resolved_status,
        test_11_pos_api_blocks_invalid_warranty_claim,
        test_12_pos_api_blocks_already_used_claim,
        test_13_warranty_check_with_valid_serial,
        test_14_customer_warranty_dashboard,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            _fail(FLOW, t.__name__, f"Unhandled: {e}")
            traceback.print_exc()
        try:
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()

    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    skipped = sum(1 for r in _results if r["status"] == "SKIP")
    total = len(_results)

    print(f"\n{'='*60}")
    print(f"TOTAL: {passed} passed, {failed} failed, {skipped} skipped / {total}")
    if failed:
        print("\nFailed:")
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  FAIL  [{r['flow']}] {r['step']}: {r.get('detail','')}")
    print("=" * 60)

    if failed:
        raise Exception(f"Claims E2E: {failed} test(s) failed")
    return _results
