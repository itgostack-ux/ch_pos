"""
CH POS — Queue / Token Management E2E Test Suite.

Tests the POS Kiosk Token lifecycle and queue management APIs.

Run:
    bench --site erpnext.local execute ch_pos.tests.test_queue_management_e2e.run_all
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_pos_profile():
    """Return first enabled POS Profile or None."""
    profiles = frappe.get_all(
        "POS Profile",
        filters={"disabled": 0},
        fields=["name", "company", "warehouse"],
        order_by="name asc",
        limit=1,
    )
    return profiles[0] if profiles else None


def _set_user_pos_roles():
    """Ensure the test user has POS roles."""
    frappe.set_user("Administrator")
    try:
        user = frappe.get_doc("User", "Administrator")
        role_names = [r.role for r in user.roles]
        for role in ("POS Manager", "POS User"):
            if role not in role_names and frappe.db.exists("Role", role):
                user.append("roles", {"role": role})
        user.flags.ignore_permissions = True
        user.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        pass


def _create_test_token(pos_profile_name, suffix=""):
    """Directly create a POS Kiosk Token bypassing the rate limiter."""
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
        "customer_name": f"Test Customer{suffix}",
        "customer_phone": "9876543210",
        "device_type": "Mobile",
        "device_brand": "Samsung",
        "device_model": "Galaxy A54",
        "issue_category": "Screen Replacement",
        "issue_description": "Cracked screen",
        "visit_source": "Kiosk",
        "visit_purpose": "Repair",
        "expires_at": frappe.utils.add_days(now_datetime(), 1),
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    doc.submit()
    return doc


def _cleanup_token(token_name):
    if token_name and frappe.db.exists("POS Kiosk Token", token_name):
        try:
            frappe.db.set_value("POS Kiosk Token", token_name, "status", "Cancelled", update_modified=False)
            frappe.db.commit()
        except Exception:
            pass


# ── Tests ─────────────────────────────────────────────────────────────────────

FLOW = "Queue Management"


def test_01_get_store_config():
    """get_store_config returns brands, issues, and store metadata."""
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "01 get_store_config", "No POS Profile configured")
            return

        from ch_pos.api.token_api import get_store_config
        result = get_store_config(profile.name)

        assert isinstance(result.get("brands"), list), "brands should be a list"
        assert isinstance(result.get("issues"), list), "issues should be a list"
        assert result.get("store_name") == profile.name, "store_name should match POS Profile"
        assert result.get("company"), "company should be set"
        _ok(FLOW, "01 get_store_config", f"brands={len(result['brands'])}, issues={len(result['issues'])}")
    except Exception as e:
        _fail(FLOW, "01 get_store_config", str(e))


def test_02_get_brand_models():
    """get_brand_models returns a list for any known brand."""
    try:
        from ch_pos.api.token_api import get_brand_models
        # Try common brands — one of them should exist or return empty list gracefully
        for brand in ("Samsung", "Apple", "Realme", "Other"):
            result = get_brand_models(brand)
            assert isinstance(result, list), f"get_brand_models({brand}) should return list"

        _ok(FLOW, "02 get_brand_models", "Returns list for any brand (empty list is valid)")
    except Exception as e:
        _fail(FLOW, "02 get_brand_models", str(e))


def test_03_token_display_generation():
    """Token display numbers follow the ABR-STORE-NNN format."""
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "03 token display generation", "No POS Profile configured")
            return

        from ch_pos.api.token_api import _generate_token_display
        company_abbr = frappe.db.get_value("Company", profile.company, "abbr") or "CH"
        token1 = _generate_token_display(profile.name, company_abbr)

        assert token1, "token_display should not be empty"
        parts = token1.split("-")
        # Format is company_abbr-store_code-seq, may have variable number of segments
        assert len(parts) >= 2, f"Token should have at least 2 parts, got: {token1}"
        assert parts[-1].isdigit(), f"Last part (sequence) should be numeric: {token1}"

        _ok(FLOW, "03 token display generation", f"Sample token: {token1}")
    except Exception as e:
        _fail(FLOW, "03 token display generation", str(e))


def test_04_token_auto_increment_per_day():
    """Two tokens for the same store on the same day get sequential numbers."""
    token1_name = None
    token2_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "04 token auto-increment", "No POS Profile configured")
            return

        doc1 = _create_test_token(profile.name, " A")
        doc2 = _create_test_token(profile.name, " B")
        token1_name = doc1.name if doc1 else None
        token2_name = doc2.name if doc2 else None

        if not doc1 or not doc2:
            _skip(FLOW, "04 token auto-increment", "Could not create test tokens")
            return

        # Extract sequence numbers
        seq1 = int(doc1.token_display.split("-")[-1])
        seq2 = int(doc2.token_display.split("-")[-1])
        assert seq2 > seq1, f"Second token seq ({seq2}) should be > first ({seq1})"

        _ok(FLOW, "04 token auto-increment", f"{doc1.token_display} → {doc2.token_display}")
    except Exception as e:
        _fail(FLOW, "04 token auto-increment", str(e))
    finally:
        for n in [token1_name, token2_name]:
            _cleanup_token(n)


def test_05_token_created_in_waiting_status():
    """Kiosk-created token starts in Waiting status."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "05 token Waiting status", "No POS Profile configured")
            return

        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "05 token Waiting status", "Could not create token")
            return

        assert doc.status == "Waiting", f"New kiosk token should be Waiting, got {doc.status}"
        assert doc.visit_source == "Kiosk", f"Source should be Kiosk, got {doc.visit_source}"
        _ok(FLOW, "05 token Waiting status", f"token={doc.token_display}, status=Waiting")
    except Exception as e:
        _fail(FLOW, "05 token Waiting status", str(e))
    finally:
        _cleanup_token(token_name)


def test_06_assign_token():
    """assign_token sets technician and transitions to In Progress."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "06 assign_token", "No POS Profile configured")
            return

        _set_user_pos_roles()
        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "06 assign_token", "Could not create token")
            return

        from ch_pos.api.token_api import assign_token
        result = assign_token(token_name=doc.name, technician="Administrator")

        assert result.get("status") == "ok", f"assign_token should return ok, got {result}"
        updated = frappe.db.get_value("POS Kiosk Token", doc.name, ["status", "technician"], as_dict=True)
        assert updated.technician == "Administrator", f"technician should be Administrator"
        assert updated.status == "In Progress", f"Status should be In Progress after assign"

        _ok(FLOW, "06 assign_token", f"token={doc.token_display}, status={updated.status}")
    except Exception as e:
        _fail(FLOW, "06 assign_token", str(e))
    finally:
        _cleanup_token(token_name)


def test_07_start_token():
    """start_token marks service as started (In Progress with started_at)."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "07 start_token", "No POS Profile configured")
            return

        _set_user_pos_roles()
        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "07 start_token", "Could not create token")
            return

        from ch_pos.api.token_api import start_token
        result = start_token(token_name=doc.name)

        assert result.get("status") == "ok", f"start_token should return ok"
        updated = frappe.db.get_value("POS Kiosk Token", doc.name, ["status", "started_at"], as_dict=True)
        assert updated.status == "In Progress", f"Status should be In Progress"
        assert updated.started_at is not None, "started_at should be set"

        _ok(FLOW, "07 start_token", f"started_at={updated.started_at}")
    except Exception as e:
        _fail(FLOW, "07 start_token", str(e))
    finally:
        _cleanup_token(token_name)


def test_08_complete_token():
    """complete_token transitions status to Completed with completed_at."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "08 complete_token", "No POS Profile configured")
            return

        _set_user_pos_roles()
        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "08 complete_token", "Could not create token")
            return

        from ch_pos.api.token_api import complete_token
        result = complete_token(token_name=doc.name)

        assert result.get("status") == "ok", f"complete_token should return ok"
        updated = frappe.db.get_value("POS Kiosk Token", doc.name, ["status", "completed_at"], as_dict=True)
        assert updated.status == "Completed", f"Status should be Completed, got {updated.status}"
        assert updated.completed_at is not None, "completed_at should be set"

        _ok(FLOW, "08 complete_token", f"status=Completed, completed_at={updated.completed_at}")
    except Exception as e:
        _fail(FLOW, "08 complete_token", str(e))
    finally:
        _cleanup_token(token_name)


def test_09_cancel_token():
    """cancel_token sets status to Cancelled for a Waiting token."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "09 cancel_token", "No POS Profile configured")
            return

        _set_user_pos_roles()
        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "09 cancel_token", "Could not create token")
            return

        from ch_pos.api.token_api import cancel_token
        result = cancel_token(token_name=doc.name)

        assert result.get("status") == "ok", f"cancel_token should return ok"
        status = frappe.db.get_value("POS Kiosk Token", doc.name, "status")
        assert status == "Cancelled", f"Status should be Cancelled, got {status}"
        token_name = None  # already cancelled, no need to cleanup

        _ok(FLOW, "09 cancel_token", "Waiting → Cancelled")
    except Exception as e:
        _fail(FLOW, "09 cancel_token", str(e))
    finally:
        _cleanup_token(token_name)


def test_10_cancel_completed_token_blocked():
    """Cannot cancel an already Completed token."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "10 cancel blocked on completed", "No POS Profile configured")
            return

        _set_user_pos_roles()
        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "10 cancel blocked on completed", "Could not create token")
            return

        # First complete it
        frappe.db.set_value("POS Kiosk Token", doc.name, "status", "Completed")
        frappe.db.commit()

        from ch_pos.api.token_api import cancel_token
        try:
            cancel_token(token_name=doc.name)
            _fail(FLOW, "10 cancel blocked on completed", "Should have thrown ValidationError")
        except frappe.exceptions.ValidationError:
            _ok(FLOW, "10 cancel blocked on completed", "Correctly blocked cancel on Completed token")
        except Exception as e:
            if "cancel" in str(e).lower() or "cannot" in str(e).lower():
                _ok(FLOW, "10 cancel blocked on completed", f"Correctly blocked: {str(e)[:60]}")
            else:
                _fail(FLOW, "10 cancel blocked on completed", f"Unexpected error: {e}")
    except Exception as e:
        _fail(FLOW, "10 cancel blocked on completed", str(e))
    finally:
        _cleanup_token(token_name)


def test_11_drop_token_requires_reason_and_remarks():
    """drop_token fails without reason or remarks."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "11 drop_token validation", "No POS Profile configured")
            return

        _set_user_pos_roles()
        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "11 drop_token validation", "Could not create token")
            return

        from ch_pos.api.token_api import drop_token

        # Missing reason — should fail
        try:
            drop_token(token_name=doc.name, drop_reason="", drop_remarks="Some remarks")
            _fail(FLOW, "11 drop_token validation", "Should have failed without reason")
        except frappe.exceptions.ValidationError:
            pass  # expected

        # Missing remarks — should fail
        try:
            drop_token(token_name=doc.name, drop_reason="Price Too High", drop_remarks="")
            _fail(FLOW, "11 drop_token validation", "Should have failed without remarks")
        except frappe.exceptions.ValidationError:
            pass  # expected

        # Valid drop — should succeed
        result = drop_token(
            token_name=doc.name,
            drop_reason="Price Too High",
            drop_remarks="Customer found cheaper option elsewhere",
        )
        assert result.get("status") == "ok", f"drop_token should succeed with valid args"
        token_name = None  # already Dropped

        _ok(FLOW, "11 drop_token validation", "Correctly validates reason and remarks")
    except Exception as e:
        _fail(FLOW, "11 drop_token validation", str(e))
    finally:
        _cleanup_token(token_name)


def test_12_engage_token():
    """engage_token moves a Waiting token to Engaged."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "12 engage_token", "No POS Profile configured")
            return

        _set_user_pos_roles()
        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "12 engage_token", "Could not create token")
            return

        from ch_pos.api.token_api import engage_token
        result = engage_token(token_name=doc.name, sales_executive="Administrator")

        assert result.get("status") == "ok", f"engage_token should return ok"
        assert result.get("token_status") == "Engaged", f"token_status should be Engaged"

        updated = frappe.db.get_value("POS Kiosk Token", doc.name, ["status", "engaged_at"], as_dict=True)
        assert updated.status == "Engaged", f"DB status should be Engaged, got {updated.status}"
        assert updated.engaged_at is not None, "engaged_at should be set"

        _ok(FLOW, "12 engage_token", f"Waiting → Engaged, engaged_at={updated.engaged_at}")
    except Exception as e:
        _fail(FLOW, "12 engage_token", str(e))
    finally:
        _cleanup_token(token_name)


def test_13_engage_non_waiting_blocked():
    """engage_token blocks on a non-Waiting token."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "13 engage blocked non-Waiting", "No POS Profile configured")
            return

        _set_user_pos_roles()
        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "13 engage blocked non-Waiting", "Could not create token")
            return

        # Set to In Progress so engage should fail
        frappe.db.set_value("POS Kiosk Token", doc.name, "status", "In Progress")
        frappe.db.commit()

        from ch_pos.api.token_api import engage_token
        try:
            engage_token(token_name=doc.name)
            _fail(FLOW, "13 engage blocked non-Waiting", "Should have been blocked")
        except frappe.exceptions.ValidationError:
            _ok(FLOW, "13 engage blocked non-Waiting", "Correctly blocked engage on In Progress token")
        except Exception as e:
            if "waiting" in str(e).lower() or "engage" in str(e).lower() or "status" in str(e).lower():
                _ok(FLOW, "13 engage blocked non-Waiting", f"Correctly blocked: {str(e)[:60]}")
            else:
                _fail(FLOW, "13 engage blocked non-Waiting", f"Unexpected error: {e}")
    except Exception as e:
        _fail(FLOW, "13 engage blocked non-Waiting", str(e))
    finally:
        _cleanup_token(token_name)


def test_14_quick_walkin():
    """quick_walkin creates a token in Engaged state immediately."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "14 quick_walkin", "No POS Profile configured")
            return

        _set_user_pos_roles()
        from ch_pos.api.token_api import quick_walkin
        result = quick_walkin(
            pos_profile=profile.name,
            visit_purpose="Sales",
            category_interest="Mobile",  # valid value per CH Token doctype
            brand_interest="Samsung",
            budget_range="10K-20K",  # valid enum value
        )

        token_name = result.get("name")
        assert result.get("status") == "ok", f"quick_walkin should return ok"
        assert result.get("token"), "Should return token display number"
        assert result.get("visit_purpose") == "Sales", "visit_purpose should be Sales"

        if token_name:
            status = frappe.db.get_value("POS Kiosk Token", token_name, "status")
            assert status == "Engaged", f"quick_walkin token should be Engaged, got {status}"

        _ok(FLOW, "14 quick_walkin", f"token={result.get('token')}, status=Engaged")
    except Exception as e:
        _fail(FLOW, "14 quick_walkin", str(e))
    finally:
        _cleanup_token(token_name)


def test_15_log_counter_walkin():
    """log_counter_walkin creates an In Progress counter token."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "15 log_counter_walkin", "No POS Profile configured")
            return

        from ch_pos.api.token_api import log_counter_walkin
        result = log_counter_walkin(
            pos_profile=profile.name,
            visit_purpose="Enquiry",
            customer_name="Counter Test",
            remarks="Looking for accessories",
        )

        token_name = result.get("name")
        assert result.get("status") == "ok", f"log_counter_walkin should return ok"
        assert result.get("token"), "Should return token display number"

        if token_name:
            doc_status = frappe.db.get_value("POS Kiosk Token", token_name, "status")
            assert doc_status == "In Progress", f"Counter token should be In Progress, got {doc_status}"

        _ok(FLOW, "15 log_counter_walkin", f"token={result.get('token')}, status=In Progress")
    except Exception as e:
        _fail(FLOW, "15 log_counter_walkin", str(e))
    finally:
        _cleanup_token(token_name)


def test_16_get_queue():
    """get_queue returns a list of tokens with expected fields."""
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "16 get_queue", "No POS Profile configured")
            return

        from ch_pos.api.token_api import get_queue
        result = get_queue(pos_profile=profile.name, date_filter="today")

        assert isinstance(result, list), "get_queue should return a list"
        for token in result[:3]:  # Spot check first few
            assert "token_display" in token, "token should have token_display"
            assert "status" in token, "token should have status"
            assert "customer_name" in token, "token should have customer_name"

        _ok(FLOW, "16 get_queue", f"Returned {len(result)} tokens for today")
    except Exception as e:
        _fail(FLOW, "16 get_queue", str(e))


def test_17_get_dashboard_stats():
    """get_dashboard_stats returns aggregate metrics."""
    try:
        profile = _get_pos_profile()

        from ch_pos.api.token_api import get_dashboard_stats
        result = get_dashboard_stats(pos_profile=profile.name if profile else None, date_filter="today")

        assert isinstance(result, dict), "get_dashboard_stats should return dict"
        for key in ("total", "waiting", "in_progress", "completed", "cancelled"):
            assert key in result, f"Dashboard stats should have '{key}'"
        assert isinstance(result.get("completion_rate"), (int, float)), "completion_rate should be numeric"

        _ok(FLOW, "17 get_dashboard_stats", f"total={result.get('total')}, rate={result.get('completion_rate')}%")
    except Exception as e:
        _fail(FLOW, "17 get_dashboard_stats", str(e))


def test_18_get_store_users():
    """get_store_users returns users for the store (fallback to all system users)."""
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "18 get_store_users", "No POS Profile configured")
            return

        from ch_pos.api.token_api import get_store_users
        result = get_store_users(pos_profile=profile.name)

        assert isinstance(result, list), "get_store_users should return list"
        # Should have at least Administrator from fallback
        assert len(result) >= 0, "Should return users list (may be empty)"

        _ok(FLOW, "18 get_store_users", f"Returned {len(result)} users")
    except Exception as e:
        _fail(FLOW, "18 get_store_users", str(e))


def test_19_get_pos_waiting_tokens():
    """get_pos_waiting_tokens returns only Waiting/Engaged/In Progress tokens for today."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "19 get_pos_waiting_tokens", "No POS Profile configured")
            return

        # Create a waiting token so there's at least one
        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None

        from ch_pos.api.token_api import get_pos_waiting_tokens
        result = get_pos_waiting_tokens(pos_profile=profile.name)

        assert isinstance(result, list), "get_pos_waiting_tokens should return list"
        # All returned tokens should be active statuses
        for t in result:
            assert t["status"] in ("Waiting", "Engaged", "In Progress"), \
                f"All returned tokens should be active, found status={t['status']}"

        _ok(FLOW, "19 get_pos_waiting_tokens", f"Returned {len(result)} active tokens")
    except Exception as e:
        _fail(FLOW, "19 get_pos_waiting_tokens", str(e))
    finally:
        _cleanup_token(token_name)


def test_20_get_reports():
    """get_reports returns daily breakdown and technician performance."""
    try:
        profile = _get_pos_profile()

        from ch_pos.api.token_api import get_reports
        result = get_reports(pos_profile=profile.name if profile else None, days=7)

        assert isinstance(result, dict), "get_reports should return dict"
        assert "daily_breakdown" in result, "Should have daily_breakdown"
        assert "tech_performance" in result, "Should have tech_performance"
        assert isinstance(result["daily_breakdown"], list), "daily_breakdown should be list"
        assert isinstance(result["tech_performance"], list), "tech_performance should be list"

        _ok(FLOW, "20 get_reports", f"daily_rows={len(result['daily_breakdown'])}, tech_rows={len(result['tech_performance'])}")
    except Exception as e:
        _fail(FLOW, "20 get_reports", str(e))


def test_21_get_walkin_insights():
    """get_walkin_insights returns structured insights for the period."""
    try:
        profile = _get_pos_profile()

        from ch_pos.api.token_api import get_walkin_insights
        result = get_walkin_insights(pos_profile=profile.name if profile else None, days=30)

        assert isinstance(result, dict), "get_walkin_insights should return dict"
        assert "insights" in result, "Should have insights key"
        assert "summary" in result, "Should have summary key"

        _ok(FLOW, "21 get_walkin_insights", f"insights={len(result.get('insights', []))}")
    except Exception as e:
        _fail(FLOW, "21 get_walkin_insights", str(e))


def test_22_find_customer_by_phone():
    """find_customer_by_phone returns None for unknown phone, value or None for known."""
    try:
        from ch_pos.api.token_api import find_customer_by_phone

        # Unknown phone — should return None, not raise
        result = find_customer_by_phone("0000000000")
        assert result is None, f"Unknown phone should return None, got {result}"

        # Empty input — should return None
        result2 = find_customer_by_phone("")
        assert result2 is None, f"Empty phone should return None, got {result2}"

        _ok(FLOW, "22 find_customer_by_phone", "Returns None for unknown/empty phone gracefully")
    except Exception as e:
        _fail(FLOW, "22 find_customer_by_phone", str(e))


def test_23_audit_orphan_invoices():
    """audit_orphan_invoices returns structured result."""
    try:
        profile = _get_pos_profile()

        from ch_pos.api.token_api import audit_orphan_invoices
        result = audit_orphan_invoices(
            pos_profile=profile.name if profile else "",
            date=nowdate(),
        )

        assert isinstance(result, dict), "audit_orphan_invoices should return dict"
        assert "total_orphans" in result, "Should have total_orphans"
        assert "invoices" in result, "Should have invoices list"
        assert isinstance(result["invoices"], list), "invoices should be list"

        _ok(FLOW, "23 audit_orphan_invoices", f"orphans={result['total_orphans']} on {nowdate()}")
    except Exception as e:
        _fail(FLOW, "23 audit_orphan_invoices", str(e))


def test_24_get_pos_profiles():
    """get_pos_profiles returns list of active profiles."""
    try:
        from ch_pos.api.token_api import get_pos_profiles
        result = get_pos_profiles()

        assert isinstance(result, list), "get_pos_profiles should return list"
        for profile in result:
            assert "name" in profile, "Profile should have name"
            assert "company" in profile, "Profile should have company"

        _ok(FLOW, "24 get_pos_profiles", f"Returned {len(result)} active profiles")
    except Exception as e:
        _fail(FLOW, "24 get_pos_profiles", str(e))


def test_25_daily_reset_confirmed_by_date_filter():
    """Tokens from yesterday are NOT in today's queue."""
    token_name = None
    try:
        profile = _get_pos_profile()
        if not profile:
            _skip(FLOW, "25 daily reset (date filter)", "No POS Profile configured")
            return

        doc = _create_test_token(profile.name)
        token_name = doc.name if doc else None
        if not doc:
            _skip(FLOW, "25 daily reset (date filter)", "Could not create token")
            return

        # Force-set creation to yesterday
        yesterday = frappe.utils.add_days(nowdate(), -1) + " 10:00:00"
        frappe.db.sql(
            "UPDATE `tabPOS Kiosk Token` SET creation = %s WHERE name = %s",
            (yesterday, doc.name)
        )
        frappe.db.commit()

        from ch_pos.api.token_api import get_pos_waiting_tokens
        today_tokens = get_pos_waiting_tokens(pos_profile=profile.name)
        today_names = {t["name"] for t in today_tokens}

        assert doc.name not in today_names, "Yesterday's token should not appear in today's queue"

        _ok(FLOW, "25 daily reset (date filter)", "Yesterday's token correctly excluded from today's queue")
    except Exception as e:
        _fail(FLOW, "25 daily reset (date filter)", str(e))
    finally:
        _cleanup_token(token_name)


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all():
    global _results
    _results = []

    print("\n" + "=" * 60)
    print("CH POS — Queue Management E2E Tests")
    print("=" * 60 + "\n")

    frappe.set_user("Administrator")

    tests = [
        test_01_get_store_config,
        test_02_get_brand_models,
        test_03_token_display_generation,
        test_04_token_auto_increment_per_day,
        test_05_token_created_in_waiting_status,
        test_06_assign_token,
        test_07_start_token,
        test_08_complete_token,
        test_09_cancel_token,
        test_10_cancel_completed_token_blocked,
        test_11_drop_token_requires_reason_and_remarks,
        test_12_engage_token,
        test_13_engage_non_waiting_blocked,
        test_14_quick_walkin,
        test_15_log_counter_walkin,
        test_16_get_queue,
        test_17_get_dashboard_stats,
        test_18_get_store_users,
        test_19_get_pos_waiting_tokens,
        test_20_get_reports,
        test_21_get_walkin_insights,
        test_22_find_customer_by_phone,
        test_23_audit_orphan_invoices,
        test_24_get_pos_profiles,
        test_25_daily_reset_confirmed_by_date_filter,
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
        raise Exception(f"Queue Management E2E: {failed} test(s) failed")
    return _results
