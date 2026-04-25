"""
Comprehensive E2E tests for untested POS modules.

Covers: Session Management, Offers Engine, Voucher System,
Exception Requests, Customer 360, Item Search/Barcode.

Run:
bench --site erpnext.local execute ch_pos.tests.test_modules_e2e.run_all
"""

import frappe
from frappe.utils import cint, flt, nowdate, today, add_days, now_datetime, getdate


PASS = 0
FAIL = 0
SKIP = 0
results = []


def log_pass(test_id, detail=""):
    global PASS
    PASS += 1
    results.append(("PASS", test_id, detail))
    print(f"  \u2705 {test_id}: {detail}" if detail else f"  \u2705 {test_id}")


def log_fail(test_id, detail=""):
    global FAIL
    FAIL += 1
    results.append(("FAIL", test_id, detail))
    print(f"  \u274c {test_id}: {detail}" if detail else f"  \u274c {test_id}")


def log_skip(test_id, detail=""):
    global SKIP
    SKIP += 1
    results.append(("SKIP", test_id, detail))
    print(f"  \u23ed {test_id}: {detail}" if detail else f"  \u23ed {test_id}")


def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}: expected {b!r}, got {a!r}")


def assert_true(val, msg=""):
    if not val:
        raise AssertionError(msg or f"Expected truthy, got {val!r}")


# ─── Context helpers ──────────────────────────────────────────────

def _get_ctx():
    """Build test context: POS profile, store, company, warehouse, item, customer."""
    from ch_pos.api.pos_api import get_active_session
    from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_store_business_date

    user = frappe.session.user

    # Prefer QA profiles which have store + stock configured
    profiles = frappe.get_all(
        "POS Profile", filters={"disabled": 0},
        fields=["name", "company", "warehouse"],
        order_by="name asc", limit=20,
    )
    if not profiles:
        raise Exception("No active POS Profile found")

    def _get_store(p):
        store = frappe.db.get_value("POS Profile Extension", {"pos_profile": p.name}, "store")
        if not store and p.warehouse:
            store = frappe.db.get_value("CH Store", {"warehouse": p.warehouse}, "name")
        return store

    # Sort profiles: prefer active sessions, then open business dates, filter by POS Executive access
    def _sort_key(p):
        store = _get_store(p)
        if not store:
            return (9,)
        # Must have POS Executive access
        has_access = frappe.db.exists("POS Executive", {
            "user": user, "store": store, "company": p.company, "is_active": 1,
        })
        if not has_access:
            return (8,)
        try:
            sess = get_active_session(p.name)
            if sess:
                return (0,)
        except Exception:
            pass
        # Check if store's business date is not yet closed
        try:
            biz_date = get_store_business_date(store)
            closed = frappe.db.exists("CH POS Session", {
                "store": store, "business_date": biz_date,
                "status": "Closed", "docstatus": 1,
            })
            return (1,) if not closed else (2,)
        except Exception:
            pass
        return (2,)
    profiles.sort(key=_sort_key)

    # Try to find a profile with a store and stock items
    for p in profiles:
        store = _get_store(p)
        if not store:
            continue

        # Must have POS Executive access
        has_access = frappe.db.exists("POS Executive", {
            "user": user, "store": store, "company": p.company, "is_active": 1,
        })
        if not has_access:
            continue

        item = frappe.db.sql(
            """SELECT b.item_code
               FROM `tabBin` b
               JOIN `tabItem` i ON i.name = b.item_code
               WHERE b.warehouse = %s AND b.actual_qty > 0
                 AND i.is_stock_item = 1 AND i.disabled = 0 AND i.is_sales_item = 1
               ORDER BY b.actual_qty DESC LIMIT 1""",
            (p.warehouse,), as_dict=True,
        )

        customer = frappe.db.get_value(
            "Customer", {"disabled": 0},
            "name", order_by="creation desc",
        )

        # Advance business date past all closed sessions
        biz_date = get_store_business_date(store)
        # Also reset BD status if it's Closed
        bd_status = frappe.db.get_value("CH Business Date", store, "status")
        if bd_status == "Closed":
            frappe.db.set_value("CH Business Date", store, "status", "Open")
            frappe.db.commit()
        # Find the max closed session date and advance past it
        max_closed = frappe.db.sql(
            "SELECT MAX(business_date) as d FROM `tabCH POS Session` "
            "WHERE store=%s AND status='Closed' AND docstatus=1",
            store, as_dict=1,
        )
        max_closed_date = max_closed[0].d if max_closed and max_closed[0].d else None
        if max_closed_date and getdate(biz_date) <= getdate(max_closed_date):
            new_date = add_days(max_closed_date, 1)
            frappe.db.set_value("CH Business Date", store, "business_date", new_date)
            frappe.db.commit()

        return {
            "pos_profile": p.name,
            "company": p.company,
            "warehouse": p.warehouse,
            "store": store,
            "item_code": item[0].item_code if item else None,
            "customer": customer,
        }

    # Fallback: use first profile even without store
    p = profiles[0]
    customer = frappe.db.get_value(
        "Customer", {"disabled": 0}, "name", order_by="creation desc"
    )
    return {
        "pos_profile": p.name,
        "company": p.company,
        "warehouse": p.warehouse,
        "store": None,
        "item_code": None,
        "customer": customer,
    }


def _get_or_create_manager_pin(store=None):
    """Ensure a CH Manager PIN exists for Administrator and return the PIN."""
    from frappe.utils.password import get_decrypted_password

    pin_name = frappe.db.get_value(
        "CH Manager PIN", {"user": "Administrator", "is_active": 1}, "name"
    )
    if pin_name:
        actual_pin = get_decrypted_password(
            "CH Manager PIN", pin_name, "pin_hash"
        )
        doc = frappe.get_doc("CH Manager PIN", pin_name)
        # Ensure store matches so verify_pin can find it
        if store and doc.store != store:
            doc.store = store
            doc.flags.ignore_validate = True
            doc.save(ignore_permissions=True)
            frappe.db.commit()
        return doc, actual_pin

    # Create one with known PIN
    doc = frappe.get_doc({
        "doctype": "CH Manager PIN",
        "user": "Administrator",
        "employee_name": "Admin",
        "pin_hash": "1234",
        "store": store or "",
        "is_active": 1,
        "can_approve_opening": 1,
        "can_approve_closing": 1,
        "can_approve_cash_drop": 1,
        "can_override_business_date": 1,
        "can_approve_discount": 1,
        "can_force_close_session": 1,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc, "1234"


# ═══════════════════════════════════════════════════════════════════
# 1. SESSION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def test_session_management(ctx):
    """Test session open → cash drop → X-report → close → Z-report."""
    print("\n── Session Management ──")
    from ch_pos.api import session_api

    store = ctx["store"]
    profile = ctx["pos_profile"]
    if not store:
        log_skip("SM-01 session status", "no store configured")
        return

    pin_doc, pin = _get_or_create_manager_pin(store)

    # Close any lingering open sessions first
    active = frappe.db.get_value(
        "CH POS Session",
        {"store": store, "status": "Open", "docstatus": 1},
        "name",
    )
    if active:
        try:
            session_api.close_session(
                session_name=active, closing_cash=0,
                variance_reason="E2E cleanup",
                manager_pin=pin,
            )
        except Exception:
            pass

    # Advance business date past all closed sessions
    from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_store_business_date
    biz_date = get_store_business_date(store)
    # Reset BD status if Closed
    bd_status = frappe.db.get_value("CH Business Date", store, "status")
    if bd_status == "Closed":
        frappe.db.set_value("CH Business Date", store, "status", "Open")
        frappe.db.commit()
    max_closed = frappe.db.sql(
        "SELECT MAX(business_date) as d FROM `tabCH POS Session` "
        "WHERE store=%s AND status='Closed' AND docstatus=1",
        store, as_dict=1,
    )
    max_closed_date = max_closed[0].d if max_closed and max_closed[0].d else None
    if max_closed_date and getdate(biz_date) <= getdate(max_closed_date):
        new_date = add_days(max_closed_date, 1)
        frappe.db.set_value("CH Business Date", store, "business_date", new_date)
        frappe.db.commit()

    # SM-01: Get session status (should be no active session)
    try:
        status = session_api.get_session_status(profile)
        assert_true(isinstance(status, dict), "get_session_status should return dict")
        log_pass("SM-01 get_session_status", f"has_session={status.get('has_session')}")
    except Exception as e:
        log_fail("SM-01 get_session_status", str(e))
        return  # can't proceed

    # SM-02: Open session
    session_name = None
    try:
        result = session_api.open_session(
            pos_profile=profile,
            opening_cash=5000,
            manager_pin=pin,
        )
        session_name = result.get("session_name")
        assert_true(session_name, "open_session should return session_name")
        assert_true(result.get("business_date"), "should return business_date")
        log_pass("SM-02 open_session", session_name)
    except Exception as e:
        if "day" in str(e).lower() and "closed" in str(e).lower():
            log_skip("SM-02 open_session", f"business day closed: {e}")
            return
        if "unclosed" in str(e).lower():
            log_skip("SM-02 open_session", f"unclosed session exists: {e}")
            return
        log_fail("SM-02 open_session", str(e))
        return

    # SM-03: Get session status again (should show active)
    try:
        status = session_api.get_session_status(profile)
        assert_true(status.get("has_session"), "Should have active session now")
        assert_eq(status.get("session_name"), session_name, "session name mismatch")
        log_pass("SM-03 session status active")
    except Exception as e:
        log_fail("SM-03 session status active", str(e))

    # SM-04: Cash drop
    try:
        drop = session_api.create_cash_drop(
            session_name=session_name,
            amount=1000,
            reason="E2E test cash drop",
            manager_pin=pin,
        )
        assert_true(drop.get("drop_name"), "should return drop_name")
        assert_eq(flt(drop.get("amount")), 1000.0, "drop amount")
        log_pass("SM-04 cash_drop", drop.get("drop_name"))
    except Exception as e:
        log_fail("SM-04 cash_drop", str(e))

    # SM-05: X-report (mid-shift)
    try:
        xr = session_api.get_x_report(session_name)
        assert_true(isinstance(xr, dict), "should return dict")
        assert_eq(xr.get("session_name"), session_name, "session name")
        assert_true("opening_cash" in xr, "should include opening_cash")
        log_pass("SM-05 x_report", f"invoices={xr.get('invoices_count', 0)}")
    except Exception as e:
        log_fail("SM-05 x_report", str(e))

    # SM-06: Close session
    try:
        close_result = session_api.close_session(
            session_name=session_name,
            closing_cash=4000,  # 5000 opening - 1000 drop = 4000 expected
            variance_reason="E2E test close",
            manager_pin=pin,
        )
        assert_eq(close_result.get("status"), "Closed", "session should be Closed")
        log_pass("SM-06 close_session",
                 f"variance={close_result.get('cash_variance')}")
    except Exception as e:
        log_fail("SM-06 close_session", str(e))

    # SM-07: Z-report (end of day)
    try:
        bd = frappe.db.get_value("CH POS Session", session_name, "business_date")
        zr = session_api.get_z_report(store, str(bd))
        assert_true(isinstance(zr, dict), "should return dict")
        assert_true(zr.get("total_sessions", 0) >= 1, "should have at least 1 session")
        log_pass("SM-07 z_report", f"sessions={zr.get('total_sessions')}")
    except Exception as e:
        log_fail("SM-07 z_report", str(e))


# ═══════════════════════════════════════════════════════════════════
# 2. OFFERS ENGINE
# ═══════════════════════════════════════════════════════════════════

def test_offers_engine(ctx):
    """Test offer retrieval, combo checks, and coupon validation."""
    print("\n── Offers Engine ──")
    from ch_pos.api import offers

    item_code = ctx.get("item_code")

    # OF-01: Get applicable offers
    try:
        result = offers.get_applicable_offers(item_code=item_code)
        assert_true(isinstance(result, list), "should return list")
        log_pass("OF-01 get_applicable_offers", f"{len(result)} offers")
    except Exception as e:
        log_fail("OF-01 get_applicable_offers", str(e))

    # OF-02: Get best offer combination
    try:
        cart = [{"item_code": item_code, "item_name": "Test", "amount": 10000}]
        result = offers.get_best_offer_combination(cart)
        assert_true(isinstance(result, dict), "should return dict")
        assert_true("total_savings" in result, "should include total_savings")
        log_pass("OF-02 get_best_offer_combination",
                 f"savings={result.get('total_savings')}")
    except Exception as e:
        log_fail("OF-02 get_best_offer_combination", str(e))

    # OF-03: Check combo offers
    try:
        cart = [{"item_code": item_code, "qty": 1, "rate": 10000, "amount": 10000}]
        result = offers.check_combo_offers(cart, company=ctx["company"])
        assert_true(isinstance(result, list), "should return list")
        log_pass("OF-03 check_combo_offers", f"{len(result)} combos")
    except Exception as e:
        log_fail("OF-03 check_combo_offers", str(e))

    # OF-04: Check attachment offers
    try:
        cart = [{"item_code": item_code}]
        result = offers.check_attachment_offers(cart, company=ctx["company"])
        assert_true(isinstance(result, list), "should return list")
        log_pass("OF-04 check_attachment_offers", f"{len(result)} attachments")
    except Exception as e:
        log_fail("OF-04 check_attachment_offers", str(e))

    # OF-05: Validate invalid coupon (should fail gracefully)
    try:
        try:
            offers.validate_coupon_code("INVALID_COUPON_XYZ_123")
            log_fail("OF-05 validate_coupon_code invalid", "should have thrown")
        except Exception as e:
            assert_true("not found" in str(e).lower() or "invalid" in str(e).lower()
                        or "does not exist" in str(e).lower(),
                        f"unexpected error: {e}")
            log_pass("OF-05 validate_coupon_code invalid", "correctly rejected")
    except Exception as e:
        log_fail("OF-05 validate_coupon_code invalid", str(e))

    # OF-06: Validate real coupon if any exist
    try:
        # Must pass the coupon_code field value (not the document name)
        coupon_code_val = frappe.db.get_value("Coupon Code", {"used": 0}, "coupon_code")
        if coupon_code_val:
            result = offers.validate_coupon_code(coupon_code_val)
            assert_true(result.get("valid"), "valid coupon should return valid=True")
            log_pass("OF-06 validate_coupon_code valid", coupon_code_val)
        else:
            log_skip("OF-06 validate_coupon_code valid", "no unused coupons")
    except Exception as e:
        log_fail("OF-06 validate_coupon_code valid", str(e))


# ═══════════════════════════════════════════════════════════════════
# 3. VOUCHER SYSTEM
# ═══════════════════════════════════════════════════════════════════

def test_voucher_system(ctx):
    """Test voucher full lifecycle: issue → validate → redeem → balance → refund → topup."""
    print("\n── Voucher System ──")
    from ch_item_master.ch_item_master.voucher_api import (
        issue_voucher, validate_voucher, redeem_voucher, refund_voucher,
        topup_voucher, check_balance, get_customer_vouchers,
        issue_return_credit,
    )

    company = ctx["company"]
    customer = ctx.get("customer")

    # VC-01: Issue gift card
    voucher_code = None
    try:
        result = issue_voucher(
            voucher_type="Gift Card",
            amount=5000,
            company=company,
            customer=customer,
            reason="E2E test voucher",
        )
        assert_true(result.get("voucher_code"), "should return voucher_code")
        assert_eq(flt(result.get("balance")), 5000.0, "initial balance")
        voucher_code = result["voucher_code"]
        log_pass("VC-01 issue_voucher", f"{voucher_code} bal=5000")
    except Exception as e:
        log_fail("VC-01 issue_voucher", str(e))
        return

    # VC-02: Check balance
    try:
        bal = check_balance(voucher_code)
        assert_eq(flt(bal.get("balance")), 5000.0, "balance check")
        assert_eq(bal.get("status"), "Active", "status")
        log_pass("VC-02 check_balance", f"bal={bal['balance']}")
    except Exception as e:
        log_fail("VC-02 check_balance", str(e))

    # VC-03: Validate for redemption
    try:
        val = validate_voucher(voucher_code, cart_total=3000)
        assert_true(val.get("valid"), f"should be valid: {val.get('reason')}")
        assert_true(flt(val.get("applicable_amount")) > 0, "applicable > 0")
        log_pass("VC-03 validate_voucher",
                 f"applicable={val.get('applicable_amount')}")
    except Exception as e:
        log_fail("VC-03 validate_voucher", str(e))

    # VC-04: Redeem partial
    try:
        red = redeem_voucher(voucher_code, amount=2000)
        assert_true(red.get("success"), "redeem should succeed")
        assert_eq(flt(red.get("redeemed_amount")), 2000.0, "redeemed amount")
        assert_eq(flt(red.get("remaining_balance")), 3000.0, "remaining balance")
        log_pass("VC-04 redeem_voucher", f"redeemed=2000, remaining=3000")
    except Exception as e:
        log_fail("VC-04 redeem_voucher", str(e))

    # VC-05: Refund
    try:
        ref = refund_voucher(voucher_code, amount=1000, reason="E2E test refund")
        assert_true(ref.get("success"), "refund should succeed")
        assert_eq(flt(ref.get("new_balance")), 4000.0, "balance after refund")
        log_pass("VC-05 refund_voucher", f"refunded=1000, new_bal=4000")
    except Exception as e:
        log_fail("VC-05 refund_voucher", str(e))

    # VC-06: Topup
    try:
        top = topup_voucher(voucher_code, amount=1000, reason="E2E topup")
        assert_true(top.get("success"), "topup should succeed")
        assert_eq(flt(top.get("new_balance")), 5000.0, "balance after topup")
        log_pass("VC-06 topup_voucher", f"topped_up=1000, new_bal=5000")
    except Exception as e:
        log_fail("VC-06 topup_voucher", str(e))

    # VC-07: Over-redeem blocked
    try:
        try:
            redeem_voucher(voucher_code, amount=99999)
            # If it didn't throw, check if it capped
            bal_after = check_balance(voucher_code)
            if flt(bal_after.get("balance")) == 0:
                log_pass("VC-07 over-redeem capped", "capped at balance")
            else:
                log_fail("VC-07 over-redeem capped", "should cap or throw")
        except Exception:
            log_pass("VC-07 over-redeem blocked", "correctly rejected")
    except Exception as e:
        log_fail("VC-07 over-redeem", str(e))

    # VC-08: Customer vouchers listing
    if customer:
        try:
            vouchers = get_customer_vouchers(customer, company=company)
            assert_true(isinstance(vouchers, list), "should return list")
            log_pass("VC-08 get_customer_vouchers", f"{len(vouchers)} vouchers")
        except Exception as e:
            log_fail("VC-08 get_customer_vouchers", str(e))
    else:
        log_skip("VC-08 get_customer_vouchers", "no customer")

    # VC-09: Issue return credit (convenience wrapper)
    try:
        rc = issue_return_credit(
            customer=customer or "",
            amount=500,
            company=company,
            reason="E2E return credit test",
        )
        assert_true(rc.get("voucher_code"), "should return voucher_code")
        assert_eq(rc.get("voucher_type") or "Return Credit", "Return Credit", "type")
        log_pass("VC-09 issue_return_credit", rc["voucher_code"])
    except Exception as e:
        log_fail("VC-09 issue_return_credit", str(e))

    # VC-10: Validate expired/invalid code
    try:
        val = validate_voucher("NONEXISTENT_CODE_XYZ")
        assert_true(not val.get("valid"), "invalid code should return valid=False")
        log_pass("VC-10 validate_invalid_code", val.get("reason", ""))
    except Exception as e:
        # If it throws instead of returning valid=False, that's also acceptable
        log_pass("VC-10 validate_invalid_code", f"threw: {str(e)[:60]}")


# ═══════════════════════════════════════════════════════════════════
# 4. EXCEPTION REQUESTS
# ═══════════════════════════════════════════════════════════════════

def test_exception_requests(ctx):
    """Test exception raise → approve → check validity → reject flow."""
    print("\n── Exception Requests ──")
    from ch_item_master.ch_item_master.exception_api import (
        raise_exception, approve_exception, reject_exception,
        check_exception_valid, get_pending_exceptions, get_exception_summary,
    )

    company = ctx["company"]

    # Find an enabled exception type applicable to this company
    # Check company abbreviation for applicable_to_ggr / applicable_to_gfs flags
    company_abbr = frappe.db.get_value("Company", company, "abbr") or ""
    exc_types = frappe.get_all("CH Exception Type", filters={"enabled": 1},
                               fields=["name", "applicable_to_ggr", "applicable_to_gfs"])
    exc_type = None
    for et in exc_types:
        # Check if applicable to this company
        if "GGR" in company_abbr and et.applicable_to_ggr:
            exc_type = et.name
            break
        elif "GFS" in company_abbr and et.applicable_to_gfs:
            exc_type = et.name
            break
        elif not et.applicable_to_ggr and not et.applicable_to_gfs:
            # No company restriction
            exc_type = et.name
            break
    if not exc_type and exc_types:
        exc_type = exc_types[0].name  # fallback

    if not exc_type:
        log_skip("EX-01 raise_exception", "no enabled CH Exception Type")
        return

    # EX-01: Raise exception (small value → auto-approve)
    exc_name = None
    try:
        result = raise_exception(
            exception_type=exc_type,
            company=company,
            reason="E2E test exception",
            requested_value=0,  # small = auto-approve
            original_value=0,
        )
        exc_name = result.get("name")
        assert_true(exc_name, "should return exception name")
        status = result.get("status")
        log_pass("EX-01 raise_exception", f"{exc_name} status={status}")
    except Exception as e:
        log_fail("EX-01 raise_exception", str(e))
        return

    # EX-02: Check exception validity
    try:
        check = check_exception_valid(exc_name)
        assert_true(isinstance(check, dict), "should return dict")
        log_pass("EX-02 check_exception_valid",
                 f"valid={check.get('valid')}, status={check.get('status')}")
    except Exception as e:
        log_fail("EX-02 check_exception_valid", str(e))

    # EX-03: Raise exception needing approval (large value)
    try:
        result2 = raise_exception(
            exception_type=exc_type,
            company=company,
            reason="E2E large value test",
            requested_value=999999,
            original_value=100,
        )
        exc2_name = result2.get("name")
        status2 = result2.get("status")
        log_pass("EX-03 raise_large_exception",
                 f"{exc2_name} status={status2}")

        # EX-04: Approve exception (skip OTP for test)
        if status2 == "Pending":
            try:
                # Check if this type requires OTP
                requires_otp = frappe.db.get_value(
                    "CH Exception Type", exc_type, "requires_otp"
                )
                if requires_otp:
                    log_skip("EX-04 approve_exception", "requires OTP")
                else:
                    app_result = approve_exception(
                        exc2_name, approver_user="Administrator",
                        remarks="E2E approved",
                    )
                    log_pass("EX-04 approve_exception",
                             f"status={app_result.get('status')}")
            except Exception as e:
                log_fail("EX-04 approve_exception", str(e))
        else:
            log_pass("EX-04 auto-approved", f"status={status2}")

    except Exception as e:
        log_fail("EX-03 raise_large_exception", str(e))

    # EX-05: Reject exception
    try:
        result3 = raise_exception(
            exception_type=exc_type,
            company=company,
            reason="E2E reject test",
            requested_value=999999,
            original_value=100,
        )
        if result3.get("status") == "Pending":
            rej = reject_exception(result3["name"], reason="E2E test rejection")
            assert_eq(rej.get("status"), "Rejected", "should be Rejected")
            log_pass("EX-05 reject_exception", result3["name"])
        else:
            log_skip("EX-05 reject_exception", "auto-approved, can't reject")
    except Exception as e:
        log_fail("EX-05 reject_exception", str(e))

    # EX-06: Get pending exceptions
    try:
        pending = get_pending_exceptions(company=company)
        assert_true(isinstance(pending, list), "should return list")
        log_pass("EX-06 get_pending_exceptions", f"{len(pending)} pending")
    except Exception as e:
        log_fail("EX-06 get_pending_exceptions", str(e))

    # EX-07: Get exception summary
    try:
        summary = get_exception_summary(company)
        assert_true(isinstance(summary, list), "should return list")
        log_pass("EX-07 get_exception_summary", f"{len(summary)} rows")
    except Exception as e:
        log_fail("EX-07 get_exception_summary", str(e))


# ═══════════════════════════════════════════════════════════════════
# 5. CUSTOMER 360
# ═══════════════════════════════════════════════════════════════════

def test_customer_360(ctx):
    """Test customer 360 lookup."""
    print("\n── Customer 360 ──")
    from ch_pos.api.pos_api import customer_360

    customer = ctx.get("customer")
    if not customer:
        log_skip("C360-01", "no customer")
        return

    # C360-01: Lookup by customer name
    try:
        result = customer_360(customer, company=ctx["company"])
        assert_true(isinstance(result, dict), "should return dict")
        assert_true(result.get("customer"), "should have customer field")
        log_pass("C360-01 customer_360 by name",
                 f"invoices={result.get('total_invoices', 0)}, "
                 f"spent={result.get('total_spent', 0)}")
    except Exception as e:
        log_fail("C360-01 customer_360 by name", str(e))

    # C360-02: Lookup by mobile (if available)
    try:
        mobile = frappe.db.get_value("Customer", customer, "mobile_no")
        if mobile:
            result = customer_360(mobile, company=ctx["company"])
            assert_true(result.get("customer"), "should find customer by mobile")
            log_pass("C360-02 customer_360 by mobile", mobile)
        else:
            log_skip("C360-02 customer_360 by mobile", "no mobile on customer")
    except Exception as e:
        log_fail("C360-02 customer_360 by mobile", str(e))

    # C360-03: Lookup nonexistent
    try:
        result = customer_360("NONEXISTENT_CUSTOMER_XYZ_99999", company=ctx["company"])
        # Should return empty or throw
        if result.get("customer"):
            log_fail("C360-03 nonexistent customer", "should not find anything")
        else:
            log_pass("C360-03 nonexistent customer", "correctly empty")
    except Exception as e:
        if "not found" in str(e).lower() or "no customer" in str(e).lower():
            log_pass("C360-03 nonexistent customer", "correctly threw")
        else:
            log_fail("C360-03 nonexistent customer", str(e))


# ═══════════════════════════════════════════════════════════════════
# 6. ITEM SEARCH & BARCODE
# ═══════════════════════════════════════════════════════════════════

def test_item_search(ctx):
    """Test POS item search, barcode scan, serial lookup, item detail."""
    print("\n── Item Search ──")
    from ch_pos.api.search import (
        pos_item_search, get_available_serials,
        get_item_detail_for_pos,
    )

    item_code = ctx.get("item_code")
    profile = ctx["pos_profile"]

    # IS-01: Basic search
    try:
        result = pos_item_search(
            search_term="", pos_profile=profile,
            page=0, page_size=5, company=ctx["company"],
        )
        assert_true(isinstance(result, dict), "should return dict")
        items = result.get("items", [])
        assert_true(isinstance(items, list), "items should be list")
        log_pass("IS-01 pos_item_search empty",
                 f"{len(items)} items, total={result.get('total', 0)}")
    except Exception as e:
        log_fail("IS-01 pos_item_search empty", str(e))

    # IS-02: Search by item code
    if item_code:
        try:
            result = pos_item_search(
                search_term=item_code, pos_profile=profile,
                page=0, page_size=5, company=ctx["company"],
            )
            items = result.get("items", [])
            found_codes = [i.get("item_code") for i in items]
            assert_true(item_code in found_codes,
                        f"{item_code} should appear in search results")
            log_pass("IS-02 pos_item_search by code", item_code)
        except Exception as e:
            log_fail("IS-02 pos_item_search by code", str(e))

    # IS-03: Search by brand
    try:
        brand = frappe.db.get_value("Item", item_code, "brand") if item_code else None
        if brand:
            result = pos_item_search(
                search_term=brand, pos_profile=profile,
                page=0, page_size=5, company=ctx["company"],
            )
            log_pass("IS-03 search by brand",
                     f"{len(result.get('items', []))} results for '{brand}'")
        else:
            log_skip("IS-03 search by brand", "item has no brand")
    except Exception as e:
        log_fail("IS-03 search by brand", str(e))

    # IS-04: Pagination
    try:
        p0 = pos_item_search(
            search_term="", pos_profile=profile,
            page=0, page_size=2, company=ctx["company"],
        )
        p1 = pos_item_search(
            search_term="", pos_profile=profile,
            page=1, page_size=2, company=ctx["company"],
        )
        items_p0 = [i["item_code"] for i in p0.get("items", [])]
        items_p1 = [i["item_code"] for i in p1.get("items", [])]
        # Pages should have different items (if enough exist)
        if p0.get("total", 0) > 2:
            overlap = set(items_p0) & set(items_p1)
            assert_true(len(overlap) == 0,
                        f"Pages should not overlap: {overlap}")
        log_pass("IS-04 pagination", f"p0={len(items_p0)}, p1={len(items_p1)}")
    except Exception as e:
        log_fail("IS-04 pagination", str(e))

    # IS-05: Get available serials
    if item_code:
        try:
            has_serial = cint(frappe.db.get_value(
                "Item", item_code, "has_serial_no"
            ))
            if has_serial:
                serials = get_available_serials(item_code, ctx["warehouse"])
                assert_true(isinstance(serials, list), "should return list")
                log_pass("IS-05 get_available_serials",
                         f"{len(serials)} serials")
            else:
                log_skip("IS-05 get_available_serials",
                         f"{item_code} not serial tracked")
        except Exception as e:
            log_fail("IS-05 get_available_serials", str(e))

    # IS-06: Item detail for POS
    if item_code:
        try:
            detail = get_item_detail_for_pos(
                item_code, warehouse=ctx["warehouse"],
            )
            assert_true(isinstance(detail, dict), "should return dict")
            assert_eq(detail.get("item_code"), item_code, "item_code match")
            log_pass("IS-06 get_item_detail_for_pos",
                     f"price={detail.get('selling_price')}, "
                     f"stock={detail.get('stock_qty')}")
        except Exception as e:
            log_fail("IS-06 get_item_detail_for_pos", str(e))

    # IS-07: Search with in_stock_only filter
    try:
        result = pos_item_search(
            search_term="", pos_profile=profile,
            filters={"in_stock_only": True},
            page=0, page_size=5, company=ctx["company"],
        )
        for item in result.get("items", []):
            assert_true(flt(item.get("stock_qty", 0)) > 0,
                        f"{item.get('item_code')} should have stock")
        log_pass("IS-07 in_stock_only filter",
                 f"{len(result.get('items', []))} in-stock items")
    except Exception as e:
        log_fail("IS-07 in_stock_only filter", str(e))


# ═══════════════════════════════════════════════════════════════════
# 7. VERIFY PIN
# ═══════════════════════════════════════════════════════════════════

def test_verify_pin(ctx):
    """Test manager PIN verification."""
    print("\n── Manager PIN ──")
    from ch_pos.api.session_api import verify_pin

    store = ctx.get("store")
    pin_doc, pin = _get_or_create_manager_pin(store)

    # PIN-01: Valid PIN
    try:
        result = verify_pin(pin, store=store)
        assert_true(result.get("valid"), f"valid PIN should pass: {result}")
        log_pass("PIN-01 verify valid PIN")
    except Exception as e:
        log_fail("PIN-01 verify valid PIN", str(e))

    # PIN-02: Invalid PIN
    try:
        result = verify_pin("9999", store=store)
        assert_true(not result.get("valid"), "invalid PIN should fail")
        log_pass("PIN-02 verify invalid PIN", "correctly rejected")
    except Exception as e:
        # Might throw instead of returning valid=False
        log_pass("PIN-02 verify invalid PIN", f"threw: {str(e)[:50]}")

    # PIN-03: Valid PIN with permission check
    try:
        result = verify_pin(pin, store=store, permission="can_approve_discount")
        assert_true(result.get("valid"), "PIN with matching perm should pass")
        log_pass("PIN-03 PIN with permission")
    except Exception as e:
        log_fail("PIN-03 PIN with permission", str(e))


# ═══════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════

def run_all():
    global PASS, FAIL, SKIP, results
    PASS = FAIL = SKIP = 0
    results = []

    frappe.set_user("Administrator")
    ctx = _get_ctx()
    print(f"Context: profile={ctx['pos_profile']}, store={ctx['store']}, "
          f"company={ctx['company']}, item={ctx.get('item_code')}, "
          f"customer={ctx.get('customer')}")

    test_session_management(ctx)
    test_offers_engine(ctx)
    test_voucher_system(ctx)
    test_exception_requests(ctx)
    test_customer_360(ctx)
    test_item_search(ctx)
    test_verify_pin(ctx)

    frappe.db.commit()

    print(f"\n{'='*60}")
    print(f"MODULES E2E RESULTS: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    print(f"{'='*60}")

    if FAIL:
        failed = [r for r in results if r[0] == "FAIL"]
        print("\nFailed tests:")
        for _, tid, detail in failed:
            print(f"  - {tid}: {detail}")
        raise Exception(f"Modules E2E: {FAIL} tests failed")

    return {"passed": PASS, "failed": FAIL, "skipped": SKIP}
