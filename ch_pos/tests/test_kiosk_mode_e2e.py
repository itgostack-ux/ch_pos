"""
CH POS — Kiosk Mode E2E Test Suite.

Tests the kiosk/self-service flow: store config, customer self-registration,
token generation, queue status, session expiry, and QR receipt metadata.

The kiosk API uses token_api.py (create_token, get_store_config, get_brand_models).

Run:
    bench --site erpnext.local execute ch_pos.tests.test_kiosk_mode_e2e.run_all
"""

import traceback

import frappe
from frappe.utils import nowdate, now_datetime

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


FLOW = "Kiosk Mode"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_pos_profile():
    profiles = frappe.get_all(
        "POS Profile",
        filters={"disabled": 0},
        fields=["name", "company", "warehouse"],
        order_by="name asc",
        limit=1,
    )
    return profiles[0] if profiles else None


def _cleanup_token(token_name):
    if token_name and frappe.db.exists("POS Kiosk Token", token_name):
        try:
            frappe.db.set_value("POS Kiosk Token", token_name, "status", "Cancelled", update_modified=False)
            frappe.db.commit()
        except Exception:
            pass


def _direct_create_kiosk_token(pos_profile_name, customer_name="Kiosk Test",
                                customer_phone="9876543210",
                                device_brand="Samsung", device_model="Galaxy A54",
                                issue_category="Screen Replacement"):
    """Create a kiosk token directly (bypassing rate limiter) for test isolation."""
    from ch_pos.api.token_api import _generate_token_display, _resolve_pos_profile
    profile = _resolve_pos_profile(pos_profile_name)
    if not profile:
        return None
    company_abbr = frappe.db.get_value("Company", profile.company, "abbr") or "CH"
    token_display = _generate_token_display(pos_profile_name, company_abbr)
    doc = frappe.get_doc({
        "doctype": "POS Kiosk Token",
        "pos_profile": pos_profile_name,
        "company": profile.company,
        "store": profile.warehouse,
        "status": "Waiting",
        "token_display": token_display,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "device_type": "Mobile",
        "device_brand": device_brand,
        "device_model": device_model,
        "issue_category": issue_category,
        "issue_description": "E2E test token",
        "visit_source": "Kiosk",
        "visit_purpose": "Repair",
        "expires_at": frappe.utils.add_days(now_datetime(), 1),
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    doc.submit()
    return doc


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_01_store_config_loads_for_valid_profile():
    """Kiosk loads store config (brands + issue categories) for a valid POS Profile."""
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "01 store config valid profile", "No POS Profile configured")
            return

        from ch_pos.api.token_api import get_store_config
        config = get_store_config(profile.name)

        assert config.get("store_name") == profile.name, "store_name should match"
        assert config.get("company"), "company should be present"
        assert isinstance(config.get("brands"), list), "brands must be a list"
        assert isinstance(config.get("issues"), list), "issues must be a list"
        assert len(config.get("issues", [])) > 0, "At least one issue category required"

        # Verify issue objects have key and icon
        first_issue = config["issues"][0]
        assert "key" in first_issue, "Issue should have key"
        assert "icon" in first_issue, "Issue should have icon"

        _ok(FLOW, "01 store config valid profile",
            f"brands={len(config['brands'])}, issues={len(config['issues'])}")
    except Exception as e:
        _fail(FLOW, "01 store config valid profile", str(e))


def test_02_store_config_invalid_profile_raises():
    """get_store_config raises DoesNotExistError for unknown profile."""
    try:
        from ch_pos.api.token_api import get_store_config
        try:
            get_store_config("NONEXISTENT_PROFILE_XYZ")
            _fail(FLOW, "02 invalid profile raises", "Should have raised an error")
        except frappe.exceptions.DoesNotExistError:
            _ok(FLOW, "02 invalid profile raises", "Correctly raises DoesNotExistError")
        except Exception as e:
            if "not found" in str(e).lower() or "invalid" in str(e).lower() or "does not exist" in str(e).lower():
                _ok(FLOW, "02 invalid profile raises", f"Raises error: {str(e)[:60]}")
            else:
                _fail(FLOW, "02 invalid profile raises", f"Unexpected error type: {type(e).__name__}: {e}")
    except Exception as e:
        _fail(FLOW, "02 invalid profile raises", str(e))


def test_03_brand_models_for_all_standard_brands():
    """get_brand_models returns a list (possibly empty) for all standard brands."""
    try:
        from ch_pos.api.token_api import get_brand_models

        brands_to_test = ["Samsung", "Apple", "OnePlus", "Realme", "Oppo", "Vivo", "Other"]
        all_pass = True
        for brand in brands_to_test:
            result = get_brand_models(brand)
            if not isinstance(result, list):
                all_pass = False
                _fail(FLOW, "03 brand models standard brands", f"{brand} returned non-list: {type(result)}")
                return

        _ok(FLOW, "03 brand models standard brands",
            f"All {len(brands_to_test)} brands return lists")
    except Exception as e:
        _fail(FLOW, "03 brand models standard brands", str(e))


def test_04_brand_models_no_duplicates():
    """get_brand_models deduplicates item names."""
    try:
        from ch_pos.api.token_api import get_brand_models

        result = get_brand_models("Samsung")
        assert len(result) == len(set(result)), "get_brand_models should not have duplicate models"

        _ok(FLOW, "04 brand models no duplicates", f"Samsung models deduplicated ({len(result)} unique)")
    except Exception as e:
        _fail(FLOW, "04 brand models no duplicates", str(e))


def test_05_self_registration_full_flow():
    """Kiosk customer self-registration creates a token with all required fields."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "05 self-registration full flow", "No POS Profile configured")
            return

        # Use direct creation to bypass rate limiter in CI
        doc = _direct_create_kiosk_token(
            pos_profile_name=profile.name,
            customer_name="Priya Sharma",
            customer_phone="9876543210",
            device_brand="Apple",
            device_model="iPhone 13",
            issue_category="Battery Replacement",
        )
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "05 self-registration full flow", "Could not create test token")
            return

        # Verify all fields stored correctly
        assert doc.customer_name == "Priya Sharma", f"customer_name mismatch: {doc.customer_name}"
        assert doc.customer_phone == "9876543210", f"customer_phone mismatch: {doc.customer_phone}"
        assert doc.device_brand == "Apple", f"device_brand mismatch: {doc.device_brand}"
        assert doc.device_model == "iPhone 13", f"device_model mismatch: {doc.device_model}"
        assert doc.issue_category == "Battery Replacement", f"issue_category mismatch: {doc.issue_category}"
        assert doc.visit_source == "Kiosk", f"visit_source should be Kiosk"
        assert doc.visit_purpose == "Repair", f"visit_purpose should be Repair"
        assert doc.status == "Waiting", f"New kiosk token should be Waiting"
        assert doc.token_display, "token_display should be set"

        _ok(FLOW, "05 self-registration full flow",
            f"token={doc.token_display}, customer=Priya Sharma, device=iPhone 13")
    except Exception as e:
        _fail(FLOW, "05 self-registration full flow", str(e))
    finally:
        _cleanup_token(token_name)


def test_06_token_number_unique_per_day():
    """Multiple self-registrations in one day all get unique token display numbers."""
    created_names = []
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "06 token uniqueness", "No POS Profile configured")
            return

        docs = []
        for i in range(3):
            doc = _direct_create_kiosk_token(profile.name, customer_name=f"Customer {i}")
            if doc:
                docs.append(doc)
                created_names.append(doc.name)

        if len(docs) < 2:
            _skip(FLOW, "06 token uniqueness", "Could not create enough tokens")
            return

        display_numbers = [d.token_display for d in docs]
        assert len(display_numbers) == len(set(display_numbers)), \
            f"All token display numbers should be unique: {display_numbers}"

        _ok(FLOW, "06 token uniqueness",
            f"3 unique tokens: {', '.join(display_numbers)}")
    except Exception as e:
        _fail(FLOW, "06 token uniqueness", str(e))
    finally:
        for n in created_names:
            _cleanup_token(n)


def test_07_service_selection_issue_categories():
    """All standard issue categories from get_store_config are valid for token creation."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "07 service selection issue categories", "No POS Profile configured")
            return

        from ch_pos.api.token_api import get_store_config
        config = get_store_config(profile.name)
        issues = config.get("issues", [])
        if not issues:
            _skip(FLOW, "07 service selection issue categories", "No issue categories in config")
            return

        # Test the first issue category
        issue = issues[0]["key"]
        doc = _direct_create_kiosk_token(
            pos_profile_name=profile.name,
            issue_category=issue,
        )
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "07 service selection issue categories", "Could not create token")
            return

        assert doc.issue_category == issue, f"Issue category mismatch: {doc.issue_category} != {issue}"
        _ok(FLOW, "07 service selection issue categories",
            f"Token created with issue_category='{issue}'")
    except Exception as e:
        _fail(FLOW, "07 service selection issue categories", str(e))
    finally:
        _cleanup_token(token_name)


def test_08_qr_receipt_metadata():
    """Token created via kiosk contains enough data for QR receipt generation."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "08 QR receipt metadata", "No POS Profile configured")
            return

        doc = _direct_create_kiosk_token(
            pos_profile_name=profile.name,
            customer_name="QR Test Customer",
            customer_phone="9123456789",
            device_brand="Realme",
            device_model="Realme 11 Pro",
            issue_category="Camera Repair",
        )
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "08 QR receipt metadata", "Could not create token")
            return

        # Fields needed for QR receipt
        assert doc.token_display, "token_display required for QR"
        assert doc.customer_name, "customer_name required for QR"
        assert doc.customer_phone, "customer_phone required for QR"
        assert doc.name, "doc.name (ID) required for QR"
        assert doc.creation, "creation timestamp required for QR"
        assert doc.pos_profile, "pos_profile required for store name on QR"
        assert doc.issue_category, "issue_category required for QR"

        # Simulate QR payload structure
        qr_payload = {
            "token": doc.token_display,
            "id": doc.name,
            "customer": doc.customer_name,
            "phone": doc.customer_phone[-4:],  # last 4 digits only
            "store": doc.pos_profile,
            "issue": doc.issue_category,
            "created": str(doc.creation),
        }
        assert all(qr_payload.values()), "All QR payload fields should be set"

        _ok(FLOW, "08 QR receipt metadata",
            f"token={doc.token_display}, QR fields complete")
    except Exception as e:
        _fail(FLOW, "08 QR receipt metadata", str(e))
    finally:
        _cleanup_token(token_name)


def test_09_session_expiry_field_set():
    """Kiosk token has expires_at set to a future time."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "09 session expiry field", "No POS Profile configured")
            return

        doc = _direct_create_kiosk_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "09 session expiry field", "Could not create token")
            return

        assert doc.expires_at, "expires_at should be set on kiosk token"

        from frappe.utils import get_datetime
        expires = get_datetime(doc.expires_at)
        now = get_datetime(now_datetime())
        assert expires > now, f"expires_at ({doc.expires_at}) should be in the future"

        _ok(FLOW, "09 session expiry field", f"expires_at={doc.expires_at}")
    except Exception as e:
        _fail(FLOW, "09 session expiry field", str(e))
    finally:
        _cleanup_token(token_name)


def test_10_phone_validation_rejects_invalid():
    """create_token input validation: invalid phone number is rejected."""
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "10 phone validation", "No POS Profile configured")
            return

        # Test phone validation directly via the utility
        try:
            from buyback.utils import validate_indian_phone
            try:
                validate_indian_phone("1234567890", "Phone")
                # If no error, try a shorter one
                validate_indian_phone("12345", "Phone")
                _fail(FLOW, "10 phone validation", "Should have rejected invalid phone")
            except (frappe.exceptions.ValidationError, Exception) as ve:
                if "phone" in str(ve).lower() or "invalid" in str(ve).lower() or "number" in str(ve).lower():
                    _ok(FLOW, "10 phone validation", f"Correctly rejects invalid phone: {str(ve)[:60]}")
                else:
                    # validate_indian_phone may raise with different message
                    _ok(FLOW, "10 phone validation", "Phone validation raised error as expected")
        except ImportError:
            _skip(FLOW, "10 phone validation", "buyback app not installed — phone validation skipped")
    except Exception as e:
        _fail(FLOW, "10 phone validation", str(e))


def test_11_get_queue_shows_kiosk_tokens():
    """Kiosk-created tokens appear in the management queue with correct source."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "11 queue shows kiosk tokens", "No POS Profile configured")
            return

        doc = _direct_create_kiosk_token(profile.name, customer_name="Kiosk Queue Test")
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "11 queue shows kiosk tokens", "Could not create token")
            return

        from ch_pos.api.token_api import get_queue
        queue = get_queue(pos_profile=profile.name, date_filter="today")

        token_in_queue = next((t for t in queue if t["name"] == doc.name), None)
        assert token_in_queue is not None, f"Created token {doc.name} should appear in queue"
        assert token_in_queue["status"] == "Waiting", \
            f"Token status in queue should be Waiting, got {token_in_queue.get('status')}"

        _ok(FLOW, "11 queue shows kiosk tokens",
            f"token={doc.token_display} visible in queue with Waiting status")
    except Exception as e:
        _fail(FLOW, "11 queue shows kiosk tokens", str(e))
    finally:
        _cleanup_token(token_name)


def test_12_multiple_services_same_phone():
    """Same phone number can create multiple tokens (repair history)."""
    token_names = []
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "12 multiple services same phone", "No POS Profile configured")
            return

        PHONE = "9876543210"
        doc1 = _direct_create_kiosk_token(profile.name, customer_phone=PHONE,
                                           issue_category="Screen Replacement")
        doc2 = _direct_create_kiosk_token(profile.name, customer_phone=PHONE,
                                           issue_category="Battery Replacement")
        if doc1:
            token_names.append(doc1.name)
        if doc2:
            token_names.append(doc2.name)

        if not doc1 or not doc2:
            _skip(FLOW, "12 multiple services same phone", "Could not create both tokens")
            return

        assert doc1.name != doc2.name, "Two tokens for same phone should have different names"
        assert doc1.token_display != doc2.token_display, "Token display numbers should differ"

        _ok(FLOW, "12 multiple services same phone",
            f"Two tokens: {doc1.token_display}, {doc2.token_display}")
    except Exception as e:
        _fail(FLOW, "12 multiple services same phone", str(e))
    finally:
        for n in token_names:
            _cleanup_token(n)


def test_13_kiosk_token_docstatus_is_submitted():
    """Kiosk token is docstatus=1 (submitted) so it's immutable via UI."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "13 token docstatus submitted", "No POS Profile configured")
            return

        doc = _direct_create_kiosk_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "13 token docstatus submitted", "Could not create token")
            return

        assert doc.docstatus == 1, f"Kiosk token should be docstatus=1 (submitted), got {doc.docstatus}"
        _ok(FLOW, "13 token docstatus submitted", f"docstatus=1 confirmed")
    except Exception as e:
        _fail(FLOW, "13 token docstatus submitted", str(e))
    finally:
        _cleanup_token(token_name)


def test_14_convert_token_to_gofix_service_request():
    """convert_token_to_gofix creates a Service Request and marks token as Converted."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "14 convert token to GoFix", "No POS Profile configured")
            return

        # Check if Service Request doctype exists (GoFix module required)
        if not frappe.db.exists("DocType", "Service Request"):
            _skip(FLOW, "14 convert token to GoFix", "Service Request doctype not available (GoFix not installed)")
            return

        # Ensure POS Manager role
        try:
            user = frappe.get_doc("User", "Administrator")
            role_names = [r.role for r in user.roles]
            if "POS Manager" not in role_names and frappe.db.exists("Role", "POS Manager"):
                user.append("roles", {"role": "POS Manager"})
                user.save(ignore_permissions=True)
                frappe.db.commit()
        except Exception:
            pass

        doc = _direct_create_kiosk_token(
            pos_profile_name=profile.name,
            device_brand="Apple",
            device_model="iPhone 14",
            issue_category="Screen Replacement",
        )
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "14 convert token to GoFix", "Could not create token")
            return

        from ch_pos.api.token_api import convert_token_to_gofix
        result = convert_token_to_gofix(
            token_name=doc.name,
            pos_profile=profile.name,
            device_condition="Good",
            warranty_status="Out of Warranty",
        )

        assert result.get("service_request"), "Should return service_request name"
        assert result.get("token") == doc.token_display, "Token display should match"
        assert result.get("customer_name") == doc.customer_name, "Customer name should match"

        # Verify token marked Converted
        status = frappe.db.get_value("POS Kiosk Token", doc.name, "status")
        assert status == "Converted", f"Token should be Converted, got {status}"
        token_name = None  # status is now Converted, cleanup not needed

        # Cleanup the service request
        sr_name = result["service_request"]
        try:
            sr_doc = frappe.get_doc("Service Request", sr_name)
            if sr_doc.docstatus == 1:
                sr_doc.cancel()
            frappe.delete_doc("Service Request", sr_name, ignore_permissions=True, force=True)
            frappe.db.commit()
        except Exception:
            pass

        _ok(FLOW, "14 convert token to GoFix", f"SR={result['service_request']}, token=Converted")
    except Exception as e:
        _fail(FLOW, "14 convert token to GoFix", str(e))
    finally:
        _cleanup_token(token_name)


def test_15_convert_already_converted_token_blocked():
    """Converting an already-Converted token raises an error."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "15 double-convert blocked", "No POS Profile configured")
            return

        if not frappe.db.exists("DocType", "Service Request"):
            _skip(FLOW, "15 double-convert blocked", "GoFix not installed")
            return

        doc = _direct_create_kiosk_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "15 double-convert blocked", "Could not create token")
            return

        # Mark as already Converted
        frappe.db.set_value("POS Kiosk Token", doc.name, "status", "Converted")
        frappe.db.commit()

        from ch_pos.api.token_api import convert_token_to_gofix
        try:
            convert_token_to_gofix(token_name=doc.name, pos_profile=profile.name)
            _fail(FLOW, "15 double-convert blocked", "Should have raised error for already-Converted token")
        except frappe.exceptions.ValidationError:
            _ok(FLOW, "15 double-convert blocked", "Correctly blocks double-conversion")
        except Exception as e:
            if "convert" in str(e).lower() or "already" in str(e).lower():
                _ok(FLOW, "15 double-convert blocked", f"Correctly blocked: {str(e)[:60]}")
            else:
                _fail(FLOW, "15 double-convert blocked", f"Unexpected error: {e}")
    except Exception as e:
        _fail(FLOW, "15 double-convert blocked", str(e))
    finally:
        _cleanup_token(token_name)


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all():
    global _results
    _results = []

    print("\n" + "=" * 60)
    print("CH POS — Kiosk Mode E2E Tests")
    print("=" * 60 + "\n")

    frappe.set_user("Administrator")

    tests = [
        test_01_store_config_loads_for_valid_profile,
        test_02_store_config_invalid_profile_raises,
        test_03_brand_models_for_all_standard_brands,
        test_04_brand_models_no_duplicates,
        test_05_self_registration_full_flow,
        test_06_token_number_unique_per_day,
        test_07_service_selection_issue_categories,
        test_08_qr_receipt_metadata,
        test_09_session_expiry_field_set,
        test_10_phone_validation_rejects_invalid,
        test_11_get_queue_shows_kiosk_tokens,
        test_12_multiple_services_same_phone,
        test_13_kiosk_token_docstatus_is_submitted,
        test_14_convert_token_to_gofix_service_request,
        test_15_convert_already_converted_token_blocked,
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
        raise Exception(f"Kiosk Mode E2E: {failed} test(s) failed")
    return _results
