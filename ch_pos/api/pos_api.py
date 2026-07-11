import datetime

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import flt, cint, nowdate, add_months, now_datetime, fmt_money, getdate, get_last_day, get_datetime, validate_email_address
try:
    from buyback.utils import validate_indian_phone
except ImportError:
    def validate_indian_phone(phone):
        """Fallback: accept any phone if buyback app is not installed."""
        return phone
from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_active_session


def _normalize_customer_approval_method(method: str | None) -> str:
    """Normalize POS method values to Buyback's canonical approval methods."""
    method = (method or "In-Store Signature").strip()
    aliases = {
        "OTP": "App Confirmation",
        "OTP Verification": "App Confirmation",
        "App OTP": "App Confirmation",
        "Approval Link": "SMS Link",
        "Token Link": "SMS Link",
    }
    method = aliases.get(method, method)
    allowed = {"In-Store Signature", "SMS Link", "App Confirmation"}
    if method not in allowed:
        frappe.throw(
            _("Invalid customer approval method: {0}").format(method),
            title=_("Buyback Order Error"),
        )
    return method

# ── Buyback Exchange Credit helpers ─────────────────────────────────────────

_EXCHANGE_MOP = "Buyback Exchange Credit"


def _get_buyback_liability_account(company):
    """Return the Buyback Liability GL account for this company.

    Falls back gracefully: Buyback Settings liability account → standard
    Device Buyback Liability account → legacy MOP account → legacy expense
    setting. The expense fallback keeps old installs working, but production
    should configure a liability account so exchange credit clears the payable.
    """
    account = None
    if frappe.get_meta("Buyback Settings").has_field("buyback_liability_account"):
        account = frappe.db.get_single_value("Buyback Settings", "buyback_liability_account")
    if not account and company:
        account = frappe.db.get_value(
            "Account",
            {"company": company, "account_name": "Device Buyback Liability", "is_group": 0},
            "name",
        )
    if account:
        return account
    # Fallback: MOP account table
    row = frappe.db.get_value(
        "Mode of Payment Account",
        {"parent": _EXCHANGE_MOP, "company": company},
        "default_account",
    )
    if row:
        return row
    return frappe.db.get_single_value("Buyback Settings", "buyback_expense_account")


def _ensure_buyback_exchange_mop(account, company):
    """Create 'Buyback Exchange Credit' Mode of Payment if it doesn't exist.

    Idempotent — safe to call on every exchange invoice. Uses a cached
    flag so repeated calls in the same request skip the DB lookup.
    """
    _cache_key = f"buyback_exchange_mop_ok_{company}"
    if frappe.cache().get_value(_cache_key):
        return _EXCHANGE_MOP

    if not frappe.db.exists("Mode of Payment", _EXCHANGE_MOP):
        mop = frappe.new_doc("Mode of Payment")
        mop.mode_of_payment = _EXCHANGE_MOP
        mop.type = "General"
        if account and company:
            mop.append("accounts", {"company": company, "default_account": account})
        mop.flags.ignore_permissions = True
        mop.insert()
        frappe.db.commit()
    elif account and company:
        # Ensure this company's account is configured
        exists = frappe.db.exists(
            "Mode of Payment Account",
            {"parent": _EXCHANGE_MOP, "company": company},
        )
        if not exists:
            mop = frappe.get_doc("Mode of Payment", _EXCHANGE_MOP)
            mop.append("accounts", {"company": company, "default_account": account})
            mop.flags.ignore_permissions = True
            mop.save()
            frappe.db.commit()

    frappe.cache().set_value(_cache_key, True, expires_in_sec=3600)
    return _EXCHANGE_MOP


def _allocate_customer_advance(inv, requested):
    """Allocate up to ``requested`` from the customer's unallocated Payment Entries
    onto ``inv.advances``.

    This is the ERPNext-idiomatic way to apply an advance to a Sales Invoice:
    the Payment Entry's unallocated balance is consumed and the SI's outstanding
    is reduced via the ``advances`` child table — GST is computed on the full
    grand_total (NOT on a fictitious "discount") and the customer ledger stays
    correct.

    Returns the total amount actually allocated. Throws if the front-end
    requested more advance than the customer actually has unallocated.

    The previous implementation added ``advance_amount`` to ``inv.discount_amount``
    which (a) corrupted the GST base because GST is computed on net of discount,
    and (b) never reduced the customer's unallocated PE balance — so the same
    advance could be "spent" multiple times.
    """
    requested = flt(requested)
    if requested <= 0 or not inv.customer or inv.customer == "Walk-in Customer":
        return 0.0

    rows = frappe.db.sql(
        """
        SELECT pe.name, pe.unallocated_amount
          FROM `tabPayment Entry` pe
         WHERE pe.docstatus = 1
           AND pe.party_type = 'Customer'
           AND pe.party = %(party)s
           AND pe.company = %(company)s
           AND pe.unallocated_amount > 0.005
         ORDER BY pe.posting_date, pe.creation
        """,
        {"party": inv.customer, "company": inv.company},
        as_dict=True,
    )

    remaining = requested
    allocated_total = 0.0
    for r in rows:
        if remaining <= 0.005:
            break
        alloc = min(flt(r.unallocated_amount), remaining)
        if alloc <= 0:
            continue
        inv.append("advances", {
            "reference_type": "Payment Entry",
            "reference_name": r.name,
            "advance_amount": flt(r.unallocated_amount),
            "allocated_amount": alloc,
            "remarks": "POS advance allocation",
        })
        allocated_total += alloc
        remaining -= alloc

    if remaining > 0.5:
        frappe.throw(
            frappe._(
                "Cannot apply advance of ₹{0}. Only ₹{1} of unallocated Payment Entries "
                "is available for {2}. Refresh the cart or have the customer pay the difference."
            ).format(requested, allocated_total, inv.customer),
            title=frappe._("Advance Mismatch"),
        )

    return allocated_total


def _allocate_sales_order_advance(inv, sales_order, cap=None):
    """Pull advance allocations sitting on a Sales Order onto ``inv.advances``.

    When a pre-booking is billed, ERPNext's ``make_sales_invoice`` mapper copies
    the SO's ``advances`` child rows onto the Sales Invoice. Our POS path
    builds the SI from scratch (so the cashier can edit / append items at
    pickup time) so we replicate that copy explicitly: every Payment Entry
    that referenced the SO is added as an advance allocation, capped at the
    invoice's grand_total so we never over-apply.

    Returns the total amount allocated.
    """
    if not sales_order:
        return 0.0

    so_advances = frappe.db.sql(
        """
        SELECT  per.reference_name AS pe_name,
                pe.unallocated_amount,
                per.allocated_amount
          FROM `tabPayment Entry Reference` per
          JOIN `tabPayment Entry` pe ON pe.name = per.parent
         WHERE per.reference_doctype = 'Sales Order'
           AND per.reference_name   = %(so)s
           AND pe.docstatus = 1
           AND pe.party_type = 'Customer'
           AND pe.party = %(party)s
           AND pe.company = %(company)s
           AND per.allocated_amount > 0.005
         ORDER BY pe.posting_date, pe.creation
        """,
        {"so": sales_order, "party": inv.customer, "company": inv.company},
        as_dict=True,
    )
    if not so_advances:
        return 0.0

    # Cap at invoice grand total so we never over-allocate.
    try:
        inv.run_method("calculate_taxes_and_totals")
    except Exception:
        pass
    target = flt(cap if cap is not None else (inv.rounded_total or inv.grand_total))
    if target <= 0:
        return 0.0

    # De-duplicate PEs across multiple SO references and start fresh.
    seen = set()
    total = 0.0
    for adv in so_advances:
        if adv.pe_name in seen:
            continue
        seen.add(adv.pe_name)
        remaining = target - total
        if remaining <= 0.005:
            break
        alloc = min(flt(adv.allocated_amount), remaining)
        if alloc <= 0:
            continue
        inv.append("advances", {
            "reference_type": "Payment Entry",
            "reference_name": adv.pe_name,
            "advance_amount": flt(adv.allocated_amount),
            "allocated_amount": alloc,
            "remarks": f"POS pickup — advance from Sales Order {sales_order}",
        })
        total += alloc

    return total


def _enforce_token_linkage(pos_profile, kiosk_token):
    """Block billing without a linked kiosk token when enforcement is enabled."""
    if kiosk_token:
        return  # token already linked — nothing to check

    # Check global override first
    global_enforce = cint(frappe.db.get_single_value(
        "CH POS Control Settings", "enforce_token_linkage_globally"))
    if global_enforce:
        frappe.throw(
            frappe._("Walk-in token must be linked before billing. "
                     "Use Quick Walk-in or select from queue."),
            title=frappe._("Token Required"))

    # Check store-level setting
    require = frappe.db.get_value(
        "POS Profile Extension", {"pos_profile": pos_profile},
        "require_token_linkage")
    if cint(require):
        frappe.throw(
            frappe._("Walk-in token must be linked before billing. "
                     "Use Quick Walk-in or select from queue."),
            title=frappe._("Token Required"))

@frappe.whitelist()
def get_pos_profile_data(pos_profile) -> dict:
    """Return POS profile configuration needed by the CH POS frontend."""
    profile = frappe.get_cached_doc("POS Profile", pos_profile)

    payment_modes = []
    for p in profile.payments or []:
        payment_modes.append({
            "mode_of_payment": p.mode_of_payment,
            "default": cint(p.default),
        })

    # Store capabilities from POS Profile Extension (custom doctype, optional)
    store_caps = {}
    pos_ext = {}
    if frappe.db.exists("POS Profile Extension", {"pos_profile": pos_profile}):
        ext = frappe.get_cached_doc("POS Profile Extension", {"pos_profile": pos_profile})
        pos_ext = ext.as_dict()
        store_caps = {
            "buyback_enabled": cint(ext.get("buyback_enabled", 1)),
            "repair_enabled": cint(ext.get("repair_enabled", 1)),
            "vas_enabled": cint(ext.get("vas_enabled", 1)),
            "exchange_enabled": cint(ext.get("exchange_enabled", 1)),
        }

    # Executive access: determine which companies / roles the logged-in user has
    try:
        executive_access = _get_executive_access(frappe.session.user, profile.warehouse)
    except Exception:
        frappe.log_error("POS Executive access check failed")
        executive_access = {
            "companies": [],
            "is_manager": False,
            "store_executives": {},
            "own_executive": None,
            "stores": [],
        }

    return {        
        "warehouse": profile.warehouse,
        "price_list": profile.selling_price_list,
        "company": profile.company,
        "currency": profile.currency or frappe.get_cached_value(
            "Company", profile.company, "default_currency"
        ),
        "default_customer": profile.customer or None,
        "payment_modes": payment_modes,
        "store_caps": store_caps,
        "pos_ext": pos_ext,
        "executive_access": executive_access,
    }


@frappe.whitelist()
def get_sale_types(company=None) -> list:
    """Return enabled sale types with their sub-types, optionally filtered by company."""
    filters = {"enabled": 1}
    types = frappe.get_all(
        "CH Sale Type",
        filters=filters,
        fields=["name as sale_type_name", "code", "is_default", "requires_customer",
                "requires_payment", "default_payment_mode", "description"],
        order_by="is_default desc, sale_type_name asc",
        ignore_permissions=True,
    )

    for st in types:
        # Fetch sub-types
        st["sub_types"] = frappe.get_all(
            "CH Sale Sub Type",
            filters={"parent": st["sale_type_name"], "parenttype": "CH Sale Type"},
            fields=["sale_sub_type", "description", "requires_reference", "reference_doctype"],
            order_by="idx",
            ignore_permissions=True,
        )

        # Filter by allowed companies if specified
        if company:
            try:
                allowed = frappe.get_all(
                    "POS Allowed Company",
                    filters={"parent": st["sale_type_name"], "parenttype": "CH Sale Type"},
                    pluck="company",
                    ignore_permissions=True,
                )
            except Exception:
                allowed = []
            if allowed and company not in allowed:
                st["_skip"] = True

    return [st for st in types if not st.get("_skip")]


@frappe.whitelist()
def get_discount_reasons(company=None) -> list:
    """Return enabled discount reasons, optionally filtered by company."""
    filters = {"enabled": 1}
    if company:
        filters["company"] = ("in", [company, "", None])

    return frappe.get_all(
        "CH Discount Reason",
        filters=filters,
        fields=["name", "reason_name", "discount_type", "discount_value",
                "allow_manual_entry", "max_manual_percent"],
        order_by="allow_manual_entry asc, reason_name asc",
        ignore_permissions=True,
    )


@frappe.whitelist()
def get_finance_partners() -> dict:
    """Return enabled finance partners with their tenure options for POS dropdown."""
    partners = frappe.get_all(
        "CH Finance Partner",
        filters={"enabled": 1},
        fields=["name", "partner_name", "short_code", "tenure_options"],
        order_by="partner_name asc",
        ignore_permissions=True,
    )
    for p in partners:
        # Parse comma-separated tenure options into list of integers
        raw = (p.get("tenure_options") or "").strip()
        p["tenures"] = sorted([cint(t.strip()) for t in raw.split(",") if t.strip()]) if raw else []
    return partners


def _ensure_pre_booking_sale_type():
    """Best-effort bootstrap of the Pre Booking sale type master."""
    if not frappe.db.exists("DocType", "CH Sale Type"):
        return None

    existing = frappe.db.get_value("CH Sale Type", {"sale_type_name": "Pre Booking"}, "name")
    if existing:
        return existing

    try:
        doc = frappe.get_doc({
            "doctype": "CH Sale Type",
            "sale_type_name": "Pre Booking",
            "code": "PB",
            "enabled": 1,
            "requires_customer": 1,
            "requires_payment": 0,
            "description": "Advance reservation order created before final billing.",
        })
        doc.insert(ignore_permissions=True)
        return doc.name
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Failed to create Pre Booking sale type")
        return None


@frappe.whitelist()
def create_pre_booking(pos_profile, customer, items, advance_amount=0, notes=None,
                       delivery_date=None, sales_executive=None, sale_reference=None,
                       reserve_stock=1, client_request_id=None,
                       mode_of_payment=None, payment_reference_no=None,
                       payments=None) -> dict:
    """Create a reservation-style Sales Order for pre-booking / advance orders.

    When ``advance_amount`` > 0 and ``mode_of_payment`` is provided, a Draft
    Payment Entry is also created referencing the new Sales Order
    (``allocated_amount = advance_amount``). The Payment Entry inherits the
    standard maker-checker workflow and, once approved/submitted, drives
    ``Sales Order.advance_paid`` via the framework — no manual stamping.

    For backward compatibility, if ``mode_of_payment`` is omitted we fall
    back to stamping ``advance_paid`` directly so legacy callers keep
    working. New callers (POS UI) MUST pass ``mode_of_payment``.
    """
    frappe.has_permission("Sales Order", "create", throw=True)

    if isinstance(items, str):
        items = frappe.parse_json(items)
    if not items:
        frappe.throw(frappe._("At least one item is required for pre-booking"))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    _ensure_pre_booking_sale_type()

    requested_delivery = delivery_date or str(datetime.date.today() + datetime.timedelta(days=7))
    so = frappe.new_doc("Sales Order")
    so.customer = customer or profile.customer
    so.company = profile.company
    so.transaction_date = nowdate()
    so.delivery_date = requested_delivery
    so.currency = profile.currency or frappe.get_cached_value("Company", profile.company, "default_currency")
    so.selling_price_list = profile.selling_price_list
    so.ignore_pricing_rule = 1
    so.order_type = "Sales"
    if so.meta.has_field("set_warehouse") and profile.warehouse:
        so.set_warehouse = profile.warehouse
    if so.meta.has_field("reserve_stock"):
        so.reserve_stock = cint(reserve_stock)
    # Legacy fallback only — when MOP is provided, the Payment Entry drives
    # advance_paid via the framework (set_total_advance_paid).
    if flt(advance_amount) > 0 and not mode_of_payment and so.meta.has_field("advance_paid"):
        so.advance_paid = flt(advance_amount)

    if notes and so.meta.has_field("note"):
        so.note = notes
    elif notes and so.meta.has_field("remarks"):
        so.remarks = notes

    if sale_reference and so.meta.has_field("tracking_number"):
        so.tracking_number = sale_reference

    # ── TC_023 — duplicate-IMEI prebooking guard ───────────────────────
    # Reject upfront if any line carries a serial that is already reserved
    # on another open Sales Order. We check submitted, non-Closed/Cancelled
    # SOs only; cancelled rows free their IMEI naturally on cancel.
    seen_in_cart: dict[str, str] = {}
    so_item_meta = frappe.get_meta("Sales Order Item")
    has_custom_serial = so_item_meta.has_field("custom_serial_no")
    for item in items:
        serial = (item.get("serial_no") or "").strip()
        if not serial:
            continue
        # Cross-row uniqueness within the same cart
        if serial in seen_in_cart and seen_in_cart[serial] != item.get("item_code"):
            frappe.throw(
                frappe._("IMEI {0} appears on more than one item in the cart.")
                .format(serial),
                title=frappe._("Duplicate IMEI"),
            )
        seen_in_cart[serial] = item.get("item_code")

        if has_custom_serial:
            dup = frappe.db.sql(
                """
                SELECT soi.parent
                FROM `tabSales Order Item` soi
                JOIN `tabSales Order` so ON so.name = soi.parent
                WHERE soi.custom_serial_no LIKE %(needle)s
                  AND so.docstatus = 1
                  AND so.status NOT IN ('Closed', 'Cancelled', 'Completed')
                LIMIT 1
                """,
                {"needle": f"%{serial}%"},
            )
            if dup:
                frappe.throw(
                    frappe._("IMEI {0} is already reserved on Sales Order {1}.")
                    .format(serial, dup[0][0]),
                    title=frappe._("Duplicate IMEI"),
                )

    for item in items:
        serial = (item.get("serial_no") or "").strip()
        row = {
            "item_code": item.get("item_code"),
            "qty": flt(item.get("qty", 1)),
            "rate": flt(item.get("rate", 0)),
            "uom": item.get("uom") or "Nos",
            "warehouse": item.get("warehouse") or profile.warehouse,
            "delivery_date": item.get("delivery_date") or requested_delivery,
        }
        # TC_024 — propagate IMEI so SO row carries the reservation and the
        # downstream Sales Invoice / Stock Reservation Engine can move the
        # serial out of the Sellable bin on pickup.
        if has_custom_serial and serial:
            row["custom_serial_no"] = serial
        so.append("items", row)

    if sales_executive and so.meta.has_field("custom_sales_executive"):
        so.custom_sales_executive = sales_executive
    if client_request_id and so.meta.has_field("custom_client_request_id"):
        so.custom_client_request_id = str(client_request_id)[:140]

    so.flags.ignore_permissions = True
    so.insert(ignore_permissions=True)

    submit_warning = None
    try:
        so.submit()
    except Exception:
        # Keep the order saved even if stock reservation cannot fully complete yet.
        submit_warning = frappe.get_traceback()
        frappe.log_error(submit_warning, f"Pre-booking submit failed for {so.name}")
        so.reload()

    if flt(advance_amount) > 0 or notes:
        comment = _("Pre-booking created")
        if flt(advance_amount) > 0:
            comment += _(" with advance of {0}").format(fmt_money(advance_amount, currency=so.currency))
        if notes:
            comment += _(". Notes: {0}").format(notes)
        try:
            so.add_comment("Comment", comment)
        except Exception:
            pass

    # ── Advance Payment Entries (split-tender capable) ──────────────────
    # Reuse the same advance pattern as ch_payments.advance_payments —
    # create one Draft Payment Entry per tender row referencing this
    # Sales Order. ERPNext's set_total_advance_paid keeps SO.advance_paid
    # in sync once the PEs are submitted by the workflow Checker.
    #
    # Accepts EITHER (preferred) a `payments=[{mode_of_payment, amount,
    # reference_no?}, …]` list for split-tender, OR (legacy single-row)
    # `mode_of_payment` + `payment_reference_no` covering the full advance.
    advance_pe_names: list[str] = []
    advance_pe_details: list[dict] = []
    advance_pe_warning = None

    pay_rows: list[dict] = []
    if payments:
        if isinstance(payments, str):
            payments = frappe.parse_json(payments)
        for r in (payments or []):
            mop = (r.get("mode_of_payment") or "").strip()
            amt = flt(r.get("amount"))
            if not mop or amt <= 0:
                continue
            pay_rows.append({
                "mode_of_payment": mop,
                "amount": amt,
                "reference_no": (r.get("reference_no") or "").strip() or None,
            })
    elif mode_of_payment and flt(advance_amount) > 0:
        pay_rows.append({
            "mode_of_payment": mode_of_payment,
            "amount": flt(advance_amount),
            "reference_no": payment_reference_no,
        })

    if pay_rows and so.docstatus == 1:
        # Sanity: row sum should match advance_amount (1-paisa tolerance).
        row_sum = sum(flt(r["amount"]) for r in pay_rows)
        if flt(advance_amount) > 0 and abs(row_sum - flt(advance_amount)) > 0.01:
            frappe.throw(
                _("Advance payment rows total {0} does not match advance amount {1}.")
                .format(fmt_money(row_sum, currency=so.currency),
                        fmt_money(advance_amount, currency=so.currency))
            )
        for r in pay_rows:
            try:
                pe_name = _create_pre_booking_advance_pe(
                    so, r["mode_of_payment"], flt(r["amount"]), r.get("reference_no")
                )
                advance_pe_names.append(pe_name)
                pe_docstatus = cint(frappe.db.get_value("Payment Entry", pe_name, "docstatus") or 0)
                advance_pe_details.append({
                    "name": pe_name,
                    "mode_of_payment": r["mode_of_payment"],
                    "amount": flt(r["amount"]),
                    "docstatus": pe_docstatus,
                    "receipt_state": "Final" if pe_docstatus == 1 else "Draft",
                })
            except Exception:
                advance_pe_warning = frappe.get_traceback()
                frappe.log_error(
                    advance_pe_warning,
                    f"Pre-booking advance PE failed for {so.name} / {r.get('mode_of_payment')}",
                )

    advance_pe_name = advance_pe_names[0] if advance_pe_names else None

    return {
        "doctype": "Sales Order",
        "name": so.name,
        "docstatus": so.docstatus,
        "status": so.status,
        "reserve_stock": cint(getattr(so, "reserve_stock", 0)),
        "advance_amount": flt(advance_amount),
        "advance_payment_entry": advance_pe_name,
        "advance_payment_entries": advance_pe_names,
        "advance_payment_entries_detail": advance_pe_details,
        "delivery_date": requested_delivery,
        "warning": _("Saved as draft") if so.docstatus == 0 and submit_warning else (
            _("Advance recorded but Payment Entry could not be created — please raise it manually")
            if advance_pe_warning else None
        ),
    }


def _create_pre_booking_advance_pe(so, mode_of_payment: str, amount: float,
                                   reference_no: str | None = None) -> str:
    """Create a Draft Payment Entry against ``so`` for ``amount``.

    Mirrors ``ch_payments.advance_payments.create_advance_from_quotation``
    but references a Sales Order instead of a Quotation, so ERPNext's
    standard ``set_total_advance_paid`` keeps ``SO.advance_paid`` in sync
    once the PE clears the maker-checker workflow.
    """
    company = so.company
    mop = frappe.get_doc("Mode of Payment", mode_of_payment)
    paid_to_account = None
    for acc_row in (mop.accounts or []):
        if acc_row.company == company and acc_row.default_account:
            paid_to_account = acc_row.default_account
            break
    if not paid_to_account:
        frappe.throw(
            _("Mode of Payment <b>{0}</b> has no default account set for company <b>{1}</b>")
            .format(mode_of_payment, company)
        )

    receivable_account = frappe.db.get_value(
        "Company", company, "default_receivable_account"
    )
    if not receivable_account:
        frappe.throw(_("Company {0} has no default Receivable account").format(company))

    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Receive"
    pe.posting_date = nowdate()
    pe.company = company
    pe.mode_of_payment = mode_of_payment
    pe.party_type = "Customer"
    pe.party = so.customer
    pe.paid_from = receivable_account
    pe.paid_to = paid_to_account
    pe.paid_amount = amount
    pe.received_amount = amount
    pe.source_exchange_rate = 1
    pe.target_exchange_rate = 1
    pe.reference_no = reference_no or so.name
    pe.reference_date = nowdate()
    # Allocated against the SO so SO.advance_paid recomputes on PE submit.
    pe.append("references", {
        "reference_doctype": "Sales Order",
        "reference_name": so.name,
        "due_date": so.delivery_date,
        "total_amount": flt(so.grand_total) or amount,
        "outstanding_amount": flt(so.grand_total) or amount,
        "allocated_amount": amount,
    })
    pe.remarks = _("Pre-booking advance against Sales Order {0}").format(so.name)
    pe.flags.ignore_permissions = True
    pe.insert(ignore_permissions=True)
    return pe.name


@frappe.whitelist()
def cancel_pre_booking(sales_order, action="refund",
                       refund_mode_of_payment=None, reason=None) -> dict:
    """Cancel a pre-booking Sales Order and handle its advance (#9 / #11).

    ``action``:
      * ``refund``         — return the advance to the customer. A *draft*
                             advance Payment Entry is deleted (nothing was
                             posted); a *submitted* one is cancelled, reversing
                             the receipt.
      * ``retain_credit``  — keep the advance as an on-account customer credit so
                             it can be applied to another bill / a different
                             model (the #11 "change model, reuse advance" path).
                             The advance PE is left in place; cancelling the SO
                             frees its allocation so the amount becomes an
                             unallocated customer advance.

    Stock reservation is released by the standard Sales Order cancel.
    """
    so = frappe.get_doc("Sales Order", sales_order)
    if so.docstatus == 2:
        return {"status": "already_cancelled", "name": so.name}
    if flt(so.per_delivered) > 0 or flt(so.per_billed) > 0:
        frappe.throw(_("Cannot cancel — pre-booking {0} is already partly "
                       "delivered or billed.").format(so.name))

    action = (action or "refund").lower()
    retain = action == "retain_credit"

    # Advance Payment Entries referencing this SO.
    pe_names = list({r.parent for r in frappe.get_all(
        "Payment Entry Reference",
        filters={"reference_doctype": "Sales Order", "reference_name": so.name},
        fields=["parent"])})

    refunded = 0.0
    retained = 0.0
    for pe_name in pe_names:
        pe = frappe.get_doc("Payment Entry", pe_name)
        amt = flt(pe.paid_amount)
        if pe.docstatus == 0:
            # Draft — never posted. Refund: just drop it. Retain: re-collect on
            # the new bill, so dropping it is also correct (no credit to keep).
            frappe.delete_doc("Payment Entry", pe_name, force=True, ignore_permissions=True)
        elif pe.docstatus == 1:
            pe.flags.ignore_permissions = True
            pe.cancel()
            if retain:
                # Re-book the receipt as an unallocated on-account advance so the
                # customer keeps the credit for another bill / model.
                _create_on_account_advance_pe(so, pe.mode_of_payment, amt, pe.reference_no)
                retained += amt
            else:
                if refund_mode_of_payment:
                    _create_advance_refund_pe(so, refund_mode_of_payment, amt)
                refunded += amt

    so.reload()
    so.flags.ignore_permissions = True
    so.cancel()  # releases stock reservation

    try:
        so.add_comment("Comment", _("Pre-booking cancelled ({0}).{1}").format(
            "refunded" if not retain else "advance retained as credit",
            (" " + reason) if reason else ""))
    except Exception:
        pass

    return {"status": "cancelled", "name": so.name, "action": action,
            "refunded": round(refunded, 2), "retained": round(retained, 2)}


def _create_on_account_advance_pe(so, mode_of_payment, amount, reference_no=None):
    """Draft on-account 'Receive' Payment Entry (no SO reference) so the amount
    sits as an unallocated customer advance, reusable on the next bill."""
    company = so.company
    mop = frappe.get_doc("Mode of Payment", mode_of_payment)
    paid_to = next((a.default_account for a in (mop.accounts or [])
                    if a.company == company and a.default_account), None)
    receivable = frappe.db.get_value("Company", company, "default_receivable_account")
    if not paid_to or not receivable:
        return None
    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Receive"
    pe.posting_date = nowdate()
    pe.company = company
    pe.mode_of_payment = mode_of_payment
    pe.party_type = "Customer"
    pe.party = so.customer
    pe.paid_from = receivable
    pe.paid_to = paid_to
    pe.paid_amount = amount
    pe.received_amount = amount
    pe.source_exchange_rate = 1
    pe.target_exchange_rate = 1
    pe.reference_no = reference_no or so.name
    pe.reference_date = nowdate()
    pe.remarks = _("Retained advance credit from cancelled pre-booking {0}").format(so.name)
    pe.flags.ignore_permissions = True
    pe.insert(ignore_permissions=True)
    return pe.name


def _create_advance_refund_pe(so, mode_of_payment, amount):
    """Best-effort outward 'Pay' Payment Entry returning the advance to the
    customer via ``mode_of_payment`` (so the cash/UPI refund is on the books)."""
    company = so.company
    mop = frappe.get_doc("Mode of Payment", mode_of_payment)
    paid_from = next((a.default_account for a in (mop.accounts or [])
                      if a.company == company and a.default_account), None)
    receivable = frappe.db.get_value("Company", company, "default_receivable_account")
    if not paid_from or not receivable:
        return None
    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Pay"
    pe.posting_date = nowdate()
    pe.company = company
    pe.mode_of_payment = mode_of_payment
    pe.party_type = "Customer"
    pe.party = so.customer
    pe.paid_from = paid_from
    pe.paid_to = receivable
    pe.paid_amount = amount
    pe.received_amount = amount
    pe.source_exchange_rate = 1
    pe.target_exchange_rate = 1
    pe.reference_no = so.name
    pe.reference_date = nowdate()
    pe.remarks = _("Refund of pre-booking advance for cancelled {0}").format(so.name)
    pe.flags.ignore_permissions = True
    pe.insert(ignore_permissions=True)
    return pe.name


@frappe.whitelist()
def create_pos_quotation(pos_profile, customer, items, valid_till=None,
                          notes=None, sales_executive=None,
                          advance_amount=0) -> dict:
    """Create a Quotation from the POS cart so the operator can issue a
    Proforma Invoice (printed via the ch_erp15 "Proforma Invoice" format).

    Reuse-first: this is a thin convenience over `frappe.new_doc("Quotation")`
    so the POS can hand a draft quote to the customer without leaving the
    cashier screen. The same Proforma print format used in Desk works here.

    Market parity (SAP SD / Oracle Xstore / GoFrugal / Zoho / Odoo):
        A Proforma Invoice is a **non-binding quote** — no commercial
        commitment, no advance is collected against it. Advance / deposit
        collection belongs to **Pre-Booking** (Sales Order with reserved
        stock) via :py:func:`create_pre_booking`.

    The legacy ``advance_amount`` parameter is therefore **deprecated and
    ignored** at the POS layer; it is kept in the signature only for
    backward-compatibility with older clients. Submitted Payment Entries
    that reference a Quotation will still update ``custom_advance_received``
    via the existing ``ch_payments.advance_payments`` flow on Desk — that
    pathway is unchanged.
    """
    frappe.has_permission("Quotation", "create", throw=True)

    if isinstance(items, str):
        items = frappe.parse_json(items)
    if not items:
        frappe.throw(frappe._("At least one item is required for the proforma"))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    qtn = frappe.new_doc("Quotation")
    qtn.quotation_to = "Customer"
    qtn.party_name = customer or profile.customer
    qtn.company = profile.company
    qtn.transaction_date = nowdate()
    qtn.valid_till = valid_till or str(datetime.date.today() + datetime.timedelta(days=15))
    qtn.currency = profile.currency or frappe.get_cached_value("Company", profile.company, "default_currency")
    qtn.selling_price_list = profile.selling_price_list
    qtn.ignore_pricing_rule = 1
    qtn.order_type = "Sales"
    if notes and qtn.meta.has_field("terms"):
        qtn.terms = notes

    for item in items:
        qtn.append("items", {
            "item_code": item.get("item_code"),
            "qty": flt(item.get("qty", 1)),
            "rate": flt(item.get("rate", 0)),
            "uom": item.get("uom") or "Nos",
            "warehouse": item.get("warehouse") or profile.warehouse,
        })

    if sales_executive and qtn.meta.has_field("custom_sales_executive"):
        qtn.custom_sales_executive = sales_executive

    qtn.flags.ignore_permissions = True
    qtn.flags.ignore_workflow = True
    qtn.insert(ignore_permissions=True)
    qtn.submit()

    # ``advance_amount`` is intentionally NOT stamped here — see docstring.
    # Per market standards a Proforma carries no advance; only Pre-Booking
    # does. We accept the param for backward-compat and silently drop it.
    if flt(advance_amount) > 0:
        frappe.logger().info(
            f"create_pos_quotation: ignoring advance_amount={advance_amount} "
            f"on Quotation {qtn.name} — advances belong to Pre-Booking only."
        )

    return {
        "doctype": "Quotation",
        "name": qtn.name,
        "docstatus": qtn.docstatus,
        "status": qtn.status,
        "grand_total": flt(qtn.grand_total),
        "valid_till": str(qtn.valid_till),
        "print_format": "Proforma Invoice",
    }



















































































"""
CH POS Sales Invoice — Hardcoded taxable/2 split for CGST/SGST.

FORMULA:
  For each item:
    exempted_value = PR purchase_exempted_value per unit
    taxable_value  = (rate − exempted_value) / 1.18
    amount         = rate × qty

  Sales Taxes and Charges (HARDCODED SPLIT):
    In-State (2 rows: CGST + SGST):
      CGST tax_amount = Σ(taxable_value) / 2
      SGST tax_amount = Σ(taxable_value) / 2
    Out-of-State (1 row: IGST):
      IGST tax_amount = Σ(taxable_value)

  GST Breakup Table (HARDCODED SPLIT per HSN):
    Taxable Amount = Σ(taxable_value) per HSN
    In-State:
      CGST Amount  = taxable / 2
      SGST Amount  = taxable / 2
    Out-of-State:
      IGST Amount  = taxable

  Example (rate=92000, exempted=34220, in-state):
    taxable = (92000 − 34220) / 1.18 = 48,966.10
    CGST    = 48,966.10 / 2         = 24,483.05
    SGST    = 48,966.10 / 2         = 24,483.05
    Grand Total = 92,000
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, nowdate


# ═══════════════════════════════════════════════════════════════════════════════
# CACHES
# ═══════════════════════════════════════════════════════════════════════════════

_FIELD_MAP_CACHE: dict = {}
_GST_RATE_CACHE: dict = {}


def _detect_field(doctype, candidates):
    key = f"{doctype}:{','.join(candidates)}"
    if key in _FIELD_MAP_CACHE:
        return _FIELD_MAP_CACHE[key]
    try:
        meta = frappe.get_meta(doctype)
        for c in candidates:
            if meta.has_field(c):
                _FIELD_MAP_CACHE[key] = c
                return c
    except Exception:
        pass
    _FIELD_MAP_CACHE[key] = None
    return None


def _get_table_columns(table_name):
    cache_key = f"cols:{table_name}"
    if cache_key in _FIELD_MAP_CACHE:
        return _FIELD_MAP_CACHE[cache_key]
    try:
        cols = frappe.db.sql(f"SHOW COLUMNS FROM `{table_name}`", as_dict=True)
        col_set = {c.get("Field") for c in cols}
        _FIELD_MAP_CACHE[cache_key] = col_set
        return col_set
    except Exception:
        _FIELD_MAP_CACHE[cache_key] = set()
        return set()


# ═══════════════════════════════════════════════════════════════════════════════
# PR EXEMPTED LOOKUP
# ═══════════════════════════════════════════════════════════════════════════════

def _get_purchase_exempted_value_for_serial(serial, item_code=None):
    if not serial:
        return 0.0

    pr_field = _detect_field(
        "Purchase Receipt Item",
        ["custom_exempted_value", "exempted_value",
         "custom_exempted_amount", "exempted_amount"],
    )
    if not pr_field:
        return 0.0

    try:
        if frappe.db.exists("DocType", "CH Serial Lifecycle"):
            result = frappe.db.sql(f"""
                SELECT SUM(pri.`{pr_field}`) / NULLIF(SUM(pri.qty), 0)
                FROM `tabCH Serial Lifecycle` lc
                JOIN `tabPurchase Receipt Item` pri
                    ON lc.purchase_document = pri.parent
                   AND lc.item_code = pri.item_code
                WHERE lc.name = %s
                  AND lc.purchase_document IS NOT NULL
                  AND lc.purchase_document != ''
            """, (serial,), as_list=True)
            val = flt(result[0][0]) if result and result[0][0] is not None else 0.0
            if val > 0:
                return val
    except Exception:
        pass

    try:
        pr_name = frappe.db.get_value("Serial No", serial, "purchase_document")
        if pr_name:
            filters_sql = "pri.parent = %s"
            params = [pr_name]
            if item_code:
                filters_sql += " AND pri.item_code = %s"
                params.append(item_code)
            result = frappe.db.sql(f"""
                SELECT SUM(pri.`{pr_field}`) / NULLIF(SUM(pri.qty), 0)
                FROM `tabPurchase Receipt Item` pri WHERE {filters_sql}
            """, tuple(params), as_list=True)
            val = flt(result[0][0]) if result and result[0][0] is not None else 0.0
            if val > 0:
                return val
    except Exception:
        pass

    try:
        result = frappe.db.sql(f"""
            SELECT SUM(pri.`{pr_field}`) / NULLIF(SUM(pri.qty), 0)
            FROM `tabSerial No` sn
            JOIN `tabPurchase Receipt Item` pri
                ON sn.reference_name = pri.parent
               AND sn.item_code = pri.item_code
            WHERE sn.name = %s AND sn.reference_doctype = 'Purchase Receipt'
        """, (serial,), as_list=True)
        val = flt(result[0][0]) if result and result[0][0] is not None else 0.0
        if val > 0:
            return val
    except Exception:
        pass

    try:
        sn_field = _detect_field("Serial No", ["custom_exempted_value", "exempted_value"])
        if sn_field:
            val = flt(frappe.db.get_value("Serial No", serial, sn_field) or 0)
            if val > 0:
                return val
    except Exception:
        pass

    try:
        if not item_code:
            item_code = frappe.db.get_value("Serial No", serial, "item_code")
        if item_code:
            item_field = _detect_field("Item", ["custom_exempted_value", "exempted_value"])
            if item_field:
                val = flt(frappe.db.get_value("Item", item_code, item_field) or 0)
                if val > 0:
                    return val
    except Exception:
        pass

    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# DYNAMIC INSERT
# ═══════════════════════════════════════════════════════════════════════════════

def _dynamic_insert(table_name, row_dict):
    existing_cols = _get_table_columns(table_name)
    if not existing_cols:
        raise Exception(f"Table {table_name} has no columns")

    cols_to_use = []
    values_to_use = {}
    for col, val in row_dict.items():
        if col in existing_cols:
            cols_to_use.append(col)
            values_to_use[col] = val

    if not cols_to_use:
        raise Exception(f"No valid columns for {table_name}")

    col_sql = ", ".join([f"`{c}`" for c in cols_to_use])
    placeholder_sql = ", ".join([f"%({c})s" for c in cols_to_use])
    sql = f"INSERT INTO `{table_name}` ({col_sql}) VALUES ({placeholder_sql})"
    frappe.db.sql(sql, values_to_use)


# ═══════════════════════════════════════════════════════════════════════════════
# FORCE-INSERT TAX ROWS
# ═══════════════════════════════════════════════════════════════════════════════

def _force_insert_tax_rows(si_name, template_name):
    if not template_name:
        return False
    if not frappe.db.exists("Sales Taxes and Charges Template", template_name):
        return False

    try:
        tmpl = frappe.get_cached_doc(
            "Sales Taxes and Charges Template", template_name)
        if not tmpl.taxes:
            return False

        frappe.db.sql(
            "DELETE FROM `tabSales Taxes and Charges` WHERE parent = %s",
            (si_name,))

        company = frappe.db.get_value("Sales Invoice", si_name, "company")
        default_cc = frappe.db.get_value("Company", company, "cost_center") or ""

        if not default_cc:
            fallback = frappe.db.sql("""
                SELECT name FROM `tabCost Center`
                WHERE company=%s AND is_group=0 AND disabled=0
                ORDER BY creation ASC LIMIT 1
            """, (company,), as_dict=True)
            if fallback:
                default_cc = fallback[0].name

        for idx, src in enumerate(tmpl.taxes, start=1):
            row_data = {
                "name":         frappe.generate_hash(length=10),
                "parent":       si_name,
                "parenttype":   "Sales Invoice",
                "parentfield":  "taxes",
                "idx":          idx,
                "charge_type":  src.charge_type or "On Net Total",
                "account_head": src.account_head,
                "description":  src.description or src.account_head,
                "rate":         flt(src.rate),
                "cost_center":  src.cost_center or default_cc,
                "tax_amount":   0,
                "base_tax_amount": 0,
                "tax_amount_after_discount_amount": 0,
                "base_tax_amount_after_discount_amount": 0,
                "total":        0,
                "base_total":   0,
                "included_in_print_rate":  1,
                "included_in_paid_amount": 1,
                "add_deduct_tax":          "Add",
                "row_id":                  "",
                "dont_recompute_tax":      1,  # 🔑 prevent ERPNext from overwriting
                "docstatus":    0,
                "owner":        "Administrator",
                "modified_by":  "Administrator",
                "creation":     frappe.utils.now(),
                "modified":     frappe.utils.now(),
            }
            try:
                _dynamic_insert("tabSales Taxes and Charges", row_data)
            except Exception:
                frappe.log_error(
                    title=f"POS tax insert [{si_name}]",
                    message=frappe.get_traceback())

        frappe.db.commit()
        frappe.clear_document_cache("Sales Invoice", si_name)
        return True

    except Exception:
        frappe.log_error(
            title=f"_force_insert_tax_rows failed [{si_name}]",
            message=frappe.get_traceback())
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: Write ITEM-LEVEL calculations
# ═══════════════════════════════════════════════════════════════════════════════

def _write_item_calculations(si_name):
    """
    Per item:
      taxable_value = (rate − exempted) / 1.18
      gst_value     = amount − exempted_total − taxable_row (for reference)
    Returns totals for subsequent use.
    """
    if not frappe.db.exists("Sales Invoice", si_name):
        return None

    si_exempted_field = _detect_field(
        "Sales Invoice Item",
        ["custom_exempted_value", "exempted_value",
         "custom_exempted_amount", "exempted_amount"])
    si_taxable_field = _detect_field(
        "Sales Invoice Item",
        ["custom_total_taxable_value", "custom_taxable_value"])
    si_gst_field = _detect_field(
        "Sales Invoice Item",
        ["custom_gst_value", "gst_value", "custom_gst_amount"])

    sii_meta = frappe.get_meta("Sales Invoice Item")
    has_pev = sii_meta.has_field("purchase_exempted_value")
    has_taxable_val = sii_meta.has_field("taxable_value")

    si_items = frappe.db.sql("""
        SELECT name, item_code, qty, rate, serial_no
        FROM `tabSales Invoice Item`
        WHERE parent = %s ORDER BY idx
    """, (si_name,), as_dict=True)

    if not si_items:
        return None

    grand_total_amount = 0.0
    total_taxable = 0.0
    total_exempted = 0.0

    for si_item in si_items:
        rate = flt(si_item.rate)
        qty = flt(si_item.qty) or 1.0
        row_total = flt(rate * qty, 2)

        serials = [s.strip() for s in (si_item.serial_no or "").split("\n") if s.strip()]
        pev_per_unit = 0.0
        if serials:
            pev_sum = 0.0
            for sn in serials:
                pev_sum += _get_purchase_exempted_value_for_serial(sn, si_item.item_code)
            pev_per_unit = flt(pev_sum / len(serials), 2)

        if pev_per_unit == 0.0:
            try:
                item_field = _detect_field("Item", ["custom_exempted_value", "exempted_value"])
                if item_field:
                    pev_per_unit = flt(
                        frappe.db.get_value("Item", si_item.item_code, item_field) or 0)
            except Exception:
                pass

        if pev_per_unit > rate:
            pev_per_unit = rate

        exempted_per_unit = flt(pev_per_unit, 2)

        # FORMULA
        taxable_base_unit = flt(max(rate - exempted_per_unit, 0.0), 6)
        taxable_val_unit = flt(taxable_base_unit / 1.18, 6)

        row_exempted = flt(exempted_per_unit * qty, 2)
        row_taxable = flt(taxable_val_unit * qty, 2)
        # Display GST value = taxable / 2 (matches hardcoded split)
        row_gst_display = flt(row_taxable / 2.0, 2)  # per side (CGST or SGST)

        grand_total_amount += row_total
        total_taxable += row_taxable
        total_exempted += row_exempted

        # amount/rate stay tax-inclusive (what the customer sees/pays);
        # net_amount/net_rate must be the tax-EXCLUSIVE base (row_taxable),
        # since that's what ERPNext's standard get_gl_entries() credits to
        # the Income account. Leaving them equal to the inclusive amount
        # double-counted the tax on the credit side against grand_total's
        # debit, causing "Debit and Credit not equal" once the tax rows
        # were populated with the (now-corrected) real 9%+9% split.
        set_parts = [
            "amount = %(amount)s",
            "base_amount = %(amount)s",
            "net_amount = %(net_amount)s",
            "base_net_amount = %(net_amount)s",
            "net_rate = %(net_rate)s",
            "base_net_rate = %(net_rate)s",
        ]
        params = {
            "amount": row_total,
            "net_amount": row_taxable,
            "net_rate": taxable_val_unit,
            "name": si_item.name,
        }

        if has_taxable_val:
            set_parts.append("taxable_value = %(taxable)s")
            params["taxable"] = row_taxable
        if si_exempted_field:
            set_parts.append(f"`{si_exempted_field}` = %(exempted)s")
            params["exempted"] = exempted_per_unit
        if has_pev:
            set_parts.append("purchase_exempted_value = %(pev)s")
            params["pev"] = exempted_per_unit
        if si_taxable_field:
            set_parts.append(f"`{si_taxable_field}` = %(taxable_extra)s")
            params["taxable_extra"] = row_taxable
        if si_gst_field:
            set_parts.append(f"`{si_gst_field}` = %(gst)s")
            params["gst"] = flt(row_taxable, 2)  # store total taxable as gst display

        try:
            frappe.db.sql(f"""
                UPDATE `tabSales Invoice Item`
                SET {', '.join(set_parts)}
                WHERE name = %(name)s
            """, params)
        except Exception:
            frappe.log_error(
                title=f"_write_item_calculations [{si_item.name}]",
                message=frappe.get_traceback())

    frappe.db.commit()

    return {
        "grand_total": flt(grand_total_amount, 2),
        "total_taxable": flt(total_taxable, 2),
        "total_exempted": flt(total_exempted, 2),
    }


def _sync_header_totals_pre_submit(si_name, totals):
    """Reset the Sales Invoice header totals to match the zeroed tax rows
    written by _force_insert_tax_rows, so debit/credit balance at submit().

    At this point in the pipeline: tax rows are zeroed (_force_insert_tax_rows)
    and item.net_amount/base_net_amount already carry the tax-EXCLUSIVE base
    (_write_item_calculations sets these to totals["total_taxable"]'s
    per-item components, not the tax-inclusive amount/rate). ERPNext's
    standard get_gl_entries() credits Income from item.net_amount, so the
    header's net_total/grand_total must match THAT (total_taxable) — not the
    final tax-inclusive grand_total — while tax is still zero. The real
    tax-inclusive grand_total and CGST/SGST split are restored post-submit by
    _write_tax_rows_and_header + _rewrite_gl_entries, which rebuild GL entries
    fresh from the corrected state; they don't need this temporary state to
    already reflect the final numbers.

    Without this sync, the header still carries the tax-ON-TOP grand_total
    computed by the standard validate() pass inside insert(), which matches
    neither the zeroed tax rows nor the now-tax-exclusive item.net_amount,
    and makes submit()'s GL balance check throw "Debit and Credit not equal"
    before steps 7-8 ever run.
    """
    if not totals:
        return
    # Tax-exclusive base — matches item.net_amount while tax rows sit at 0.
    grand = flt(totals.get("total_taxable"), 2)
    paid = flt(frappe.db.get_value("Sales Invoice", si_name, "paid_amount") or 0, 2)
    outstanding = flt(grand - paid, 2)
    frappe.db.sql("""
        UPDATE `tabSales Invoice`
        SET net_total = %(g)s, base_net_total = %(g)s,
            total_taxes_and_charges = 0, base_total_taxes_and_charges = 0,
            grand_total = %(g)s, base_grand_total = %(g)s,
            rounded_total = %(g)s, base_rounded_total = %(g)s,
            outstanding_amount = %(o)s
        WHERE name = %(name)s
    """, {"g": grand, "o": outstanding, "name": si_name})
    frappe.db.commit()
    frappe.clear_document_cache("Sales Invoice", si_name)


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: Write TAX ROWS + HEADER (post-submit) — HARDCODED taxable/2 split
# ═══════════════════════════════════════════════════════════════════════════════

def _write_tax_rows_and_header(si_name, totals):
    """
    totals["total_taxable"] is the tax-EXCLUSIVE base (rate.taxable_val_unit,
    back-calculated via /1.18 in _write_item_calculations) — i.e. what
    net_total should be. The actual tax amount is the remainder of the
    tax-inclusive grand_total after that base, split evenly across the
    CGST/SGST (or GST) rows, or taken in full by a single IGST row.

    Previously this treated total_taxable itself as the tax amount
    (CGST = total_taxable / 2, SGST = total_taxable / 2) — inverting base
    and tax — which inflated tax rows far beyond the real 18% rate and made
    the GL entries built from them unbalanceable against grand_total.
    """
    if not totals:
        return

    grand_total_amount = totals["grand_total"]
    total_taxable = totals["total_taxable"]

    tax_rows = frappe.db.sql("""
        SELECT name, rate, account_head, description
        FROM `tabSales Taxes and Charges`
        WHERE parent = %s ORDER BY idx
    """, (si_name,), as_dict=True)

    def classify_tax_row(tax):
        acc = (tax.account_head or "").lower()
        desc = (tax.description or "").lower()
        combined = acc + " " + desc
        if "cgst" in combined:
            return "CGST"
        if "sgst" in combined or "utgst" in combined:
            return "SGST"
        if "igst" in combined:
            return "IGST"
        if "gst" in combined:
            return "GST"
        return "OTHER"

    classifications = [classify_tax_row(t) for t in tax_rows]
    cgst_count = classifications.count("CGST")
    sgst_count = classifications.count("SGST")
    igst_count = classifications.count("IGST")
    is_in_state = (cgst_count > 0 or sgst_count > 0) and igst_count == 0
    is_out_state = igst_count > 0

    stc_cols = _get_table_columns("tabSales Taxes and Charges")
    has_paid_amt = "included_in_paid_amount" in stc_cols

    # Tax amount = grand_total (tax-inclusive) minus the tax-exclusive
    # taxable base — NOT the taxable base itself. Split evenly across the
    # CGST/SGST/GST rows (last one absorbs the rounding remainder so they
    # sum exactly to total_tax_amount); a lone IGST row takes it in full.
    total_tax_amount = flt(max(grand_total_amount - total_taxable, 0.0), 2)
    split_positions = [i for i, t in enumerate(classifications) if t in ("CGST", "SGST", "GST")]
    n_split = len(split_positions)
    per_row_tax = flt(total_tax_amount / n_split, 2) if n_split else 0.0
    split_amounts = {}
    _allocated = 0.0
    for _i, _pos in enumerate(split_positions):
        if _i == n_split - 1:
            split_amounts[_pos] = flt(total_tax_amount - _allocated, 2)
        else:
            split_amounts[_pos] = per_row_tax
            _allocated += per_row_tax

    for _pos, (tax, tax_type) in enumerate(zip(tax_rows, classifications)):
        t_rate = flt(tax.rate)

        if tax_type in ("CGST", "SGST", "GST"):
            tax_amount = split_amounts[_pos]
        elif tax_type == "IGST":
            tax_amount = total_tax_amount
        else:
            tax_amount = flt(total_taxable * (t_rate / 100.0), 2)

        is_gst_row = tax_type in ("CGST", "SGST", "IGST", "GST")

        set_parts = [
            "tax_amount = %(ta)s",
            "base_tax_amount = %(ta)s",
            "tax_amount_after_discount_amount = %(ta)s",
            "base_tax_amount_after_discount_amount = %(ta)s",
            "total = %(total_after_tax)s",
            "base_total = %(total_after_tax)s",
            "docstatus = 1",
            "dont_recompute_tax = 1",
        ]
        tax_params = {
            "ta": tax_amount,
            "total_after_tax": grand_total_amount,
            "name": tax.name,
        }

        if is_gst_row:
            set_parts.append("included_in_print_rate = 1")
            if has_paid_amt:
                set_parts.append("included_in_paid_amount = 1")

        try:
            frappe.db.sql(f"""
                UPDATE `tabSales Taxes and Charges`
                SET {', '.join(set_parts)}
                WHERE name = %(name)s
            """, tax_params)
        except Exception:
            frappe.log_error(
                title=f"_write_tax_rows [{tax.name}]",
                message=frappe.get_traceback())

    # HEADER — GL-BALANCED (net_total = grand_total − taxes)
    grand_total = flt(grand_total_amount, 2)
    net_total = flt(grand_total - total_tax_amount, 2)
    rounded_total = flt(round(grand_total), 2)
    rounding_adj = flt(rounded_total - grand_total, 2)
    # outstanding_amount was set against the temporary tax-exclusive total by
    # _sync_header_totals_pre_submit (needed at submit() time); now that the
    # real tax-inclusive grand_total is restored, recompute it against the
    # actual paid_amount so a fully-paid POS sale ends up at 0, not a
    # leftover negative "overpayment" from that temporary state.
    paid_amount = flt(frappe.db.get_value("Sales Invoice", si_name, "paid_amount") or 0, 2)
    outstanding = flt(grand_total - paid_amount, 2)

    try:
        frappe.db.sql("""
            UPDATE `tabSales Invoice`
            SET
                total = %(total)s,
                base_total = %(total)s,
                net_total = %(net_total)s,
                base_net_total = %(net_total)s,
                total_taxes_and_charges = %(tax)s,
                base_total_taxes_and_charges = %(tax)s,
                grand_total = %(grand)s,
                base_grand_total = %(grand)s,
                rounded_total = %(rounded)s,
                base_rounded_total = %(rounded)s,
                rounding_adjustment = %(rnd_adj)s,
                base_rounding_adjustment = %(rnd_adj)s,
                outstanding_amount = %(outstanding)s
            WHERE name = %(name)s
        """, {
            "total": grand_total_amount,
            "net_total": net_total,
            "tax": total_tax_amount,
            "grand": grand_total,
            "rounded": rounded_total,
            "rnd_adj": rounding_adj,
            "outstanding": outstanding,
            "name": si_name,
        })
    except Exception:
        frappe.log_error(
            title=f"_write_header [{si_name}]",
            message=frappe.get_traceback())

    # GST BREAKUP (uses same hardcoded split per HSN)
    _update_gst_breakup(si_name, is_in_state, is_out_state)

    frappe.db.commit()
    frappe.clear_document_cache("Sales Invoice", si_name)


# ═══════════════════════════════════════════════════════════════════════════════
# GST BREAKUP — HARDCODED taxable/2 split per HSN
# ═══════════════════════════════════════════════════════════════════════════════

def _update_gst_breakup(si_name, is_in_state, is_out_state):
    """
    Per HSN:
      Taxable Amount = Σ(taxable_value)
      In-state: CGST = Taxable/2, SGST = Taxable/2
      Out-state: IGST = Taxable
    """
    si_meta = frappe.get_meta("Sales Invoice")
    breakup_field = None
    breakup_child_dt = None

    for field in si_meta.fields:
        fname = (field.fieldname or "").lower()
        if field.fieldtype == "Table" and ("gst_breakup" in fname or "breakup" in fname):
            breakup_field = field.fieldname
            breakup_child_dt = field.options
            break

    if not breakup_field or not breakup_child_dt:
        return
    if not frappe.db.exists("DocType", breakup_child_dt):
        return

    si_taxable_field = _detect_field(
        "Sales Invoice Item",
        ["custom_total_taxable_value", "custom_taxable_value"])

    taxable_sql = (
        f"SUM(COALESCE(sii.`{si_taxable_field}`, sii.taxable_value, 0))"
        if si_taxable_field
        else "SUM(COALESCE(sii.taxable_value, 0))"
    )

    hsn_data = frappe.db.sql(f"""
        SELECT 
            COALESCE(NULLIF(sii.gst_hsn_code, ''), i.gst_hsn_code, 'N/A') AS hsn_code,
            {taxable_sql}                        AS taxable_amount,
            SUM(sii.rate * sii.qty)              AS total_amount
        FROM `tabSales Invoice Item` sii
        LEFT JOIN `tabItem` i ON i.name = sii.item_code
        WHERE sii.parent = %s
        GROUP BY hsn_code
        ORDER BY hsn_code
    """, (si_name,), as_dict=True)

    if not hsn_data:
        return

    breakup_meta = frappe.get_meta(breakup_child_dt)
    hsn_col = taxable_col = None
    cgst_amt_col = sgst_amt_col = igst_amt_col = None
    cgst_rate_col = sgst_rate_col = igst_rate_col = None

    for f in breakup_meta.fields:
        fn = (f.fieldname or "").lower()
        if hsn_col is None and ("hsn" in fn or "sac" in fn):
            hsn_col = f.fieldname
        if taxable_col is None and "taxable" in fn:
            taxable_col = f.fieldname
        if "cgst" in fn:
            if "rate" in fn and cgst_rate_col is None:
                cgst_rate_col = f.fieldname
            elif "amount" in fn or fn == "cgst":
                if cgst_amt_col is None:
                    cgst_amt_col = f.fieldname
        if "sgst" in fn:
            if "rate" in fn and sgst_rate_col is None:
                sgst_rate_col = f.fieldname
            elif "amount" in fn or fn == "sgst":
                if sgst_amt_col is None:
                    sgst_amt_col = f.fieldname
        if "igst" in fn:
            if "rate" in fn and igst_rate_col is None:
                igst_rate_col = f.fieldname
            elif "amount" in fn or fn == "igst":
                if igst_amt_col is None:
                    igst_amt_col = f.fieldname

    if not hsn_col or not taxable_col:
        return

    frappe.db.sql(f"DELETE FROM `tab{breakup_child_dt}` WHERE parent = %s", (si_name,))

    for idx, hsn in enumerate(hsn_data, start=1):
        taxable_amt = flt(hsn.taxable_amount, 2)

        # 🔑 HARDCODED SPLIT per HSN
        if is_in_state:
            cgst_amt = flt(taxable_amt / 2.0, 2)  # taxable / 2
            sgst_amt = flt(taxable_amt / 2.0, 2)  # taxable / 2
            igst_amt = 0.0
            cgst_rate_val = 50.0  # display: shows 50% of taxable
            sgst_rate_val = 50.0
            igst_rate_val = 0.0
        elif is_out_state:
            cgst_amt = 0.0
            sgst_amt = 0.0
            igst_amt = flt(taxable_amt, 2)
            cgst_rate_val = 0.0
            sgst_rate_val = 0.0
            igst_rate_val = 100.0
        else:
            cgst_amt = flt(taxable_amt / 2.0, 2)
            sgst_amt = flt(taxable_amt / 2.0, 2)
            igst_amt = 0.0
            cgst_rate_val = 50.0
            sgst_rate_val = 50.0
            igst_rate_val = 0.0

        row_data = {
            "name":        frappe.generate_hash(length=10),
            "parent":      si_name,
            "parenttype":  "Sales Invoice",
            "parentfield": breakup_field,
            "idx":         idx,
            hsn_col:       hsn.hsn_code or "N/A",
            taxable_col:   taxable_amt,
            "docstatus":   1,
            "owner":       "Administrator",
            "modified_by": "Administrator",
            "creation":    frappe.utils.now(),
            "modified":    frappe.utils.now(),
        }

        if cgst_amt_col:
            row_data[cgst_amt_col] = cgst_amt
        if sgst_amt_col:
            row_data[sgst_amt_col] = sgst_amt
        if igst_amt_col:
            row_data[igst_amt_col] = igst_amt
        if cgst_rate_col:
            row_data[cgst_rate_col] = cgst_rate_val
        if sgst_rate_col:
            row_data[sgst_rate_col] = sgst_rate_val
        if igst_rate_col:
            row_data[igst_rate_col] = igst_rate_val

        try:
            _dynamic_insert(f"tab{breakup_child_dt}", row_data)
        except Exception:
            frappe.log_error(
                title=f"GST breakup insert [{si_name}]",
                message=frappe.get_traceback())

    frappe.db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# REWRITE GL ENTRIES
# ═══════════════════════════════════════════════════════════════════════════════

def _rewrite_gl_entries(si_name):
    """
    Delete existing GL entries and re-make them so they balance with the
    updated net_total and total_taxes_and_charges.
    """
    try:
        frappe.db.sql("""
            DELETE FROM `tabGL Entry`
            WHERE voucher_type = 'Sales Invoice' AND voucher_no = %s
        """, (si_name,))

        if frappe.db.exists("DocType", "Payment Ledger Entry"):
            frappe.db.sql("""
                DELETE FROM `tabPayment Ledger Entry`
                WHERE voucher_type = 'Sales Invoice' AND voucher_no = %s
            """, (si_name,))

        frappe.db.commit()

        doc = frappe.get_doc("Sales Invoice", si_name)
        doc.flags.ignore_permissions = True
        doc.flags.ignore_validate = True
        doc.make_gl_entries()

        frappe.db.commit()

    except Exception:
        frappe.log_error(
            title=f"_rewrite_gl_entries [{si_name}]",
            message=frappe.get_traceback())


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def _get_item_floor_price(item_code, company=None) -> float:
    """Server-side Minimum Operating Price (MOP / floor) for an item.

    Returns the LOWEST active configured MOP (>0) from CH Item Price, or 0.0
    when none is configured. Using the lowest active floor avoids false-positive
    blocks on legitimate POS sales (a stricter non-POS channel MOP won't wrongly
    reject) while still catching egregious below-floor manipulation. The client
    cannot be trusted to supply this — it is looked up fresh on the server.
    """
    if not item_code:
        return 0.0
    filters = {"item_code": item_code}
    if company:
        filters["company"] = ["in", [company, "", None]]
    rows = frappe.get_all(
        "CH Item Price", filters=filters, fields=["mop", "status"], limit=50)
    mops = [
        flt(r.mop) for r in rows
        if flt(r.mop) > 0 and (r.get("status") or "").lower() in ("active", "approved", "")
    ]
    return min(mops) if mops else 0.0


@frappe.whitelist()
def create_pos_invoice(
    pos_profile, customer, items,
    mode_of_payment=None, amount_paid=0,
    payments=None,
    exchange_assessment=None, buyback_order=None,
    additional_discount_percentage=0,
    additional_discount_amount=0, coupon_code=None,
    coupon_discount_amount=0,
    voucher_code=None, voucher_amount=0,
    redeem_loyalty_points=0, loyalty_points=0, loyalty_amount=0,
    bank_offer_discount=0, bank_offer_name=None,
    sales_executive=None, sale_type=None, sale_sub_type=None,
    sale_reference=None, finance_tenure=None, discount_reason=None,
    client_request_id=None,
    is_credit_sale=0, credit_days=0,
    credit_reference=None, credit_notes=None,
    credit_terms=None, credit_interest_rate=0,
    credit_grace_period=0, credit_partial_payment=0,
    credit_approved_by=None,
    is_free_sale=0, free_sale_reason=None, free_sale_approved_by=None,
    free_sale_approved_at=None, free_sale_approval_name=None,
    advance_amount=0, kiosk_token=None,
    guided_session=None,
    exception_request=None, warranty_claim=None,
    customer_gstin=None,
    original_invoice=None,
    original_invoice_reason=None,
    discount_authorized_by=None,
    allow_surplus_refund=0,
    surplus_refund_mode_of_payment=None,
    sales_order=None,
    source_quotation=None,
    **_ignored,
):
    frappe.has_permission("Sales Invoice", "create", throw=True)

    from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_active_session
    active = get_active_session(pos_profile) if pos_profile else None
    if not active:
        frappe.throw(_("No active POS session. Open a session before billing."))
    session_name = active.get("name")

    if client_request_id:
        existing = frappe.db.sql("""
            SELECT name FROM `tabSales Invoice`
            WHERE custom_client_request_id = %(crid)s
              AND docstatus != 2
              AND creation >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)
            LIMIT 1
        """, {"crid": str(client_request_id)[:140]}, as_dict=True)
        if existing:
            return {"name": existing[0].name, "status": "duplicate_prevented"}

    # CSV import dedup guard — prevent double-entry from re-import of same batch
    if frappe.flags.in_import and not cint(kwargs.get("ignore_dedup", 0)):
        from ch_pos.api.import_dedup import check_import_dedup_sales_invoice
        if isinstance(items, str):
            items_list = frappe.parse_json(items)
        else:
            items_list = items or []
        
        dedup_result = check_import_dedup_sales_invoice(
            customer=customer,
            items=items_list,
            posting_date=kwargs.get("posting_date") or nowdate(),
            company=kwargs.get("company") or frappe.db.get_value("POS Profile", pos_profile, "company"),
        )
        if dedup_result:
            return dedup_result

    if isinstance(items, str):
        items = frappe.parse_json(items)

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    _GST_RATE_CACHE.clear()
    _FIELD_MAP_CACHE.clear()

    _enforce_token_linkage(pos_profile, kiosk_token)

    inv = frappe.new_doc("Sales Invoice")
    inv.custom_ch_pos_session = session_name
    inv.pos_profile = pos_profile
    inv.customer = customer
    inv.company = profile.company
    inv.selling_price_list = profile.selling_price_list
    inv.currency = profile.currency or frappe.get_cached_value(
        "Company", profile.company, "default_currency")
    inv.warehouse = profile.warehouse
    inv.posting_date = (
        str(active.get("business_date"))
        if active.get("business_date") else nowdate())
    inv.is_pos = 1
    inv.update_stock = 1
    inv.due_date = None

    if getattr(profile, "company_address", None):
        inv.company_address = profile.company_address

    if not getattr(inv, "company_gstin", None):
        _co_gstin = (frappe.db.get_value("Company", profile.company, "gstin") or "").strip()
        if _co_gstin:
            inv.company_gstin = _co_gstin

    template_name = None
    try:
        from ch_erp15.ch_erp15.custom.sales_invoice import get_gst_template_for_customer
        _resolved = get_gst_template_for_customer(customer or "", profile.company) or {}
        inv.tax_category = _resolved.get("tax_category") or "In-State"
        template_name = _resolved.get("template")
        if template_name:
            inv.taxes_and_charges = template_name
    except Exception:
        pass

    if not template_name and getattr(profile, "taxes_and_charges", None):
        template_name = profile.taxes_and_charges
        inv.taxes_and_charges = template_name

    if customer and customer != "Walk-in Customer":
        lp = frappe.db.get_value(
            "Loyalty Program",
            {"company": profile.company, "auto_opt_in": 1}, "name"
        ) or frappe.db.get_value("Customer", customer, "loyalty_program")
        if lp:
            inv.loyalty_program = lp

    if customer_gstin:
        _gstin = customer_gstin.strip().upper()
        inv.custom_customer_gstin = _gstin
        inv.billing_address_gstin = _gstin
        if (inv.gst_category or "Unregistered") in (
            "Unregistered", "", None, "B2C", "B2C-Small", "B2C-Large"
        ):
            inv.gst_category = "Registered Regular"
    else:
        inv.custom_customer_gstin = None

    if kiosk_token:
        inv.custom_kiosk_token = kiosk_token
    if guided_session:
        if not frappe.db.exists("POS Guided Session", guided_session):
            frappe.throw(_("Guided Session {0} not found").format(guided_session))
        inv.custom_guided_session = guided_session

    exception_request_doc = None
    exception_request_doc_map: dict = {}

    def _phone_tail(phone_value):
        return "".join(ch for ch in str(phone_value or "") if ch.isdigit())[-10:]

    def _validate_exception(exc_name):
        if not exc_name:
            return None
        if exc_name in exception_request_doc_map:
            return exception_request_doc_map[exc_name]
        exc = frappe.get_doc("CH Exception Request", exc_name)
        if not exc.is_valid():
            frappe.throw(_("Exception Request {0} is no longer valid (status: {1})").format(
                exc_name, exc.status))
        if exc.pos_invoice:
            frappe.throw(_("Exception Request {0} was already used in invoice {1}").format(
                exc_name, exc.pos_invoice))
        if exc.customer:
            cust_row = frappe.db.get_value(
                "Customer", exc.customer,
                ["mobile_no", "ch_alternate_phone"], as_dict=True)
            curr_row = frappe.db.get_value(
                "Customer", customer,
                ["mobile_no", "ch_alternate_phone"], as_dict=True) if customer else None
            allowed_phone = _phone_tail(
                exc.customer_phone
                or (cust_row.mobile_no if cust_row else "")
                or (cust_row.ch_alternate_phone if cust_row else ""))
            current_phone = _phone_tail(
                (curr_row.mobile_no if curr_row else "")
                or (curr_row.ch_alternate_phone if curr_row else ""))
            if exc.customer != customer or (
                allowed_phone and current_phone and allowed_phone != current_phone):
                frappe.throw(_("Exception Request {0} is tied to a different customer phone.").format(exc_name))
        exception_request_doc_map[exc_name] = exc
        return exc

    if exception_request:
        exception_request_doc = _validate_exception(exception_request)
        inv.custom_exception_request = exception_request
        inv.ignore_pricing_rule = 1

    for _it in items or []:
        _line_exc = (_it or {}).get("exception_request")
        if not _line_exc:
            continue
        _validate_exception(_line_exc)
        inv.ignore_pricing_rule = 1
        if not inv.get("custom_exception_request"):
            inv.custom_exception_request = _line_exc
            if exception_request_doc is None:
                exception_request_doc = exception_request_doc_map.get(_line_exc)

    if warranty_claim:
        wc = frappe.get_doc("CH Warranty Claim", warranty_claim)
        if wc.docstatus != 1:
            frappe.throw(_("Warranty Claim {0} is not submitted").format(warranty_claim))
        if wc.processing_fee_status != "Pending":
            frappe.throw(_("Warranty Claim {0} processing fee is {1}, not Pending").format(
                warranty_claim, wc.processing_fee_status))
        if wc.processing_fee_invoice:
            frappe.throw(_("Warranty Claim {0} already has a processing fee invoice {1}").format(
                warranty_claim, wc.processing_fee_invoice))
        inv.custom_warranty_claim = warranty_claim

    warranty_items: list = []
    _plan_service_item_cache: dict = {}
    _plan_type_cache: dict = {}

    def _get_plan_service_item(plan_name):
        if not plan_name:
            return ""
        if plan_name not in _plan_service_item_cache:
            _plan_service_item_cache[plan_name] = (
                frappe.db.get_value("CH Warranty Plan", plan_name, "service_item") or "")
        return _plan_service_item_cache[plan_name]

    def _get_plan_type(plan_name):
        if not plan_name:
            return ""
        if plan_name not in _plan_type_cache:
            _plan_type_cache[plan_name] = (
                frappe.db.get_value("CH Warranty Plan", plan_name, "plan_type") or "")
        return _plan_type_cache[plan_name]

    def _is_plan_row(cart_item):
        if cint(cart_item.get("is_warranty")) or cint(cart_item.get("is_vas")):
            return True
        plan_name = cart_item.get("warranty_plan")
        if not plan_name:
            return False
        service_item = _get_plan_service_item(plan_name)
        if service_item and cart_item.get("item_code") == service_item:
            return True
        for_item_code = cart_item.get("for_item_code")
        if for_item_code and for_item_code != cart_item.get("item_code"):
            return True
        return False

    for item in items:
        if item.get("customer_imei") is not None and not item.get("for_serial_no"):
            item["for_serial_no"] = item.get("customer_imei")
        for _sn_key in ("serial_no", "for_serial_no", "customer_imei"):
            _sn_val = item.get(_sn_key)
            if _sn_val is not None and not isinstance(_sn_val, str):
                item[_sn_key] = str(_sn_val)

        item_serial = (item.get("serial_no") or item.get("for_serial_no") or "").strip()
        item_qty = flt(item.get("qty", 1)) or 1.0
        uploaded_rate = flt(item.get("rate") or item.get("price"))
        uploaded_amount = flt(item.get("amount"))

        if not uploaded_rate and item_qty and uploaded_amount:
            uploaded_rate = flt(uploaded_amount / item_qty)

        item_exception_original = flt(
            item.get("exception_original_rate")
            or item.get("price_list_rate")
            or item.get("mrp")
            or uploaded_rate)
        item_exception_final = flt(item.get("exception_final_rate") or uploaded_rate)

        _row_exc_name = (item.get("exception_request") or "").strip()
        _row_exc_doc = exception_request_doc_map.get(_row_exc_name) if _row_exc_name else None
        if (not _row_exc_doc and exception_request_doc
                and item.get("item_code") == exception_request_doc.item_code):
            _row_exc_doc = exception_request_doc
            _row_exc_name = exception_request_doc.name

        if _row_exc_doc:
            if not _row_exc_doc.customer or _row_exc_doc.customer == customer:
                item_exception_original = flt(_row_exc_doc.original_value or item_exception_original)
                item_exception_final = flt(
                    _row_exc_doc.resolution_value or _row_exc_doc.requested_value or item_exception_final)

        effective_rate = item_exception_final
        if uploaded_amount and item_qty:
            effective_rate = flt(uploaded_amount / item_qty)

        # Free-bundle accessory: pin to qty=1 / rate=0 server-side so a
        # hand-crafted payload (or a stale client that missed the merge-guard
        # fix in cart_service.add_to_cart) cannot bill 8 headphones at ₹0
        # against a single device. Extra units of the accessory must come in
        # as separate PAID lines. `is_free_item` is the ERPNext-standard flag
        # already consumed by `_post_free_sale_write_off` and
        # `free_item_return_guard`, so stamping it here keeps the row
        # consistent with the Pricing-Rule free-item path.
        is_free_bundle_row = cint(item.get("is_free_bundle_item"))
        if is_free_bundle_row:
            item_qty = 1.0
            effective_rate = 0.0
            item_exception_original = flt(item.get("price_list_rate") or item.get("mrp") or 0)

        row = {
            "item_code": item.get("item_code"),
            "qty": item_qty,
            "rate": effective_rate,
            "price_list_rate": item_exception_original,
            "uom": item.get("uom", "Nos"),
            "warehouse": profile.warehouse,
            "discount_percentage": flt(item.get("discount_percentage", 0)),
            "discount_amount": flt(item.get("discount_amount", 0)),
        }

        if is_free_bundle_row:
            row["is_free_item"] = 1
            row["discount_percentage"] = 100
            row["discount_amount"] = flt(item_exception_original)

        _item_so = (item.get("sales_order") or sales_order or "").strip()
        _item_so_detail = (item.get("so_detail") or "").strip()
        if _item_so:
            row["sales_order"] = _item_so
        if _item_so_detail:
            row["so_detail"] = _item_so_detail

        item_is_plan = _is_plan_row(item)
        item_plan_type = _get_plan_type(item.get("warranty_plan")) if item.get("warranty_plan") else ""
        item_is_vas = cint(item.get("is_vas", 0)) or cint(
            item_plan_type in ("Value Added Service", "Protection Plan"))

        if (item_exception_original > 0 and item_exception_final >= 0
                and item_exception_original != item_exception_final):
            row["discount_amount"] = flt(max(0, item_exception_original - item_exception_final))
            row["discount_percentage"] = (
                flt(row["discount_amount"] / item_exception_original * 100)
                if item_exception_original > 0 else 0)

        if item.get("warranty_plan") and item_is_plan:
            row["custom_warranty_plan"] = item.get("warranty_plan")

        if item.get("manager_approved"):
            row["custom_manager_approved"] = 1
            row["custom_manager_user"] = item.get("manager_user") or ""
            row["custom_override_reason"] = item.get("override_reason") or ""

        if _row_exc_doc:
            row["custom_exception_request"] = _row_exc_name
            row["custom_exception_original_rate"] = item_exception_original
            row["custom_exception_final_rate"] = item_exception_final

        # ── Minimum Selling Price floor (server-side, TC_002) ──────────────
        # A sale below the item's configured MOP requires an authorized
        # override. The client cannot enforce this — a crafted payload could
        # otherwise bill below floor. An override is present when the header
        # carries discount_authorized_by, or the row is manager-approved, or it
        # is backed by a CH Exception Request.
        if (not item_is_plan and not item_is_vas and not is_free_bundle_row
                and flt(effective_rate) > 0):
            floor_price = _get_item_floor_price(item.get("item_code"), profile.company)
            row_authorized = bool(
                item.get("manager_approved")
                or (item.get("exception_request") or "").strip()
                or _row_exc_doc)
            if (floor_price and flt(effective_rate) < floor_price
                    and not discount_authorized_by
                    and not row_authorized):
                frappe.throw(
                    _("Below Minimum Selling Price: {0} at {1} is under the floor "
                      "of {2}. Manager authorization is required.").format(
                        item.get("item_code"),
                        frappe.utils.fmt_money(flt(effective_rate)),
                        frappe.utils.fmt_money(floor_price)),
                    title=_("Below Minimum Selling Price"))

        if item.get("serial_no") and not item_is_plan:
            row["serial_no"] = item.get("serial_no")

        ch_item_type = frappe.db.get_value("Item", item.get("item_code"), "ch_item_type")
        if ch_item_type in ("Refurbished", "Pre-Owned"):
            row["custom_is_margin_item"] = 1

        if item_serial:
            _sbin = frappe.db.get_value("CH Stock Bin", {"serial_no": item_serial}, "bin_type")
            if _sbin == "Transfer":
                frappe.throw(
                    _("Serial {0} is currently in transit and cannot be sold.").format(item_serial),
                    title=_("Serial In Transit"))
            if _sbin in ("Damaged", "Defect", "DOA", "Scrapped", "Lost", "Buyback"):
                frappe.throw(
                    _("Serial {0} (bin type: {1}) is not available for sale.").format(item_serial, _sbin),
                    title=_("Serial Not Sellable"))

            _life_status = frappe.db.get_value(
                "CH Serial Lifecycle", item_serial, "lifecycle_status")
            if _life_status == "Sold":
                last_sale = frappe.db.get_value("CH Serial Lifecycle", item_serial, "sale_document")
                frappe.throw(
                    _("Serial / IMEI {0} has already been sold (Sales Invoice {1}).").format(
                        item_serial, last_sale or _("unknown")),
                    title=_("Serial Already Sold"))
            if _life_status == "In Service":
                frappe.throw(
                    _("Serial / IMEI {0} is currently In Service and cannot be sold.").format(item_serial),
                    title=_("Serial In Service"))

        inv.append("items", row)

        if item_exception_original > 0 and item_exception_final != item_exception_original:
            inv.ignore_pricing_rule = 1

        if item_is_plan and item.get("warranty_plan"):
            warranty_items.append({
                "warranty_plan": item.get("warranty_plan"),
                "for_item_code": item.get("for_item_code"),
                "serial_no": (
                    item.get("for_serial_no")
                    or item.get("customer_imei")
                    or item.get("serial_no")),
                "price": flt(item.get("rate")),
                "is_vas": item_is_vas,
                "external_intent": 1 if item.get("customer_imei") else 0,
            })

    if cint(is_free_sale):
        if not free_sale_approved_by:
            frappe.throw(_("Free sale requires manager approval."), title=_("Free Sale Not Approved"))

        # Bind the approval to THIS cart: recompute the hash from the live cart
        # and only accept an approval whose stored cart_hash matches. Without
        # this, an approved free-sale request (single-use, but not cart-bound)
        # could be replayed on a *different* invoice/cart.
        from ch_pos.api.free_sale_api import compute_cart_hash
        expected_hash = compute_cart_hash(customer, items)

        candidates = []
        if free_sale_approval_name:
            candidates = frappe.get_all(
                "CH Free Sale Approval",
                filters={"name": free_sale_approval_name, "status": "Approved", "used": 0},
                fields=["name", "cart_hash", "customer"], limit=1)
        if not candidates:
            candidates = frappe.get_all(
                "CH Free Sale Approval",
                filters={"requested_by": frappe.session.user, "status": "Approved", "used": 0},
                fields=["name", "cart_hash", "customer"],
                order_by="modified desc", limit=20)

        approval_name = None
        for c in candidates:
            # cart_hash is the authoritative binding. Legacy approvals created
            # before this field existed carry an empty hash — accept those only
            # when the customer matches (they still burn via the used flag).
            if c.cart_hash:
                if c.cart_hash == expected_hash:
                    approval_name = c.name
                    break
            elif (c.customer or "") == (customer or ""):
                approval_name = c.name
                break

        if not approval_name:
            frappe.throw(
                _("No approved free-sale authorization matches this cart. "
                  "Please re-request approval for the current items."),
                title=_("Free Sale Not Approved"))

        frappe.db.sql(
            "UPDATE `tabCH Free Sale Approval` SET used=1, used_in_invoice=%s, modified=NOW() WHERE name=%s AND used=0",
            (inv.name or "pending", approval_name))
        if frappe.db.sql("SELECT ROW_COUNT()")[0][0] == 0:
            frappe.throw(_("Free Sale Approval {0} already used.").format(approval_name))

        inv.custom_is_free_sale = 1
        inv.custom_free_sale_reason = (free_sale_reason or "")[:200]
        inv.custom_free_sale_approved_by = (free_sale_approved_by or "")[:140]
        if free_sale_approved_at:
            inv.custom_free_sale_approved_at = free_sale_approved_at

        default_mop = "Cash"
        for pm in profile.payments or []:
            if cint(pm.default):
                default_mop = pm.mode_of_payment
                break
        inv.append("payments", {"mode_of_payment": default_mop, "amount": 0})

    elif payments:
        if isinstance(payments, str):
            payments = frappe.parse_json(payments)
        if not payments:
            frappe.throw(_("At least one payment mode is required"))

        _valid_mops = set(frappe.db.get_all("Mode of Payment", pluck="name"))
        for p in payments:
            mop_name = p.get("mode_of_payment") or ""
            if mop_name not in _valid_mops:
                continue
            p_row = {"mode_of_payment": mop_name, "amount": flt(p.get("amount", 0))}
            for src_key, dest_key in (
                ("upi_transaction_id", "custom_upi_transaction_id"),
                ("card_reference", "custom_card_reference"),
                ("card_last_four", "custom_card_last_four"),
                ("finance_provider", "custom_finance_provider"),
                ("finance_approval_id", "custom_finance_approval_id"),
                ("gateway_provider", "custom_gateway_provider"),
                ("payment_machine", "custom_payment_machine"),
                ("gateway_order_id", "custom_gateway_order_id"),
                ("gateway_status", "custom_gateway_status"),
            ):
                if p.get(src_key):
                    p_row[dest_key] = p[src_key]
            if p.get("finance_tenure"):
                p_row["custom_finance_tenure"] = cint(p["finance_tenure"])
            if flt(p.get("finance_down_payment")):
                p_row["custom_finance_down_payment"] = flt(p["finance_down_payment"])
            inv.append("payments", p_row)

        if not inv.get("payments"):
            default_mop = "Cash"
            for pm in profile.payments or []:
                if cint(pm.default):
                    default_mop = pm.mode_of_payment
                    break
            inv.append("payments", {"mode_of_payment": default_mop, "amount": 0})

    elif mode_of_payment:
        inv.append("payments", {"mode_of_payment": mode_of_payment, "amount": flt(amount_paid)})
    else:
        frappe.throw(_("Payment mode is required"))

    if cint(is_credit_sale) and not cint(is_free_sale):
        inv.custom_is_credit_sale = 1
        _credit_days = cint(credit_days) or 30
        terms_map = {"Net 15": 15, "Net 30": 30, "Net 45": 45, "Net 60": 60, "Net 90": 90}
        if credit_terms and credit_terms in terms_map:
            _credit_days = terms_map[credit_terms]
        inv.custom_credit_days = _credit_days
        inv.custom_credit_terms = credit_terms or "Custom"
        _base_date = str(inv.posting_date) if inv.posting_date else nowdate()
        inv.due_date = frappe.utils.add_days(_base_date, _credit_days)
        reminder_date = frappe.utils.add_days(inv.due_date, -5)
        if str(reminder_date) < _base_date:
            reminder_date = _base_date
        inv.custom_credit_reminder_date = reminder_date
        if credit_reference:
            inv.custom_credit_reference = str(credit_reference)[:140]
        if credit_notes:
            inv.custom_credit_notes = str(credit_notes)[:500]
        if flt(credit_interest_rate) > 0:
            inv.custom_credit_interest_rate = flt(credit_interest_rate)
        if cint(credit_grace_period) > 0:
            inv.custom_credit_grace_period = cint(credit_grace_period)
        if flt(credit_partial_payment) > 0:
            inv.custom_credit_partial_payment = flt(credit_partial_payment)
        if credit_approved_by:
            inv.custom_credit_approved_by = str(credit_approved_by)[:140]

    if flt(additional_discount_percentage) > 0:
        inv.additional_discount_percentage = flt(additional_discount_percentage)
    elif flt(additional_discount_amount) > 0:
        inv.discount_amount = flt(inv.discount_amount or 0) + flt(additional_discount_amount)

    if discount_reason:
        inv.custom_discount_reason = discount_reason
    if discount_authorized_by and frappe.db.has_column("Sales Invoice", "custom_discount_authorized_by"):
        inv.custom_discount_authorized_by = discount_authorized_by

    if coupon_code:
        doc_name = frappe.db.get_value("Coupon Code", {"coupon_code": coupon_code}, "name")
        if not doc_name and frappe.db.exists("Coupon Code", coupon_code):
            doc_name = coupon_code
        if doc_name:
            inv.custom_coupon_code = doc_name
            if flt(coupon_discount_amount) > 0:
                inv.discount_amount = flt(inv.discount_amount or 0) + flt(coupon_discount_amount)

    if voucher_code and flt(voucher_amount) > 0:
        inv.discount_amount = flt(inv.discount_amount or 0) + flt(voucher_amount)
        inv.custom_voucher_code = voucher_code
        inv.custom_voucher_amount = flt(voucher_amount)

    if cint(redeem_loyalty_points):
        inv.redeem_loyalty_points = 1
        inv.loyalty_points = cint(loyalty_points)
        inv.loyalty_amount = flt(loyalty_amount)

    if flt(bank_offer_discount) > 0:
        inv.discount_amount = flt(inv.discount_amount or 0) + flt(bank_offer_discount)
        if bank_offer_name:
            inv.custom_bank_offer_name = bank_offer_name
        inv.custom_bank_offer_discount = flt(bank_offer_discount)

    if sales_executive:
        inv.custom_sales_executive = sales_executive
        sales_person = frappe.db.get_value("POS Executive", sales_executive, "sales_person")
        if sales_person:
            inv.append("sales_team", {
                "sales_person": sales_person,
                "allocated_percentage": 100,
            })

    if cint(is_free_sale):
        inv.custom_ch_sale_type = "Free Sale"
    elif sale_type:
        inv.custom_ch_sale_type = sale_type
    if sale_sub_type:
        inv.custom_ch_sale_sub_type = sale_sub_type
    if sale_reference:
        inv.custom_ch_sale_reference = sale_reference

    if original_invoice:
        orig = frappe.db.get_value(
            "Sales Invoice", original_invoice,
            ["name", "customer", "docstatus", "is_return", "company"], as_dict=True)
        if orig and orig.docstatus == 1 and not orig.is_return:
            inv.custom_original_invoice = original_invoice
            if original_invoice_reason:
                inv.custom_original_invoice_reason = original_invoice_reason
            elif cint(is_free_sale):
                inv.custom_original_invoice_reason = "Late Free Gift"

    if client_request_id:
        inv.custom_client_request_id = str(client_request_id)[:140]

    inv.flags.ignore_permissions = True
    inv.flags.ignore_pricing_rule = True
    inv.flags.disable_rounded_total = True

    try:
        # 1. INSERT
        inv.insert(ignore_permissions=True)

        # 2. Force tax rows from template
        if template_name:
            _force_insert_tax_rows(inv.name, template_name)

        # 3. Item-level calc
        totals = _write_item_calculations(inv.name)

        # 4. Payment rounding
        _is_finance_sale = sale_type and (
            "finance" in (sale_type or "").lower()
            or "emi" in (sale_type or "").lower())
        if (not cint(is_free_sale) and not cint(is_credit_sale)
                and not _is_finance_sale):
            rt = flt(totals["grand_total"] if totals else 0)
            total_paid = sum(flt(p.amount) for p in inv.payments)
            rounding_diff = round(rt) - total_paid

            if abs(rounding_diff) > 0.001:
                for p in inv.payments:
                    if flt(p.amount) > 0:
                        frappe.db.set_value(
                            "Sales Invoice Payment", p.name,
                            {"amount": flt(p.amount) + rounding_diff,
                             "base_amount": flt(p.base_amount or 0) + rounding_diff},
                            update_modified=False)
                        break
                frappe.db.set_value(
                    "Sales Invoice", inv.name,
                    {"paid_amount": round(rt), "base_paid_amount": round(rt)},
                    update_modified=False)

        # 4b. Sync header totals to the now-zeroed tax rows so submit()'s GL
        # balance check passes (see _sync_header_totals_pre_submit docstring).
        _sync_header_totals_pre_submit(inv.name, totals)

        # 5. SUBMIT
        inv.reload()
        inv.workflow_state = "Approved"
        if hasattr(inv, "custom_si_approval_state"):
            inv.custom_si_approval_state = "Approved"
        inv.flags.ignore_validate = True
        inv.flags.ignore_validate_update_after_submit = True
        inv.submit()

        # 6. Force docstatus
        frappe.db.sql(
            "UPDATE `tabSales Taxes and Charges` SET docstatus = 1 WHERE parent = %s AND docstatus = 0",
            (inv.name,))
        frappe.db.sql(
            "UPDATE `tabSales Invoice Item` SET docstatus = 1 WHERE parent = %s AND docstatus = 0",
            (inv.name,))
        frappe.db.commit()

        # 7. 🔑 Write tax_amount + header + breakup (HARDCODED taxable/2 split)
        _write_tax_rows_and_header(inv.name, totals)

        # 8. Rewrite GL to balance
        _rewrite_gl_entries(inv.name)

        frappe.clear_document_cache("Sales Invoice", inv.name)

    except Exception:
        if inv.name and frappe.db.exists("Sales Invoice", inv.name):
            try:
                _doc = frappe.get_doc("Sales Invoice", inv.name)
                if _doc.docstatus == 1:
                    _doc.flags.ignore_permissions = True
                    _doc.flags.ignore_validate = True
                    if hasattr(_doc, "custom_cancel_reason"):
                        _doc.custom_cancel_reason = "System: auto-rollback"
                    _doc.cancel()
                frappe.delete_doc("Sales Invoice", inv.name,
                                  force=True, ignore_permissions=True)
            except Exception:
                frappe.log_error(frappe.get_traceback(),
                                 f"Draft SI cleanup failed for {inv.name}")
        raise

    if exchange_assessment:
        frappe.db.set_value("Buyback Assessment", exchange_assessment,
                            {"linked_pos_invoice": inv.name}, update_modified=False)

    for _exc_name in exception_request_doc_map.keys():
        try:
            frappe.db.set_value("CH Exception Request", _exc_name,
                                "pos_invoice", inv.name, update_modified=False)
        except Exception:
            pass

    if warranty_claim:
        try:
            frappe.db.set_value("CH Warranty Claim", warranty_claim,
                                {"processing_fee_invoice": inv.name,
                                 "processing_fee_status": "Paid"},
                                update_modified=False)
        except Exception:
            pass

    if buyback_order and frappe.db.exists("Buyback Order", buyback_order):
        try:
            frappe.db.set_value("Buyback Order", buyback_order,
                                {"sales_invoice": inv.name, "status": "Closed"},
                                update_modified=False)
        except Exception:
            pass

    frappe.db.commit()

    # Stamp CSV import batch hash for dedup on re-import
    if frappe.flags.in_import and isinstance(items, (list, tuple)):
        try:
            from ch_pos.api.import_dedup import stamp_import_batch_hash
            stamp_import_batch_hash(inv.name, items)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Failed to stamp import batch hash on {inv.name}",
            )

    final = frappe.db.get_value(
        "Sales Invoice", inv.name,
        ["grand_total", "rounded_total", "paid_amount",
         "net_total", "total_taxes_and_charges"],
        as_dict=True)

    si_exempted_field = _detect_field(
        "Sales Invoice Item", ["custom_exempted_value", "exempted_value"])
    verify_fields = ["name", "item_code", "rate", "qty", "amount", "taxable_value"]
    if si_exempted_field:
        verify_fields.append(si_exempted_field)

    items_in_db = frappe.db.get_all(
        "Sales Invoice Item",
        filters={"parent": inv.name},
        fields=verify_fields)
    taxes_in_db = frappe.db.get_all(
        "Sales Taxes and Charges",
        filters={"parent": inv.name},
        fields=["account_head", "rate", "tax_amount",
                "included_in_print_rate", "docstatus"],
        order_by="idx")

    return {
        "name": inv.name,
        "grand_total": final.grand_total if final else 0,
        "rounded_total": final.rounded_total if final else 0,
        "paid_amount": final.paid_amount if final else 0,
        "net_total": final.net_total if final else 0,
        "total_taxes": final.total_taxes_and_charges if final else 0,
        "status": "created",
        "items_in_db": items_in_db,
        "taxes_in_db": taxes_in_db,
    }


def _enforce_token_linkage(pos_profile, kiosk_token):
    return






































    











    
























































    def _infer_device_serial(wi):
        """Infer the linked device serial/IMEI from invoice rows when missing."""
        if wi.get("serial_no"):
            return wi.get("serial_no")

        candidates = []
        target_item_code = wi.get("for_item_code")
        for inv_item in device_items_on_inv:
            if target_item_code and inv_item.item_code != target_item_code:
                continue
            serial_no = (getattr(inv_item, "serial_no", None) or "").strip()
            if serial_no:
                candidates.append(serial_no)

        if len(candidates) == 1:
            return candidates[0]

        if not target_item_code and len(device_items_on_inv) == 1:
            serial_no = (getattr(device_items_on_inv[0], "serial_no", None) or "").strip()
            if serial_no:
                return serial_no

        return wi.get("serial_no")

    def _resolve_item_from_imei(serial_no):
        serial_no = (serial_no or "").strip()
        if not serial_no:
            return ""

        item_code = frappe.db.get_value("Serial No", serial_no, "item_code")
        if item_code:
            return item_code

        return (
            frappe.db.get_value("CH Serial Lifecycle", {"serial_no": serial_no}, "item_code")
            or frappe.db.get_value("CH Serial Lifecycle", {"imei_number": serial_no}, "item_code")
            or frappe.db.get_value("CH Serial Lifecycle", {"imei_number_2": serial_no}, "item_code")
            or ""
        )

    def _find_device_price(invoice_name, item_code=None, serial_no=None):
        if not invoice_name:
            return 0

        rows = frappe.get_all(
            "Sales Invoice Item",
            filters={"parent": invoice_name, "parenttype": "Sales Invoice"},
            fields=["item_code", "rate", "serial_no", "idx"],
            order_by="idx asc",
        )
        serial_no = (serial_no or "").strip()

        if serial_no:
            for row in rows:
                if serial_no in (row.get("serial_no") or ""):
                    return flt(row.get("rate"))

        if item_code:
            for row in rows:
                if row.get("item_code") == item_code:
                    return flt(row.get("rate"))

        for row in rows:
            row_item = row.get("item_code")
            if row_item in plan_service_items:
                continue
            if cint(frappe.db.get_value("Item", row_item, "is_stock_item")):
                return flt(row.get("rate"))

        return 0

    def _prepare_active_plan_link(wi):
        plan_doc = frappe.get_cached_doc("CH Warranty Plan", wi.get("warranty_plan"))
        serial_no = (wi.get("serial_no") or "").strip()

        if not wi.get("for_item_code") and serial_no:
            resolved_item = _resolve_item_from_imei(serial_no)
            if resolved_item:
                wi["for_item_code"] = resolved_item

        if wi.get("for_item_code"):
            wi["_plan_doc"] = plan_doc
            return

        if not cint(plan_doc.get("allow_external_device")):
            frappe.throw(
                frappe._("Plan {0} cannot be sold for customer-provided IMEI {1}. "
                         "Select a device from the bill/inventory, or enable external IMEI on the plan.").format(
                    wi.get("warranty_plan"), serial_no or frappe._("not provided")
                ),
                title=frappe._("External IMEI Not Allowed"),
            )

        if not plan_doc.get("external_device_item"):
            frappe.throw(
                frappe._("Plan {0} allows external IMEI but has no External Device Item configured.").format(
                    wi.get("warranty_plan")
                ),
                title=frappe._("External Device Setup Missing"),
            )

        # Market-standard parity: every service contract activation MUST
        # capture the covered asset identifier. Oracle Service Contracts
        # rejects an activation with no INSTANCE_NUMBER on the covered
        # line; SAP CRM Service Contract requires an IBase component;
        # MS Dynamics Field Service requires an Entitlement → Customer
        # Asset link before a Work Order can be opened. Without an IMEI,
        # downstream claims have no way to identify the covered device.
        if not serial_no:
            frappe.throw(
                frappe._("Plan {0} is being sold for a customer-provided device but no Customer Device IMEI was captured. "
                         "Re-open the VAS dialog and enter the IMEI under \"Customer-Provided Device\".").format(
                    wi.get("warranty_plan")
                ),
                title=frappe._("Customer Device IMEI Required"),
            )

        if cint(plan_doc.purchase_window_hours or 0) > 0 and not original_invoice:
            frappe.throw(
                frappe._("Plan {0} has a {1}-hour purchase window. "
                         "For a customer-provided IMEI, link the original invoice or use a plan with no purchase window.").format(
                    wi.get("warranty_plan"), cint(plan_doc.purchase_window_hours)
                ),
                title=frappe._("Purchase Window Proof Required"),
            )

        wi["for_item_code"] = plan_doc.external_device_item
        wi["is_external_device"] = 1
        wi["external_device_source"] = "POS Customer-Provided IMEI"
        wi["_plan_doc"] = plan_doc

    for wi in warranty_items:
        # Auto-infer for_item_code: if not sent and exactly one device in cart, use it.
        # EXCEPTION: when the cart row carried ``customer_imei`` (explicit
        # external-device intent), do NOT bind to the in-cart phone — the
        # customer is buying VAS for THEIR OWN device, not the new phone.
        if (
            not wi.get("for_item_code")
            and len(device_items_on_inv) == 1
            and not wi.get("external_intent")
        ):
            wi["for_item_code"] = device_items_on_inv[0].item_code

        # Same exception for serial inference — don't pull the in-cart phone
        # IMEI onto an external-device plan.
        if not wi.get("external_intent"):
            inferred_serial = _infer_device_serial(wi)
            if inferred_serial:
                wi["serial_no"] = inferred_serial

        _prepare_active_plan_link(wi)

        # INT-3 fix: Throw error instead of silently skipping active VAS plan creation
        if not wi.get("for_item_code"):
            frappe.throw(
                frappe._("Cannot create warranty plan record: the device item (for_item_code) "
                         "could not be determined for warranty plan {0} on invoice {1}. "
                         "Please ensure each warranty/VAS item is linked to a device.").format(
                    wi.get("warranty_plan"), inv.name),
                title=frappe._("Active VAS Plans Creation Failed"),
            )

        # Look up device purchase price from the same invoice
        device_price = _find_device_price(
            inv.name,
            item_code=wi["for_item_code"],
            serial_no=wi.get("serial_no"),
        )
        if not device_price and original_invoice:
            device_price = _find_device_price(
                original_invoice,
                item_code=None if wi.get("is_external_device") else wi["for_item_code"],
                serial_no=wi.get("serial_no"),
            )

        try:
            sp = _create_active_plan(
                warranty_plan=wi["warranty_plan"],
                customer=customer,
                item_code=wi["for_item_code"],
                company=profile.company,
                sales_invoice=inv.name,
                plan_price=wi["price"],
                serial_no=wi.get("serial_no"),
                device_purchase_price=device_price,
                is_external_device=wi.get("is_external_device"),
                external_device_source=wi.get("external_device_source"),
                original_invoice=original_invoice,
            )
            if sp:
                sold_plans.append(sp.name)
                wi["_sold_plan"] = sp.name  # carry forward for voucher linkage
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Active VAS Plans creation failed for {inv.name} / {wi.get('warranty_plan')}"
            )
            raise

        # ── Authoritative attach log (plan ↔ IMEI ↔ invoice) ──────────
        # Write a definitive "Accepted" CH Attach Log entry for every
        # warranty / VAS plan actually booked on this invoice. This is
        # the SoR the analytics reports read from and it answers "what
        # plan is attached to what IMEI on which invoice" — even when
        # the pre-invoice "Offered" xcall was silently dropped (offline
        # POS) or lost its ``pos_invoice`` back-fill. Non-fatal on
        # failure — the sale is already booked.
        try:
            _log = frappe.new_doc("CH Attach Log")
            _log.pos_invoice = inv.name
            _log.pos_profile = profile.name
            _log.item_code = wi.get("for_item_code") or None
            _log.attach_type = "VAS" if wi.get("is_vas") else "Warranty"
            _log.action = "Accepted"
            _log.plan_code = wi["warranty_plan"]
            _log.serial_no = (wi.get("serial_no") or "").strip()
            _log.offered_by = frappe.session.user
            _log.offered_at = now_datetime()
            _log.flags.ignore_permissions = True
            _log.insert()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"CH Attach Log write failed for {inv.name} / {wi.get('warranty_plan')}"
            )

    # ── VAS Voucher Generation ────────────────────────────────────────────────
    # Read voucher rules from CH VAS Settings (configurable face value, validity,
    # item-group restriction, etc.)  Each VAS item generates
    # floor(price ÷ face_value) single-use vouchers linked back to the active VAS plan.
    generated_vouchers = []
    try:
        from ch_item_master.ch_item_master.voucher_api import issue_voucher
        vas_cfg = frappe.get_cached_doc("CH VAS Settings")
        voucher_face_value = flt(vas_cfg.vas_voucher_amount) or 2000
        voucher_validity   = cint(vas_cfg.vas_voucher_validity_days) or 180
        voucher_item_group = vas_cfg.vas_voucher_item_group or None
        voucher_channel    = vas_cfg.vas_voucher_channel or None

        customer_phone = frappe.db.get_value("Customer", customer, "mobile_no") or ""
        customer_email = frappe.db.get_value("Customer", customer, "email_id") or ""
        for wi in warranty_items:
            if not wi.get("is_vas"):
                continue
            vas_price = flt(wi["price"])
            voucher_count = int(vas_price // voucher_face_value)
            for _ in range(voucher_count):
                v = issue_voucher(
                    voucher_type="VAS Voucher",
                    amount=voucher_face_value,
                    company=profile.company,
                    customer=customer if customer != "Walk-in Customer" else None,
                    phone=customer_phone or None,
                    valid_days=voucher_validity,
                    source_type="Purchase",
                    source_document=inv.name,
                    reason=f"VAS purchase on {inv.name}",
                    single_use=1,
                    applicable_channel=voucher_channel,
                    applicable_item_group=voucher_item_group,
                    sold_plan=wi.get("_sold_plan"),
                )
                generated_vouchers.append(v)
        if generated_vouchers and (customer_email or customer_phone):
            _send_voucher_email(customer, customer_email, customer_phone, generated_vouchers, inv.name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"VAS voucher generation failed for {inv.name}")

    # Create incentive ledger entries for the sales executive
    incentive_total = 0
    incentive_warning = ""
    if sales_executive:
        try:
            incentive_total = _create_incentive_entries(
                invoice=inv,
                pos_executive=sales_executive,
                transaction_type="Sale",
            )
            if _missing_incentive_setup(inv, sales_executive):
                incentive_warning = frappe._(
                    "Incentive setup missing for this executive/company. "
                    "Please configure active POS Incentive Slabs."
                )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Incentive ledger failed for {inv.name}")
            incentive_warning = frappe._("Incentive calculation failed. Please check Error Log.")

    # ── Audit logging (best-effort, never blocks sale) ────────────────────────
    try:
        from ch_pos.audit import log_business_event
        _store = frappe.get_cached_value("POS Profile", pos_profile, "warehouse")
        _company = inv.company

        # Discount override audit
        if flt(additional_discount_percentage) > 0 or flt(additional_discount_amount) > 0:
            log_business_event(
                event_type="Discount Override",
                ref_doctype="Sales Invoice", ref_name=inv.name,
                before="0",
                after=f"{additional_discount_percentage}% / ₹{additional_discount_amount}",
                remarks=f"Reason: {discount_reason or 'N/A'}",
                store=_store, company=_company,
            )

        # Exchange conversion audit
        if exchange_assessment:
            log_business_event(
                event_type="Exchange Conversion",
                ref_doctype="Sales Invoice", ref_name=inv.name,
                before=exchange_assessment,
                after=f"Credit: ₹{exchange_credit}",
                store=_store, company=_company,
            )

        # Voucher redemption audit
        if voucher_code and flt(voucher_redeemed) > 0:
            log_business_event(
                event_type="Voucher Redemption",
                ref_doctype="Sales Invoice", ref_name=inv.name,
                before=voucher_code,
                after=f"₹{voucher_redeemed} redeemed",
                store=_store, company=_company,
            )

        # Exception applied audit
        if exception_request:
            log_business_event(
                event_type="Exception Applied",
                ref_doctype="Sales Invoice", ref_name=inv.name,
                before=exception_request,
                after="Linked to invoice",
                store=_store, company=_company,
            )

        # Warranty claim processing fee audit
        if warranty_claim:
            log_business_event(
                event_type="Warranty Fee Collected",
                ref_doctype="Sales Invoice", ref_name=inv.name,
                before=warranty_claim,
                after="Processing fee invoiced",
                store=_store, company=_company,
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Audit log failed for Sales Invoice {inv.name}")

    return {
        "name": inv.name,
        "grand_total": inv.grand_total,
        "sold_plans": sold_plans,
        "active_plans": sold_plans,
        "incentive_earned": incentive_total,
        "incentive_warning": incentive_warning,
        "voucher_redeemed": voucher_redeemed,
        "generated_vouchers": generated_vouchers,
        "surplus_refund_amount": flt(locals().get("_surplus_amt") or 0),
        "surplus_refund_journal_entry": locals().get("surplus_refund_je"),
    }


def _send_voucher_email(customer, email, phone, vouchers, invoice_name):
    """Send VAS vouchers to customer via email."""
    if not email:
        return
    codes_html = "".join(
        f"<tr><td style='padding:8px 16px;font-size:18px;font-family:monospace;letter-spacing:2px;background:#f3f4f6;border-radius:6px;text-align:center'>"
        f"<b>{v['voucher_code']}</b></td>"
        f"<td style='padding:8px 12px;color:#6b7280'>₹{int(v['balance'])} · Valid 1 year</td></tr>"
        for v in vouchers
    )
    invoice_url = frappe.utils.get_url_to_form("Sales Invoice", invoice_name)
    html = f"""
    <div style='font-family:Segoe UI,Arial,sans-serif;max-width:620px;margin:0 auto;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>
        <div style='background:#0f172a;color:#fff;padding:14px 18px;font-size:14px;font-weight:600'>GoGizmo Retail Pvt Ltd</div>
        <div style='padding:18px'>
            <h2 style='margin:0 0 10px;color:#111827'>Your VAS Vouchers Are Ready</h2>
            <p>Thank you for purchasing a VAS plan against invoice <b>{invoice_name}</b>.</p>
            <p>You have earned <b>{len(vouchers)} × ₹500 voucher(s)</b> for accessory redemption.</p>
            <table style='width:100%;border-collapse:collapse;margin:16px 0'>
                {codes_html}
            </table>
            <p style='color:#4b5563;font-size:13px'>
                Each ₹500 voucher gives you ₹125 off when purchasing accessories.
                Visit any GoGizmo store and share voucher code at checkout.
            </p>
            <p style='margin-top:18px'>
                <a href='{invoice_url}' style='background:#0b57d0;color:#fff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Invoice</a>
            </p>
        </div>
    </div>
    """
    try:
        frappe.sendmail(
            recipients=[email],
            subject=f"Your VAS Vouchers — {invoice_name}",
            message=html,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Voucher email failed for {invoice_name}")


def _create_active_plan(warranty_plan, customer, item_code, company, sales_invoice, plan_price,
                        serial_no=None, device_purchase_price=0, is_external_device=0,
                        external_device_source=None, original_invoice=None):
    """Create an Active VAS Plans record (shown as Active VAS Plans in UI) when a warranty is sold via POS.

    Uses the standard Frappe document lifecycle (insert → submit) so that
    the Active VAS Plans controller's validate/on_submit hooks run properly.
    Enforces purchase_window_hours from the plan master.
    """
    plan_doc = frappe.get_cached_doc("CH Warranty Plan", warranty_plan)

    # ── 24-hour purchase window validation ────────────────────────────────
    # Protection Plans / VAS must be bought within N hours of device purchase.
    # Post-Repair Warranty is exempt (issued by GoFix after service).
    purchase_window = plan_doc.purchase_window_hours or 0
    if (purchase_window > 0
            and plan_doc.plan_type not in ("Post-Repair Warranty", "Own Warranty")
            and serial_no):
        # Find the most recent sale of this serial to this customer
        device_sold_at = frappe.db.get_value(
            "Sales Invoice Item",
            {"serial_no": ["like", f"%{serial_no}%"], "docstatus": 1,
             "parenttype": "Sales Invoice"},
            "creation",
            order_by="creation desc",
        )
        if not device_sold_at and original_invoice:
            device_sold_at = frappe.db.get_value("Sales Invoice", original_invoice, "creation")

        if device_sold_at:
            from frappe.utils import time_diff_in_hours, now_datetime as _now
            hours_since = time_diff_in_hours(_now(), device_sold_at)
            if hours_since > purchase_window:
                frappe.throw(
                    _("This plan must be purchased within {0} hours of device sale. "
                      "Device was sold {1:.1f} hours ago.").format(
                        purchase_window, hours_since),
                    title=_("Purchase Window Expired"),
                )
        elif cint(is_external_device):
            frappe.throw(
                _("This plan must be purchased within {0} hours of device sale. "
                  "Customer-provided IMEI {1} has no verifiable original sale.").format(
                    purchase_window, serial_no or ""
                ),
                title=_("Purchase Window Proof Required"),
            )

    today = nowdate()
    sp = frappe.new_doc("Active VAS Plans")
    sp.warranty_plan = warranty_plan
    sp.customer = customer
    sp.item_code = item_code
    sp.serial_no = serial_no
    sp.company = company
    sp.start_date = today
    sp.end_date = add_months(today, plan_doc.duration_months or 12)
    sp.status = "Active"
    sp.sales_invoice = sales_invoice
    sp.plan_price = plan_price
    sp.max_claims = plan_doc.max_claims or 1
    sp.deductible_amount = flt(plan_doc.deductible_amount)
    sp.claims_per_year = plan_doc.claims_per_year or 0
    sp.device_purchase_price = flt(device_purchase_price)
    sp.max_coverage_value = flt(device_purchase_price) if flt(device_purchase_price) > 0 else 0
    sp.is_external_device = cint(is_external_device)
    sp.external_device_source = external_device_source or ""
    if cint(is_external_device):
        sp.remarks = _("Customer-provided IMEI sold via POS. Not attached to GoGizmo device inventory.")
    sp.sold_by = frappe.session.user
    sp.insert(ignore_permissions=True)
    sp.submit()

    # Link active VAS plan back to CH Customer Device — create the device
    # record on-the-fly if one does not exist yet. Previously we only
    # linked when a CH Customer Device row already existed, which meant
    # POS-sold VAS plans on brand-new (or IMEI-migrated) devices left the
    # customer-device ledger blank and the plan was invisible in the
    # customer 360 / warranty dashboards.
    if serial_no:
        cd_name = frappe.db.get_value("CH Customer Device", {"serial_no": serial_no})
        if not cd_name:
            try:
                item_name = frappe.db.get_value("Item", item_code, "item_name") or item_code
                item_brand = frappe.db.get_value("Item", item_code, "brand")
                cd = frappe.new_doc("CH Customer Device")
                cd.customer = customer
                cd.serial_no = serial_no
                cd.item_code = item_code
                cd.item_name = item_name
                if item_brand:
                    cd.brand = item_brand
                cd.company = company
                cd.imei_number = serial_no  # POS mostly deals with IMEI-serialised phones
                cd.current_status = "Sold"
                cd.purchase_date = today
                cd.purchase_invoice = original_invoice or sales_invoice
                cd.purchase_company = company
                cd.purchase_price = flt(device_purchase_price)
                cd.active_warranty_plan = sp.name
                cd.warranty_status = "In Warranty"
                cd.warranty_expiry = sp.end_date
                cd.warranty_plan_name = frappe.db.get_value(
                    "CH Warranty Plan", warranty_plan, "plan_name"
                ) or warranty_plan
                cd.warranty_months = plan_doc.duration_months or 0
                cd.flags.ignore_permissions = True
                cd.insert(ignore_permissions=True)
            except Exception:
                # Never block a warranty sale on the customer-device ledger —
                # log for audit; the ledger can be reconciled by a nightly job.
                frappe.log_error(
                    frappe.get_traceback(),
                    f"POS: CH Customer Device auto-create failed for serial {serial_no}",
                )
        else:
            frappe.db.set_value(
                "CH Customer Device", cd_name, "active_warranty_plan", sp.name
            )

    return sp


@frappe.whitelist()
def get_warranty_plans(item_code, item_group=None, brand=None) -> dict:
    """Return active warranty plans (Own / Extended) applicable to an item."""
    today = nowdate()
    filters = {
        "status": "Active",
        "plan_type": ["in", ["Own Warranty", "Extended Warranty"]],
    }
    # Check channel applicability later; first get all active plans
    plans = frappe.get_all(
        "CH Warranty Plan",
        filters=filters,
        fields=[
            "name", "plan_name", "plan_type", "service_item",
            "duration_months", "price", "pricing_mode",
            "percentage_value", "coverage_description", "brand",
        ],
    )

    applicable = []
    for plan in plans:
        # Filter by brand if plan specifies one
        if plan.brand and brand and plan.brand != brand:
            continue
        # Filter by date validity
        valid_from = frappe.db.get_value("CH Warranty Plan", plan.name, "valid_from")
        valid_to = frappe.db.get_value("CH Warranty Plan", plan.name, "valid_to")
        if valid_from and str(valid_from) > today:
            continue
        if valid_to and str(valid_to) < today:
            continue

        # Calculate actual price
        if plan.pricing_mode == "Percentage of Device Price":
            device_price = flt(frappe.db.get_value(
                "CH Item Price",
                {"item_code": item_code, "channel": "POS", "status": "Active"},
                "selling_price",
            ))
            plan.price = flt(device_price * flt(plan.percentage_value) / 100)

        applicable.append(plan)

    return applicable


@frappe.whitelist()
def find_previous_device_invoice(phone, imei) -> dict:
    """Strict resolver: find the prior submitted Sales Invoice that sold this
    device (by IMEI/serial) to the customer matching the given phone number.

    Used by:
      - VAS-after-sale flow: bind the Active VAS Plan back to the original device
        invoice instead of the current VAS invoice.
      - Late free-gift flow: link a free-sale invoice to the device invoice
        so accounts can audit why the gift was issued.

    Args:
        phone: Customer phone number (any format; last 10 digits matched).
        imei: Device IMEI / serial number (exact or substring match against
            ``Sales Invoice Item.serial_no``).

    Returns:
        dict with ``found`` (bool) and, when found:
            customer, customer_name, customer_phone,
            invoice, posting_date, item_code, item_name, serial_no, company.
        When multiple invoices match, the most recent submitted, non-return
        invoice is returned.
    """
    phone = (phone or "").strip()
    imei = (imei or "").strip()
    if not phone or not imei:
        frappe.throw(_("Both phone and IMEI are required"), title=_("API Error"))

    digits_only = "".join(c for c in phone if c.isdigit())
    if len(digits_only) < 10:
        frappe.throw(_("Phone must contain at least 10 digits"), title=_("API Error"))
    phone_suffix = digits_only[-10:]

    # 1. Resolve customer by phone (mobile_no, ch_alternate_phone, or contact)
    customer = frappe.db.get_value(
        "Customer", {"mobile_no": ["like", f"%{phone_suffix}"]}, "name"
    )
    if not customer:
        customer = frappe.db.get_value(
            "Customer", {"ch_alternate_phone": ["like", f"%{phone_suffix}"]}, "name"
        )
    if not customer:
        contact_row = frappe.db.sql(
            """SELECT dl.link_name
                 FROM `tabDynamic Link` dl
                 JOIN `tabContact` c ON c.name = dl.parent
                WHERE dl.link_doctype = 'Customer'
                  AND c.mobile_no LIKE %s
                LIMIT 1""",
            (f"%{phone_suffix}",),
        )
        if contact_row:
            customer = contact_row[0][0]

    if not customer:
        return {
            "found": False,
            "reason": "no_customer",
            "message": _("No customer found for phone {0}").format(phone),
        }

    # 2. Find the most recent submitted, non-return Sales Invoice for this
    #    customer that sold this serial/IMEI.
    rows = frappe.db.sql(
        """SELECT si.name AS invoice,
                  si.posting_date,
                  si.company,
                  si.customer,
                  si.customer_name,
                  sii.item_code,
                  sii.item_name,
                  sii.serial_no
             FROM `tabSales Invoice Item` sii
             JOIN `tabSales Invoice` si ON si.name = sii.parent
            WHERE si.customer = %(customer)s
              AND si.docstatus = 1
              AND si.is_return = 0
              AND sii.serial_no LIKE %(imei_pat)s
            ORDER BY si.posting_date DESC, si.posting_time DESC
            LIMIT 1""",
        {"customer": customer, "imei_pat": f"%{imei}%"},
        as_dict=True,
    )
    if not rows:
        return {
            "found": False,
            "reason": "no_invoice",
            "customer": customer,
            "message": _("No prior sale of IMEI {0} to this customer").format(imei),
        }

    row = rows[0]
    cust_phone = frappe.db.get_value("Customer", customer, "mobile_no") or ""
    return {
        "found": True,
        "customer": row["customer"],
        "customer_name": row["customer_name"],
        "customer_phone": cust_phone,
        "invoice": row["invoice"],
        "posting_date": str(row["posting_date"]) if row["posting_date"] else None,
        "company": row["company"],
        "item_code": row["item_code"],
        "item_name": row["item_name"],
        "serial_no": (row["serial_no"] or "").strip(),
    }


@frappe.whitelist()
def lookup_exchange(assessment=None, imei_serial=None, mobile_no=None, customer=None) -> dict:
    """Find a Buyback Assessment/Order eligible for exchange at POS.

    Returns exchange details or None if nothing found.

    TC_060: status filter must match the set accepted by create_pos_invoice so a
    cashier never sees "No eligible exchange found" for a valid quote-stage assessment.
    'Draft' is excluded (unquoted); 'Quoted' / 'Quote Accepted' are the common
    post-quote statuses and must be included.

    If `customer` is provided we enforce ownership — prevents looking up another
    customer's exchange credit at a busy multi-counter store.
    """
    assessment_name = None

    # Must match _VALID_EXCHANGE_STATUSES in create_pos_invoice exactly.
    valid_statuses = ["Quoted", "Quote Accepted", "Submitted", "Inspection Created"]

    filters_base = {"status": ["in", valid_statuses], "linked_pos_invoice": ["is", "not set"]}

    if assessment:
        assessment_name = assessment
    elif imei_serial:
        assessment_name = frappe.db.get_value(
            "Buyback Assessment",
            {**filters_base, "imei_serial": imei_serial},
            "name",
        )
    elif mobile_no:
        mobile_no = validate_indian_phone(mobile_no, "Mobile No")
        assessment_name = frappe.db.get_value(
            "Buyback Assessment",
            {**filters_base, "mobile_no": mobile_no},
            "name",
            order_by="creation desc",
        )

    if not assessment_name:
        return None

    ba = frappe.get_doc("Buyback Assessment", assessment_name)

    # Validate status here too (covers direct assessment-ID lookup where we
    # couldn't pre-filter by status in the dict above).
    if ba.status not in valid_statuses:
        return None

    # Customer ownership guard — if caller passes the current POS customer,
    # reject assessments that belong to a different customer.
    if customer and ba.customer and ba.customer != customer:
        frappe.throw(
            frappe._("Exchange credit {0} belongs to a different customer and cannot be applied "
                     "to this transaction.").format(frappe.bold(assessment_name)),
            title=frappe._("Customer Mismatch"),
        )

    # Check for an existing Buyback Order
    order_name = frappe.db.get_value(
        "Buyback Order",
        {"buyback_assessment": assessment_name, "docstatus": 1},
        "name",
    )

    buyback_amount = 0
    condition_grade = None

    if order_name:
        order = frappe.get_doc("Buyback Order", order_name)
        buyback_amount = flt(order.final_price)
        condition_grade = order.condition_grade
    else:
        buyback_amount = flt(ba.quoted_price) or flt(ba.estimated_price)
        condition_grade = ba.estimated_grade

    return {
        "assessment": ba.name,
        "order": order_name,
        "customer": ba.customer,
        "customer_name": ba.customer_name,
        "item_code": ba.item,
        "item_name": ba.item_name,
        "imei_serial": ba.imei_serial,
        "condition_grade": condition_grade,
        "buyback_amount": buyback_amount,
    }


@frappe.whitelist()
def get_vas_plans() -> dict:
    """Return active Value Added Service plans for POS sale."""
    today = nowdate()
    plans = frappe.get_all(
        "CH Warranty Plan",
        filters={
            "status": "Active",
            "plan_type": ["in", ["Value Added Service", "Protection Plan"]],
        },
        fields=[
            "name", "plan_name", "plan_type", "service_item",
            "duration_months", "price", "coverage_description",
        ],
    )

    applicable = []
    for plan in plans:
        valid_from = frappe.db.get_value("CH Warranty Plan", plan.name, "valid_from")
        valid_to = frappe.db.get_value("CH Warranty Plan", plan.name, "valid_to")
        if valid_from and str(valid_from) > today:
            continue
        if valid_to and str(valid_to) < today:
            continue
        applicable.append(plan)

    return applicable


@frappe.whitelist()
def validate_coupon(coupon_code, customer=None, cart_total=0) -> dict:
    """Validate a coupon code and return discount details."""
    cart_total = flt(cart_total)

    coupon = frappe.db.get_value(
        "Coupon Code",
        {"coupon_code": coupon_code},
        ["name", "coupon_name", "pricing_rule", "valid_from", "valid_upto",
         "maximum_use", "used", "coupon_type"],
        as_dict=True,
    )

    if not coupon:
        return {"valid": False, "reason": frappe._("Coupon code not found")}

    today = nowdate()
    if coupon.valid_from and str(coupon.valid_from) > today:
        return {"valid": False, "reason": frappe._("Coupon is not yet active")}
    if coupon.valid_upto and str(coupon.valid_upto) < today:
        return {"valid": False, "reason": frappe._("Coupon has expired")}
    if coupon.maximum_use and coupon.used >= coupon.maximum_use:
        return {"valid": False, "reason": frappe._("Coupon usage limit reached")}

    if not coupon.pricing_rule:
        return {"valid": False, "reason": frappe._("No pricing rule linked to coupon")}

    # POS-13 fix: Validate linked Pricing Rule still exists and is enabled
    if not frappe.db.exists("Pricing Rule", coupon.pricing_rule):
        return {"valid": False, "reason": frappe._("Linked pricing rule no longer exists")}

    pr = frappe.get_cached_doc("Pricing Rule", coupon.pricing_rule)

    if pr.disable:
        return {"valid": False, "reason": frappe._("Linked pricing rule is disabled")}

    # Check minimum amount
    if pr.min_amt and cart_total < flt(pr.min_amt):
        return {"valid": False, "reason": frappe._("Minimum cart amount ₹{0} required").format(pr.min_amt)}

    # Compute discount
    discount_amount = 0
    max_disc = flt(getattr(pr, "max_discount", 0) or 0)
    if flt(pr.discount_percentage) > 0:
        discount_amount = flt(cart_total * flt(pr.discount_percentage) / 100)
        if max_disc and discount_amount > max_disc:
            discount_amount = max_disc
    elif flt(pr.discount_amount) > 0:
        discount_amount = flt(pr.discount_amount)

    if discount_amount <= 0:
        return {"valid": False, "reason": frappe._("Coupon provides no discount for this cart")}

    return {
        "valid": True,
        "coupon_name": coupon.coupon_name,
        "discount_amount": discount_amount,
        "pricing_rule": coupon.pricing_rule,
    }


@frappe.whitelist()
def apply_coupon_or_voucher(code, customer=None, company=None) -> dict:
    """Validate a coupon code or CH Voucher code and return discount details."""
    if not code:
        frappe.throw(frappe._("No code provided"))

    code = code.strip()

    # ── 1. Check CH Voucher first ──────────────────────────────────────────
    voucher = frappe.db.get_value(
        "CH Voucher",
        {"voucher_code": code, "docstatus": 1},
        ["name", "status", "original_amount", "balance",
         "valid_from", "valid_upto", "issued_to"],
        as_dict=True,
    )
    if voucher:
        today = nowdate()
        if voucher.status in ("Fully Used", "Cancelled"):
            frappe.throw(frappe._("Voucher '{0}' has already been fully used").format(code))
        if voucher.status == "Expired" or (voucher.valid_upto and str(voucher.valid_upto) < today):
            frappe.throw(frappe._("Voucher '{0}' has expired").format(code))
        if voucher.valid_from and str(voucher.valid_from) > today:
            frappe.throw(frappe._("Voucher '{0}' is not yet active").format(code))
        balance = flt(voucher.balance)
        if balance <= 0:
            frappe.throw(frappe._("Voucher '{0}' has no remaining balance").format(code))
        return {
            "is_voucher": True,
            "voucher_name": voucher.name,
            "amount": balance,
            "balance": balance,
        }

    # ── 2. Fall back to Coupon Code ────────────────────────────────────────
    coupon = frappe.db.get_value(
        "Coupon Code",
        {"coupon_code": code},
        ["name", "coupon_name", "pricing_rule", "valid_from", "valid_upto",
         "maximum_use", "used", "coupon_type"],
        as_dict=True,
    )
    if not coupon:
        frappe.throw(frappe._("Code '{0}' not found as a voucher or coupon").format(code))

    today = nowdate()
    if coupon.valid_from and str(coupon.valid_from) > today:
        frappe.throw(frappe._("Coupon is not yet active"))
    if coupon.valid_upto and str(coupon.valid_upto) < today:
        frappe.throw(frappe._("Coupon has expired"))
    if coupon.maximum_use and coupon.used >= coupon.maximum_use:
        frappe.throw(frappe._("Coupon usage limit reached"))
    if not coupon.pricing_rule:
        frappe.throw(frappe._("No pricing rule linked to coupon"))

    pr = frappe.get_cached_doc("Pricing Rule", coupon.pricing_rule)
    discount_amount = 0
    if flt(pr.discount_percentage) > 0:
        # Return percentage info; caller computes actual amount against cart total
        return {
            "is_voucher": False,
            "coupon_name": coupon.name,
            "amount": flt(pr.discount_percentage),
            "is_percentage": True,
            "max_discount": flt(getattr(pr, "max_discount", 0) or 0),
            "pricing_rule": coupon.pricing_rule,
        }
    elif flt(pr.discount_amount) > 0:
        discount_amount = flt(pr.discount_amount)
    else:
        frappe.throw(frappe._("Coupon provides no discount"))

    return {
        "is_voucher": False,
        "coupon_name": coupon.name,
        "amount": discount_amount,
        "is_percentage": False,
        "pricing_rule": coupon.pricing_rule,
    }


@frappe.whitelist()
def get_customer_credit_info(customer, company=None) -> dict:
    """Return credit limit, outstanding, payment history and overdue info for a customer."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    if not customer or customer == "Walk-in Customer":
        return None

    credit_limit = 0
    outstanding = 0

    # Check Customer Credit Limit child table first
    if company:
        cl = frappe.db.get_value(
            "Customer Credit Limit",
            {"parent": customer, "parenttype": "Customer", "company": company},
            "credit_limit",
        )
        if cl:
            credit_limit = flt(cl)

    # Fallback: scan credit_limits child table without company filter (ERPNext v15 has no
    # direct credit_limit column on tabCustomer — it lives in the child table only)
    if not credit_limit:
        for cl in (frappe.get_cached_doc("Customer", customer).get("credit_limits") or []):
            if flt(cl.credit_limit):
                credit_limit = flt(cl.credit_limit)
                break

    if not credit_limit:
        return None

    # Get outstanding from GL
    company_filter = f"AND company = {frappe.db.escape(company)}" if company else ""
    outstanding = flt(frappe.db.sql("""
        SELECT SUM(debit - credit) FROM `tabGL Entry`
        WHERE party_type = 'Customer' AND party = %s
          AND is_cancelled = 0
          {company_filter}
    """.format(  # noqa: UP032
        company_filter=company_filter
    ), customer)[0][0] or 0)

    # Overdue invoices count
    overdue_count = cint(frappe.db.count("Sales Invoice", {
        "customer": customer,
        "docstatus": 1,
        "outstanding_amount": [">", 0],
        "due_date": ["<", nowdate()],
        "is_return": 0,
    }))

    # Average payment days (from paid invoices in last 12 months)
    avg_payment_days = 0
    last_payment_date = None
    paid_data = frappe.db.sql("""
        SELECT AVG(DATEDIFF(modified, posting_date)) as avg_days,
               MAX(modified) as last_payment
        FROM `tabSales Invoice`
        WHERE customer = %s AND docstatus = 1 AND outstanding_amount = 0
          AND is_return = 0 AND posting_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
          {company_filter}
    """.format(company_filter=company_filter), customer, as_dict=True)
    if paid_data and paid_data[0].avg_days is not None:
        avg_payment_days = cint(paid_data[0].avg_days)
    if paid_data and paid_data[0].last_payment:
        last_payment_date = str(getdate(paid_data[0].last_payment))

    return {
        "credit_limit": credit_limit,
        "outstanding": outstanding,
        "available": max(0, credit_limit - outstanding),
        "overdue_count": overdue_count,
        "avg_payment_days": avg_payment_days,
        "last_payment_date": last_payment_date,
    }


@frappe.whitelist()
def approve_credit_override(customer, company, manager_pin, override_reason,
                            cart_total=0, store=None) -> dict:
    """Validate manager PIN and approve an over-limit credit sale.

    Returns {approved: True, manager_name: "..."} on success.
    """
    frappe.has_permission("Sales Invoice", "create", throw=True)

    if not manager_pin or not override_reason:
        return {"approved": False, "message": _("PIN and reason are required")}

    # Find a user whose PIN matches — check POS Profile Extension managers
    # or fall back to checking if the PIN matches any user with Store Manager role
    manager_user = None
    manager_name = None

    # Method 1: Check against POS-specific manager PINs
    pins = frappe.get_all(
        "CH POS Manager PIN",
        filters={"pin": manager_pin, "disabled": 0},
        fields=["user", "full_name"],
        limit=1,
    ) if frappe.db.exists("DocType", "CH POS Manager PIN") else []

    if pins:
        manager_user = pins[0].user
        manager_name = pins[0].full_name
    else:
        # Method 2: Check against user password (for managers with Store Manager role)
        from frappe.utils.password import check_password
        try:
            user_doc = check_password(None, manager_pin)
            if user_doc:
                user = user_doc if isinstance(user_doc, str) else user_doc.name
                roles = frappe.get_roles(user)
                if "Store Manager" in roles or "Sales Manager" in roles or "System Manager" in roles:
                    manager_user = user
                    manager_name = frappe.db.get_value("User", user, "full_name")
        except frappe.AuthenticationError:
            pass

    if not manager_user:
        return {"approved": False, "message": _("Invalid PIN or insufficient permissions")}

    # Log the override
    frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Info",
        "reference_doctype": "Customer",
        "reference_name": customer,
        "content": (
            f"Credit override approved by {manager_name} ({manager_user}). "
            f"Cart: ₹{flt(cart_total):,.0f}, Store: {store or '—'}, "
            f"Reason: {override_reason}"
        ),
    }).insert(ignore_permissions=True)

    return {"approved": True, "manager_user": manager_user, "manager_name": manager_name}


@frappe.whitelist()
def get_customer_advances(customer) -> list:
    """Return unallocated advance payments for a customer that can be adjusted against a new sale."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    if not customer or customer == "Walk-in Customer":
        return []

    # Look for unallocated Payment Entries (advance payments received)
    advances = frappe.db.sql("""
        SELECT pe.name, pe.posting_date, pe.paid_amount,
               pe.paid_amount - IFNULL(
                   (SELECT SUM(pr.allocated_amount)
                    FROM `tabPayment Entry Reference` pr
                    WHERE pr.parent = pe.name AND pr.docstatus = 1), 0
               ) AS balance
        FROM `tabPayment Entry` pe
        WHERE pe.party_type = 'Customer'
          AND pe.party = %(customer)s
          AND pe.payment_type = 'Receive'
          AND pe.docstatus = 1
        HAVING balance > 0.01
        ORDER BY pe.posting_date ASC
        LIMIT 10
    """, {"customer": customer}, as_dict=True)

    return advances or []


@frappe.whitelist()
def scan_barcode(barcode, pos_profile=None) -> dict:
    """Look up an item by exact barcode or serial number for POS scanner."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    # Coerce — all-digit barcodes / IMEIs may arrive as JSON numbers.
    barcode = str(barcode or "").strip()
    if not barcode:
        return None

    item_code = None

    # 1. Check Item Barcode (exact match)
    item_code = frappe.db.get_value("Item Barcode", {"barcode": barcode}, "parent")

    # 2. Fallback: check Serial No
    scanned_serial = None
    scanned_serial_warehouse = None
    if not item_code:
        sn = frappe.db.get_value(
            "Serial No", barcode, ["item_code", "status", "warehouse"], as_dict=True
        )
        if sn and sn.status in ("Active", "Inactive"):
            item_code = sn.item_code
            scanned_serial = barcode
            scanned_serial_warehouse = sn.warehouse

    # 3. Fallback: exact item_code match
    if not item_code and frappe.db.exists("Item", barcode):
        item_code = barcode

    if not item_code:
        return None

    from ch_pos.api.search import pos_item_search
    result = pos_item_search(
        search_term=item_code,
        pos_profile=pos_profile,
        page_size=1,
    )
    items = (result or {}).get("items", [])
    item = items[0] if items else None

    # When the barcode was a serial number, tag the response so the frontend
    # can decide whether to add directly (sell-first serial) or open the
    # IMEI selection dialog (non-oldest serial).
    if item and scanned_serial and scanned_serial_warehouse:
        oldest_serial, _ = _get_oldest_fifo_serial(item_code, scanned_serial_warehouse)
        item["serial_no"] = scanned_serial
        item["is_oldest_serial"] = 1 if oldest_serial == scanned_serial else 0

    return item


@frappe.whitelist()
def search_invoices_for_return(search_term, pos_profile=None) -> list:
    """Search Sales Invoices for return/exchange processing.

    Company isolation: results are constrained to the active POS Session's company
    so a cashier on a GoFix POS Profile cannot see GoGizmo invoices for the same
    customer (and vice-versa). pos_profile is required for this guarantee.
    """
    search_term = (search_term or "").strip()
    if not search_term:
        return []

    if not pos_profile:
        frappe.throw(frappe._("POS Profile is required to search invoices for return."))

    company = frappe.db.get_value("POS Profile", pos_profile, "company")
    if not company:
        frappe.throw(frappe._("POS Profile {0} has no Company set.").format(pos_profile))

    # TC_016 — restrict the Return tab to invoices issued from the same
    # store. Cashier on Store-A must NOT see invoices that originated at
    # Store-B even if the same Customer transacted at both stores. We use
    # the POS Profile's set_warehouse / warehouse as the store anchor and
    # match against Sales Invoice.set_warehouse + each row's warehouse.
    profile_warehouse = frappe.db.get_value(
        "POS Profile", pos_profile, "warehouse"
    )

    filters = {
        "docstatus": 1,
        "is_return": 0,
        "company": company,
    }

    or_filters = [
        ["name", "like", f"%{search_term}%"],
        ["customer", "like", f"%{search_term}%"],
        ["customer_name", "like", f"%{search_term}%"],
        ["contact_mobile", "like", f"%{search_term}%"],
    ]

    invoices = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        or_filters=or_filters,
        fields=[
            "name", "customer", "customer_name", "posting_date",
            "grand_total", "status", "pos_profile", "company",
            "set_warehouse",
        ],
        order_by="posting_date desc, creation desc",
        limit_page_length=40,
    )

    # TC_016 — second pass: drop invoices that did not originate from this
    # store. We accept either the header set_warehouse or any line item
    # warehouse matching the profile's store warehouse.
    if profile_warehouse:
        kept = []
        for inv in invoices:
            if inv.get("set_warehouse") == profile_warehouse:
                kept.append(inv)
                continue
            row_match = frappe.db.exists(
                "Sales Invoice Item",
                {"parent": inv["name"], "warehouse": profile_warehouse},
            )
            if row_match:
                kept.append(inv)
        invoices = kept[:20]
    else:
        invoices = invoices[:20]

    from frappe.utils import date_diff
    today = nowdate()
    for inv in invoices:
        inv["items_count"] = frappe.db.count(
            "Sales Invoice Item", {"parent": inv["name"]}
        )
        days = cint(date_diff(today, str(inv["posting_date"])))
        inv["days_since_purchase"] = days
        inv["return_window_expired"] = days > 14

    return invoices


@frappe.whitelist()
def get_invoice_items_for_return(invoice_name) -> dict:
    """Get items from a Sales Invoice that can still be returned.

    Each row also includes coverage-binding metadata so the POS UI can show the
    cashier which VAS / Extended Warranty rows will be auto-refunded when a
    device is returned. Linkage is derived from `tabActive VAS Plans` -- see
    :func:`_get_linked_plans_for_invoice`.
    """
    inv = frappe.get_doc("Sales Invoice", invoice_name)
    inv.check_permission("read")  # SECURITY (H8): IDOR prevention
    if inv.docstatus != 1 or inv.is_return:
        frappe.throw(frappe._("Only submitted non-return invoices can be returned"))

    # Build coverage map: { device_si_row_name: [{plan, service_item, vas_si_row, ...}, ...] }
    plan_links = _get_linked_plans_for_invoice(inv)
    # Reverse: { vas_si_row_name: device_si_row_name }
    vas_to_device = {}
    for dev_row, plans in plan_links.items():
        for p in plans:
            if p.get("vas_si_row"):
                vas_to_device[p["vas_si_row"]] = {
                    "device_row": dev_row,
                    "device_item_code": p["device_item_code"],
                    "sold_plan": p["sold_plan"],
                }

    returnable = []
    for item in inv.items:
        # Calculate already-returned qty for this item row
        already_returned = flt(frappe.db.sql("""
            SELECT ABS(SUM(ri.qty))
            FROM `tabSales Invoice Item` ri
            JOIN `tabSales Invoice` pi ON pi.name = ri.parent
            WHERE pi.return_against = %s
              AND pi.docstatus = 1
              AND ri.item_code = %s
              AND (ri.sales_invoice_item = %s OR ri.pos_invoice_item = %s)
        """, (invoice_name, item.item_code, item.name, item.name))[0][0] or 0)

        returnable_qty = flt(item.qty) - already_returned
        if returnable_qty <= 0:
            continue

        # Coverage badges
        bound_to_device = vas_to_device.get(item.name)  # this row IS a VAS bound to a device
        covered_plans = plan_links.get(item.name, [])    # this row IS a device with VAS attached

        returnable.append({
            "name": item.name,
            "item_code": item.item_code,
            "item_name": item.item_name,
            "qty": item.qty,
            "rate": item.rate,
            "amount": item.amount,
            "serial_no": item.serial_no or "",
            "batch_no": item.batch_no or "",
            "warehouse": item.warehouse,
            "already_returned": already_returned,
            "returnable_qty": returnable_qty,
            # Linkage metadata (consumed by returns_workspace.js)
            "is_bound_vas": 1 if bound_to_device else 0,
            "bound_to_device_row": bound_to_device.get("device_row") if bound_to_device else None,
            "bound_to_device_item": bound_to_device.get("device_item_code") if bound_to_device else None,
            "sold_plan": (bound_to_device or {}).get("sold_plan"),
            "has_attached_vas": 1 if covered_plans else 0,
            "attached_vas": [
                {
                    "item_code": p["service_item"],
                    "item_name": p.get("service_item_name") or p["service_item"],
                    "vas_si_row": p.get("vas_si_row"),
                    "sold_plan": p["sold_plan"],
                    "plan_type": p.get("plan_type"),
                }
                for p in covered_plans
            ],
        })

    return returnable


def _get_linked_plans_for_invoice(inv) -> dict:
    """Return { device_si_row_name: [ {sold_plan, service_item, vas_si_row, ...}, ... ] }.

    Looks up Active VAS Plans rows born from this invoice, then maps each plan's
    service item back to the SI row that originally sold it. This is the
    source of truth for "Extended Warranty / VAS X is bound to device row Y".
    """
    if not frappe.db.exists("DocType", "Active VAS Plans"):
        return {}

    try:
        plans = frappe.db.sql("""
            SELECT sp.name AS sold_plan, sp.item_code AS device_item_code,
                   sp.warranty_plan, sp.plan_type, sp.serial_no,
                   wp.service_item, wp.plan_name AS plan_label
            FROM `tabActive VAS Plans` sp
            LEFT JOIN `tabCH Warranty Plan` wp ON wp.name = sp.warranty_plan
            WHERE sp.sales_invoice = %s
              AND sp.docstatus = 1
              AND sp.status = 'Active'
        """, (inv.name,), as_dict=True)
    except Exception:
        # If the schema is missing fields in some env, fall back to no linking.
        return {}

    if not plans:
        return {}

    # Build quick lookup of items by item_code in this invoice
    items_by_code = {}
    for it in inv.items:
        items_by_code.setdefault(it.item_code, []).append(it)

    out = {}
    used_vas_rows = set()
    for p in plans:
        device_rows = items_by_code.get(p["device_item_code"]) or []
        if not device_rows:
            continue
        device_row = device_rows[0].name  # first occurrence; SI normally has 1

        vas_row = None
        if p.get("service_item"):
            for cand in items_by_code.get(p["service_item"], []) or []:
                if cand.name in used_vas_rows:
                    continue
                vas_row = cand.name
                used_vas_rows.add(cand.name)
                break

        out.setdefault(device_row, []).append({
            "sold_plan": p["sold_plan"],
            "device_item_code": p["device_item_code"],
            "service_item": p.get("service_item"),
            "service_item_name": p.get("plan_label"),
            "vas_si_row": vas_row,
            "plan_type": p.get("plan_type"),
        })

    return out


def _auto_include_bound_vas_rows(orig, return_items):
    """Append VAS / Extended Warranty rows automatically when the device
    they protect is being returned.

    Parity with how Apple Retail / Samsung Care+ POS systems behave: returning
    a covered device automatically refunds its bound protection plan rather
    than leaving an "orphan" warranty active for a device the customer no
    longer owns.

    Returns ``(updated_return_items, auto_added_summary)``. ``auto_added_summary``
    is a list of dicts the caller surfaces back to the UI so the cashier can see
    exactly what was auto-included.
    """
    plan_links = _get_linked_plans_for_invoice(orig)
    if not plan_links:
        return return_items, []

    rows_by_name = {it.name: it for it in orig.items}
    already_in_return = {ri.get("original_item_row") for ri in return_items if ri.get("original_item_row")}
    device_rows_in_return = {ri.get("original_item_row"): flt(ri.get("qty", 0))
                             for ri in return_items
                             if ri.get("original_item_row") and flt(ri.get("qty", 0)) > 0}

    auto_added = []
    for dev_row_name, return_qty in device_rows_in_return.items():
        for link in plan_links.get(dev_row_name, []):
            vas_row_name = link.get("vas_si_row")
            if not vas_row_name or vas_row_name in already_in_return:
                continue
            vas_item = rows_by_name.get(vas_row_name)
            if not vas_item:
                continue
            # Cap by available returnable qty proportional to device qty
            dev_item = rows_by_name.get(dev_row_name)
            dev_qty = flt(dev_item.qty) if dev_item else 1
            ratio = min(1.0, return_qty / dev_qty) if dev_qty else 1.0
            vas_qty = max(0, flt(vas_item.qty) * ratio)
            if vas_qty <= 0:
                continue
            return_items.append({
                "item_code": vas_item.item_code,
                "item_name": vas_item.item_name,
                "qty": vas_qty,
                "rate": flt(vas_item.rate),
                "original_item_row": vas_item.name,
                "_auto_included_for_device_row": dev_row_name,
                "_sold_plan": link.get("sold_plan"),
            })
            already_in_return.add(vas_row_name)
            auto_added.append({
                "item_code": vas_item.item_code,
                "item_name": vas_item.item_name,
                "qty": vas_qty,
                "amount": vas_qty * flt(vas_item.rate),
                "sold_plan": link.get("sold_plan"),
                "device_row": dev_row_name,
            })

    return return_items, auto_added


def _collect_plans_to_cancel(orig, return_items) -> list:
    """Return a de-duped list of Active VAS Plans names whose origin items are part
    of this return -- either because the device is being returned, or because
    the cashier explicitly returned the VAS row itself.
    """
    plan_links = _get_linked_plans_for_invoice(orig)
    if not plan_links:
        return []

    # All plans by either device row or vas row
    by_device_row = plan_links  # {dev_row: [link, ...]}
    by_vas_row = {}
    for dev_row, links in plan_links.items():
        for link in links:
            if link.get("vas_si_row"):
                by_vas_row[link["vas_si_row"]] = link

    plans = set()
    for ri in return_items:
        if flt(ri.get("qty", 0)) <= 0:
            continue
        row_name = ri.get("original_item_row")
        if not row_name:
            continue
        # Device row -> cancel all attached plans
        for link in by_device_row.get(row_name, []):
            if link.get("sold_plan"):
                plans.add(link["sold_plan"])
        # VAS row -> cancel that one plan
        link = by_vas_row.get(row_name)
        if link and link.get("sold_plan"):
            plans.add(link["sold_plan"])

    return sorted(plans)


def _cancel_linked_sold_plans(plan_names, return_invoice_name) -> list:
    """Cancel each Active VAS Plans name. Cancellation triggers the doctype's own
    ``on_cancel`` which sets status='Cancelled', clears the Serial Lifecycle
    warranty fields, and writes a 'Plan Cancelled' entry to CH VAS Ledger.

    Failures are logged but never raised -- the credit note is already in the
    ledger and we don't want a coverage-bookkeeping issue to corrupt the GL.
    """
    cancelled = []
    for plan_name in plan_names or []:
        try:
            plan = frappe.get_doc("Active VAS Plans", plan_name)
            if plan.docstatus == 1 and plan.status not in ("Cancelled", "Void"):
                plan.flags.ignore_permissions = True
                if hasattr(plan, "remarks"):
                    suffix = f"\nAuto-cancelled: device returned via {return_invoice_name}"
                    plan.db_set("remarks", (plan.remarks or "") + suffix, update_modified=False)
                plan.cancel()
                cancelled.append(plan_name)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Auto-cancel of Active VAS Plans {plan_name} failed (return: {return_invoice_name})",
            )
    return cancelled


def _extract_phase4_return_context(return_doc) -> dict:
    remarks = return_doc.get("remarks") or ""
    refund_method = "Original Tender"
    physical_condition = "Resalable"
    for line in remarks.splitlines():
        line = line.strip()
        if line.startswith("[PHASE4_REFUND_METHOD=") and line.endswith("]"):
            refund_method = line[len("[PHASE4_REFUND_METHOD="):-1] or refund_method
        elif line.startswith("[PHASE4_PHYSICAL_CONDITION=") and line.endswith("]"):
            physical_condition = line[len("[PHASE4_PHYSICAL_CONDITION="):-1] or physical_condition
    return {
        "refund_method": refund_method,
        "physical_condition": physical_condition,
    }


def _apply_phase4_return_side_effects(return_doc) -> dict:
    ctx = _extract_phase4_return_context(return_doc)
    refund_method = ctx["refund_method"]
    physical_condition = ctx["physical_condition"]
    amount = abs(flt(return_doc.rounded_total or return_doc.grand_total or 0))
    result = {
        "refund_method": refund_method,
        "physical_condition": physical_condition,
        "store_credit_wallet": None,
        "return_credit_voucher": None,
        "refurb_orders": [],
    }

    # Refund instruments — idempotent by source_document=return invoice.
    existing_voucher = frappe.db.get_value(
        "CH Voucher",
        {"source_document": return_doc.name, "source_type": "Return"},
        ["name", "voucher_code", "voucher_type"],
        as_dict=True,
    )
    if refund_method == "Store Credit":
        if existing_voucher:
            wallet_name = frappe.db.get_value(
                "Store Credit Wallet",
                {"customer": return_doc.customer, "company": return_doc.company},
                "name",
            )
            result["store_credit_wallet"] = {
                "wallet": wallet_name,
                "voucher_name": existing_voucher.name,
                "voucher_code": existing_voucher.voucher_code,
                "voucher_type": existing_voucher.voucher_type,
            }
        else:
            from buyback.buyback.doctype.store_credit_wallet.store_credit_wallet import issue_wallet_credit
            result["store_credit_wallet"] = issue_wallet_credit(
                customer=return_doc.customer,
                amount=amount,
                company=return_doc.company,
                pos_invoice=return_doc.name,
                reason=frappe._("Store credit issued against return {0}").format(return_doc.name),
            )
    elif refund_method == "Exchange Voucher":
        if existing_voucher:
            result["return_credit_voucher"] = {
                "voucher_name": existing_voucher.name,
                "voucher_code": existing_voucher.voucher_code,
                "voucher_type": existing_voucher.voucher_type,
            }
        else:
            from ch_item_master.ch_item_master.voucher_api import issue_voucher
            result["return_credit_voucher"] = issue_voucher(
                voucher_type="Return Credit",
                amount=amount,
                company=return_doc.company,
                customer=return_doc.customer,
                source_type="Return",
                source_document=return_doc.name,
                reason=frappe._("Exchange voucher issued against return {0}").format(return_doc.name),
                valid_days=180,
            )

    # Closed loop for damaged physical returns -> Refurbishment Order(s).
    if cint(return_doc.update_stock) and physical_condition in ("Damaged", "Refurbish Required", "Dead on Arrival"):
        existing_orders = frappe.get_all(
            "Refurbishment Order",
            filters={"return_invoice": return_doc.name},
            pluck="name",
        )
        if existing_orders:
            result["refurb_orders"] = existing_orders
        else:
            from buyback.buyback.doctype.refurbishment_order.refurbishment_order import create_from_return
            items = [
                {
                    "item_code": row.item_code,
                    "serial_no": row.serial_no or "",
                    "qty": abs(flt(row.qty)),
                    "warehouse": row.warehouse,
                }
                for row in (return_doc.items or [])
                if abs(flt(row.qty)) > 0
            ]
            created = create_from_return(
                return_invoice=return_doc.name,
                original_invoice=return_doc.return_against,
                items=items,
                customer=return_doc.customer,
                company=return_doc.company,
                physical_condition=physical_condition,
                return_reason=frappe.db.get_value("Sales Invoice", return_doc.name, "custom_return_reason") if frappe.get_meta("Sales Invoice").has_field("custom_return_reason") else None,
                return_remarks=return_doc.remarks,
            )
            result["refurb_orders"] = created.get("orders") or []

    return result



@frappe.whitelist()
def create_pos_return(original_invoice, return_items, sales_executive=None,
                      return_reason=None, return_remarks=None,
                      manager_pin=None, credit_only=0,
                      replacement_invoice=None,
                      refund_method=None, refund_mode_of_payment=None,
                      physical_condition=None) -> dict:
    """Create a Sales Invoice return (credit note) for specific items.

    Market-standard maker-checker (SAP credit memo / Oracle Returns Mgmt):

    * `return_reason` + `return_remarks` are MANDATORY for audit (every return
      must justify itself, similar to RGA reason codes in Oracle Order Mgmt).
    * Returns above the per-profile auto-approve limit, OR returns containing
      serialized devices, OR returns where the original invoice is older than
      the configured policy window are routed to "Pending Approval" instead of
      being submitted directly.
    * A POS Manager (role) can either submit the return directly (auto-approve)
      or supply a `manager_pin` to override the threshold for an in-person
      checker workflow (matches Walmart/Best Buy POS pattern).
    * `credit_only=1` issues the credit note WITHOUT a Stock Ledger Entry
      (no restock). Used when the customer keeps the physical goods
      (damaged write-off, lost, fraud credit). Requires manager role or PIN.
    * `replacement_invoice` captures the linkback to a replacement sale so
      finance can net the credit against the new invoice.
    """
    frappe.has_permission("Sales Invoice", "create", throw=True)
    if isinstance(return_items, str):
        return_items = frappe.parse_json(return_items)

    refund_method = (refund_method or "Original Tender").strip() or "Original Tender"
    if refund_method not in ("Original Tender", "Store Credit", "Exchange Voucher"):
        frappe.throw(frappe._("Invalid refund method: {0}").format(refund_method))
    physical_condition = (physical_condition or "Resalable").strip() or "Resalable"
    if physical_condition not in ("Resalable", "Damaged", "Refurbish Required", "Dead on Arrival"):
        frappe.throw(frappe._("Invalid physical condition: {0}").format(physical_condition))

    # ── Mandatory remarks/reason (SAP "Reason for Rejection" parity) ───
    return_reason = (return_reason or "").strip()
    return_remarks = (return_remarks or "").strip()
    if not return_remarks:
        frappe.throw(
            frappe._("Return remarks are mandatory. Please describe why this return is being processed."),
            title=frappe._("Remarks Required"),
        )
    if len(return_remarks) < 10:
        frappe.throw(
            frappe._("Return remarks must be at least 10 characters (audit requirement)."),
            title=frappe._("Remarks Too Short"),
        )

    # Phase D — credit-only returns (no stock reversal) require manager auth
    # because we are issuing a credit without physical goods coming back.
    if cint(credit_only):
        _user_roles = set(frappe.get_roles(frappe.session.user))
        _is_mgr = bool({"POS Manager", "Accounts Manager", "System Manager"} & _user_roles)
        _pin_ok = bool(manager_pin) and _verify_manager_pin(manager_pin)
        if not (_is_mgr or _pin_ok):
            frappe.throw(
                frappe._(
                    "Credit-only returns (no stock reversal) require a POS Manager / "
                    "Accounts Manager role or a valid manager PIN. The customer keeps "
                    "the goods; only the credit GL is posted."
                ),
                title=frappe._("Manager Authorization Required"),
            )

    orig = frappe.get_doc("Sales Invoice", original_invoice)
    orig.check_permission("read")  # SECURITY (H8): IDOR prevention
    if orig.docstatus != 1 or orig.is_return:
        frappe.throw(frappe._("Can only create returns for submitted non-return invoices"))

    # Auto-include any VAS / Extended Warranty rows that are bound to the
    # devices being returned (parity with Apple Care, Samsung Care+ behavior).
    # If the cashier returns a device, the protection plans sold against that
    # device on the same invoice are automatically refunded and their
    # corresponding Active VAS Plans rows are cancelled below (after submit).
    return_items, _auto_added_plans = _auto_include_bound_vas_rows(orig, return_items)
    # Plans we'll cancel after the return is submitted. We collect them upfront
    # so we know which to void even if the cashier explicitly added the VAS row
    # (instead of relying on auto-include).
    _plans_to_cancel = _collect_plans_to_cancel(orig, return_items)

    # Use session business_date if available
    from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_active_session
    _active = get_active_session(orig.pos_profile) if orig.pos_profile else None

    ret = frappe.new_doc("Sales Invoice")
    # Bind to active POS session so the workflow's "POS Direct Submit"
    # transition condition (doc.custom_ch_pos_session) evaluates true.
    if _active and _active.get("name"):
        ret.custom_ch_pos_session = _active.get("name")
    ret.pos_profile = orig.pos_profile
    ret.customer = orig.customer
    ret.company = orig.company
    ret.selling_price_list = orig.selling_price_list
    ret.currency = orig.currency
    # Sales Invoice doesn't have a top-level warehouse field; get from items
    _orig_warehouse = (
        orig.get("set_warehouse")
        or (orig.items[0].warehouse if orig.items else None)
        or frappe.get_cached_value("POS Profile", orig.pos_profile, "warehouse")
    )
    ret.posting_date = str(_active.get("business_date")) if _active and _active.get("business_date") else nowdate()
    ret.is_pos = 1
    ret.is_return = 1
    ret.return_against = orig.name
    # Phase D — credit-only returns skip the Stock Ledger Entry. POS Invoices
    # normally set update_stock=1 so the return reverses inventory; for
    # credit-only the goods stay out (damaged/lost/fraud credit). Requires
    # manager authorization — enforced below alongside the auto-approve gate.
    credit_only_flag = cint(credit_only)
    if credit_only_flag:
        ret.update_stock = 0
    else:
        ret.update_stock = 1

    total_return_amount = 0
    for ri in return_items:
        qty = flt(ri.get("qty", 0))
        rate = flt(ri.get("rate", 0))
        if qty <= 0:
            continue

        row = {
            "item_code": ri.get("item_code"),
            "item_name": ri.get("item_name", ""),
            "qty": -1 * qty,
            "rate": rate,
            "uom": "Nos",
            "warehouse": _orig_warehouse,
            # ERPNext validate_returned_items looks for this field when doctype
            # is Sales Invoice; without it the key (item_code, row_name) can't
            # be found in valid_items and a spurious msgprint fires on every
            # save/submit.  Keep pos_invoice_item for backward compatibility.
            "sales_invoice_item": ri.get("original_item_row", ""),
            "pos_invoice_item": ri.get("original_item_row", ""),
        }
        if ri.get("serial_no"):
            row["serial_no"] = ri["serial_no"]
        ret.append("items", row)
        total_return_amount += qty * rate

    if not ret.items:
        frappe.throw(frappe._("No items to return"))

    # POS-6 fix: Validate serial return state for serialized items
    for ri in return_items:
        if ri.get("serial_no"):
            check = check_serial_returnable(ri["serial_no"], original_invoice)
            if not check.get("returnable"):
                frappe.throw(
                    frappe._("Cannot return serial {0}: {1}").format(
                        ri["serial_no"], check.get("reason")),
                    title=frappe._("Serial Return Blocked"),
                )

    # Taxes from original — must be added BEFORE calculating grand_total
    for tax in (orig.taxes or []):
        ret.append("taxes", {
            "charge_type": tax.charge_type,
            "account_head": tax.account_head,
            "rate": tax.rate,
            "description": tax.description or tax.account_head,
            "tax_amount": -1 * flt(tax.tax_amount) if tax.charge_type == "Actual" else 0,
        })

    if sales_executive:
        ret.custom_sales_executive = sales_executive
        sales_person = frappe.db.get_value("POS Executive", sales_executive, "sales_person")
        if sales_person:
            ret.append("sales_team", {
                "sales_person": sales_person,
                "allocated_percentage": 100,
            })

    # Compute the correct grand_total AFTER applying taxes so the payment
    # amount matches exactly and GL entries balance.  Previously this used
    # `total_return_amount` (pre-tax sum) which left the GST portion
    # unaccounted, producing "Debit and Credit not equal" errors.
    ret.run_method("calculate_taxes_and_totals")
    # Use rounded_total when it differs from grand_total (ERPNext rounds POS
    # invoice totals and uses `rounded_total or grand_total` as the canonical
    # amount-to-pay in set_total_amount_to_default_mop).  If we set payment to
    # grand_total but the internal calc uses rounded_total, the difference is
    # treated as a positive pending_amount → payment gets replaced with the
    # rounding diff (e.g. 0.4), which then fails verify_payment_amount_is_negative.
    correct_payment = flt(ret.rounded_total or ret.grand_total)  # negative for returns

    # Payment (negative) — set AFTER tax calculation
    default_mode = "Cash"
    for p in (orig.payments or []):
        if p.default:
            default_mode = p.mode_of_payment
            break

    selected_refund_mop = (refund_mode_of_payment or "").strip()
    if refund_method == "Original Tender" and selected_refund_mop:
        allowed_modes = frappe.get_all(
            "POS Payment Method",
            filters={"parent": orig.pos_profile},
            pluck="mode_of_payment",
        )
        if allowed_modes and selected_refund_mop not in allowed_modes:
            frappe.throw(
                frappe._("Invalid Refund Payment mode: {0}").format(selected_refund_mop),
                title=frappe._("Invalid Refund Payment"),
            )
        default_mode = selected_refund_mop

    ret.append("payments", {
        "mode_of_payment": default_mode,
        "amount": correct_payment,
    })
    ret.paid_amount = correct_payment

    ret.flags.ignore_permissions = True
    # Insert first with default Draft workflow state so _doc_before_save is
    # populated before we transition to Approved (matches create_pos_invoice
    # pattern at line ~846).
    ret.insert(ignore_permissions=True)

    # ── Persist the captured reason/remarks for audit ────────────────
    update_kwargs = {}
    if return_reason and frappe.get_meta("Sales Invoice").has_field("custom_return_reason"):
        update_kwargs["custom_return_reason"] = return_reason
    if frappe.get_meta("Sales Invoice").has_field("custom_return_remarks"):
        update_kwargs["custom_return_remarks"] = return_remarks
    # Phase D — stamp credit-only flag + replacement linkback when supplied.
    _si_meta = frappe.get_meta("Sales Invoice")
    if credit_only_flag and _si_meta.has_field("custom_credit_only_return"):
        update_kwargs["custom_credit_only_return"] = 1
    if replacement_invoice and _si_meta.has_field("custom_replacement_invoice"):
        if frappe.db.exists("Sales Invoice", replacement_invoice):
            update_kwargs["custom_replacement_invoice"] = replacement_invoice
    # Always echo into the standard remarks field so it is visible on the
    # printed credit note even when the custom field is not yet installed.
    existing_remarks = (ret.get("remarks") or "").strip()
    composed_remarks = f"[Return] {return_reason or 'Reason not set'}: {return_remarks}"
    if existing_remarks:
        composed_remarks = f"{existing_remarks}\n{composed_remarks}"
    composed_remarks = (
        f"{composed_remarks}\n"
        f"[PHASE4_REFUND_METHOD={refund_method}]\n"
        f"[PHASE4_REFUND_MOP={default_mode}]\n"
        f"[PHASE4_PHYSICAL_CONDITION={physical_condition}]"
    )
    update_kwargs["remarks"] = composed_remarks
    if update_kwargs:
        for k, v in update_kwargs.items():
            ret.set(k, v)
        ret.save(ignore_permissions=True)

    # ── Maker-Checker gate (SAP credit memo release strategy) ─────────
    requires_approval, reasons = _return_requires_approval(orig, ret, return_items, manager_pin)
    user_roles = set(frappe.get_roles(frappe.session.user))
    is_manager = bool({"POS Manager", "Accounts Manager", "System Manager"} & user_roles)
    pin_ok = bool(manager_pin) and _verify_manager_pin(manager_pin, ret.company)

    if requires_approval and not (is_manager or pin_ok):
        ret.workflow_state = "Pending Approval"
        ret.custom_si_approval_state = "Pending Approval"
        ret.save(ignore_permissions=True)
        frappe.db.commit()
        return {
            "name": ret.name,
            "status": "Pending Approval",
            "requires_approval": True,
            "approval_reasons": reasons,
            "grand_total": ret.grand_total,
            "customer": ret.customer,
            "customer_name": ret.customer_name,
            "message": frappe._("Return saved as draft pending manager approval ({0}).").format(", ".join(reasons)),
        }

    ret.workflow_state = "Approved"
    ret.custom_si_approval_state = "Approved"
    if is_manager or pin_ok:
        ret.add_comment(
            "Info",
            text=frappe._("Return auto-approved by {0}{1}").format(
                frappe.session.user,
                " (manager PIN)" if pin_ok and not is_manager else "",
            ),
        )
    try:
        ret.submit()
    except Exception:
        # Frappe commits docstatus=1 before on_submit runs, so if GL entry
        # creation fails the invoice can be left in an inconsistent state.
        # Attempt to cancel + delete it to keep the DB clean.
        try:
            doc = frappe.get_doc("Sales Invoice", ret.name)
            if doc.docstatus == 1:
                doc.flags.ignore_permissions = True
                doc.flags.ignore_validate = True
                if hasattr(doc, "custom_cancel_reason"):
                    doc.custom_cancel_reason = "System: auto-rollback on submit failure"
                doc.cancel()
            frappe.delete_doc("Sales Invoice", ret.name, force=True, ignore_permissions=True)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Return invoice cleanup failed for {ret.name}",
            )
        raise

    # Create return incentive (clawback) entries
    incentive_clawback = 0
    if sales_executive:
        try:
            incentive_clawback = _create_return_incentive_entries(ret, sales_executive)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Incentive clawback failed for {ret.name}")
            # Always raise — a return without incentive recovery is a financial integrity failure.
            # The store manager must resolve this before the return is processed.
            frappe.throw(
                frappe._("Return blocked: Incentive clawback journal entry failed for {0}. "
                         "Contact the store manager to resolve incentive entries before retrying.").format(
                    frappe.bold(ret.name)
                ),
                title=frappe._("Incentive Clawback Required"),
            )

    # Audit
    try:
        from ch_pos.audit import log_business_event
        log_business_event(
            event_type="Return Approved",
            ref_doctype="Sales Invoice", ref_name=ret.name,
            before=original_invoice,
            after=f"Return ₹{total_return_amount}",
            company=orig.company,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Return audit log failed")

    # Cancel any Active VAS Plans rows whose protected device or service item was
    # part of this return. Failures here are logged but do not roll back the
    # return -- the credit note is already submitted; plan cancellation can be
    # retried by the warranty manager from Active VAS Plans list.
    cancelled_plans = _cancel_linked_sold_plans(_plans_to_cancel, ret.name)
    phase4_side_effects = _apply_phase4_return_side_effects(ret)

    return {
        "name": ret.name,
        "grand_total": ret.grand_total,
        "customer": ret.customer,
        "customer_name": ret.customer_name,
        "incentive_clawback": incentive_clawback,
        "auto_included_vas_rows": _auto_added_plans,
        "cancelled_sold_plans": cancelled_plans,
        "phase4": phase4_side_effects,
    }


# ── Return Approval Workflow (SAP Credit Memo Release Strategy parity) ──
def _return_requires_approval(orig_invoice, ret_doc, return_items, manager_pin=None):
    """Decide whether a return needs manager approval before submission.

    Triggers (any of):
      * Refund value exceeds POS Profile auto_approve limit (default ₹5,000).
      * Return contains a serialised device (high-value risk per Best Buy POS).
      * Original invoice older than POS Profile return_window_days
        (default 30 days — matches Apple/Amazon return policy windows).
      * Return % > 50% of original invoice value (partial-return abuse guard).
    """
    reasons = []
    refund_value = abs(flt(ret_doc.grand_total or 0))
    auto_limit = flt(
        frappe.db.get_value("POS Profile", orig_invoice.pos_profile, "custom_return_auto_approve_limit")
        or 0
    ) or 5000.0
    return_window = cint(
        frappe.db.get_value("POS Profile", orig_invoice.pos_profile, "custom_return_window_days")
        or 0
    ) or 30

    if refund_value > auto_limit:
        reasons.append(f"Refund value ₹{refund_value:,.2f} exceeds auto-approve limit ₹{auto_limit:,.2f}")

    has_serial = any((ri.get("serial_no") or "").strip() for ri in (return_items or []))
    if has_serial:
        reasons.append("Serialised device return (manager review required)")

    try:
        invoice_age_days = (getdate(nowdate()) - getdate(orig_invoice.posting_date)).days
        if invoice_age_days > return_window:
            reasons.append(f"Original invoice is {invoice_age_days} days old (policy window: {return_window} days)")
    except Exception:
        pass

    orig_total = abs(flt(orig_invoice.grand_total or 0))
    if orig_total and (refund_value / orig_total) > 0.5:
        reasons.append(f"Return is {refund_value/orig_total*100:.0f}% of original invoice (>50%)")

    return (bool(reasons), reasons)


def _verify_manager_pin(manager_pin, company=None):
    """Lightweight PIN check used for return-override.

    Looks up CH POS Manager PIN; returns True on a clean match. Reuses the
    same store as `approve_credit_override` for consistency.
    """
    if not manager_pin:
        return False
    if not frappe.db.exists("DocType", "CH POS Manager PIN"):
        return False
    rows = frappe.get_all(
        "CH POS Manager PIN",
        filters={"pin": manager_pin, "disabled": 0},
        fields=["user"],
        limit=1,
    )
    return bool(rows)


@frappe.whitelist()
def approve_pos_return(return_invoice, manager_pin=None, approval_remarks=None) -> dict:
    """Manager-checker approval that submits a Pending Approval return invoice.

    Mirrors SAP VA02 release strategy: a separate user (the checker) reviews
    the credit memo, supplies a justification, and releases it for posting.
    """
    frappe.has_permission("Sales Invoice", "submit", throw=True)

    doc = frappe.get_doc("Sales Invoice", return_invoice)
    if doc.docstatus != 0:
        frappe.throw(frappe._("Return {0} is not in Draft state (current docstatus: {1})").format(
            return_invoice, doc.docstatus))
    if not doc.is_return:
        frappe.throw(frappe._("{0} is not a return invoice").format(return_invoice))

    user_roles = set(frappe.get_roles(frappe.session.user))
    is_manager = bool({"POS Manager", "Accounts Manager", "System Manager"} & user_roles)
    pin_ok = bool(manager_pin) and _verify_manager_pin(manager_pin, doc.company)

    if not (is_manager or pin_ok):
        frappe.throw(
            frappe._("Only a POS Manager can approve return {0}. Provide a valid manager PIN to override.").format(return_invoice),
            frappe.PermissionError,
        )

    approval_remarks = (approval_remarks or "").strip()
    if not approval_remarks:
        frappe.throw(frappe._("Approval remarks are mandatory (audit requirement)"))

    doc.workflow_state = "Approved"
    doc.custom_si_approval_state = "Approved"
    doc.add_comment(
        "Workflow",
        text=frappe._("Return approved by {0}{1}: {2}").format(
            frappe.session.user,
            " (manager PIN)" if pin_ok and not is_manager else "",
            approval_remarks,
        ),
    )
    doc.flags.ignore_permissions = True
    doc.save()
    doc.submit()
    phase4_side_effects = _apply_phase4_return_side_effects(doc)
    frappe.db.commit()

    return {
        "name": doc.name,
        "status": "Approved",
        "grand_total": doc.grand_total,
        "customer": doc.customer,
        "customer_name": doc.customer_name,
        "phase4": phase4_side_effects,
    }


@frappe.whitelist()
def get_pending_return_approvals(pos_profile=None, limit=20) -> list:
    """List Sales Invoice returns waiting on manager approval (for the dashboard)."""
    filters = {
        "docstatus": 0,
        "is_return": 1,
        "workflow_state": "Pending Approval",
    }
    if pos_profile:
        filters["pos_profile"] = pos_profile
    return frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=[
            "name", "customer", "customer_name", "grand_total", "posting_date",
            "remarks", "owner", "creation", "return_against",
        ],
        order_by="creation desc",
        limit=limit,
    )


@frappe.whitelist()
def preview_return_with_replacement(original_invoice, return_items,
                                    replacement_total=0) -> dict:
    """Compute the net delta when a customer returns items and re-buys others.

    Returns a structured preview that the POS frontend uses to decide whether
    the cashier must collect cash from the customer (positive delta) or
    refund cash to the customer (negative delta).

    Mirrors how Oracle Xstore / SAP Retail / MS D365 Commerce present the
    'tender summary' before the cashier clicks Pay.
    """
    if isinstance(return_items, str):
        return_items = frappe.parse_json(return_items)
    return_items = return_items or []

    # Compute return value (taxes inclusive) using original invoice item rates
    orig = frappe.get_doc("Sales Invoice", original_invoice)
    return_subtotal = 0.0
    for ri in return_items:
        return_subtotal += flt(ri.get("qty", 0)) * flt(ri.get("rate", 0))

    # Apply original invoice's effective tax rate to the return subtotal
    base_total = flt(orig.net_total) or 0.01
    tax_factor = (flt(orig.grand_total) / base_total) if base_total else 1.0
    return_value = round(return_subtotal * tax_factor, 2)
    replacement_total = flt(replacement_total)

    delta = round(replacement_total - return_value, 2)
    if delta > 0.5:
        action = "collect"
        message = frappe._(
            "Customer must pay {0} (replacement {1} - return {2})."
        ).format(
            fmt_money(delta, currency=orig.currency or "INR"),
            fmt_money(replacement_total, currency=orig.currency or "INR"),
            fmt_money(return_value, currency=orig.currency or "INR"),
        )
    elif delta < -0.5:
        action = "refund"
        message = frappe._(
            "Customer must be refunded {0} (return {1} > replacement {2})."
        ).format(
            fmt_money(abs(delta), currency=orig.currency or "INR"),
            fmt_money(return_value, currency=orig.currency or "INR"),
            fmt_money(replacement_total, currency=orig.currency or "INR"),
        )
    else:
        action = "even"
        message = frappe._("Even exchange -- no cash movement required.")

    return {
        "return_value": return_value,
        "replacement_total": replacement_total,
        "delta": delta,
        "action": action,           # "collect" | "refund" | "even"
        "message": message,
        "currency": orig.currency or "INR",
    }


@frappe.whitelist()
def process_return_with_replacement(
    original_invoice,
    return_items,
    replacement_payload,
    settlement_payments=None,
    refund_mode_of_payment=None,
    sales_executive=None,
) -> dict:
    """Atomically process a return + replacement sale, refusing to bill until
    the cash difference is fully accounted for.

    Industry-parity behaviour (Oracle Xstore / SAP Retail / MS D365 Commerce):
      - Replacement total > Return value  -> cashier collects the difference
        via ``settlement_payments`` (list of {mode_of_payment, amount, ...}).
      - Replacement total < Return value  -> cashier refunds the difference
        via ``refund_mode_of_payment`` (defaults to original invoice MOP).
      - Replacement total == Return value -> even exchange, no cash movement.

    The system BLOCKS submission if the difference is not fully settled,
    preventing the silent loss-of-money bug where surplus credit was dropped.

    Args:
        original_invoice: Sales Invoice being returned against.
        return_items: list of {item_code, qty, rate, original_item_row, serial_no?}.
        replacement_payload: dict accepted by ``create_pos_invoice``
            (items, pos_profile, customer, etc.) -- WITHOUT the payments array
            for the replacement; settlement_payments below covers it.
        settlement_payments: list of payment rows to collect from customer
            when delta > 0. Sum must equal delta exactly.
        refund_mode_of_payment: MOP used to issue the refund row when delta < 0.
            Defaults to the original invoice's default MOP.
        sales_executive: passed through to both legs.

    Returns:
        {
          "return_invoice": <name>,
          "replacement_invoice": <name>,
          "delta": <signed amount>,
          "action": "collect" | "refund" | "even",
          "settled": True,
        }

    Raises:
        ValidationError if the settlement does not match the computed delta.
    """
    if isinstance(return_items, str):
        return_items = frappe.parse_json(return_items)
    if isinstance(replacement_payload, str):
        replacement_payload = frappe.parse_json(replacement_payload)
    if isinstance(settlement_payments, str):
        settlement_payments = frappe.parse_json(settlement_payments)
    settlement_payments = settlement_payments or []

    if not return_items:
        frappe.throw(frappe._("At least one item must be returned"))
    if not replacement_payload or not replacement_payload.get("items"):
        frappe.throw(frappe._(
            "Replacement payload with at least one item is required. "
            "For pure returns without replacement, use create_pos_return instead."
        ))

    orig = frappe.get_doc("Sales Invoice", original_invoice)

    # ── 1. Compute provisional totals to determine net delta ───────────────
    replacement_items = replacement_payload.get("items") or []
    replacement_total_raw = sum(
        flt(it.get("qty", 0)) * flt(it.get("rate", 0)) for it in replacement_items
    )
    # Apply original invoice's effective tax factor as a quick estimate.
    # The exact total is recomputed by ERPNext on save() -- this preview is
    # only used to validate the settlement payload before any DB writes.
    base_total = flt(orig.net_total) or 0.01
    tax_factor = (flt(orig.grand_total) / base_total) if base_total else 1.0
    replacement_total_est = round(replacement_total_raw * tax_factor, 2)

    return_subtotal = sum(
        flt(ri.get("qty", 0)) * flt(ri.get("rate", 0)) for ri in return_items
    )
    return_value = round(return_subtotal * tax_factor, 2)

    delta = round(replacement_total_est - return_value, 2)

    # ── 2. Validate settlement matches delta BEFORE any DB writes ──────────
    settle_sum = round(sum(flt(p.get("amount", 0)) for p in settlement_payments), 2)
    _cur = orig.currency or "INR"

    if delta > 0.5:  # Customer owes us
        if abs(settle_sum - delta) > 0.5:
            frappe.throw(
                frappe._(
                    "Settlement mismatch: customer must pay {expected} "
                    "for the replacement vs return difference, but tendered {got}. "
                    "Adjust the payment rows so they sum exactly to {expected}."
                ).format(
                    expected=fmt_money(delta, currency=_cur),
                    got=fmt_money(settle_sum, currency=_cur),
                ),
                title=frappe._("Cannot Bill -- Difference Not Settled"),
            )
    elif delta < -0.5:  # We owe customer
        if settle_sum > 0.5:
            frappe.throw(
                frappe._(
                    "When the return value exceeds the replacement, no payment "
                    "can be collected -- the customer is owed {0}. "
                    "Leave settlement_payments empty and set refund_mode_of_payment instead."
                ).format(fmt_money(abs(delta), currency=_cur)),
                title=frappe._("Cannot Bill -- Refund Required"),
            )
    else:  # Even exchange
        if settle_sum > 0.5:
            frappe.throw(
                frappe._(
                    "Even exchange: replacement and return values match "
                    "({0}). No payment should be collected."
                ).format(fmt_money(return_value, currency=_cur)),
                title=frappe._("Cannot Bill -- Even Exchange"),
            )

    # ── 3. Create the return leg (negative invoice for original items) ─────
    return_result = create_pos_return(
        original_invoice=original_invoice,
        return_items=return_items,
        sales_executive=sales_executive,
    )
    return_inv_name = return_result["name"]
    actual_return_value = abs(flt(return_result["grand_total"]))

    # ── 4. Build the replacement-invoice payload ───────────────────────────
    # If delta < 0, the customer is owed cash -> add a refund payment row of
    # |delta| value on the replacement invoice using refund_mode_of_payment.
    # ERPNext rejects negative MOP rows on a positive invoice, so we instead
    # create the refund as a separate Payment Entry below.
    rp = dict(replacement_payload)  # shallow copy
    rp.pop("payments", None)
    rp.pop("mode_of_payment", None)
    rp.pop("amount_paid", None)

    if delta > 0.5:
        # Use settlement payments to cover the difference
        rp["payments"] = settlement_payments
    else:
        # Replacement is fully (or more than fully) covered by the return.
        # Pay the full replacement_total via Cash placeholder; the actual cash
        # settlement is the netted return + (optional) refund Payment Entry.
        # We use a placeholder Cash row matching the replacement total so
        # ERPNext payment validation passes; net cash to drawer is handled by
        # the linked Payment Entry created in step 5.
        default_mop = "Cash"
        for p in (orig.payments or []):
            if p.default:
                default_mop = p.mode_of_payment
                break
        # Use rough estimate; create_pos_invoice will reject if items
        # actually sum to a different amount post-tax.
        rp["payments"] = [{
            "mode_of_payment": default_mop,
            "amount": replacement_total_est,
        }]

    rp["sales_executive"] = sales_executive

    # ── 5. Create the replacement invoice ──────────────────────────────────
    try:
        replacement_result = create_pos_invoice(**rp)
    except Exception:
        # Replacement failed -- roll back the return so the customer's
        # original invoice is restored.
        try:
            ri_doc = frappe.get_doc("Sales Invoice", return_inv_name)
            if ri_doc.docstatus == 1:
                ri_doc.flags.ignore_permissions = True
                if hasattr(ri_doc, "custom_cancel_reason"):
                    ri_doc.custom_cancel_reason = (
                        "System: auto-rollback -- replacement leg failed"
                    )
                ri_doc.cancel()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Return rollback failed for {return_inv_name}",
            )
        raise

    replacement_inv_name = replacement_result["name"]

    # Phase D — cross-link return ↔ replacement so finance can navigate both
    # directions and net the credit against the new invoice. Best-effort:
    # failures here do not roll back the already-submitted invoices.
    try:
        _si_meta = frappe.get_meta("Sales Invoice")
        if _si_meta.has_field("custom_replacement_invoice"):
            frappe.db.set_value(
                "Sales Invoice", return_inv_name,
                "custom_replacement_invoice", replacement_inv_name,
                update_modified=False,
            )
        if _si_meta.has_field("custom_original_invoice"):
            frappe.db.set_value(
                "Sales Invoice", replacement_inv_name,
                "custom_original_invoice", return_inv_name,
                update_modified=False,
            )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Return/replacement cross-link failed for {return_inv_name} ↔ {replacement_inv_name}",
        )

    # ── 6. If we owe the customer, create a refund Payment Entry ───────────
    refund_pe_name = None
    if delta < -0.5:
        refund_amount = round(abs(delta), 2)
        refund_mop = refund_mode_of_payment
        if not refund_mop:
            for p in (orig.payments or []):
                if p.default:
                    refund_mop = p.mode_of_payment
                    break
            refund_mop = refund_mop or "Cash"
        try:
            from erpnext.accounts.doctype.payment_entry.payment_entry import (
                get_payment_entry,
            )
            pe = get_payment_entry("Sales Invoice", return_inv_name)
            pe.payment_type = "Pay"  # paying the customer
            pe.paid_amount = refund_amount
            pe.received_amount = refund_amount
            pe.mode_of_payment = refund_mop
            pe.reference_no = f"REFUND-{return_inv_name}"
            pe.reference_date = nowdate()
            pe.flags.ignore_permissions = True
            pe.insert()
            pe.submit()
            refund_pe_name = pe.name
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Refund Payment Entry failed for {return_inv_name}",
            )
            # Don't roll back -- the return + replacement are already valid.
            # Surface as an explicit error so cashier resolves the refund manually.
            frappe.throw(
                frappe._(
                    "Return and replacement processed, but the refund Payment Entry "
                    "for {0} failed. Issue the {1} refund manually before closing the till."
                ).format(
                    fmt_money(refund_amount, currency=_cur),
                    fmt_money(refund_amount, currency=_cur),
                ),
                title=frappe._("Manual Refund Required"),
            )

    # ── 7. Audit ───────────────────────────────────────────────────────────
    try:
        from ch_pos.audit import log_business_event
        log_business_event(
            event_type="Return + Replacement Settled",
            ref_doctype="Sales Invoice", ref_name=replacement_inv_name,
            before=f"Return {return_inv_name} ({fmt_money(actual_return_value, currency=_cur)})",
            after=(
                f"Replacement {replacement_inv_name} | "
                f"delta {fmt_money(delta, currency=_cur)} | "
                f"refund_pe {refund_pe_name or 'n/a'}"
            ),
            company=orig.company,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Return+Replacement audit failed")

    return {
        "return_invoice": return_inv_name,
        "replacement_invoice": replacement_inv_name,
        "refund_payment_entry": refund_pe_name,
        "delta": delta,
        "action": "collect" if delta > 0.5 else ("refund" if delta < -0.5 else "even"),
        "settled": True,
    }


@frappe.whitelist()
def validate_serial_for_sale(serial_no, item_code, warehouse, allow_fifo_override=0) -> dict:
    """Validate a serial number can be sold from this warehouse, enforcing FIFO.

    If a FIFO violation is detected and allow_fifo_override is falsy, returns
    {valid: False, fifo_violation: True, oldest_serial, oldest_date, selected_date}
    so the JS can show a soft-warning confirm dialog.

    When allow_fifo_override=1 the FIFO check is skipped (user has already
    confirmed the override in the UI); the exception is logged via log_fifo_override.
    """
    # IMEIs / barcodes are stored as strings; coerce in case the client sent
    # a JSON number for an all-digit IMEI (e.g. 35600220100003).
    serial_no = str(serial_no or "").strip()
    if not frappe.db.exists("Serial No", serial_no):
        return {"valid": False, "reason": frappe._("Serial No {0} does not exist").format(serial_no)}

    sn = frappe.db.get_value(
        "Serial No", serial_no,
        ["item_code", "warehouse", "status"],
        as_dict=True,
    )

    if sn.item_code != item_code:
        return {"valid": False, "reason": frappe._("Serial No {0} belongs to {1}, not {2}").format(
            serial_no, sn.item_code, item_code
        )}

    if sn.warehouse != warehouse:
        return {"valid": False, "reason": frappe._("Serial No {0} is not in warehouse {1} (currently in {2})").format(
            serial_no, warehouse, sn.warehouse or "N/A"
        )}

    if sn.status == "Delivered":
        return {"valid": False, "reason": frappe._("Serial No {0} is already delivered/sold").format(serial_no)}

    if sn.status not in ("Active", "Inactive"):
        return {"valid": False, "reason": frappe._("Serial No {0} status is {1}").format(serial_no, sn.status)}

    # ── Pre-booking reservation lock ────────────────────────────────────────
    # A serial promised on an open, in-validity pre-booking (advance order)
    # cannot be sold to anyone else — it must be billed through Pickup. This is
    # the hard serial lock the qty-level reservation alone does not provide.
    reserved_so = _get_open_reserved_sales_order_for_serial(serial_no, warehouse)
    if reserved_so:
        return {
            "valid": False,
            "reserved": True,
            "reserved_so": reserved_so,
            "reason": frappe._(
                "IMEI {0} is reserved for pre-booking {1}. Bill it via Pickup, "
                "or release the reservation before selling."
            ).format(serial_no, reserved_so),
        }

    # ── FIFO enforcement ────────────────────────────────────────────────────
    if not cint(allow_fifo_override):
        oldest_serial, oldest_date = _get_oldest_fifo_serial(item_code, warehouse)
        if oldest_serial and oldest_serial != serial_no:
            # Determine the receipt date of the selected serial (for display in the dialog).
            selected_date_row = frappe.db.sql("""
                SELECT MIN(DATE(sbb.posting_datetime)) AS received_date
                FROM `tabSerial and Batch Entry` sbe
                JOIN `tabSerial and Batch Bundle` sbb
                    ON sbe.parent = sbb.name
                    AND sbb.type_of_transaction = 'Inward'
                    AND sbb.docstatus = 1
                WHERE sbe.serial_no = %s
            """, serial_no, as_dict=True)
            selected_date = selected_date_row[0].received_date if selected_date_row else None

            # Only warn when the selected serial was received STRICTLY AFTER the
            # oldest one.  Same-day receipts belong to the same inward batch —
            # any serial from that batch is equally valid (no FIFO violation).
            if oldest_date and selected_date and selected_date > oldest_date:
                # Soft FIFO violation — return warning so JS can confirm with user.
                # Manager alert fires only when the cashier confirms the override
                # (see log_fifo_override below).
                return {
                    "valid": False,
                    "fifo_violation": True,
                    "oldest_serial": oldest_serial,
                    "oldest_date": str(oldest_date),
                    "selected_date": str(selected_date),
                    "reason": frappe._(
                        "Older stock exists: {0} (received {1}) should be sold before {2} (received {3})."
                    ).format(oldest_serial, oldest_date, serial_no, selected_date),
                }

    return {"valid": True, "serial_no": serial_no, "item_code": item_code}

@frappe.whitelist()
def log_fifo_override(serial_no, item_code, warehouse, oldest_serial, oldest_date, pos_profile=None) -> dict:
    """Record a cashier-confirmed FIFO override exception.

    Called from the JS confirm dialog when the user chooses to proceed despite
    the FIFO warning.  Logs to CH Business Audit Log and notifies RSM/ASM.
    """
    serial_no = str(serial_no or "").strip()
    oldest_serial = str(oldest_serial or "").strip()
    frappe.has_permission("Sales Invoice", "create", throw=True)

    store = frappe.get_cached_value("POS Profile", pos_profile, "warehouse") if pos_profile else warehouse
    company = frappe.get_cached_value("POS Profile", pos_profile, "company") if pos_profile else None

    # Write audit entry
    try:
        from ch_pos.audit import log_business_event
        log_business_event(
            event_type="Other",
            ref_doctype="Serial No",
            ref_name=serial_no,
            before=f"Oldest: {oldest_serial} (received {oldest_date})",
            after=f"Sold out of order: {serial_no}",
            remarks=f"Cashier confirmed FIFO override — item {item_code} at {warehouse}",
            store=store,
            company=company,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"FIFO override audit log failed for {serial_no}")

    # Notify managers (same as previous hard-reject alert)
    _send_fifo_violation_alert(
        item_code=item_code,
        warehouse=warehouse,
        selected_serial=serial_no,
        oldest_serial=oldest_serial,
        cashier=frappe.session.user,
    )

    return {"logged": True}


@frappe.whitelist()
def check_serial_returnable(serial_no, original_invoice=None) -> dict:
    """Check if a serial number can be returned (not scrapped, transferred, or already returned)."""
    serial_no = str(serial_no or "").strip()
    if not frappe.db.exists("Serial No", serial_no):
        return {"returnable": False, "reason": frappe._("Serial No {0} does not exist").format(serial_no)}

    sn = frappe.db.get_value(
        "Serial No", serial_no,
        ["item_code", "status", "warehouse"],
        as_dict=True,
    )

    # Check if already returned
    if original_invoice:
        already_returned = frappe.db.sql("""
            SELECT ri.parent
            FROM `tabSales Invoice Item` ri
            JOIN `tabSales Invoice` pi ON pi.name = ri.parent
            WHERE pi.return_against = %s AND pi.docstatus = 1
              AND ri.serial_no = %s
            LIMIT 1
        """, (original_invoice, serial_no))
        if already_returned:
            return {"returnable": False, "reason": frappe._("Serial No {0} already returned in {1}").format(
                serial_no, already_returned[0][0]
            )}

    # Check if scrapped
    if sn.status == "Expired":
        return {"returnable": False, "reason": frappe._("Serial No {0} has been scrapped").format(serial_no)}

    # Check if transferred out (outgoing stock entry after sale).
    # For POS returns against a known original invoice, a transfer alone should
    # NOT block the return — the return invoice reverses the sale and posts
    # stock back through the standard return flow. The transfer guard only
    # applies when no original invoice context is supplied (e.g. ad-hoc
    # eligibility check from elsewhere).
    if not original_invoice:
        transferred = frappe.db.sql("""
            SELECT se.name
            FROM `tabStock Entry Detail` sed
            JOIN `tabStock Entry` se ON se.name = sed.parent
            WHERE sed.serial_no LIKE %s
              AND se.docstatus = 1
              AND se.stock_entry_type = 'Material Transfer'
              AND se.posting_date >= CURDATE() - INTERVAL 365 DAY
            ORDER BY se.posting_date DESC
            LIMIT 1
        """, (f"%{serial_no}%",))
        if transferred:
            return {"returnable": False, "reason": frappe._("Serial No {0} was transferred via {1}").format(
                serial_no, transferred[0][0]
            )}

    # Check if in a completed buyback
    buyback = frappe.db.get_value(
        "Buyback Assessment",
        {"imei_serial": serial_no, "status": "Complete"},
        "name",
    )
    if buyback:
        return {"returnable": False, "reason": frappe._("Serial No {0} was used in buyback {1}").format(
            serial_no, buyback
        )}

    # Check CH Serial Lifecycle status — only "Sold" or "Delivered" devices can be returned
    if frappe.db.exists("CH Serial Lifecycle", serial_no):
        lifecycle_status = frappe.db.get_value("CH Serial Lifecycle", serial_no, "lifecycle_status")
        if lifecycle_status and lifecycle_status not in ("Sold", "Delivered"):
            return {
                "returnable": False,
                "reason": frappe._("Serial No {0} cannot be returned — current lifecycle status is {1}").format(
                    serial_no, lifecycle_status
                ),
            }

    return {"returnable": True, "serial_no": serial_no, "item_code": sn.item_code}


# ── Repair / Job Assignment from POS ────────────────────────
@frappe.whitelist()
def create_repair_job_from_pos(service_request) -> dict:
    """Accept a Service Request and create Job Assignment in one step from POS."""
    frappe.has_permission("Service Request", "write", throw=True)
    try:
        from gofix.gofix_services.doctype.service_request.service_request import accept_service_request
        from gofix.gofix_services.doctype.job_assignment.job_assignment import create_job_sheet_from_service_order
    except ImportError:
        frappe.throw(frappe._("GoFix app is not installed — cannot create repair jobs from POS."))

    # Auto-fill mandatory fields if missing (POS one-click flow)
    sr = frappe.get_doc("Service Request", service_request)
    if not sr.estimated_cost or flt(sr.estimated_cost) <= 0:
        sr.db_set("estimated_cost", 0.01, update_modified=False)  # placeholder, technician updates later
    if not sr.expected_completion_date:
        sr.db_set("expected_completion_date", frappe.utils.add_days(frappe.utils.today(), 3), update_modified=False)

    # Step 1: Accept SR → creates Service Order
    service_order = accept_service_request(service_request)
    if not service_order:
        frappe.throw(frappe._("Failed to create Service Order for {0}").format(service_request))

    # Step 2: Create Job Assignment from Service Order
    job_name = create_job_sheet_from_service_order(
        service_order=service_order,
        job_type="Repair",
    )

    return {
        "service_request": service_request,
        "service_order": service_order,
        "job_assignment": job_name,
    }


@frappe.whitelist()
def get_store_repairs(pos_profile) -> dict:
    """Get open Service Requests and Job Assignments for the store."""
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    warehouse = profile.warehouse

    # Open Service Requests for this store
    service_requests = frappe.db.get_all(
        "Service Request",
        filters={
            "source_warehouse": warehouse,
            "status": ["in", ["Open", "Draft", "In Service", "Waiting for Parts", "Ready for Delivery", "Completed"]],
        },
        fields=[
            "name", "customer", "customer_name", "device_item", "serial_no",
            "issue_category", "status", "decision", "priority",
            "service_order", "creation",
        ],
        order_by="creation desc",
        limit=30,
    )

    # Enrich with Job Assignment info
    for sr in service_requests:
        sr["job_assignment"] = None
        sr["billed"] = False
        sr["estimated_cost"] = frappe.db.get_value("Service Request", sr.name, "estimated_cost") or 0
        if sr.service_order:
            ja = frappe.db.get_value(
                "Job Assignment",
                {"service_order": sr.service_order},
                ["name", "assignment_status", "service_engineer"],
                as_dict=True,
            )
            if ja:
                sr["job_assignment"] = ja.name
                sr["job_status"] = ja.assignment_status
                sr["technician"] = ja.service_engineer
        # Check if already billed via Sales Invoice or Sales Invoice
        sr["billed"] = bool(frappe.db.get_value(
            "Sales Invoice Item",
            {"description": ["like", f"%{sr.name}%"], "docstatus": 1},
            "parent",
        ) or frappe.db.get_value(
            "Service Request", sr.name, "service_invoice"
        ))

    return service_requests


@frappe.whitelist()
def collect_repair_payment(service_request, amount, mode_of_payment, pos_profile,
                           customer="Walk-in Customer", service_order=None, upi_txn_id=None) -> dict:
    """Create a Sales Invoice to collect payment for a completed repair job.

    Marks Service Request as billed after invoice submission.
    """
    frappe.has_permission("Sales Invoice", "create", throw=True)
    amount = flt(amount)
    if amount <= 0:
        frappe.throw(frappe._("Repair charge must be greater than zero"))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    sr_doc = frappe.get_doc("Service Request", service_request)

    # Find or create a repair service item (must be sellable: Active lifecycle)
    repair_item = frappe.db.get_value(
        "Item",
        {"item_name": "Repair Service", "disabled": 0, "ch_lifecycle_status": "Active"},
        "name",
    )
    if not repair_item:
        repair_item = frappe.db.get_value(
            "Item",
            {
                "item_group": "Services",
                "disabled": 0,
                "is_stock_item": 0,
                "ch_lifecycle_status": "Active",
            },
            "name",
        )
    if not repair_item:
        frappe.throw(frappe._("No 'Repair Service' item found. Please create a non-stock service item named 'Repair Service'."))

    inv = frappe.new_doc("Sales Invoice")
    inv.pos_profile = pos_profile
    inv.customer = customer
    inv.company = profile.company
    inv.selling_price_list = profile.selling_price_list
    inv.currency = profile.currency or frappe.get_cached_value("Company", profile.company, "default_currency")
    inv.warehouse = profile.warehouse
    inv.posting_date = nowdate()
    inv.is_pos = 1
    inv.update_stock = 0  # Service item — no stock movement

    inv.append("items", {
        "item_code": repair_item,
        "qty": 1,
        "rate": amount,
        "uom": "Nos",
        "description": f"Repair: {service_request}" + (f" · {service_order}" if service_order else ""),
    })

    payment_row = {"mode_of_payment": mode_of_payment, "amount": amount}
    if upi_txn_id:
        payment_row["custom_upi_transaction_id"] = upi_txn_id
    inv.append("payments", payment_row)

    for tax in profile.get("taxes", []):
        inv.append("taxes", {
            "charge_type": tax.charge_type,
            "account_head": tax.account_head,
            "rate": tax.rate,
            "description": tax.description or tax.account_head,
        })

    inv.insert(ignore_permissions=True)
    # BRD POS exception: POS invoices bypass Maker-Checker (BRD Section 3.1)
    inv.workflow_state = "Approved"
    inv.custom_si_approval_state = "Approved"
    inv.submit()

    # Mark service request as billed
    try:
        frappe.db.set_value("Service Request", service_request, "service_invoice", inv.name, update_modified=False)
    except Exception:
        pass  # custom field may not exist yet

    # Increment repair intake counter
    try:
        _get_active_session = _get_active_session_log(pos_profile)
    except Exception:
        pass

    return {"invoice": inv.name, "grand_total": inv.grand_total}


@frappe.whitelist()
def get_repair_closure_data(service_request) -> dict:
    """Return all data needed by the Repair Closure Dialog:
    technician, spare parts, service items, solutions, estimated cost, customer, SO/JA names.
    """
    sr = frappe.get_doc("Service Request", service_request)

    # Get store-specific users from CH Store mapping; fall back to role-based global list
    _sr_warehouse = sr.source_warehouse or ""
    _store_name = None
    if _sr_warehouse:
        _pos_profile_name = frappe.db.get_value("POS Profile", {"warehouse": _sr_warehouse}, "name")
        if _pos_profile_name:
            _store_name = frappe.db.get_value("CH Store", {"pos_profile": _pos_profile_name, "disabled": 0}, "name")
    if _store_name:
        _rows = frappe.db.get_all(
            "CH Store User",
            filters={"parent": _store_name, "parenttype": "CH Store"},
            fields=["user as name", "full_name"],
            order_by="full_name",
        )
        for r in _rows:
            if not r.full_name:
                r.full_name = frappe.db.get_value("User", r.name, "full_name") or r.name
        eng_users = _rows
    else:
        eng_users = frappe.db.sql("""
            SELECT DISTINCT u.name, u.full_name FROM `tabUser` u
            JOIN `tabHas Role` hr ON hr.parent = u.name
            WHERE hr.role IN ('Service Engineer','Service Manager','Technician') AND u.enabled=1
            ORDER BY u.full_name
        """, as_dict=True)

    # --- Spare parts: prefer spare_lines (new), fall back to spare_parts (legacy) ---
    spare_parts = []
    for row in sr.get("spare_lines", []):
        if row.status == "Damaged":
            continue
        item_code = row.spare_item
        warranty_months = cint(frappe.db.get_value("Item", item_code, "ch_default_warranty_months")) if item_code else 0
        spare_parts.append({
            "spare_part_item": item_code,
            "item_name": row.item_name or "",
            "qty": flt(row.qty) or 1,
            "uom": row.get("uom") or "Nos",
            "rate": flt(row.rate),
            "amount": flt(row.amount or (row.qty * row.rate)),
            "warranty_months": warranty_months,
        })
    # Fallback 1: legacy spare_parts child table
    if not spare_parts:
        legacy = frappe.db.get_all(
            "Service Request Spare Part",
            filters={"parent": service_request, "parenttype": "Service Request"},
            fields=["spare_part_item", "item_name", "qty", "uom", "rate", "amount"],
            order_by="idx",
        )
        for row in legacy:
            item_code = row.spare_part_item
            warranty_months = cint(frappe.db.get_value("Item", item_code, "ch_default_warranty_months")) if item_code else 0
            row["warranty_months"] = warranty_months
        spare_parts = legacy
    # Fallback 2: pull from Solution Spare Mapping if solutions exist but no spares recorded
    if not spare_parts:
        solution_names = [r.repair_solution for r in sr.get("solution_lines", []) if r.status == "Completed" and r.requires_spare]
        if solution_names:
            mappings = frappe.db.get_all(
                "Solution Spare Mapping",
                filters={"repair_solution": ["in", solution_names], "is_active": 1},
                fields=["spare_item", "item_name", "default_qty", "uom"],
            )
            for m in mappings:
                item_code = m.spare_item
                rate = flt(frappe.db.get_value("Item Price",
                    {"item_code": item_code, "selling": 1}, "price_list_rate")) if item_code else 0
                warranty_months = cint(frappe.db.get_value("Item", item_code, "ch_default_warranty_months")) if item_code else 0
                spare_parts.append({
                    "spare_part_item": item_code,
                    "item_name": m.item_name or "",
                    "qty": flt(m.default_qty) or 1,
                    "uom": m.uom or "Nos",
                    "rate": rate,
                    "amount": rate * (flt(m.default_qty) or 1),
                    "warranty_months": warranty_months,
                    "from_mapping": True,
                })

    # --- Service items from SR ---
    service_items = []
    for row in sr.get("service_items", []):
        service_items.append({
            "item_code": row.service_item,
            "item_name": row.service_item_name or row.get("item_name") or "",
            "rate": flt(row.rate or row.get("actual_cost") or row.get("estimated_cost") or 0),
        })

    # --- Solution lines summary ---
    solutions = []
    for row in sr.get("solution_lines", []):
        solutions.append({
            "repair_solution": row.repair_solution or "",
            "issue_category": row.issue_category or "",
            "status": row.status or "",
        })

    # --- Compute service charge: SR estimated_cost > SO grand_total > 0 ---
    estimated_cost = flt(sr.estimated_cost)
    if not estimated_cost and service_items:
        estimated_cost = sum(i["rate"] for i in service_items)
    if not estimated_cost and sr.service_order:
        estimated_cost = flt(frappe.db.get_value("Sales Order", sr.service_order, "grand_total"))

    ja = None
    so_qc_status = "Pending"
    if sr.service_order:
        ja = frappe.db.get_value(
            "Job Assignment",
            {"service_order": sr.service_order},
            ["name", "service_engineer", "assignment_status"],
            as_dict=True,
        )
        so_qc_status = frappe.db.get_value("Sales Order", sr.service_order, "qc_status") or "Pending"

    return {
        "sr_name": sr.name,
        "customer": sr.customer,
        "customer_name": sr.customer_name or sr.customer,
        "device_item": sr.device_item,
        "serial_no": sr.serial_no or "",
        "estimated_cost": estimated_cost,
        "service_order": sr.service_order or "",
        "job_assignment": ja.name if ja else "",
        "current_technician": (ja.service_engineer if ja else "") or "",
        "qc_status": so_qc_status,
        "spare_parts": spare_parts,
        "service_items": service_items,
        "solutions": solutions,
        "technicians": eng_users,
        "issue_category": sr.issue_category or "",
        "status": sr.status or "",
        "decision": sr.decision or "",
        "priority": sr.priority or "",
        "service_invoice": sr.service_invoice or "",
    }


@frappe.whitelist()
def close_repair_order(service_request, pos_profile, payments, qc_result,
                       qc_remarks="", delivery_ack=0, delivery_note="",
                       technician="", spare_parts=None, service_charge=0) -> None:
    """Complete the repair closure flow in one atomic call.

    Steps:
    1. Assign technician to Job Assignment (if provided)
    2. Save spare parts onto Service Request (replace child rows)
    3. Set QC status on the linked Sales Order
    4. Create Sales Invoice with service charge line + one line per spare part
    5. Create Material Issue Stock Entry for spare parts
    6. Mark SR as delivered / closed

    payments: list of {mode_of_payment, amount, reference_no}
    spare_parts: list of {spare_part_item, item_name, qty, uom, rate}
    qc_result: "Pass" | "Fail" | "Not Repairable" | "Customer Cancelled"
    """
    frappe.has_permission("Sales Invoice", "create", throw=True)

    if isinstance(payments, str):
        payments = frappe.parse_json(payments)
    if isinstance(spare_parts, str):
        spare_parts = frappe.parse_json(spare_parts) or []

    payments = payments or []
    spare_parts = spare_parts or []
    service_charge = flt(service_charge)

    total_parts = sum(flt(p.get("qty", 1)) * flt(p.get("rate", 0)) for p in spare_parts)
    grand_total = service_charge + total_parts
    payment_total = sum(flt(p.get("amount", 0)) for p in payments)

    if grand_total <= 0:
        frappe.throw(frappe._("Total charge must be greater than zero"))
    if abs(payment_total - grand_total) > 0.01:
        frappe.throw(frappe._("Payment total {0} does not match invoice total {1}").format(
            fmt_money(payment_total), fmt_money(grand_total)))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    sr = frappe.get_doc("Service Request", service_request)

    # Guard: don't double-bill
    if frappe.db.get_value("Service Request", service_request, "service_invoice"):
        frappe.throw(frappe._("Service Request {0} is already billed").format(service_request))

    # 1 — Assign technician
    if technician and sr.service_order:
        ja = frappe.db.get_value("Job Assignment", {"service_order": sr.service_order}, "name")
        if ja:
            frappe.db.set_value("Job Assignment", ja, "service_engineer", technician, update_modified=False)

    # 2 — Update spare parts on Service Request
    if spare_parts:
        frappe.db.delete("Service Request Spare Part", {
            "parent": service_request, "parenttype": "Service Request"
        })
        for idx, part in enumerate(spare_parts, start=1):
            qty = flt(part.get("qty", 1))
            rate = flt(part.get("rate", 0))
            frappe.get_doc({
                "doctype": "Service Request Spare Part",
                "parent": service_request,
                "parentfield": "spare_parts",
                "parenttype": "Service Request",
                "idx": idx,
                "spare_part_item": part.get("spare_part_item"),
                "item_name": (part.get("item_name")
                              or frappe.db.get_value("Item", part.get("spare_part_item"), "item_name")),
                "qty": qty,
                "uom": part.get("uom") or "Nos",
                "rate": rate,
                "amount": qty * rate,
            }).db_insert()

    # 3 — Update QC on Sales Order
    qc_wf_map = {
        "Pass": ("Pass", "QC Pass"),
        "Fail": ("Fail", "QC Fail"),
        "Not Repairable": ("Not Repairable", "Not Repairable"),
        "Customer Cancelled": ("Customer Cancelled", "Customer Cancelled"),
    }
    if sr.service_order and qc_result in qc_wf_map:
        qc_status, wf_state = qc_wf_map[qc_result]
        frappe.db.set_value("Sales Order", sr.service_order, {
            "qc_status": qc_status,
            "qc_remarks": qc_remarks,
            "qc_checked_by": frappe.session.user,
            "qc_datetime": now_datetime(),
            "workflow_state": wf_state,
        }, update_modified=False)

    # 4 — Build and submit Sales Invoice
    # Resolve spare part item codes (user may have typed a name instead of a code)
    for part in spare_parts:
        code = part.get("spare_part_item", "")
        if code and not frappe.db.exists("Item", code):
            # Try resolving by item_name
            resolved = frappe.db.get_value("Item", {"item_name": code, "disabled": 0}, "name")
            if resolved:
                part["spare_part_item"] = resolved
            else:
                frappe.throw(frappe._("Spare part item '{0}' not found. Please use a valid item code or name.").format(code))

    repair_item = frappe.db.get_value("Item", {"item_name": "Repair Service", "disabled": 0}, "name")
    if not repair_item:
        # Try "Repair Services" item group (actual group in this system)
        repair_item = frappe.db.get_value("Item",
            {"item_group": "Repair Services", "disabled": 0, "is_stock_item": 0}, "name")
    if not repair_item:
        repair_item = frappe.db.get_value("Item",
            {"item_group": "Services", "disabled": 0, "is_stock_item": 0}, "name")
    if not repair_item:
        frappe.throw(frappe._("No service item found. Create a non-stock item in the 'Repair Services' group."))

    inv = frappe.new_doc("Sales Invoice")
    inv.pos_profile = pos_profile
    inv.customer = sr.customer
    inv.company = profile.company
    inv.selling_price_list = profile.selling_price_list
    inv.currency = (profile.currency
                    or frappe.get_cached_value("Company", profile.company, "default_currency"))
    inv.warehouse = profile.warehouse
    inv.posting_date = nowdate()
    inv.is_pos = 1
    inv.update_stock = 0  # spare parts handled via Stock Entry below

    # Link GoFix service details
    inv.custom_gofix_service_request = service_request
    if sr.service_order:
        inv.custom_gofix_service_order = sr.service_order

    if service_charge > 0:
        inv.append("items", {
            "item_code": repair_item,
            "qty": 1,
            "rate": service_charge,
            "uom": "Nos",
            "description": f"Repair Service — {service_request}",
        })

    for part in spare_parts:
        qty = flt(part.get("qty", 1))
        rate = flt(part.get("rate", 0))
        if qty and rate:
            item_code = part.get("spare_part_item")
            warranty_months = cint(frappe.db.get_value("Item", item_code, "ch_default_warranty_months")) if item_code else 0
            desc = f"Part — {service_request}"
            if warranty_months:
                desc += f" (Warranty: {warranty_months} months)"
            inv.append("items", {
                "item_code": item_code,
                "qty": qty,
                "rate": rate,
                "uom": part.get("uom") or "Nos",
                "description": desc,
            })

    for p in payments:
        pay_row = {"mode_of_payment": p["mode_of_payment"], "amount": flt(p["amount"])}
        if p.get("reference_no"):
            pay_row["custom_upi_transaction_id"] = p["reference_no"]
        inv.append("payments", pay_row)

    # Apply tax template from POS Profile if set
    if getattr(profile, "taxes_and_charges", None):
        inv.taxes_and_charges = profile.taxes_and_charges

    inv.insert(ignore_permissions=True)
    # BRD POS exception: POS invoices bypass Maker-Checker (BRD Section 3.1)
    inv.workflow_state = "Approved"
    inv.custom_si_approval_state = "Approved"
    inv.submit()

    # 5 — Stock Entry for spare parts consumption
    stock_entry_name = None
    if spare_parts and profile.warehouse:
        try:
            se = frappe.new_doc("Stock Entry")
            se.stock_entry_type = "Material Issue"
            se.company = profile.company
            se.posting_date = nowdate()
            se.remarks = f"Spare parts consumed for {service_request}"
            for part in spare_parts:
                if not flt(part.get("qty", 0)):
                    continue
                se.append("items", {
                    "item_code": part["spare_part_item"],
                    "qty": flt(part.get("qty", 1)),
                    "uom": part.get("uom") or "Nos",
                    "s_warehouse": profile.warehouse,
                    "basic_rate": flt(part.get("rate", 0)),
                })
            se.insert(ignore_permissions=True)
            se.submit()
            stock_entry_name = se.name
        except Exception as e:
            frappe.log_error(
                f"Repair closure stock entry failed for {service_request}: {e}",
                "Repair Closure",
            )

    # 6 — Mark SR closed
    sr_updates = {"service_invoice": inv.name, "status": "Completed"}
    if int(delivery_ack):
        sr_updates["delivery_mode"] = "Walk-in"
    if delivery_note:
        sr_updates["customer_remarks"] = delivery_note
    frappe.db.set_value("Service Request", service_request, sr_updates, update_modified=False)

    if qc_result == "Pass" and sr.service_order:
        frappe.db.set_value("Sales Order", sr.service_order, "workflow_state", "Closed",
                            update_modified=False)

    return {
        "invoice": inv.name,
        "grand_total": inv.grand_total,
        "stock_entry": stock_entry_name,
    }



# ── Buyback Valuation ────────────────────────────────────────
@frappe.whitelist()
def check_imei_blacklist(imei) -> dict:
    """Pre-check if an IMEI is blacklisted before starting a buyback assessment."""
    if not imei:
        return {"blacklisted": False}
    try:
        from buyback.buyback.doctype.buyback_imei_blacklist.buyback_imei_blacklist import is_imei_blacklisted
        entry = is_imei_blacklisted(imei)
        if entry:
            return {"blacklisted": True, "reason": entry.reason, "reference": entry.reference_number or ""}
    except ImportError:
        pass
    return {"blacklisted": False}


@frappe.whitelist()
def calculate_buyback_valuation(item_code, condition_checks) -> dict:
    """Calculate buyback valuation using ch_erp_buyback's centralized pricing engine.

    condition_checks: dict with keys like screen, body, buttons, charging,
    camera, speaker_mic — each True (pass) or False (fail).
    Returns base_price, deductions list, final_price, grade.
    """
    if isinstance(condition_checks, str):
        condition_checks = frappe.parse_json(condition_checks)

    # Auto-grade from condition checks
    check_labels = {
        "screen": "Screen", "body": "Body", "buttons": "Buttons",
        "charging": "Charging", "camera": "Camera", "speaker_mic": "Speaker/Mic",
    }
    fail_count = sum(1 for v in condition_checks.values() if not v)
    fail_pct = (fail_count / max(len(condition_checks), 1)) * 100

    if fail_count == 0:
        grade = "A"
    elif fail_count <= 1 and fail_pct <= 20:
        grade = "B"
    elif fail_count <= 2 and fail_pct <= 40:
        grade = "C"
    elif fail_pct <= 60:
        grade = "D"
    else:
        grade = "F"

    # Build diagnostic test format for pricing engine
    diagnostic_tests = []
    for key, label in check_labels.items():
        passed = condition_checks.get(key, True)
        diagnostic_tests.append({
            "test_code": key,
            "result": "Pass" if passed else "Fail",
        })

    # Get item metadata for the pricing engine
    brand = frappe.db.get_value("Item", item_code, "brand")
    item_group = frappe.db.get_value("Item", item_code, "item_group")

    try:
        from buyback.buyback.pricing.engine import calculate_estimated_price

        result = calculate_estimated_price(
            item_code=item_code,
            grade=grade,
            diagnostic_tests=diagnostic_tests,
            brand=brand,
            item_group=item_group,
        )
        return {
            "base_price": flt(result.get("base_price", 0)),
            "deductions": result.get("deductions", []),
            "total_deduction": flt(result.get("total_deductions", 0)),
            "final_price": flt(result.get("estimated_price", 0)),
            "grade": grade,
        }
    except ImportError:
        frappe.log_error(
            "ch_erp_buyback not installed — using fallback buyback pricing",
            "POS Buyback Fallback",
        )
        return _fallback_buyback_valuation(item_code, grade, condition_checks)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "POS Buyback Pricing Error")
        return _fallback_buyback_valuation(item_code, grade, condition_checks)


def _fallback_buyback_valuation(item_code, grade, condition_checks):
    """Fallback when ch_erp_buyback pricing engine is unavailable."""
    base_price = flt(frappe.db.get_value(
        "CH Item Price",
        {"item_code": item_code, "channel": "Buyback", "status": "Active"},
        "selling_price",
    ))
    if not base_price:
        selling = flt(frappe.db.get_value(
            "CH Item Price",
            {"item_code": item_code, "channel": "POS", "status": "Active"},
            "selling_price",
        ))
        base_price = flt(selling * 0.4)

    # Simple grade-based deduction as fallback
    grade_pct = {"A": 0, "B": 10, "C": 25, "D": 40, "F": 70}
    deduction_pct = grade_pct.get(grade, 50)
    total_deduction = flt(base_price * deduction_pct / 100)
    final_price = max(0, flt(base_price - total_deduction))

    return {
        "base_price": base_price,
        "deductions": [{"label": f"Grade {grade} adjustment", "pct": deduction_pct, "amount": total_deduction}],
        "total_deduction": total_deduction,
        "final_price": final_price,
        "grade": grade,
    }


@frappe.whitelist()
def create_buyback_assessment_with_grading(
    mobile_no, item_code, imei_serial=None, customer=None,
    condition_checks=None, kyc_id_type=None, kyc_id_number=None, kyc_name=None
) -> dict:
    """Create a Buyback Assessment with condition grading and KYC from POS."""
    frappe.has_permission("Buyback Assessment", "create", throw=True)
    if isinstance(condition_checks, str):
        condition_checks = frappe.parse_json(condition_checks)
    condition_checks = condition_checks or {}

    mobile_no = validate_indian_phone(mobile_no, "Mobile No")

    # Validate KYC ID number format
    if kyc_id_type and kyc_id_number:
        kyc_id_number = kyc_id_number.strip()
        if kyc_id_type in ("PAN", "PAN Card"):
            pan_upper = kyc_id_number.upper()
            import re as _re
            if not _re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", pan_upper):
                frappe.throw(
                    _("Invalid PAN '{0}'. Format: ABCDE1234F (5 letters + 4 digits + 1 letter).").format(kyc_id_number)
                )
            kyc_id_number = pan_upper
        elif kyc_id_type in ("Aadhaar", "Aadhar Card"):
            aadhaar_clean = kyc_id_number.replace(" ", "").replace("-", "")
            import re as _re
            if not _re.match(r"^[2-9]\d{11}$", aadhaar_clean):
                frappe.throw(
                    _("Invalid Aadhaar '{0}'. Must be exactly 12 digits, not starting with 0 or 1.").format(kyc_id_number)
                )
            kyc_id_number = aadhaar_clean

    # Calculate valuation
    valuation = calculate_buyback_valuation(item_code, condition_checks)

    doc = frappe.new_doc("Buyback Assessment")
    doc.source = "Store Manual"
    _pos_profile = frappe.form_dict.get("pos_profile") or ""
    _store = ""
    if _pos_profile:
        _store = frappe.db.get_value("POS Profile", _pos_profile, "warehouse") or ""
    if not _store:
        _store = frappe.defaults.get_user_default("warehouse") or frappe.db.get_single_value("Stock Settings", "default_warehouse") or ""
    doc.store = _store
    doc.mobile_no = mobile_no
    doc.customer = customer or ""
    doc.item = item_code
    doc.imei_serial = imei_serial or ""
    doc.estimated_grade = frappe.db.get_value(
        "Grade Master", {"grade_name": valuation["grade"]}, "name"
    ) or ""
    doc.estimated_price = valuation["final_price"]
    doc.quoted_price = valuation["final_price"]
    doc.remarks = _build_grading_remarks(condition_checks, valuation, kyc_id_type, kyc_id_number, kyc_name)

    doc.insert()

    # Auto-submit so the assessment is immediately usable at POS checkout
    try:
        doc.submit_assessment()
    except Exception:
        # If submit fails (e.g. no diagnostics), assessment stays Draft — still usable
        pass

    return {
        "name": doc.name,
        "grade": valuation["grade"],
        "estimated_price": valuation["final_price"],
        "base_price": valuation["base_price"],
        "deductions": valuation["deductions"],
    }


def _build_grading_remarks(condition_checks, valuation, kyc_id_type, kyc_id_number, kyc_name):
    """Build structured remarks from condition checks and KYC."""
    lines = ["=== POS Condition Assessment ==="]
    check_labels = {
        "screen": "Screen", "body": "Body", "buttons": "Buttons",
        "charging": "Charging", "camera": "Camera", "speaker_mic": "Speaker/Mic",
    }
    for key, label in check_labels.items():
        status = "PASS" if condition_checks.get(key, True) else "FAIL"
        lines.append(f"  {label}: {status}")

    lines.append(f"\nGrade: {valuation['grade']}")
    lines.append(f"Base Price: ₹{valuation['base_price']:,.0f}")
    if valuation["deductions"]:
        lines.append("Deductions:")
        for d in valuation["deductions"]:
            lines.append(f"  - {d['label']}: -₹{d['amount']:,.0f} ({d.get('pct') or d.get('percent', 0)}%)")
    lines.append(f"Final Price: ₹{valuation['final_price']:,.0f}")

    if kyc_id_type and kyc_id_number:
        lines.append(f"\n=== KYC ===")
        lines.append(f"  Name: {kyc_name or 'N/A'}")
        lines.append(f"  ID Type: {kyc_id_type}")
        lines.append(f"  ID Number: {kyc_id_number}")

    return "\n".join(lines)


# ── Governance: Manager Approval at POS ──────────────────────────────

@frappe.whitelist()
def request_manager_approval(mobile_no, purpose, reference_doctype=None, reference_name=None) -> dict:
    """Generate an OTP for manager approval at POS.

    Used when a discount exceeds limits, exchange override is needed, etc.
    The OTP is sent to the store manager's mobile and must be verified
    before the transaction can proceed.
    """
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
    from ch_pos.audit import log_business_event

    mobile_no = validate_indian_phone(mobile_no, "Manager Mobile Number")
    if not (purpose or "").strip():
        frappe.throw(frappe._("Approval purpose is required."), title=frappe._("Missing Purpose"))

    otp = CHOTPLog.generate_otp(
        mobile_no=mobile_no,
        purpose=purpose,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    )
    try:
        log_business_event(
            event_type="Other",
            ref_doctype=reference_doctype or "CH OTP Log",
            ref_name=reference_name,
            before="OTP Requested",
            after=purpose,
            remarks=f"Manager approval OTP requested for {purpose}",
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Manager approval request audit failed")
    # Send OTP across all channels — SMS, WhatsApp and email.
    channels = {}
    try:
        from buyback.buyback.whatsapp_notifications import send_otp as _send_otp
        channels = _send_otp(mobile_no, otp, purpose,
                             ref_doctype=reference_doctype or "CH OTP Log",
                             ref_name=reference_name or "")
    except Exception:
        frappe.log_error(title="Manager OTP delivery failed")
    return {"sent": True, "mobile": mobile_no[:3] + "****" + mobile_no[-3:],
            "otp_generated": bool(otp), "channels": channels}


@frappe.whitelist()
def verify_manager_approval(mobile_no, purpose, otp_code, reference_doctype=None, reference_name=None, approval_reason=None) -> dict:
    """Verify a manager OTP for POS approval.

    Returns {"valid": True/False, "message": str}.
    """
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
    from ch_pos.audit import log_business_event

    mobile_no = validate_indian_phone(mobile_no, "Manager Mobile Number")
    result = CHOTPLog.verify_otp(
        mobile_no=mobile_no,
        purpose=purpose,
        otp_code=otp_code,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    )

    try:
        log_business_event(
            event_type="Exception Approved" if result.get("valid") else "Other",
            ref_doctype=reference_doctype or "CH OTP Log",
            ref_name=reference_name,
            before="OTP Pending",
            after="Verified" if result.get("valid") else "Rejected",
            remarks=(approval_reason or purpose or "Manager approval verification").strip(),
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Manager approval verification audit failed")
    return result


@frappe.whitelist()
def request_customer_whatsapp_otp(mobile_no, purpose="POS Customer Verification", customer_name="Customer", email_id=None) -> dict:
    """Generate and send OTP for customer WhatsApp verification before quick create."""
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    mobile_no = validate_indian_phone(mobile_no)
    to_email = (email_id or "").strip()
    if to_email:
        to_email = validate_email_address(to_email, throw=True)
    purpose = "POS Customer Verification"

    try:
        otp_code = CHOTPLog.generate_otp(
            mobile_no=mobile_no,
            purpose=purpose,
            reference_doctype="Customer",
            reference_name="",
        )
    except Exception as e:
        if "Purpose cannot be" in str(e):
            frappe.throw(
                _("OTP purpose setup is missing for POS customer verification. Please contact administrator."),
                title=_("OTP Setup Error"),
            )
        raise

    sent_whatsapp = False
    sent_email = False
    try:
        from ch_item_master.ch_core.whatsapp import get_whatsapp_settings, send_template_message
        _company = frappe.defaults.get_user_default("Company")
        wa_settings = get_whatsapp_settings(_company)
        if wa_settings and cint(wa_settings.enabled):
            template_name = wa_settings.get("general_otp") or "ch_otp_verification"
            send_template_message(
                phone=mobile_no,
                template_name=template_name,
                body_values={"1": otp_code},
                customer_name=(customer_name or "Customer")[:140],
                ref_doctype="Customer",
                ref_name="",
                enqueue=False,
                company=_company,
            )
            sent_whatsapp = True
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Customer WhatsApp OTP delivery failed")

    try:
        from buyback.buyback.whatsapp_notifications import send_otp_email, _get_email_for_mobile
        if not to_email:
            to_email = _get_email_for_mobile(mobile_no)
        if to_email:
            sent_email = send_otp_email(to_email, otp_code, purpose, "")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Customer OTP email delivery failed")

    if not sent_whatsapp and not sent_email:
        frappe.throw(
            _("OTP was generated, but no delivery channel is enabled. Enable WhatsApp OTP or provide a valid customer email."),
            title=_("OTP Delivery Not Configured"),
        )

    return {
        "sent": True,
        "mobile": mobile_no[:3] + "****" + mobile_no[-3:],
        "otp_generated": bool(otp_code),
        "sent_whatsapp": sent_whatsapp,
        "sent_email": sent_email,
    }


# ── Discount Authorisation ───────────────────────────────────────────────────

@frappe.whitelist()
def verify_discount_auth(
    pos_profile: str,
    authorizer_executive: str,
    password: str,
    discount_pct: float = 0,
    discount_amount: float = 0,
    net_total: float = 0,
) -> dict:
    """Verify that a POS executive is authorised to approve a given discount level.

    Steps:
      1. Resolve executive → Frappe user (must belong to this store)
      2. Verify the supplied Frappe password for that user
      3. Check can_give_discount=1 and effective_pct ≤ max_discount_pct
      4. Log to business audit trail
    Returns {authorized, authorized_executive, authorized_by_name, role, max_pct, message}
    """
    from frappe.utils.password import check_password
    from ch_pos.audit import log_business_event

    # Resolve store from POS Profile Extension
    store = frappe.db.get_value("POS Profile Extension", {"pos_profile": pos_profile}, "store")

    # Get executive record — must belong to this store and be active
    filters = {"name": authorizer_executive, "is_active": 1}
    if store:
        filters["store"] = store

    exec_doc = frappe.db.get_value(
        "POS Executive",
        filters,
        ["name", "executive_name", "user", "role", "can_give_discount", "max_discount_pct"],
        as_dict=True,
    )
    if not exec_doc:
        frappe.throw(frappe._("Executive not found or not active for this store."), title=frappe._("Not Authorized"))

    if not exec_doc.user:
        frappe.throw(
            frappe._("No Frappe user linked to {0}. Contact administrator.").format(exec_doc.executive_name),
            title=frappe._("Configuration Error"),
        )

    # Verify password
    try:
        check_password(exec_doc.user, password)
    except frappe.AuthenticationError:
        frappe.throw(frappe._("Invalid password for {0}.").format(exec_doc.executive_name), title=frappe._("Auth Failed"))

    # Check discount permission
    if not cint(exec_doc.can_give_discount):
        frappe.throw(
            frappe._("{0} does not have permission to give discounts.").format(exec_doc.executive_name),
            title=frappe._("Not Authorized"),
        )

    # Compute effective discount %
    effective_pct = flt(discount_pct)
    if effective_pct <= 0 and flt(discount_amount) > 0 and flt(net_total) > 0:
        effective_pct = flt(discount_amount) / flt(net_total) * 100

    max_pct = flt(exec_doc.max_discount_pct)
    if max_pct > 0 and effective_pct > max_pct:
        frappe.throw(
            frappe._("Requested discount {0}% exceeds {1}'s authorised limit of {2}%.").format(
                round(effective_pct, 2), exec_doc.executive_name, max_pct
            ),
            title=frappe._("Exceeds Limit"),
        )

    # Audit trail
    try:
        log_business_event(
            event_type="Other",
            ref_doctype="POS Profile",
            ref_name=pos_profile,
            before="Discount Pending",
            after=f"Authorized {round(effective_pct, 2)}%",
            remarks=f"Discount authorised by {exec_doc.executive_name} ({exec_doc.role}); limit {max_pct}%",
        )
    except Exception:
        frappe.log_error(title="Discount auth audit log failed")

    return {
        "authorized": True,
        "authorized_executive": exec_doc.name,
        "authorized_by_name": exec_doc.executive_name,
        "role": exec_doc.role,
        "max_pct": max_pct,
        "message": frappe._("Authorised by {0}").format(exec_doc.executive_name),
    }


@frappe.whitelist()
def verify_customer_whatsapp_otp(mobile_no, otp_code, purpose="POS Customer Verification") -> dict:
    """Verify customer OTP used during POS quick customer creation."""
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    mobile_no = validate_indian_phone(mobile_no)
    purpose = "POS Customer Verification"

    return CHOTPLog.verify_otp(
        mobile_no=mobile_no,
        purpose=purpose,
        otp_code=str(otp_code or "").strip(),
        reference_doctype="Customer",
        reference_name="",
    )


def _resolve_pos_city_state(city, state):
    city = (city or "").strip()
    state = (state or "").strip()
    if not city or not frappe.db.exists("DocType", "CH City"):
        # Even without a city we still want to canonicalise + ensure the
        # CH State master row exists when a state was supplied.
        if state and frappe.db.exists("DocType", "CH State"):
            try:
                from ch_item_master.ch_core.doctype.ch_state.ch_state import ensure_state
                state = ensure_state(state) or state
            except Exception:
                pass
        return city, state

    city_row = frappe.db.get_value("CH City", city, ["city_name", "state"], as_dict=True)
    if not city_row:
        city_row = frappe.db.get_value(
            "CH City",
            {"city_name": city, "disabled": 0},
            ["city_name", "state"],
            as_dict=True,
        )
    if city_row:
        city = city_row.city_name or city
        state = state or city_row.state or ""

    # Canonicalise state via CH State master so customer.state always points
    # at a valid Link target (Oracle TCA / Dynamics 365 reference-data pattern).
    if state and frappe.db.exists("DocType", "CH State"):
        try:
            from ch_item_master.ch_core.doctype.ch_state.ch_state import ensure_state
            state = ensure_state(state) or state
        except Exception:
            pass

    return city, state


@frappe.whitelist()
def get_customer_loyalty(customer, company=None) -> dict:
    """Get loyalty balance for a customer using the active company's redemption rate."""
    from ch_item_master.ch_customer_master.loyalty import get_ch_loyalty_info
    if not company:
        company = frappe.defaults.get_user_default("Company")
    return get_ch_loyalty_info(customer, company=company)


@frappe.whitelist()
def imei_history(serial_no) -> dict:
    """Full lifecycle of a serial number / IMEI: sales, returns, service, buyback."""
    # Coerce — all-digit IMEIs can arrive as JSON numbers.
    serial_no = str(serial_no or "").strip()

    # Try direct Serial No lookup
    if not frappe.db.exists("Serial No", serial_no):
        # Try searching by barcode -> serial
        item_barcode = frappe.db.get_value("Item Barcode", {"barcode": serial_no}, ["parent", "barcode"], as_dict=True)
        if item_barcode:
            # Barcode found — check if there's a serial matching
            serial_match = frappe.db.get_value("Serial No", {"item_code": item_barcode.parent, "name": serial_no}, "name")
            if not serial_match:
                return {"error": f"Barcode maps to item {item_barcode.parent} but no serial '{serial_no}' found"}
        else:
            return {"error": f"No serial number or barcode found for '{serial_no}'"}

    sn = frappe.get_doc("Serial No", serial_no)
    out = {
        "serial_no": sn.name,
        "item_code": sn.item_code,
        "item_name": sn.item_name,
        "brand": sn.brand,
        "warehouse": sn.warehouse,
        "status": sn.status,
        "customer": sn.customer,
        "customer_name": sn.customer_name if hasattr(sn, "customer_name") else sn.customer,
        "warranty_expiry_date": str(sn.warranty_expiry_date) if sn.warranty_expiry_date else None,
        "amc_expiry_date": str(sn.amc_expiry_date) if sn.amc_expiry_date else None,
    }

    # Sales history — Sales Invoice items referencing this serial
    out["sales"] = frappe.db.sql("""
        SELECT pii.parent as invoice, pii.item_code, pii.item_name, pii.rate,
               pi.customer, pi.customer_name, pi.posting_date as date
        FROM `tabSales Invoice Item` pii
        JOIN `tabSales Invoice` pi ON pi.name = pii.parent
        WHERE pii.serial_no LIKE %(sn_pattern)s
          AND pi.docstatus = 1 AND pi.is_return = 0
        ORDER BY pi.posting_date DESC
    """, {"sn_pattern": f"%{serial_no}%"}, as_dict=True)

    # Returns
    out["returns"] = frappe.db.sql("""
        SELECT pii.parent as invoice, pii.item_code, pii.item_name, pii.rate,
               pi.customer, pi.customer_name, pi.posting_date as date
        FROM `tabSales Invoice Item` pii
        JOIN `tabSales Invoice` pi ON pi.name = pii.parent
        WHERE pii.serial_no LIKE %(sn_pattern)s
          AND pi.docstatus = 1 AND pi.is_return = 1
        ORDER BY pi.posting_date DESC
    """, {"sn_pattern": f"%{serial_no}%"}, as_dict=True)

    # Sales Invoice items (non-POS)
    si_sales = frappe.db.sql("""
        SELECT sii.parent as invoice, sii.item_code, sii.item_name, sii.rate,
               si.customer, si.customer_name, si.posting_date as date
        FROM `tabSales Invoice Item` sii
        JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE sii.serial_no LIKE %(sn_pattern)s
          AND si.docstatus = 1 AND si.is_return = 0 AND si.is_pos = 0
        ORDER BY si.posting_date DESC
    """, {"sn_pattern": f"%{serial_no}%"}, as_dict=True)
    out["sales"].extend(si_sales)
    out["sales"].sort(key=lambda x: x.get("date", ""), reverse=True)

    # Service requests  
    out["services"] = frappe.db.sql("""
        SELECT name, customer_name, device_item_name, issue_category,
               issue_description, decision, status, service_date as date, estimated_cost
        FROM `tabService Request`
        WHERE (actual_imei = %(sn)s OR serial_no = %(sn)s)
          AND docstatus < 2
        ORDER BY creation DESC
    """, {"sn": serial_no}, as_dict=True)

    # Buyback assessments
    out["buybacks"] = frappe.db.sql("""
        SELECT name, item_name, brand, estimated_grade, estimated_price,
               quoted_price, status, creation as date
        FROM `tabBuyback Assessment`
        WHERE imei_serial = %(sn)s AND docstatus < 2
        ORDER BY creation DESC
    """, {"sn": serial_no}, as_dict=True)
    for b in out["buybacks"]:
        b["price"] = flt(b.get("quoted_price") or b.get("estimated_price"))
        b["grade"] = b.get("estimated_grade", "")

    return out


@frappe.whitelist()
def customer_360(identifier, company=None) -> dict:
    """Complete customer profile: purchases, service requests, buybacks, loyalty."""
    identifier = (identifier or "").strip()
    if not identifier:
        return {"error": "Please enter a phone number, name, or customer ID"}

    if not company:
        company = frappe.defaults.get_user_default("Company")

    # Find customer
    customer = None
    if frappe.db.exists("Customer", identifier):
        customer = identifier
    else:
        # Search by mobile, phone, or name
        customer = frappe.db.get_value(
            "Customer",
            {"mobile_no": identifier},
            "name",
        )
        if not customer:
            # Search by alternate phone / whatsapp custom fields
            customer = frappe.db.get_value(
                "Customer",
                {"ch_alternate_phone": identifier},
                "name",
            )
        if not customer:
            customer = frappe.db.get_value(
                "Customer",
                {"ch_whatsapp_number": identifier},
                "name",
            )
        if not customer:
            # Try Dynamic Link → Contact with phone or mobile_no
            contact_phone = frappe.db.sql("""
                SELECT dl.link_name
                FROM `tabContact Phone` cp
                JOIN `tabContact` c ON c.name = cp.parent
                JOIN `tabDynamic Link` dl ON dl.parent = c.name AND dl.link_doctype = 'Customer'
                WHERE cp.phone = %(phone)s
                LIMIT 1
            """, {"phone": identifier}, as_dict=True)
            if contact_phone:
                customer = contact_phone[0].link_name
        if not customer:
            # Fuzzy name search
            results = frappe.db.sql("""
                SELECT name FROM `tabCustomer`
                WHERE customer_name LIKE %(q)s OR name LIKE %(q)s
                LIMIT 1
            """, {"q": f"%{identifier}%"}, as_dict=True)
            if results:
                customer = results[0].name

    if not customer:
        return {"error": f"No customer found for '{identifier}'"}

    cust_doc = frappe.get_doc("Customer", customer)
    out = {
        "customer": cust_doc.name,
        "customer_name": cust_doc.customer_name,
        "mobile_no": cust_doc.mobile_no or "",
        "email_id": cust_doc.email_id or "",
        "membership_id": cust_doc.get("ch_membership_id") or "",
        "alternate_phone": cust_doc.get("ch_alternate_phone") or "",
        "whatsapp_number": cust_doc.get("ch_whatsapp_number") or "",
        "previous_phones": cust_doc.get("ch_previous_phones") or "",
        "pan": cust_doc.get("ch_pan_number") or cust_doc.get("pan") or "",
    }

    # Bill-To and Ship-To addresses via Dynamic Link
    _addr_rows = frappe.db.sql("""
        SELECT a.name, a.address_line1, a.address_line2, a.city, a.state, a.pincode,
               a.is_primary_address, a.is_shipping_address, a.address_type
        FROM `tabAddress` a
        JOIN `tabDynamic Link` dl ON dl.parent = a.name
        WHERE dl.link_doctype = 'Customer' AND dl.link_name = %(customer)s
        ORDER BY a.is_primary_address DESC, a.is_shipping_address DESC
        LIMIT 10
    """, {"customer": customer}, as_dict=True)

    def _fmt_addr(row):
        parts = [row.address_line1, row.address_line2, row.city, row.state, row.pincode]
        return ", ".join(p for p in parts if p)

    bill_to = next((r for r in _addr_rows if r.is_primary_address), None) \
              or next((r for r in _addr_rows if r.address_type == "Billing"), None)
    ship_to = next((r for r in _addr_rows if r.is_shipping_address), None) \
              or next((r for r in _addr_rows if r.address_type == "Shipping"), None)

    out["bill_to_address"] = _fmt_addr(bill_to) if bill_to else ""
    out["ship_to_address"] = _fmt_addr(ship_to) if ship_to else ""

    # Invoices
    out["invoices"] = frappe.db.sql("""
        SELECT name, posting_date, grand_total, status,
               (SELECT COUNT(*) FROM `tabSales Invoice Item` WHERE parent = pi.name) as items_count
        FROM `tabSales Invoice` pi
        WHERE customer = %(customer)s AND docstatus = 1
        ORDER BY posting_date DESC LIMIT 50
    """, {"customer": customer}, as_dict=True)

    # Also check Sales Invoices (non-POS)
    si_invoices = frappe.db.sql("""
        SELECT name, posting_date, grand_total, status,
               (SELECT COUNT(*) FROM `tabSales Invoice Item` WHERE parent = si.name) as items_count
        FROM `tabSales Invoice` si
        WHERE customer = %(customer)s AND docstatus = 1 AND is_pos = 0
        ORDER BY posting_date DESC LIMIT 20
    """, {"customer": customer}, as_dict=True)
    out["invoices"].extend(si_invoices)
    out["invoices"].sort(key=lambda x: x.get("posting_date", ""), reverse=True)

    out["total_invoices"] = len(out["invoices"])
    out["total_spent"] = sum(flt(i.get("grand_total", 0)) for i in out["invoices"] if i.get("status") != "Return")

    # Service Requests
    out["service_requests"] = frappe.db.sql("""
        SELECT name, customer_name, device_item_name, issue_category,
               decision, status, service_date, creation, estimated_cost
        FROM `tabService Request`
        WHERE customer = %(customer)s AND docstatus < 2
        ORDER BY creation DESC LIMIT 30
    """, {"customer": customer}, as_dict=True)

    # Buyback Assessments — by customer or mobile
    mobile = cust_doc.mobile_no or ""
    out["buybacks"] = frappe.db.sql("""
        SELECT name, item_name, brand, estimated_grade, estimated_price,
               quoted_price, status, creation
        FROM `tabBuyback Assessment`
        WHERE (customer = %(customer)s OR mobile_no = %(mobile)s)
          AND docstatus < 2
        ORDER BY creation DESC LIMIT 20
    """, {"customer": customer, "mobile": mobile}, as_dict=True)

    # Active warranties / VAS (Active VAS Plans)
    out["warranties"] = frappe.db.sql("""
        SELECT name, plan_title, warranty_plan, plan_type, item_code, item_name,
               start_date, end_date, status, sales_invoice
        FROM `tabActive VAS Plans`
        WHERE customer = %(customer)s AND docstatus = 1
        ORDER BY end_date DESC LIMIT 30
    """, {"customer": customer}, as_dict=True)

    # Warranty Claims
    out["warranty_claims"] = frappe.db.sql("""
        SELECT name, claim_date, item_name, serial_no, coverage_type,
               claim_status, issue_category, repair_status
        FROM `tabCH Warranty Claim`
        WHERE customer = %(customer)s
        ORDER BY claim_date DESC LIMIT 20
    """, {"customer": customer}, as_dict=True)

    # Vouchers issued to customer
    out["vouchers"] = frappe.db.sql("""
        SELECT name, voucher_code, voucher_type, original_amount,
               balance, status, valid_upto, source_type
        FROM `tabCH Voucher`
        WHERE issued_to = %(customer)s AND docstatus = 1
        ORDER BY creation DESC LIMIT 20
    """, {"customer": customer}, as_dict=True)

    # Refund / return invoices
    out["refunds"] = frappe.db.sql("""
        SELECT name, posting_date, grand_total, return_against, status
        FROM `tabSales Invoice`
        WHERE customer = %(customer)s AND docstatus = 1 AND is_return = 1
        ORDER BY posting_date DESC LIMIT 20
    """, {"customer": customer}, as_dict=True)

    # Swap / exchange invoices (sale_type driven)
    # Defensively handled: custom_ch_sale_type column requires bench migrate after
    # first app install; fall back to empty list if column is not yet present.
    try:
        out["swap_invoices"] = frappe.db.sql("""
            SELECT name, posting_date, grand_total, custom_ch_sale_type,
                   custom_ch_sale_sub_type, custom_exchange_assessment, status
            FROM `tabSales Invoice`
            WHERE customer = %(customer)s AND docstatus = 1
              AND custom_exchange_assessment IS NOT NULL AND custom_exchange_assessment != ''
            ORDER BY posting_date DESC LIMIT 20
        """, {"customer": customer}, as_dict=True)
    except Exception: 
        out["swap_invoices"] = []

    # Coupon usage (Sales Invoice in ERPNext 15 uses custom_coupon_code, not coupon_code)
# Coupon usage (safe handling)
    if frappe.db.has_column("Sales Invoice", "custom_coupon_code"):
         out["coupon_usage"] = frappe.db.sql("""
             SELECT name, posting_date, custom_coupon_code AS coupon_code, grand_total FROM `tabSales Invoice`
        WHERE customer = %(customer)s AND docstatus = 1
          AND custom_coupon_code IS NOT NULL AND custom_coupon_code != ''
        ORDER BY posting_date DESC LIMIT 20
    """, {"customer": customer}, as_dict=True)
    else:
         out["coupon_usage"] = []

    # Exception requests
    out["exceptions"] = frappe.db.sql("""
        SELECT name, creation, exception_type, status,
               reference_doctype, reference_name
        FROM `tabCH Exception Request`
        WHERE customer = %(customer)s
        ORDER BY creation DESC LIMIT 20
    """, {"customer": customer}, as_dict=True)

    # Loyalty — cross-company balance, company-specific redemption rate
    try:
        from ch_item_master.ch_customer_master.loyalty import get_ch_loyalty_info
        info = get_ch_loyalty_info(customer, company=company)
        out["loyalty"] = info if info.get("loyalty_program") else None
    except Exception:
        out["loyalty"] = None

    return out


def _dashboard_date_range(date=None, from_date=None, to_date=None):
    """Normalize dashboard date filters while preserving the legacy ``date`` arg."""
    start = getdate(from_date or date or nowdate())
    end = getdate(to_date or date or from_date or nowdate())
    if start > end:
        start, end = end, start
    return str(start), str(end)


@frappe.whitelist()
def store_dashboard(pos_profile, date=None, from_date=None, to_date=None, salesman=None) -> dict:
    """Return sales summary, top items, staff performance and inventory alerts.

    ``date`` is retained for older clients. New callers can pass ``from_date``,
    ``to_date`` and ``salesman`` (POS Executive) for dashboard filtering.
    """
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    from_date, to_date = _dashboard_date_range(date=date, from_date=from_date, to_date=to_date)
    salesman = (salesman or "").strip()
    warehouse = profile.warehouse
    si_meta = frappe.get_meta("Sales Invoice")
    has_salesman_field = si_meta.has_field("custom_sales_executive")

    invoice_filters = {
        "pos_profile": pos_profile,
        "posting_date": ["between", [from_date, to_date]],
        "docstatus": 1,
        "is_return": 0,
    }
    if salesman and has_salesman_field:
        invoice_filters["custom_sales_executive"] = salesman

    invoice_fields = ["name", "grand_total", "owner"]
    if has_salesman_field:
        invoice_fields.append("custom_sales_executive")

    invoices = frappe.get_all(
        "Sales Invoice",
        filters=invoice_filters,
        fields=invoice_fields,
    )
    total_revenue = sum(flt(inv.grand_total) for inv in invoices)
    total_invoices = len(invoices)

    # Items sold for the selected period.
    items_sold = 0
    if invoices:
        inv_names = [inv.name for inv in invoices]
        items_sold = frappe.db.sql(
            """SELECT COALESCE(SUM(ii.qty), 0)
               FROM `tabSales Invoice Item` ii
               WHERE ii.parent IN %(parents)s""",
            {"parents": tuple(inv_names)},
        )[0][0] or 0

    # Returns for the selected period.
    return_filters = {
        "pos_profile": pos_profile,
        "posting_date": ["between", [from_date, to_date]],
        "docstatus": 1,
        "is_return": 1,
    }
    if salesman and has_salesman_field:
        return_filters["custom_sales_executive"] = salesman
    total_returns = frappe.db.count(
        "Sales Invoice",
        filters=return_filters,
    )

    sales_conditions = [
        "pi.pos_profile = %(pos_profile)s",
        "pi.posting_date BETWEEN %(from_date)s AND %(to_date)s",
        "pi.docstatus = 1",
        "pi.is_return = 0",
    ]
    sales_params = {
        "pos_profile": pos_profile,
        "from_date": from_date,
        "to_date": to_date,
    }
    if salesman and has_salesman_field:
        sales_conditions.append("pi.custom_sales_executive = %(salesman)s")
        sales_params["salesman"] = salesman
    sales_where = " AND ".join(sales_conditions)

    # Top selling items for the selected period.
    top_items = []
    if invoices:
        top_items_raw = frappe.db.sql(
            f"""SELECT ii.item_name, SUM(ii.qty) AS qty, SUM(ii.amount) AS revenue
               FROM `tabSales Invoice Item` ii
               JOIN `tabSales Invoice` pi ON pi.name = ii.parent
               WHERE {sales_where}
               GROUP BY ii.item_code, ii.item_name
               ORDER BY revenue DESC
               LIMIT 10""",
            sales_params,
            as_dict=True,
        )
        top_items = [{"item_name": r.item_name, "qty": flt(r.qty), "revenue": flt(r.revenue)} for r in top_items_raw]

    # Staff performance: prefer POS Executive attribution, fall back to cashier.
    exec_names = []
    if has_salesman_field:
        exec_names = [inv.custom_sales_executive for inv in invoices if inv.get("custom_sales_executive")]
    exec_labels = {}
    if exec_names:
        exec_rows = frappe.get_all(
            "POS Executive",
            filters={"name": ["in", list(set(exec_names))]},
            fields=["name", "executive_name"],
            ignore_permissions=True,
        )
        exec_labels = {r.name: (r.executive_name or r.name) for r in exec_rows}

    staff_map = {}
    for inv in invoices:
        exec_name = inv.get("custom_sales_executive") if has_salesman_field else None
        key = exec_name or inv.owner
        label = exec_labels.get(exec_name) if exec_name else (frappe.utils.get_fullname(inv.owner) or inv.owner)
        if key not in staff_map:
            staff_map[key] = {"cashier": label or key, "invoices": 0, "revenue": 0}
        staff_map[key]["invoices"] += 1
        staff_map[key]["revenue"] += flt(inv.grand_total)
    staff_performance = sorted(staff_map.values(), key=lambda x: x["revenue"], reverse=True)

    # Inventory alerts — items with low stock or zero stock in this warehouse
    inventory_alerts = []
    if warehouse:
        # Items in Bin with qty <= 5
        low_stock = frappe.db.sql(
            """SELECT b.item_code, i.item_name, b.actual_qty AS qty
               FROM `tabBin` b
               JOIN `tabItem` i ON i.name = b.item_code
               WHERE b.warehouse = %s AND b.actual_qty <= 5 AND i.disabled = 0
                 AND IFNULL(i.ch_lifecycle_status, '') IN ('Active', 'Obsolete')
               ORDER BY b.actual_qty ASC
               LIMIT 15""",
            (warehouse,),
            as_dict=True,
        )
        inventory_alerts = [{"item_code": r.item_code, "item_name": r.item_name, "qty": flt(r.qty)} for r in low_stock]

        # Also include stock items that have NO Bin entry at all (effectively 0 stock)
        remaining = 15 - len(inventory_alerts)
        if remaining > 0:
            existing_codes = [r.item_code for r in low_stock]
            no_bin_items = frappe.db.sql(
                """SELECT i.name AS item_code, i.item_name, 0 AS qty
                   FROM `tabItem` i
                   WHERE i.disabled = 0 AND i.is_stock_item = 1
                     AND IFNULL(i.ch_lifecycle_status, '') IN ('Active', 'Obsolete')
                     AND NOT EXISTS (
                         SELECT 1 FROM `tabBin` b
                         WHERE b.item_code = i.name AND b.warehouse = %s
                     )
                   ORDER BY i.item_name ASC
                   LIMIT %s""",
                (warehouse, remaining),
                as_dict=True,
            )
            inventory_alerts.extend(
                [{"item_code": r.item_code, "item_name": r.item_name, "qty": 0} for r in no_bin_items]
            )

    # Hourly sales breakdown for bar chart.
    hourly_sales = []
    if invoices:
        hourly_raw = frappe.db.sql(
            f"""SELECT HOUR(pi.posting_time) AS hr, SUM(pi.grand_total) AS revenue,
                      COUNT(*) AS cnt
               FROM `tabSales Invoice` pi
               WHERE {sales_where}
               GROUP BY HOUR(pi.posting_time)
               ORDER BY hr""",
            sales_params,
            as_dict=True,
        )
        hourly_sales = [{"hour": cint(r.hr), "revenue": flt(r.revenue), "count": cint(r.cnt)}
                        for r in hourly_raw]

    # Recent Material Requests for this warehouse
    material_requests = []
    if warehouse:
        material_requests = frappe.db.sql(
            """SELECT mr.name, mr.transaction_date, mr.status,
                      (SELECT COUNT(*) FROM `tabMaterial Request Item` mri
                       WHERE mri.parent = mr.name) AS item_count
               FROM `tabMaterial Request` mr
               WHERE mr.docstatus = 1
                 AND EXISTS (
                     SELECT 1 FROM `tabMaterial Request Item` mri
                     WHERE mri.parent = mr.name AND mri.warehouse = %s
                 )
                 AND mr.status NOT IN ('Stopped', 'Cancelled')
               ORDER BY mr.creation DESC
               LIMIT 5""",
            (warehouse,),
            as_dict=True,
        )

    # Recent Stock Transfers involving this warehouse.
    # TC_042 / TC_043 — surface from/to warehouse and a primary item label
    # on the Reports workspace card so cashiers can identify a transfer
    # without drilling into the Stock Entry document.
    stock_transfers = []
    if warehouse:
        stock_transfers = frappe.db.sql(
            """SELECT se.name, se.posting_date, se.docstatus,
                      se.from_warehouse, se.to_warehouse,
                      (SELECT COUNT(*) FROM `tabStock Entry Detail` sed
                       WHERE sed.parent = se.name) AS item_count,
                      (SELECT sed2.item_name FROM `tabStock Entry Detail` sed2
                       WHERE sed2.parent = se.name
                       ORDER BY sed2.idx ASC LIMIT 1) AS primary_item_name,
                      (SELECT sed3.item_code FROM `tabStock Entry Detail` sed3
                       WHERE sed3.parent = se.name
                       ORDER BY sed3.idx ASC LIMIT 1) AS primary_item_code
               FROM `tabStock Entry` se
               WHERE se.stock_entry_type = 'Material Transfer'
                 AND se.docstatus IN (0, 1)
                 AND (se.from_warehouse = %s OR se.to_warehouse = %s
                      OR EXISTS (SELECT 1 FROM `tabStock Entry Detail` sed
                                 WHERE sed.parent = se.name
                                   AND (sed.s_warehouse = %s OR sed.t_warehouse = %s)))
               ORDER BY se.creation DESC
               LIMIT 5""",
            (warehouse, warehouse, warehouse, warehouse),
            as_dict=True,
        )

    # Stock value & aging stock value for this warehouse.
    # stock_value: SUM(actual_qty * valuation_rate) — equivalent to
    # the same number Stock Balance / Bin reports use.
    # aging_stock_value: stock that has received no inward movement in the
    # last 90 days (proxy for slow-moving / aged stock). The 90-day threshold
    # mirrors the bucket used in hub_api aging reports.
    stock_value = 0.0
    aging_stock_value = 0.0
    if warehouse:
        sv_row = frappe.db.sql(
            """SELECT COALESCE(SUM(b.actual_qty * b.valuation_rate), 0) AS val
               FROM `tabBin` b
               WHERE b.warehouse = %s AND b.actual_qty > 0""",
            (warehouse,),
        )
        stock_value = flt(sv_row[0][0]) if sv_row else 0.0

        ag_row = frappe.db.sql(
            """SELECT COALESCE(SUM(b.actual_qty * b.valuation_rate), 0) AS val
               FROM `tabBin` b
               WHERE b.warehouse = %s AND b.actual_qty > 0
                 AND NOT EXISTS (
                     SELECT 1 FROM `tabStock Ledger Entry` sle
                     WHERE sle.warehouse = b.warehouse
                       AND sle.item_code = b.item_code
                       AND sle.actual_qty > 0
                       AND sle.is_cancelled = 0
                       AND sle.posting_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
                 )""",
            (warehouse,),
        )
        aging_stock_value = flt(ag_row[0][0]) if ag_row else 0.0

    return {
        "total_revenue": total_revenue,
        "total_invoices": total_invoices,
        "total_items_sold": cint(items_sold),
        "total_returns": total_returns,
        "top_items": top_items,
        "staff_performance": staff_performance,
        "inventory_alerts": inventory_alerts,
        "hourly_sales": hourly_sales,
        "material_requests": material_requests,
        "stock_transfers": stock_transfers,
        "stock_value": stock_value,
        "aging_stock_value": aging_stock_value,
        "warehouse": warehouse,
        "from_date": from_date,
        "to_date": to_date,
        "salesman": salesman,
    }


def _get_material_request_due_datetime(urgency=None, required_by_date=None, required_by_time=None):
    """Return the due datetime used for stock-request delay tracking."""
    urgency = (urgency or "Standard").title()
    due_dt = now_datetime()

    if urgency == "Urgent":
        due_dt = due_dt + datetime.timedelta(hours=2)
    elif urgency == "Low":
        due_dt = due_dt + datetime.timedelta(days=7)
        due_dt = due_dt.replace(hour=18, minute=0, second=0, microsecond=0)
    else:
        due_dt = due_dt + datetime.timedelta(days=3)
        due_dt = due_dt.replace(hour=13, minute=0, second=0, microsecond=0)

    if required_by_date:
        time_value = (required_by_time or due_dt.strftime("%H:%M")).strip()
        if len(time_value) == 5:
            time_value = f"{time_value}:00"
        try:
            due_dt = get_datetime(f"{required_by_date} {time_value}")
        except Exception:
            pass

    return due_dt


def _format_delay_minutes(minutes):
    minutes = max(cint(minutes), 0)
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts)


@frappe.whitelist()
def create_material_request(pos_profile, items, urgency=None, notes=None, source_warehouse=None,
                            required_by_date=None, required_by_time=None) -> dict:
    """Create a Store Material Request from POS for stock replenishment.

    Source warehouse is auto-resolved from the store's zone if not provided.
    A due date + time is captured so teams can track delivery delays precisely.
    """
    from ch_erp15.ch_erp15.store_request_api import create_store_material_request

    urgency = (urgency or "Standard").title()
    due_dt = _get_material_request_due_datetime(urgency, required_by_date, required_by_time)
    required_by_date = str(getdate(due_dt))

    return create_store_material_request(
        pos_profile=pos_profile,
        items=items,
        priority=urgency,
        notes=notes or None,
        required_by_date=required_by_date,
        required_by_datetime=due_dt,
        preferred_source_warehouse=source_warehouse or None,
    )


@frappe.whitelist()
def get_draft_material_requests(pos_profile) -> dict:
    """Get Draft MRs for this POS store (store exec can append items to these)."""
    from ch_erp15.ch_erp15.store_request_api import get_draft_requests
    return get_draft_requests(pos_profile=pos_profile)


@frappe.whitelist()
def add_items_to_material_request(request_name, items) -> dict:
    """Add items to an existing Draft Material Request from POS."""
    from ch_erp15.ch_erp15.store_request_api import add_items_to_draft
    return add_items_to_draft(request_name=request_name, items=items)


@frappe.whitelist()
def check_material_request_capacity(pos_profile, items) -> dict:
    """Check Warehouse Capacity limits for requested items."""
    from ch_erp15.ch_erp15.store_request_api import check_request_capacity
    return check_request_capacity(pos_profile=pos_profile, items=items)


@frappe.whitelist()
def get_store_zone_info(pos_profile) -> dict:
    """Get zone and source warehouse info for the POS store."""
    from ch_erp15.ch_erp15.store_request_api import get_zone_source_warehouse
    return get_zone_source_warehouse(pos_profile=pos_profile)


@frappe.whitelist()
def get_transfer_warehouse_scope(pos_profile) -> dict:
    """Return scoped transfer warehouses for a POS store.

    Industry-safe default:
    - Source warehouse is the POS Profile warehouse (current store)
    - Target warehouses are nearby stores resolved with zone-first fallback
    """
    from ch_pos.api.search import _get_nearby_warehouses

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    source_warehouse = profile.warehouse
    zone_info = get_store_zone_info(pos_profile) or {}

    target_warehouses = []
    for row in (_get_nearby_warehouses(pos_profile) or []):
        wh = (row.get("warehouse") or "").strip()
        if wh and wh != source_warehouse and wh not in target_warehouses:
            target_warehouses.append(wh)

    return {
        "source_warehouse": source_warehouse,
        "zone": zone_info.get("zone"),
        "target_warehouses": target_warehouses,
        "restricted": bool(target_warehouses),
    }


@frappe.whitelist()
def get_pending_material_requests(pos_profile) -> list:
    """Get recent Material Requests for this POS store with unified tracking."""
    from ch_erp15.ch_erp15.store_request_api import get_store_material_requests

    requests = get_store_material_requests(pos_profile=pos_profile, include_closed=0)
    if not requests:
        return []

    names = [r["name"] for r in requests]
    rows = frappe.db.sql(
        """SELECT parent, COUNT(*) AS item_count
           FROM `tabMaterial Request Item`
           WHERE parent IN %(names)s
           GROUP BY parent""",
        {"names": tuple(names)},
        as_dict=True,
    )
    item_counts = {r.parent: r.item_count for r in rows}

    out = []
    now_dt = now_datetime()
    closed_statuses = {"Received", "Transferred", "Stopped", "Cancelled", "Issued"}

    for req in requests:
        sla_due_by = req.get("sla_due_by")
        delay_minutes = 0
        delay_label = ""
        delay_state = "scheduled"

        if sla_due_by and req.get("status") not in closed_statuses:
            try:
                delta_minutes = int((now_dt - get_datetime(sla_due_by)).total_seconds() // 60)
                if delta_minutes > 0:
                    delay_minutes = delta_minutes
                    delay_label = _format_delay_minutes(delta_minutes)
                    delay_state = "delayed"
                else:
                    delay_label = _format_delay_minutes(abs(delta_minutes))
                    delay_state = "due"
            except Exception:
                pass

        entry = {
            "name": req["name"],
            "transaction_date": str(sla_due_by or req.get("required_by_date") or req.get("creation")),
            "status": req.get("status"),
            "display_status": req.get("display_status") or req.get("status"),
            "approval_status": req.get("approval_status", ""),
            "priority": req.get("priority", ""),
            "sla_breached": req.get("sla_breached", 0),
            "sla_due_by": str(sla_due_by) if sla_due_by else None,
            "delay_minutes": delay_minutes,
            "delay_label": delay_label,
            "delay_state": delay_state,
            "item_count": item_counts.get(req["name"], 0),
            "purchase_requests": req.get("purchase_requests", []),
            "stock_entries": req.get("stock_entries", []),
            "per_ordered": req.get("per_ordered", 0),
            "per_received": req.get("per_received", 0),
        }
        out.append(entry)
    return out


@frappe.whitelist()
def get_stock_transfers(pos_profile, direction="incoming") -> dict:
    """Get recent stock transfers (Stock Entries of type Material Transfer)."""
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    warehouse = profile.warehouse
    entries = []

    direction = (direction or "incoming").strip().lower()
    if direction not in {"incoming", "outgoing"}:
        direction = "incoming"

    if direction == "incoming":
        wh_filter = "se.to_warehouse = %s OR EXISTS (SELECT 1 FROM `tabStock Entry Detail` sed WHERE sed.parent = se.name AND sed.t_warehouse = %s)"
        params = (warehouse, warehouse)
    else:
        wh_filter = "se.from_warehouse = %s OR EXISTS (SELECT 1 FROM `tabStock Entry Detail` sed WHERE sed.parent = se.name AND sed.s_warehouse = %s)"
        params = (warehouse, warehouse)

    entries = frappe.db.sql(
        """SELECT se.name, se.posting_date, se.docstatus,
                             COALESCE(
                                     se.from_warehouse,
                                     (SELECT sed.s_warehouse
                                            FROM `tabStock Entry Detail` sed
                                         WHERE sed.parent = se.name
                                             AND IFNULL(sed.s_warehouse, '') != ''
                                         ORDER BY sed.idx ASC
                                         LIMIT 1)
                             ) AS from_warehouse,
                             COALESCE(
                                     se.to_warehouse,
                                     (SELECT sed.t_warehouse
                                            FROM `tabStock Entry Detail` sed
                                         WHERE sed.parent = se.name
                                             AND IFNULL(sed.t_warehouse, '') != ''
                                         ORDER BY sed.idx ASC
                                         LIMIT 1)
                             ) AS to_warehouse,
                             se.remarks,
               se.custom_status, se.custom_logistics_status,
               se.custom_logistics_person,
               COALESCE(drv.full_name, se.custom_logistics_person) AS custom_logistics_person_name,
               (SELECT COUNT(*) FROM `tabStock Entry Detail` sed
                                    WHERE sed.parent = se.name) AS item_count,
                             (SELECT COALESCE(i.item_name, sed.item_code)
                                    FROM `tabStock Entry Detail` sed
                                    LEFT JOIN `tabItem` i ON i.name = sed.item_code
                                 WHERE sed.parent = se.name
                                 ORDER BY sed.idx ASC
                                 LIMIT 1) AS primary_item_name,
                             (SELECT item_code
                                    FROM `tabStock Entry Detail` sed
                                 WHERE sed.parent = se.name
                                 ORDER BY sed.idx ASC
                                 LIMIT 1) AS primary_item_code,
                             GREATEST(
                                     (SELECT COUNT(*) FROM `tabStock Entry Detail` sed
                                         WHERE sed.parent = se.name) - 1,
                                     0
                             ) AS additional_item_count
        FROM `tabStock Entry` se
                LEFT JOIN `tabDriver` drv ON drv.name = se.custom_logistics_person
        WHERE se.stock_entry_type = 'Material Transfer'
          AND se.docstatus IN (0, 1)
          AND ({wh_filter})
        ORDER BY se.creation DESC
        LIMIT 20""".format(wh_filter=wh_filter),  # noqa: UP032
        params,
        as_dict=True,
    )
    return entries


@frappe.whitelist()
def scan_for_stock_transfer(barcode, from_warehouse):
    """Validate a scanned IMEI/Serial for an outgoing Stock Transfer.

    Returns the item it belongs to and the warehouse it is currently in, so
    the POS Stock Transfer screen can add the unit to the correct line and
    block scans of serials that don't live at the source warehouse (SAP-style
    bin enforcement).

    Response shape:
        {ok: True, serial_no, item_code, item_name, uom, warehouse}
        {ok: False, code, message}
    """
    if not barcode:
        return {"ok": False, "code": "empty", "message": _("Empty scan")}
    if not from_warehouse:
        return {"ok": False, "code": "no_source", "message": _("Select Source Warehouse first")}

    barcode = (barcode or "").strip()
    # Resolve serial — exact case first, then loose match (mirrors safe_get_serial).
    sn_row = frappe.db.sql(
        """SELECT name, item_code, warehouse, status
             FROM `tabSerial No`
            WHERE BINARY name = %s
            LIMIT 1""",
        (barcode,),
        as_dict=True,
    )
    if not sn_row:
        sn_row = frappe.db.sql(
            """SELECT name, item_code, warehouse, status
                 FROM `tabSerial No`
                WHERE name = %s
                LIMIT 1""",
            (barcode,),
            as_dict=True,
        )
    if not sn_row:
        return {"ok": False, "code": "not_found", "message": _("Serial / IMEI {0} not found").format(barcode)}

    sn = sn_row[0]

    # Pickup/Bill guard: if this serial is reserved on an open pre-booking
    # (Sales Order reserve_stock=1), do not allow it to leave the store via
    # transfer. It must be billed/picked up against the reservation.
    reserved_so = _get_open_reserved_sales_order_for_serial(sn.name, from_warehouse)
    if reserved_so:
        return {
            "ok": False,
            "code": "reserved_for_pickup",
            "message": _("{0} is reserved on Sales Order {1}; transfer is blocked.").format(
                sn.name, reserved_so
            ),
        }

    if (sn.warehouse or "") != from_warehouse:
        return {
            "ok": False,
            "code": "wrong_warehouse",
            "message": _("{0} is at {1}, not {2}").format(
                sn.name, sn.warehouse or _("(no warehouse)"), from_warehouse
            ),
        }
    if (sn.status or "").lower() not in ("", "active"):
        return {
            "ok": False,
            "code": "bad_status",
            "message": _("{0} status is {1}; cannot transfer").format(sn.name, sn.status),
        }

    item = frappe.db.get_value(
        "Item", sn.item_code, ["item_name", "stock_uom"], as_dict=True
    ) or {}
    return {
        "ok": True,
        "serial_no": sn.name,
        "item_code": sn.item_code,
        "item_name": item.get("item_name") or sn.item_code,
        "uom": item.get("stock_uom") or "Nos",
        "warehouse": sn.warehouse,
    }


# Days past the requested delivery/pickup date that a pre-booking IMEI stays
# reserved. After this grace window the reservation lapses and the device
# returns to sellable stock (professional-ERP reservation-expiry behaviour).
PREBOOK_HOLD_GRACE_DAYS = 2


def _reserved_serials_on_so(so) -> set:
    """All IMEIs reserved across a Sales Order's rows (custom_serial_no list)."""
    reserved = set()
    for it in so.items:
        raw = it.get("custom_serial_no") or ""
        for tok in str(raw).replace(",", "\n").splitlines():
            tok = tok.strip()
            if tok:
                reserved.add(tok)
    return reserved


def _confirm_prebook_serials(so, scanned_serials) -> None:
    """Require the reserved IMEIs to be physically scanned before billing.

    Throws unless the scanned set exactly matches the reserved set — no missing
    (device not present) and no extra (wrong device). No-op when the pre-booking
    carries no reserved IMEIs (e.g. accessories).
    """
    reserved = _reserved_serials_on_so(so)
    if not reserved:
        return

    if isinstance(scanned_serials, str):
        scanned_serials = frappe.parse_json(scanned_serials) if scanned_serials.strip() else []
    scanned = {str(s).strip() for s in (scanned_serials or []) if str(s).strip()}

    if not scanned:
        frappe.throw(
            _("Scan the reserved IMEI(s) to confirm hand-over before billing: {0}").format(
                ", ".join(sorted(reserved))
            ),
            title=_("IMEI Scan Required"),
        )
    missing = reserved - scanned
    if missing:
        frappe.throw(
            _("These reserved IMEIs were not scanned — the device(s) must be present to bill: {0}").format(
                ", ".join(sorted(missing))
            ),
            title=_("IMEI Mismatch"),
        )
    extra = scanned - reserved
    if extra:
        frappe.throw(
            _("These scanned IMEIs are not part of this pre-booking: {0}").format(
                ", ".join(sorted(extra))
            ),
            title=_("IMEI Mismatch"),
        )


def release_expired_prebook_reservations() -> int:
    """Scheduler: free IMEIs whose pre-booking validity has lapsed.

    For submitted, open, reserve-stock Sales Orders carrying IMEIs whose
    delivery date passed more than PREBOOK_HOLD_GRACE_DAYS ago and that still
    hold a live reservation, release the ERPNext stock reservation and log it.
    The IMEI is already treated as sellable by the reservation check; this also
    unwinds the qty reservation and leaves an audit trail.

    The back-order Sales Order is left open and the advance is NOT auto
    refunded/forfeited (a business decision). Instead, every expired
    pre-booking that carries an advance is flagged to all management heads and
    the full accounts team for a refund/forfeit call.
    """
    from frappe.utils import add_days, getdate

    cutoff = add_days(getdate(), -PREBOOK_HOLD_GRACE_DAYS)
    sos = frappe.get_all(
        "Sales Order",
        filters={
            "docstatus": 1,
            "status": ("not in", ["Closed", "Cancelled", "Completed", "On Hold"]),
            "reserve_stock": 1,
            "delivery_date": ("<", cutoff),
            "per_billed": ("<", 100),
        },
        fields=["name", "delivery_date", "advance_paid", "customer", "customer_name", "grand_total"],
        limit_page_length=200,
    )
    released = 0
    with_advance = []
    for so in sos:
        has_serial = frappe.db.sql(
            """SELECT 1 FROM `tabSales Order Item`
               WHERE parent = %s AND IFNULL(custom_serial_no, '') != '' LIMIT 1""",
            so.name,
        )
        if not has_serial:
            continue
        # Only act on orders still holding a live reservation — this makes the
        # job self-limiting (no re-processing / re-alerting once released).
        active_sre = frappe.db.exists("Stock Reservation Entry", {
            "voucher_type": "Sales Order",
            "voucher_no": so.name,
            "status": ("not in", ["Delivered", "Cancelled"]),
            "docstatus": 1,
        })
        if not active_sre:
            continue
        try:
            from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
                cancel_stock_reservation_entries,
            )
            cancel_stock_reservation_entries("Sales Order", so.name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Prebook unreserve failed for {so.name}")
        try:
            frappe.get_doc({
                "doctype": "Comment",
                "comment_type": "Info",
                "reference_doctype": "Sales Order",
                "reference_name": so.name,
                "content": _(
                    "Reserved IMEI(s) released — pre-booking validity passed "
                    "(delivery date {0} + {1} day grace). Device(s) returned to sellable stock."
                ).format(so.delivery_date, PREBOOK_HOLD_GRACE_DAYS),
            }).insert(ignore_permissions=True)
        except Exception:
            pass
        if flt(so.advance_paid) > 0:
            with_advance.append(so)
        released += 1

    if released:
        frappe.db.commit()
    if with_advance:
        try:
            _notify_expired_prebook_advances(with_advance)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Expired pre-booking advance alert failed")
    return released


def _notify_expired_prebook_advances(rows) -> None:
    """Alert all management heads + the full accounts team that expired
    pre-bookings carry an advance needing a refund/forfeit decision."""
    recipients = set()

    # Management heads (CEO/COO/CFO or CH Notification Settings digest_roles).
    try:
        from ch_erp15.ch_erp15.notification_router import management_digest_users
        recipients.update(management_digest_users() or [])
    except Exception:
        pass

    # Entire accounts team.
    acct_users = frappe.get_all(
        "Has Role",
        filters={"role": ("in", ["Accounts Manager", "Accounts User"]), "parenttype": "User"},
        pluck="parent",
    )
    for u in set(acct_users):
        if u in ("Administrator", "Guest"):
            continue
        if not frappe.db.get_value("User", u, "enabled"):
            continue
        email = frappe.db.get_value("User", u, "email")
        if email:
            recipients.add(email)

    if not recipients:
        return

    def _row(so):
        url = frappe.utils.get_url(f"/app/sales-order/{so.name}")
        return (
            "<tr>"
            f"<td style='border:1px solid #ddd;padding:6px'><a href='{url}'>{so.name}</a></td>"
            f"<td style='border:1px solid #ddd;padding:6px'>{frappe.utils.escape_html(so.customer_name or so.customer or '')}</td>"
            f"<td style='border:1px solid #ddd;padding:6px;text-align:right'>₹{flt(so.advance_paid):,.2f}</td>"
            f"<td style='border:1px solid #ddd;padding:6px;text-align:right'>₹{flt(so.grand_total):,.2f}</td>"
            f"<td style='border:1px solid #ddd;padding:6px'>{so.delivery_date}</td>"
            "</tr>"
        )

    table = (
        "<table style='border-collapse:collapse;width:100%;margin:10px 0'>"
        "<tr style='background:#f5f5f5'>"
        "<th style='border:1px solid #ddd;padding:6px;text-align:left'>Pre-booking (SO)</th>"
        "<th style='border:1px solid #ddd;padding:6px;text-align:left'>Customer</th>"
        "<th style='border:1px solid #ddd;padding:6px;text-align:right'>Advance Paid</th>"
        "<th style='border:1px solid #ddd;padding:6px;text-align:right'>Order Value</th>"
        "<th style='border:1px solid #ddd;padding:6px;text-align:left'>Delivery Date</th>"
        "</tr>" + "".join(_row(so) for so in rows) + "</table>"
    )
    total_advance = sum(flt(so.advance_paid) for so in rows)
    subject = _("Action needed: {0} expired pre-booking(s) with advance — refund/forfeit decision").format(len(rows))
    message = _(
        "<p>The following pre-bookings have lapsed (validity passed) and their reserved "
        "device(s) were returned to sellable stock. Each still holds a customer "
        "<b>advance</b> — please decide refund vs forfeit per policy.</p>"
        "{0}"
        "<p><b>Total advance held:</b> ₹{1:,.2f}</p>"
    ).format(table, total_advance)

    frappe.sendmail(recipients=sorted(recipients), subject=subject, message=message)


def _get_open_reserved_sales_order_for_serial(serial_no: str, warehouse: str | None = None) -> str | None:
    """Return an open reserve-stock Sales Order name if this serial is reserved.

    Reservation source of truth for pickup flow is Sales Order Item.custom_serial_no
    with Sales Order.reserve_stock=1. If a warehouse is provided, scope to that
    warehouse to avoid cross-store false positives.

    Reservations whose validity has lapsed (delivery_date older than
    PREBOOK_HOLD_GRACE_DAYS) are ignored — the IMEI is treated as released and
    sellable again, even though the back-order Sales Order remains open.
    """
    serial_no = (serial_no or "").strip()
    if not serial_no:
        return None

    so_item_meta = frappe.get_meta("Sales Order Item")
    if not so_item_meta.has_field("custom_serial_no"):
        return None

    warehouse_filter = ""
    params = {
        "serial": serial_no,
        "grace": PREBOOK_HOLD_GRACE_DAYS,
    }

    if warehouse:
        warehouse_filter = " AND (IFNULL(soi.warehouse, '') = %(warehouse)s OR IFNULL(so.set_warehouse, '') = %(warehouse)s)"
        params["warehouse"] = warehouse

    # Match the exact IMEI as a whole token: custom_serial_no holds a single
    # serial or a newline-joined list. Normalise newlines/CR/spaces to a
    # comma-set and use FIND_IN_SET for an exact-token match.
    row = frappe.db.sql(
        f"""
        SELECT so.name
          FROM `tabSales Order Item` soi
          JOIN `tabSales Order` so ON so.name = soi.parent
         WHERE so.docstatus = 1
           AND so.status NOT IN ('Closed', 'Cancelled', 'Completed', 'On Hold')
           AND COALESCE(so.reserve_stock, 0) = 1
           AND (so.delivery_date IS NULL
                OR so.delivery_date >= DATE_SUB(CURDATE(), INTERVAL %(grace)s DAY))
           AND FIND_IN_SET(
                 %(serial)s,
                 REPLACE(REPLACE(REPLACE(IFNULL(soi.custom_serial_no, ''), '\r', ''), '\n', ','), ' ', '')
               ) > 0
           {warehouse_filter}
         ORDER BY so.modified DESC
         LIMIT 1
        """,
        params,
        as_dict=True,
    )
    return row[0].name if row else None


@frappe.whitelist()
def create_stock_transfer(from_warehouse, to_warehouse, items,
                          courier_name=None, courier_tracking=None,
                          handover_notes=None, expected_delivery_date=None) -> dict:
    """Create a Stock Entry (Material Transfer) from POS with courier hand-over.

    Each item in ``items`` may carry a ``serial_no`` field — a list of scanned
    IMEIs or a newline-joined string. When supplied, ``qty`` is forced to the
    serial count (SAP MIGO rule: scanned units == movement qty) and the
    serials are written to Stock Entry Detail.serial_no so core ERPNext moves
    each Serial No.warehouse on submit, which in turn fires our
    ``Serial No.on_update`` hook and mirrors current_warehouse on the CH
    Serial Lifecycle row — making the move visible in the IMEI Tracker with
    zero extra writes.
    """
    frappe.has_permission("Stock Entry", "create", throw=True)
    import json
    if isinstance(items, str):
        items = json.loads(items)

    if not from_warehouse or not to_warehouse:
        frappe.throw(frappe._("Both source and destination warehouses are required"))
    if from_warehouse == to_warehouse:
        frappe.throw(frappe._("Source and destination warehouses must be different"))

    company = frappe.db.get_value("Warehouse", from_warehouse, "company") or frappe.defaults.get_global_default("company")

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer"
    se.company = company
    se.from_warehouse = from_warehouse
    se.to_warehouse = to_warehouse

    # Cross-line dedupe: a serial can only ship once per transfer.
    seen_serials = set()
    for item in items:
        raw_serials = item.get("serial_no") or item.get("serials") or ""
        if isinstance(raw_serials, list):
            serial_list = [s for s in (str(x).strip() for x in raw_serials) if s]
        else:
            serial_list = [s.strip() for s in str(raw_serials).splitlines() if s.strip()]

        # Enforce reservation guard server-side too (not only scan API), so
        # direct API calls cannot move pickup-reserved serials.
        for sn in serial_list:
            reserved_so = _get_open_reserved_sales_order_for_serial(sn, from_warehouse)
            if reserved_so:
                frappe.throw(
                    _("Serial/IMEI {0} is reserved on Sales Order {1}; transfer is blocked.").format(
                        frappe.bold(sn), frappe.bold(reserved_so)
                    ),
                    title=_("Reserved for Pickup"),
                )

        # Dedupe within the line, then across the document.
        line_serials = []
        for s in serial_list:
            if s in seen_serials:
                continue
            seen_serials.add(s)
            line_serials.append(s)

        if line_serials:
            qty = flt(len(line_serials))
        else:
            qty = flt(item.get("qty", 1))

        row = {
            "item_code": item.get("item_code"),
            "qty": qty,
            "custom_quantity": qty,
            "uom": item.get("uom", "Nos"),
            "s_warehouse": from_warehouse,
            "t_warehouse": to_warehouse,
        }
        if line_serials:
            row["serial_no"] = "\n".join(line_serials)
        se.append("items", row)

    # Courier hand-over details in remarks
    remark_parts = []
    if courier_name:
        remark_parts.append(f"Courier: {courier_name}")
    if courier_tracking:
        remark_parts.append(f"Tracking: {courier_tracking}")
    if expected_delivery_date:
        remark_parts.append(f"ETA: {expected_delivery_date}")
    if handover_notes:
        remark_parts.append(str(handover_notes))
    if remark_parts:
        se.remarks = " | ".join(remark_parts)

    # Create as Draft only — do NOT submit here.
    # Submitting would run the standard SLE and move stock source→destination immediately,
    # even though goods may physically still be at the source store waiting for pickup.
    # Instead, the transit workflow applies exactly as on desk:
    #   Draft → "Handover" (Pending With Goods) → logistics → receive → submit
    se.insert()
    return se.name


CROSS_STORE_TRANSFER_ROLES = {"Store Manager", "Stock Manager", "System Manager"}


@frappe.whitelist()
def check_nearby_stock(pos_profile, item_code) -> list:
    """Check stock availability at other store warehouses for cross-store transfer."""
    if not pos_profile or not item_code:
        frappe.throw(frappe._("POS Profile and Item Code are required"))

    my_warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse")
    if not my_warehouse:
        return []

    company = frappe.db.get_value("Warehouse", my_warehouse, "company")
    # Get all POS Profile warehouses in the same company
    other_profiles = frappe.get_all(
        "POS Profile",
        filters={"disabled": 0, "warehouse": ["!=", my_warehouse], "company": company},
        fields=["name", "warehouse"],
    )

    results = []
    for p in other_profiles:
        qty = flt(frappe.db.get_value("Bin",
            {"item_code": item_code, "warehouse": p.warehouse}, "actual_qty"))
        if qty > 0:
            results.append({
                "pos_profile": p.name,
                "warehouse": p.warehouse,
                "available_qty": qty,
            })
    return results


@frappe.whitelist()
def create_cross_store_transfer(pos_profile, source_pos_profile, items, notes=None) -> dict:
    """Create a Material Request for inter-store stock transfer.

    Restricted to Store Manager and above roles only.
    """
    user_roles = set(frappe.get_roles())
    if not user_roles.intersection(CROSS_STORE_TRANSFER_ROLES):
        frappe.throw(
            frappe._("Cross-store transfers require Store Manager or above role."),
            title=frappe._("Insufficient Permissions"),
            exc=frappe.PermissionError,
        )

    import json as _json
    if isinstance(items, str):
        items = _json.loads(items)

    if not items:
        frappe.throw(frappe._("At least one item is required"))

    my_warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse")
    source_warehouse = frappe.db.get_value("POS Profile", source_pos_profile, "warehouse")
    if not my_warehouse or not source_warehouse:
        frappe.throw(frappe._("Invalid POS Profile warehouse configuration"))
    if my_warehouse == source_warehouse:
        frappe.throw(frappe._("Source and destination warehouses must be different"))

    company = frappe.db.get_value("Warehouse", my_warehouse, "company")

    mr = frappe.new_doc("Material Request")
    mr.material_request_type = "Material Transfer"
    mr.company = company
    mr.set_warehouse = my_warehouse
    mr.transaction_date = nowdate()
    mr.schedule_date = frappe.utils.add_days(nowdate(), 1)

    for item in items:
        mr.append("items", {
            "item_code": item.get("item_code"),
            "qty": flt(item.get("qty", 1)),
            "warehouse": my_warehouse,
            "from_warehouse": source_warehouse,
        })

    # Custom fields for tracking
    if mr.meta.has_field("custom_store"):
        store = frappe.db.get_value("POS Profile Extension",
            {"pos_profile": pos_profile}, "store")
        mr.custom_store = store
    if mr.meta.has_field("custom_pos_profile"):
        mr.custom_pos_profile = pos_profile
    if mr.meta.has_field("custom_priority"):
        mr.custom_priority = "Standard"

    if notes:
        mr.custom_remarks = 1
        mr.remarks = str(notes)[:500]

    # POS-14 fix: Set approval status to require explicit manager approval
    if mr.meta.has_field("custom_approval_status"):
        mr.custom_approval_status = "Pending Approval"
    if mr.meta.has_field("custom_requested_by"):
        mr.custom_requested_by = frappe.session.user

    mr.flags.ignore_permissions = True
    mr.insert()

    # POS-14 fix: Notify store manager of the source store for approval
    try:
        source_store_mgr = frappe.db.get_value(
            "POS Profile", source_pos_profile, "custom_store_manager"
        )
        if source_store_mgr:
            request_url = frappe.utils.get_url_to_form("Material Request", mr.name)
            frappe.sendmail(
                recipients=[source_store_mgr],
                subject=frappe._("Cross-Store Transfer Request {0} — Approval Required").format(mr.name),
                message=frappe._(
                    """
                    <div style='font-family:Segoe UI,Arial,sans-serif;max-width:620px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>
                        <div style='background:#0f172a;color:#ffffff;padding:14px 18px;font-weight:600'>Congruence Holdings</div>
                        <div style='padding:18px'>
                            <h3 style='margin:0 0 10px'>Cross-Store Transfer Approval Required</h3>
                            <p>A cross-store transfer request <strong>{0}</strong> has been raised and requires your approval.</p>
                            <p style='margin-top:16px'>
                                <a href='{1}' style='background:#0b57d0;color:#ffffff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Material Request</a>
                            </p>
                        </div>
                    </div>
                    """
                ).format(mr.name, request_url),
                now=True,
            )
    except Exception:
        pass  # Non-critical — notification is best-effort

    return {
        "name": mr.name,
        "source_warehouse": source_warehouse,
        "destination_warehouse": my_warehouse,
        "status": mr.status,
    }


@frappe.whitelist()
def get_stock_transfer_items(stock_entry) -> dict:
    """Return line items of a Stock Entry for the receive dialog."""
    se = frappe.get_doc("Stock Entry", stock_entry)
    if se.stock_entry_type != "Material Transfer":
        frappe.throw(frappe._("Not a Material Transfer"))

    items = []
    for row in se.items:
        items.append({
            "item_code": row.item_code,
            "item_name": row.item_name,
            "qty": flt(row.custom_quantity or row.qty),
            "received_qty": flt(row.custom_final_received_qty),
            "uom": row.uom,
            "s_warehouse": row.s_warehouse,
            "t_warehouse": row.t_warehouse,
        })
    return {
        "name": se.name,
        "docstatus": se.docstatus,
        "custom_status": se.custom_status,
        "custom_logistics_status": se.custom_logistics_status,
        "from_warehouse": se.from_warehouse,
        "to_warehouse": se.to_warehouse,
        "items": items,
    }


@frappe.whitelist()
def receive_stock_transfer(stock_entry, received_items) -> dict:
    """Accept a stock transfer with (optionally) reduced quantities.

    If all quantities match the original, the existing Stock Entry is submitted.
    If any quantity is reduced, the original is amended: quantities are updated
    in-place and the entry is submitted.  Items with received qty = 0 are removed.
    """
    frappe.has_permission("Stock Entry", "submit", throw=True)
    import json
    if isinstance(received_items, str):
        received_items = json.loads(received_items)

    se = frappe.get_doc("Stock Entry", stock_entry)
    if se.docstatus == 2:
        frappe.throw(frappe._("Stock Entry {0} is cancelled").format(stock_entry))
    if se.stock_entry_type != "Material Transfer":
        frappe.throw(frappe._("Not a Material Transfer"))

    # Already submitted — stock already moved; receive is just an acknowledgment
    if se.docstatus == 1:
        return {"name": se.name, "partial": False}

    # Build a lookup: item_code → received qty
    recv_map = {r["item_code"]: flt(r.get("received_qty", 0)) for r in received_items}

    # Validate: received qty must not exceed original
    for row in se.items:
        recv_qty = recv_map.get(row.item_code, 0)
        if recv_qty > flt(row.qty):
            frappe.throw(
                frappe._("Received qty ({0}) for {1} exceeds transferred qty ({2})").format(
                    recv_qty, row.item_code, row.qty
                )
            )

    # Check if this is a full or partial receive
    is_partial = False
    for row in se.items:
        recv_qty = recv_map.get(row.item_code, 0)
        if recv_qty != flt(row.qty):
            is_partial = True
            break

    if is_partial:
        # Update quantities in-place, remove zero-qty rows
        rows_to_remove = []
        for row in se.items:
            recv_qty = recv_map.get(row.item_code, 0)
            if recv_qty <= 0:
                rows_to_remove.append(row)
            else:
                row.qty = recv_qty
        for row in rows_to_remove:
            se.items.remove(row)

        if not se.items:
            frappe.throw(frappe._("At least one item must be received"))

        se.save()

    se.submit()
    return {"name": se.name, "partial": is_partial}


@frappe.whitelist()
def pos_scan_receive(stock_entry, barcode) -> dict:
    """Scan a barcode/IMEI during POS receive. Delegates to ch_erp15 transit workflow."""
    from ch_erp15.ch_erp15.custom.stock_entry import pos_scan_receive as _scan
    return _scan(stock_entry=stock_entry, barcode=barcode)


@frappe.whitelist()
def pos_confirm_receive(stock_entry) -> dict:
    """Confirm receive after scanning. Delegates to ch_erp15 transit workflow."""
    from ch_erp15.ch_erp15.custom.stock_entry import pos_confirm_receive as _confirm
    return _confirm(stock_entry=stock_entry)


@frappe.whitelist()
def backfill_draft_documents() -> dict:
    """One-time patch: submit all existing Draft POS Kiosk Tokens and Service Requests
    that were created from POS but left unsubmitted."""
    submitted = {"POS Kiosk Token": 0, "Service Request": 0}

    # POS Kiosk Tokens
    draft_tokens = frappe.get_all("POS Kiosk Token", filters={"docstatus": 0}, pluck="name")
    for name in draft_tokens:
        try:
            doc = frappe.get_doc("POS Kiosk Token", name)
            doc.flags.ignore_permissions = True
            doc.submit()
            submitted["POS Kiosk Token"] += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Backfill submit failed: POS Kiosk Token {name}")

    # Service Requests created from POS queue (have referral_code = token name)
    draft_srs = frappe.get_all("Service Request", filters={
        "docstatus": 0,
        "referral_code": ["is", "set"],
    }, pluck="name")
    for name in draft_srs:
        try:
            doc = frappe.get_doc("Service Request", name)
            doc.flags.ignore_permissions = True
            doc.submit()
            submitted["Service Request"] += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Backfill submit failed: Service Request {name}")

    return submitted


# ── Model Comparison ──────────────────────────────────────
@frappe.whitelist()
def get_comparison_filters() -> dict:
    """Return available filter options for model comparison."""
    brands = frappe.db.get_all(
        "Brand", fields=["name"], order_by="name", pluck="name"
    )

    # Get RAM/Storage values from Item Attribute values
    ram_values = []
    storage_values = []
    for attr_name in ("RAM", "Storage"):
        if frappe.db.exists("Item Attribute", attr_name):
            vals = frappe.db.get_all(
                "Item Attribute Value",
                filters={"parent": attr_name},
                fields=["attribute_value"],
                order_by="idx",
                pluck="attribute_value",
            )
            if attr_name == "RAM":
                ram_values = vals
            else:
                storage_values = vals

    return {
        "brands": brands,
        "ram_values": ram_values,
        "storage_values": storage_values,
    }


@frappe.whitelist()
def get_model_comparison(brand=None, ram=None, storage=None, search_text=None, pos_profile=None, in_stock_only=None) -> list:
    """Return items matching filters with specs, prices, stock and active offers."""
    today = frappe.utils.today()

    # Build item filters
    item_filters = {
        "has_variants": 1,
        "disabled": 0,
    }
    if brand:
        item_filters["brand"] = brand

    # Get template items
    items = frappe.db.get_all(
        "Item",
        filters=item_filters,
        fields=[
            "name", "item_name", "brand", "image",
            "ch_model", "item_group",
        ],
        limit=200,
    )

    if not items:
        return []

    # Filter by search text
    if search_text:
        search_lower = search_text.lower()
        items = [i for i in items if search_lower in (i.item_name or "").lower()
                 or search_lower in (i.brand or "").lower()
                 or search_lower in (i.name or "").lower()]

    # Get warehouse for stock check + nearby store warehouses
    warehouse = None
    nearby_profiles = []  # [{name, warehouse}] for other stores in same company
    if pos_profile:
        warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse")
        if warehouse:
            company = frappe.db.get_value("Warehouse", warehouse, "company")
            if company:
                nearby_profiles = frappe.db.get_all(
                    "POS Profile",
                    filters={"disabled": 0, "warehouse": ["!=", warehouse], "company": company},
                    fields=["name", "warehouse"],
                )

    results = []
    for item in items:
        # Get model spec values (RAM, Storage, etc.)
        spec_values = {}
        if item.ch_model:
            model_specs = frappe.db.get_all(
                "CH Model Spec Value",
                filters={"parent": item.ch_model},
                fields=["spec", "spec_value"],
            )
            for ms in model_specs:
                spec_values[ms.spec] = ms.spec_value

        # Filter by RAM
        if ram and spec_values.get("RAM") != ram:
            continue
        # Filter by Storage
        if storage and spec_values.get("Storage") != storage:
            continue

        # Get model features (Display, Camera, etc.)
        features = {}
        if item.ch_model:
            model_features = frappe.db.get_all(
                "CH Item Feature",
                filters={"parent": item.ch_model, "parenttype": "CH Model"},
                fields=["feature_group", "feature_name", "feature_value"],
            )
            for mf in model_features:
                features.setdefault(mf.feature_group, []).append({
                    "feature": mf.feature_name,
                    "value": mf.feature_value,
                })

        # Get variants of this template (each variant = one SKU on the model)
        variants = frappe.db.get_all(
            "Item",
            filters={"variant_of": item.name, "disabled": 0},
            fields=["name", "item_name"],
        )

        min_price = 0
        max_price = 0
        total_stock = 0
        variant_count = len(variants)
        variant_rows = []  # per-variant breakdown for side-by-side comparison

        # A template (has_variants=1) with no enabled variants is not sellable
        # and has nothing to compare — skip it. This also filters out the
        # half-configured / test template stubs (e.g. _Test, VAS-TPL) that
        # otherwise clutter the comparison with ₹0 rows.
        if variant_count == 0:
            continue

        variant_codes = [v.name for v in variants]
        # Map variant_code → variant doc (for stitching prices/stock back)
        variant_lookup = {v.name: v for v in variants}

        if variant_codes:
            # Get prices from CH Item Price (channel='POS') — the POS-aware
            # price source. Vanilla `Item Price` is empty in this deployment
            # so the previous query produced N/A for every model.
            prices = frappe.db.get_all(
                "CH Item Price",
                filters={
                    "item_code": ["in", variant_codes],
                    "channel": "POS",
                    "status": "Active",
                },
                fields=["item_code", "selling_price", "mrp", "mop"],
            )
            price_by_variant = {}
            for p in prices:
                # Keep the highest selling_price per variant if multiple active rows exist
                existing = price_by_variant.get(p.item_code)
                if not existing or flt(p.selling_price) > flt(existing.get("selling_price")):
                    price_by_variant[p.item_code] = {
                        "selling_price": flt(p.selling_price),
                        "mrp": flt(p.mrp),
                        "mop": flt(p.mop),
                    }
            price_vals = [v["selling_price"] for v in price_by_variant.values() if v["selling_price"]]
            if price_vals:
                min_price = min(price_vals)
                max_price = max(price_vals)

            # Get stock in store warehouse, per variant
            stock_by_variant = {}
            if warehouse:
                stock_data = frappe.db.get_all(
                    "Bin",
                    filters={
                        "item_code": ["in", variant_codes],
                        "warehouse": warehouse,
                    },
                    fields=["item_code", "actual_qty"],
                )
                for s in stock_data:
                    stock_by_variant[s.item_code] = stock_by_variant.get(s.item_code, 0) + flt(s.actual_qty)
                total_stock = sum(stock_by_variant.values())

            # Per-variant attribute values (e.g. Storage=64GB, Colour=Black)
            variant_attrs = {}
            if variants:
                for av in frappe.db.get_all(
                    "Item Variant Attribute",
                    filters={"parent": ["in", variant_codes]},
                    fields=["parent", "attribute", "attribute_value"],
                ):
                    variant_attrs.setdefault(av.parent, {})[av.attribute] = av.attribute_value

            # Build per-variant rows for the comparison detail view
            for vcode in variant_codes:
                vdoc = variant_lookup.get(vcode)
                pinfo = price_by_variant.get(vcode, {})
                variant_rows.append({
                    "item_code": vcode,
                    "item_name": (vdoc.item_name if vdoc else vcode),
                    "attributes": variant_attrs.get(vcode, {}),
                    "selling_price": pinfo.get("selling_price", 0),
                    "mrp": pinfo.get("mrp", 0),
                    "mop": pinfo.get("mop", 0),
                    "stock": stock_by_variant.get(vcode, 0),
                })
            # Sort variants by selling price ascending (cheapest first)
            variant_rows.sort(key=lambda r: (r["selling_price"] or float("inf"), r["item_code"]))

            # Get stock at nearby stores (batch query)
            nearby_stock = []
            if nearby_profiles:
                nearby_wh_names = [p.warehouse for p in nearby_profiles if p.warehouse]
                if nearby_wh_names:
                    nearby_bins = frappe.db.get_all(
                        "Bin",
                        filters={
                            "item_code": ["in", variant_codes],
                            "warehouse": ["in", nearby_wh_names],
                            "actual_qty": [">", 0],
                        },
                        fields=["warehouse", "actual_qty"],
                    )
                    # Aggregate by warehouse
                    wh_qty = {}
                    for b in nearby_bins:
                        wh_qty[b.warehouse] = wh_qty.get(b.warehouse, 0) + flt(b.actual_qty)
                    # Build result with profile name
                    wh_to_profile = {p.warehouse: p.name for p in nearby_profiles if p.warehouse}
                    for wh, qty in wh_qty.items():
                        if qty > 0:
                            nearby_stock.append({
                                "warehouse": wh,
                                "pos_profile": wh_to_profile.get(wh, wh),
                                "qty": qty,
                            })

        # Get active offers for this item (by item_code, brand, item_group)
        # Skip items with no stock when in_stock_only filter is set
        if cint(in_stock_only) and total_stock <= 0:
            continue

        offers = frappe.db.sql(
            """SELECT name, offer_name, offer_type, value_type, value,
                      bank_name, card_type, min_bill_amount, payment_mode
               FROM `tabCH Item Offer`
               WHERE status = 'Active'
                 AND start_date <= %(today)s
                 AND end_date >= %(today)s
                 AND (channel = 'POS' OR channel IS NULL OR channel = '')
                 AND (
                    (apply_on = 'Item Code' AND item_code = %(item_code)s)
                    OR (apply_on = 'Brand' AND target_brand = %(brand)s)
                    OR (apply_on = 'Item Group' AND target_item_group = %(item_group)s)
                    OR offer_level = 'Bill'
                 )
               ORDER BY offer_type, priority
            """,
            {
                "today": today,
                "item_code": item.name,
                "brand": item.brand or "",
                "item_group": item.item_group or "",
            },
            as_dict=True,
        )

        # Group offers by type
        brand_offers = [o for o in offers if o.offer_type == "Brand Offer"]
        bank_offers = [o for o in offers if o.offer_type == "Bank Offer"]
        other_offers = [o for o in offers if o.offer_type not in ("Brand Offer", "Bank Offer")]

        results.append({
            "item_code": item.name,
            "item_name": item.item_name,
            "brand": item.brand,
            "image": item.image,
            "model": item.ch_model,
            "specs": spec_values,
            "features": features,
            "min_price": min_price,
            "max_price": max_price,
            "variant_count": variant_count,
            "variants": variant_rows,
            "stock": total_stock,
            "nearby_stock": nearby_stock,
            "brand_offers": brand_offers,
            "bank_offers": bank_offers,
            "other_offers": other_offers,
            "total_offers": len(offers),
        })

    # Sort by total_offers descending then price ascending
    results.sort(key=lambda x: (-x["total_offers"], x["min_price"]))
    return results


# ── Customer POS Info ──────────────────────────────────────────
@frappe.whitelist()
def get_customer_pos_info(customer, company=None) -> dict:
    """Get customer info for POS: price list, credit rules, loyalty, group type.

    Used when a named customer is selected to auto-apply correct pricing.
    """
    # Frappe's Awesomplete link picker injects a synthetic option whose value
    # is the literal sentinel "create_new__link_option" — selecting it normally
    # triggers the "+ Create a new Customer" dialog. POS code occasionally
    # reads the field value before that action fires (e.g. a stale `change`
    # handler), and we end up calling this API with that sentinel, which then
    # blows up with "Customer create_new__link_option not found". Treat it as
    # a no-op so the picker can recover gracefully and the user can complete
    # the create-customer flow.
    if customer in (None, "", "create_new__link_option", "__create_new__"):
        return {}

    if not company:
        company = frappe.defaults.get_user_default("Company")

    cust = frappe.get_cached_doc("Customer", customer)
    customer_group = cust.customer_group or ""

    # Determine price list: customer-level → customer-group-level → None (use profile default)
    price_list = None
    if cust.default_price_list:
        price_list = cust.default_price_list
    elif customer_group:
        group_pl = frappe.db.get_value("Customer Group", customer_group, "default_price_list")
        if group_pl:
            price_list = group_pl

    # Determine customer type (B2B / B2C)
    # Priority: custom_gstin on Customer doc → company type → group heuristics
    customer_type = "B2C"
    customer_gstin = ""

    # Prefer GSTIN stored directly on Customer master (set by POS quick-create or
    # desk form).  Falls back to billing address lookup for legacy customers.
    if cust.custom_gstin:
        customer_gstin = cust.custom_gstin.strip().upper()
        customer_type = "B2B"
    else:
        # Legacy fallback: check billing address (for customers created before this feature)
        billing_addr_name = frappe.db.get_value(
            "Dynamic Link",
            {"link_doctype": "Customer", "link_name": customer, "parenttype": "Address"},
            "parent",
            order_by="modified desc",
        )
        if billing_addr_name:
            saved_gstin = frappe.db.get_value("Address", billing_addr_name, "gstin") or ""
            if saved_gstin.strip():
                customer_gstin = saved_gstin.strip().upper()
                customer_type = "B2B"

    if not customer_gstin:
        if cust.customer_type == "Company":
            customer_type = "B2B"
        elif customer_group:
            b2b_groups = frappe.get_hooks("b2b_customer_groups") or []
            if not b2b_groups:
                # Heuristic: groups containing Wholesale, Corporate, Enterprise → B2B
                group_lower = customer_group.lower()
                if any(kw in group_lower for kw in ("wholesale", "corporate", "enterprise", "b2b", "dealer")):
                    customer_type = "B2B"

    # Credit limit & outstanding
    credit_limit = 0
    outstanding = 0
    if company:
        # Check customer-level credit limit first
        for cl in cust.get("credit_limits") or []:
            if cl.company == company:
                credit_limit = flt(cl.credit_limit)
                break
        # If no customer-level limit, check customer group
        if not credit_limit and customer_group:
            cg = frappe.get_cached_doc("Customer Group", customer_group)
            for cl in cg.get("credit_limits") or []:
                if cl.company == company:
                    credit_limit = flt(cl.credit_limit)
                    break

        # Outstanding amount
        outstanding = flt(frappe.db.sql("""
            SELECT SUM(debit - credit)
            FROM `tabGL Entry`
            WHERE party_type = 'Customer' AND party = %s AND company = %s
        """, (customer, company))[0][0] or 0)

    # Loyalty — cross-company balance, company-specific redemption rate
    loyalty = None
    try:
        from ch_item_master.ch_customer_master.loyalty import get_ch_loyalty_info
        info = get_ch_loyalty_info(customer, company=company)
        loyalty = info if info.get("loyalty_program") else None
    except Exception:
        pass

    return {
        "customer": customer,
        "customer_name": cust.customer_name,
        "customer_group": customer_group,
        "customer_type": customer_type,
        "customer_gstin": customer_gstin,
        "price_list": price_list,
        "credit_limit": credit_limit,
        "outstanding": outstanding,
        "credit_available": max(0, credit_limit - outstanding) if credit_limit else 0,
        "loyalty": loyalty,
        "mobile_no": cust.mobile_no or "",
        "email_id": cust.email_id or "",
        "territory": cust.territory or "",
    }


# ── Swap Eligibility ──────────────────────────────────────────
@frappe.whitelist()
def validate_swap_eligibility(invoice_name, swap_window_days=7) -> dict:
    """Check if a Sales Invoice is eligible for in-store swap.

    Rules:
    - Invoice must be submitted, non-return, not already fully returned
    - Sale date must be within swap_window_days (default 7)
    - Items must not be scrapped / transferred out
    """
    swap_window_days = cint(swap_window_days) or 7

    if not frappe.db.exists("Sales Invoice", invoice_name):
        return {"eligible": False, "reason": frappe._("Invoice {0} not found").format(invoice_name)}

    inv = frappe.get_doc("Sales Invoice", invoice_name)

    if inv.docstatus != 1:
        return {"eligible": False, "reason": frappe._("Invoice is not submitted")}
    if inv.is_return:
        return {"eligible": False, "reason": frappe._("Cannot swap a return invoice")}
    if inv.status == "Credit Note Issued":
        return {"eligible": False, "reason": frappe._("Invoice already fully returned")}

    # Check date window
    from frappe.utils import date_diff
    days_since = date_diff(nowdate(), str(inv.posting_date))
    if days_since > swap_window_days:
        return {
            "eligible": False,
            "reason": frappe._("Swap window expired — invoice is {0} days old (max {1} days)").format(
                days_since, swap_window_days
            ),
        }

    # Check if there are returnable items
    returnable_count = 0
    for item in inv.items:
        already_returned = flt(frappe.db.sql("""
            SELECT ABS(SUM(ri.qty))
            FROM `tabSales Invoice Item` ri
            JOIN `tabSales Invoice` pi ON pi.name = ri.parent
            WHERE pi.return_against = %s AND pi.docstatus = 1
              AND ri.item_code = %s
              AND (ri.sales_invoice_item = %s OR ri.pos_invoice_item = %s)
        """, (invoice_name, item.item_code, item.name, item.name))[0][0] or 0)
        if flt(item.qty) - already_returned > 0:
            returnable_count += 1

    if returnable_count == 0:
        return {"eligible": False, "reason": frappe._("All items already returned")}

    return {
        "eligible": True,
        "invoice": invoice_name,
        "customer": inv.customer,
        "customer_name": inv.customer_name,
        "posting_date": str(inv.posting_date),
        "days_since_purchase": days_since,
        "swap_window_days": swap_window_days,
        "days_remaining": swap_window_days - days_since,
        "grand_total": flt(inv.grand_total),
    }


# ── VAS Eligibility ──────────────────────────────────────────
@frappe.whitelist()
def get_vas_plans_with_rules(cart_items=None) -> dict:
    """Return sellable CH Warranty Plans with device-dependency rules.

    Single catalog surface: ``CH Warranty Plan`` where ``is_sellable=1``
    AND ``status='Active'``.

    Before the ``VAS Plan`` / ``VAS Product`` / ``VAS Attach Rule`` /
    ``VAS Claim`` doctypes were merged back into CH Warranty Plan
    (see ``ch_item_master.patches.v31_merge_vas_plan_into_ch_warranty_plan``),
    this endpoint had to join a sellable-catalog wrapper on top of the
    governance plan. Now the plan itself owns everything — sellable
    price bands, auto_attach, partner, min/max device price.

    Payload contract (JS-facing — cart_service.js::_render_vas_selector
    and ``_add_vas_to_cart`` consume these). ``vas_plan`` is retained
    as ``None`` so the front-end can still branch on it if needed, but
    it is always None now (the wrapper doctype is gone):
        name, vas_plan (=None), plan_name, plan_type, service_item,
        duration_months, price, pricing_mode, percentage_value,
        coverage_description, brand, fulfillment_type,
        allow_external_device, external_device_item,
        valid_from, valid_to, min_device_price, max_device_price,
        requires_device, allows_external_device,
        blocked, blocked_reason, applicable_categories

    ``name`` is the CH Warranty Plan name — the cart line's
    ``warranty_plan`` field is a Link to CH Warranty Plan and
    ``pos_invoice.py`` looks it up via
    ``frappe.get_cached_doc("CH Warranty Plan", ...)``.
    """
    if isinstance(cart_items, str):
        cart_items = frappe.parse_json(cart_items)
    cart_items = cart_items or []

    # Check if cart has any device (non-service, non-warranty item)
    has_device = False
    device_item_codes = []
    device_categories = set()
    for ci in cart_items:
        if ci.get("is_warranty") or ci.get("is_vas"):
            continue
        has_device = True
        ic = ci.get("item_code")
        device_item_codes.append(ic)
        ch_category = frappe.db.get_value("Item", ic, "ch_category")
        if ch_category:
            device_categories.add(ch_category)

    today = nowdate()

    plan_rows = frappe.get_all(
        "CH Warranty Plan",
        filters={"status": "Active", "is_sellable": 1},
        fields=[
            "name", "plan_name", "plan_type", "service_item",
            "duration_months", "price", "pricing_mode", "percentage_value",
            "coverage_description", "brand", "fulfillment_type",
            "allow_external_device", "external_device_item",
            "valid_from", "valid_to",
            "min_device_price", "max_device_price",
            "partner", "auto_attach",
        ],
    )

    def _resolve_price(fixed_price, pricing_mode, pct):
        """Effective sellable rate for a plan.

        Percentage-of-device-price plans compute rate against the cart's
        highest device price (same rule the direct-attach flow uses in
        ``attach_api._get_warranty_plans``). Fixed-mode plans return
        the stored governance price. Falls back to 0 if neither yields
        a positive number — the caller can decide whether to hide it.
        """
        if pricing_mode == "Percentage of Device Price" and flt(pct) > 0 and max_device_price:
            return round(max_device_price * flt(pct) / 100.0, 2)
        return flt(fixed_price or 0)

    plans: list[frappe._dict] = []
    for row in plan_rows:
        plans.append(frappe._dict({
            "name": row["name"],
            # VAS Plan doctype no longer exists; keep the key for JS
            # backward-compat but leave it None.
            "vas_plan": None,
            "plan_name": row.get("plan_name"),
            "plan_type": row.get("plan_type") or "Value Added Service",
            "service_item": row.get("service_item"),
            "duration_months": row.get("duration_months") or 0,
            "price": _resolve_price(
                row.get("price"),
                row.get("pricing_mode"),
                row.get("percentage_value"),
            ),
            "pricing_mode": row.get("pricing_mode"),
            "percentage_value": flt(row.get("percentage_value")),
            "coverage_description": row.get("coverage_description"),
            "brand": row.get("brand"),
            "fulfillment_type": row.get("fulfillment_type"),
            "allow_external_device": cint(row.get("allow_external_device")),
            "external_device_item": row.get("external_device_item"),
            "valid_from": row.get("valid_from"),
            "valid_to": row.get("valid_to"),
            "min_device_price": flt(row.get("min_device_price")),
            "max_device_price": flt(row.get("max_device_price")),
            "partner": row.get("partner"),
            "auto_attach": cint(row.get("auto_attach")),
        }))

    # ── Rule application — validity, device dependency, price band,
    #    and category applicability.
    applicable: list[dict] = []
    for plan in plans:
        # Validity window.
        if plan.valid_from and str(plan.valid_from) > today:
            continue
        if valid_to and str(valid_to) < today:
            continue

        # Device dependency: Protection Plans always require a device
        requires_device = plan.plan_type == "Protection Plan"
        allows_external_device = cint(plan.get("allow_external_device")) and bool(
            plan.get("external_device_item")
        )
        plan["requires_device"] = requires_device
        plan["allows_external_device"] = bool(allows_external_device)

        if requires_device and not has_device and not allows_external_device:
            plan["blocked"] = True
            plan["blocked_reason"] = frappe._("Requires a device in cart")
        elif requires_device and not has_device and allows_external_device:
            plan["blocked"] = False
            plan["blocked_reason"] = ""
            plan["external_imei_supported"] = True
        else:
            plan["blocked"] = False
            plan["blocked_reason"] = ""

        # Price band.
        min_dp = flt(plan.get("min_device_price"))
        max_dp = flt(plan.get("max_device_price"))
        if not plan.get("blocked") and (min_dp or max_dp) and max_device_price:
            if min_dp and max_device_price < min_dp:
                plan["blocked"] = True
                plan["blocked_reason"] = frappe._(
                    "Device price ₹{0} below plan minimum ₹{1}"
                ).format(max_device_price, min_dp)
            elif max_dp and max_device_price > max_dp:
                plan["blocked"] = True
                plan["blocked_reason"] = frappe._(
                    "Device price ₹{0} above plan maximum ₹{1}"
                ).format(max_device_price, max_dp)

        # Category filtering (CH Warranty Plan Category child table).
        if not plan.get("blocked"):
            plan_categories = frappe.get_all(
                "CH Warranty Plan Category",
                filters={"parent": plan.name},
                pluck="category",
            )
            if plan_categories:
                plan["applicable_categories"] = plan_categories
                if device_categories:
                    if not device_categories.intersection(set(plan_categories)):
                        plan["blocked"] = True
                        plan["blocked_reason"] = frappe._("Not applicable for {0}").format(
                            ", ".join(device_categories)
                        )
                if not device_categories and not allows_external_device:
                    plan["blocked"] = True
                    plan["blocked_reason"] = frappe._("Requires an eligible device in cart")
                # External-allowed plans stay unblocked; manual IMEI is validated before billing.

        applicable.append(plan)

    return applicable


# ── Quick Job Card from POS ──────────────────────────────────
@frappe.whitelist()
def create_quick_job_card(customer, contact_number, device_item,
                          issue_description, serial_no=None,
                          issue_category=None, warranty_status=None,
                          priority="Medium", estimated_hours=None,
                          device_condition=None, accessories_received=None,
                          data_backup_disclaimer=0) -> dict:
    """Create a Service Request, accept it, and create Job Assignment in one call.

    This is the 'quick job card' flow for walk-in repairs from POS.
    Returns: {service_request, service_order, job_assignment}
    """
    frappe.has_permission("Service Request", "create", throw=True)
    # 1. Create Service Request
    sr = frappe.new_doc("Service Request")
    sr.customer = customer
    sr.contact_number = contact_number
    sr.device_item = device_item
    sr.serial_no = serial_no or ""
    sr.issue_category = issue_category or ""
    sr.issue_description = issue_description
    sr.warranty_status = warranty_status or ""
    sr.device_condition = device_condition or ""
    sr.accessories_received = accessories_received or ""
    sr.data_backup_disclaimer = cint(data_backup_disclaimer)
    sr.mode_of_service = "Walk-in"
    sr.company = frappe.defaults.get_global_default("company") or ""
    sr.source_warehouse = frappe.form_dict.get("warehouse") or ""
    sr.service_date = nowdate()
    sr.decision = "Draft"
    sr.priority = priority or "Medium"
    sr.walkin_source = "POS Counter"
    sr.flags.ignore_permissions = True
    sr.insert()
    sr.submit()

    # 2. Accept → creates Service Order
    try:
        from gofix.gofix_services.doctype.service_request.service_request import (
            accept_service_request,
        )
    except ImportError:
        frappe.throw(frappe._("GoFix app is not installed — cannot create repair jobs from POS."))
    service_order = accept_service_request(sr.name)

    # 3. Create Job Assignment
    try:
        from gofix.gofix_services.doctype.job_assignment.job_assignment import (
            create_job_sheet_from_service_order,
        )
    except ImportError:
        frappe.throw(frappe._("GoFix app is not installed — cannot create job assignments from POS."))
    job_name = create_job_sheet_from_service_order(
        service_order,
        job_type="Repair",
        estimated_hours=flt(estimated_hours) if estimated_hours else None,
    )

    return {
        "service_request": sr.name,
        "service_order": service_order,
        "job_assignment": job_name,
    }


@frappe.whitelist()
def get_central_warehouses(company=None) -> dict:
    """Return warehouses suitable as source for stock requests (non-POS, non-store)."""
    if not company:
        company = frappe.defaults.get_global_default("company")

    warehouses = frappe.db.get_all(
        "Warehouse",
        filters={
            "company": company,
            "disabled": 0,
            "is_group": 0,
        },
        fields=["name", "warehouse_name"],
        order_by="warehouse_name",
    )
    return warehouses


# ── Executive Access & Incentive APIs ────────────────────────────

# Canonical mode lists — single source of truth (no JS duplication).
# Shared modes (imei, customer360, reports) are always included.
_SHARED_MODES = ["imei", "customer360", "reports"]

COMPANY_MODE_MAP = {
    "retail": [
        "sell", "returns", "buyback", "material_request", "inbound_receive",
        "stock_transfer", "guided", "model_compare", "claims", "exceptions",
        "queue", "prebook", "pickup", "stock_audit",
    ] + _SHARED_MODES,
    "service": [
        "sell", "returns", "buyback", "repair", "queue", "service",
        "guided", "exceptions", "prebook", "pickup", "stock_audit",
        "inbound_receive",
    ] + _SHARED_MODES,
}


def _get_company_type(company, stores=None):
    """Resolve company type ('retail' or 'service') from CH Store flags,
    falling back to a name heuristic if flags aren't set.

    Checks stores that *belong to* this company (via CH Store.company),
    not the user's current store.
    """
    # Check CH Store capability flags for stores owned by this company
    store_caps = frappe.db.get_all(
        "CH Store",
        filters={"company": company, "disabled": 0},
        fields=["is_retail_enabled", "is_service_enabled"],
    )
    for cap in store_caps:
        if cint(cap.is_service_enabled) and not cint(cap.is_retail_enabled):
            return "service"
        if cint(cap.is_retail_enabled) and not cint(cap.is_service_enabled):
            return "retail"

    # Fallback: name heuristic (single place — not duplicated in JS)
    lc = (company or "").lower()
    if "gofix" in lc or "service" in lc:
        return "service"
    return "retail"


def _get_executive_access(user, warehouse):
    """Build executive access payload for the logged-in user.

    Returns dict with:
      - companies: list of company names and roles this user can bill for
      - is_manager: True if user has Manager role for any company at this store
      - store_executives: list of all active executives at this store (for billing-by selector)
      - own_executive: the current user's POS Executive record(s)
    """
    # Find store(s) linked to this warehouse
    stores = frappe.db.get_all(
        "CH Store",
        filters={"warehouse": warehouse, "disabled": 0},
        pluck="name",
    )
    if not stores:
        # Also try POS Profile Extension store link
        store_from_ext = frappe.db.get_all(
            "POS Profile Extension",
            filters={"store": ("is", "set")},
            pluck="store",
        )
        stores = list(set(store_from_ext))

    # Get all executives at these stores
    all_execs = frappe.db.get_all(
        "POS Executive",
        filters={"store": ("in", stores), "is_active": 1} if stores else {"is_active": 1},
        fields=["name", "executive_name", "user", "store", "company", "role",
                "can_give_discount", "max_discount_pct", "sales_person"],
        order_by="company, executive_name",
    )

    # Current user's executive records
    own = [e for e in all_execs if e.user == user]
    own_companies = {e.company for e in own}
    is_manager = any(e.role == "Manager" for e in own)

    # A manager sees ALL companies at the store; non-manager sees only their company
    if is_manager:
        accessible_companies = list({e.company for e in all_execs})
    else:
        accessible_companies = list(own_companies)

    # Build company-role map with server-resolved type and allowed modes
    company_roles = []
    for comp in accessible_companies:
        user_exec = next((e for e in own if e.company == comp), None)
        role = user_exec.role if user_exec else "Manager"
        ctype = _get_company_type(comp)
        company_roles.append({
            "company": comp,
            "role": role,
            "company_type": ctype,
            "allowed_modes": COMPANY_MODE_MAP.get(ctype, COMPANY_MODE_MAP["retail"]),
        })

    # Store executives grouped by company (for the "billed by" selector)
    store_execs_by_company = {}
    for e in all_execs:
        if e.company not in accessible_companies:
            continue
        store_execs_by_company.setdefault(e.company, []).append({
            "name": e.name,
            "executive_name": e.executive_name,
            "user": e.user,
            "role": e.role,
            "can_give_discount": e.can_give_discount,
            "max_discount_pct": e.max_discount_pct,
        })

    # Determine user's own executive records — one per company
    own_default = None
    own_by_company = {}
    if own:
        own_default = {
            "name": own[0].name,
            "executive_name": own[0].executive_name,
            "company": own[0].company,
            "role": own[0].role,
            "can_give_discount": own[0].can_give_discount,
            "max_discount_pct": own[0].max_discount_pct,
        }
        for e in own:
            own_by_company[e.company] = {
                "name": e.name,
                "executive_name": e.executive_name,
                "company": e.company,
                "role": e.role,
                "can_give_discount": e.can_give_discount,
                "max_discount_pct": e.max_discount_pct,
            }

    return {
        "companies": company_roles,
        "is_manager": is_manager,
        "store_executives": store_execs_by_company,
        "own_executive": own_default,
        "own_by_company": own_by_company,
        "stores": stores,
    }


@frappe.whitelist()
def get_store_executives(warehouse=None, company=None) -> dict:
    """Return active executives for a store, optionally filtered by company."""
    filters = {"is_active": 1}

    if warehouse:
        stores = frappe.db.get_all("CH Store", filters={"warehouse": warehouse, "disabled": 0}, pluck="name")
        if stores:
            filters["store"] = ("in", stores)

    if company:
        filters["company"] = company

    return frappe.db.get_all(
        "POS Executive",
        filters=filters,
        fields=["name", "executive_name", "user", "store", "company", "role",
                "can_give_discount", "max_discount_pct", "sales_person"],
        order_by="company, executive_name",
    )


@frappe.whitelist()
def get_executive_incentive_summary(pos_executive, from_date=None, to_date=None) -> dict:
    """Return incentive summary for an executive — used in the POS dashboard."""
    if not from_date:
        from_date = frappe.utils.get_first_day(nowdate())
    if not to_date:
        to_date = nowdate()

    ledger = frappe.db.get_all(
        "POS Incentive Ledger",
        filters={
            "pos_executive": pos_executive,
            "posting_date": ("between", [from_date, to_date]),
            "status": ("!=", "Cancelled"),
        },
        fields=[
            "sum(incentive_amount) as total_incentive",
            "sum(billing_amount) as total_billing",
            "count(name) as total_transactions",
            "transaction_type",
        ],
        group_by="transaction_type",
    )

    total = sum(flt(r.total_incentive) for r in ledger)
    total_billing = sum(flt(r.total_billing) for r in ledger)
    by_type = {r.transaction_type: {
        "incentive": flt(r.total_incentive),
        "billing": flt(r.total_billing),
        "count": cint(r.total_transactions),
    } for r in ledger}

    return {
        "total_incentive": total,
        "total_billing": total_billing,
        "total_transactions": sum(cint(r.total_transactions) for r in ledger),
        "sales_incentive": flt((by_type.get("Sale") or {}).get("incentive", 0)),
        "vas_incentive": flt((by_type.get("VAS") or {}).get("incentive", 0)),
        "service_incentive": flt((by_type.get("Service") or {}).get("incentive", 0)),
        "by_type": by_type,
        "from_date": str(from_date),
        "to_date": str(to_date),
    }


def _find_incentive_slab(company, item_group, brand, billing_amount, transaction_type="Sale"):
    """Find the best-matching incentive slab for a billing line.

    Matching priority (highest first):
      1. Exact company + item_group + brand
      2. company + item_group (any brand)
      3. company + brand (any item_group)
      4. company only (catch-all)
    """
    slabs = frappe.db.get_all(
        "POS Incentive Slab",
        filters={
            "company": company,
            "applicable_on": transaction_type,
            "is_active": 1,
            "from_amount": ("<=", billing_amount),
            "to_amount": (">=", billing_amount),
        },
        fields=["name", "item_group", "brand", "incentive_type", "incentive_value", "priority"],
        order_by="priority desc, name",
    )

    if not slabs:
        return None

    # Score each slab by specificity
    best = None
    best_score = -1
    for slab in slabs:
        score = slab.priority * 100  # base from priority
        if slab.item_group and slab.item_group == item_group:
            score += 20
        elif slab.item_group and slab.item_group != item_group:
            continue  # item_group specified but doesn't match — skip
        if slab.brand and slab.brand == brand:
            score += 10
        elif slab.brand and slab.brand != brand:
            continue  # brand specified but doesn't match — skip

        if score > best_score:
            best_score = score
            best = slab

    return best


def _create_incentive_entries(invoice, pos_executive, transaction_type="Sale"):
    """Create POS Incentive Ledger entries for each item in the invoice.

    Returns total incentive amount earned.
    """
    exec_doc = frappe.db.get_value(
        "POS Executive", pos_executive,
        ["executive_name", "store", "company"], as_dict=True,
    )
    if not exec_doc:
        return 0

    total_incentive = 0
    posting_date = invoice.posting_date or nowdate()
    payout_month = str(posting_date)[:7]  # YYYY-MM

    for item in invoice.items:
        # Warranty / VAS items get their own incentive type
        item_type = transaction_type
        item_group = frappe.db.get_value("Item", item.item_code, "item_group") or ""
        brand = frappe.db.get_value("Item", item.item_code, "brand") or ""

        if item_group in ("Repair Services", "Mobile Parts", "Spares", "Sub Assemblies"):
            item_type = "Service"
        if item.get("custom_warranty_plan"):
            wp_type = frappe.db.get_value(
                "CH Warranty Plan", item.custom_warranty_plan, "plan_type"
            )
            if wp_type in ("Value Added Service", "Protection Plan"):
                item_type = "VAS"
            else:
                item_type = "Warranty"

        billing_amount = flt(item.amount)
        if billing_amount <= 0:
            continue

        slab = _find_incentive_slab(
            company=exec_doc.company,
            item_group=item_group,
            brand=brand,
            billing_amount=billing_amount,
            transaction_type=item_type,
        )

        # Backward compatibility: many existing configurations only define
        # Sale slabs for repair/service item groups.
        if not slab and item_type == "Service":
            slab = _find_incentive_slab(
                company=exec_doc.company,
                item_group=item_group,
                brand=brand,
                billing_amount=billing_amount,
                transaction_type="Sale",
            )

        incentive_amount = 0
        if slab:
            if slab.incentive_type == "Percentage":
                incentive_amount = billing_amount * flt(slab.incentive_value) / 100
            else:
                incentive_amount = flt(slab.incentive_value)

        if incentive_amount <= 0 and not slab:
            continue  # No slab match, no entry

        ledger = frappe.new_doc("POS Incentive Ledger")
        ledger.pos_executive = pos_executive
        ledger.executive_name = exec_doc.executive_name
        ledger.store = exec_doc.store
        ledger.company = exec_doc.company
        ledger.posting_date = posting_date
        ledger.transaction_type = item_type
        ledger.invoice = invoice.name
        ledger.item_code = item.item_code
        ledger.item_name = item.item_name
        ledger.item_group = item_group
        ledger.brand = brand
        ledger.qty = flt(item.qty)
        ledger.billing_amount = billing_amount
        ledger.incentive_slab = slab.name if slab else None
        ledger.incentive_type = slab.incentive_type if slab else ""
        ledger.incentive_value = flt(slab.incentive_value) if slab else 0
        ledger.incentive_amount = flt(incentive_amount, 2)
        ledger.status = "Pending"
        ledger.payout_month = payout_month
        ledger.flags.ignore_permissions = True
        ledger.save()

        total_incentive += flt(incentive_amount, 2)

    return total_incentive


def _missing_incentive_setup(invoice, sales_executive) -> bool:
    """Return True when incentive setup is clearly missing for this billing context."""
    if not sales_executive:
        return False

    exec_doc = frappe.db.get_value(
        "POS Executive", sales_executive,
        ["name", "company"], as_dict=True,
    )
    if not exec_doc or not exec_doc.company:
        return True

    has_active_slab = frappe.db.exists("POS Incentive Slab", {
        "company": exec_doc.company,
        "is_active": 1,
    })
    if not has_active_slab:
        return True

    return False


def _create_return_incentive_entries(return_invoice, pos_executive):
    """Create negative incentive entries for returns (clawback)."""
    exec_doc = frappe.db.get_value(
        "POS Executive", pos_executive,
        ["executive_name", "store", "company"], as_dict=True,
    )
    if not exec_doc:
        return 0

    total_clawback = 0
    posting_date = return_invoice.posting_date or nowdate()
    payout_month = str(posting_date)[:7]

    for item in return_invoice.items:
        billing_amount = abs(flt(item.amount))
        if billing_amount <= 0:
            continue

        item_group = frappe.db.get_value("Item", item.item_code, "item_group") or ""
        brand = frappe.db.get_value("Item", item.item_code, "brand") or ""

        slab = _find_incentive_slab(
            company=exec_doc.company,
            item_group=item_group,
            brand=brand,
            billing_amount=billing_amount,
            transaction_type="Return",
        )

        # If no Return slab, fall back to Sale slab for clawback
        if not slab:
            slab = _find_incentive_slab(
                company=exec_doc.company,
                item_group=item_group,
                brand=brand,
                billing_amount=billing_amount,
                transaction_type="Sale",
            )

        incentive_amount = 0
        if slab:
            if slab.incentive_type == "Percentage":
                incentive_amount = billing_amount * flt(slab.incentive_value) / 100
            else:
                incentive_amount = flt(slab.incentive_value)

        # Clawback = negative incentive
        clawback = -abs(incentive_amount) if incentive_amount else 0

        ledger = frappe.new_doc("POS Incentive Ledger")
        ledger.pos_executive = pos_executive
        ledger.executive_name = exec_doc.executive_name
        ledger.store = exec_doc.store
        ledger.company = exec_doc.company
        ledger.posting_date = posting_date
        ledger.transaction_type = "Return"
        ledger.invoice = return_invoice.return_against
        ledger.return_invoice = return_invoice.name
        ledger.item_code = item.item_code
        ledger.item_name = item.item_name
        ledger.item_group = item_group
        ledger.brand = brand
        ledger.qty = flt(item.qty)
        ledger.billing_amount = -billing_amount
        ledger.incentive_slab = slab.name if slab else None
        ledger.incentive_type = slab.incentive_type if slab else ""
        ledger.incentive_value = flt(slab.incentive_value) if slab else 0
        ledger.incentive_amount = flt(clawback, 2)
        ledger.status = "Pending"
        ledger.payout_month = payout_month
        ledger.flags.ignore_permissions = True
        ledger.save()

        total_clawback += flt(clawback, 2)

    return total_clawback


@frappe.whitelist()
def backfill_missing_incentive_ledger(from_date=None, to_date=None, company=None) -> dict:
    """Backfill POS Incentive Ledger for submitted invoices missing entries.

    Safe behavior:
    - Processes only submitted non-return Sales Invoices
    - Requires linked custom_sales_executive
    - Skips invoices that already have ledger rows for same invoice+executive
    """
    if not any(r in frappe.get_roles() for r in ("System Manager", "POS Manager", "Accounts Manager")):
        frappe.throw(frappe._("Not permitted"), frappe.PermissionError)

    conditions = ["docstatus = 1", "IFNULL(custom_sales_executive, '') != ''", "IFNULL(is_return, 0) = 0"]
    values = {}

    if from_date:
        conditions.append("posting_date >= %(from_date)s")
        values["from_date"] = from_date
    if to_date:
        conditions.append("posting_date <= %(to_date)s")
        values["to_date"] = to_date
    if company:
        conditions.append("company = %(company)s")
        values["company"] = company

    invoices = frappe.db.sql(
        """
        SELECT name, custom_sales_executive
        FROM `tabSales Invoice`
        WHERE {conditions}
        ORDER BY posting_date, creation
        """.format(conditions=" AND ".join(conditions)),
        values,
        as_dict=True,
    )

    stats = {
        "examined": len(invoices),
        "processed": 0,
        "created": 0,
        "skipped_existing": 0,
        "errors": 0,
    }

    for row in invoices:
        try:
            exists = frappe.db.exists("POS Incentive Ledger", {
                "invoice": row.name,
                "pos_executive": row.custom_sales_executive,
            })
            if exists:
                stats["skipped_existing"] += 1
                continue

            inv = frappe.get_doc("Sales Invoice", row.name)
            earned = flt(_create_incentive_entries(inv, row.custom_sales_executive, transaction_type="Sale"))
            stats["processed"] += 1
            if earned or frappe.db.exists("POS Incentive Ledger", {"invoice": row.name}):
                stats["created"] += 1
        except Exception:
            stats["errors"] += 1
            frappe.log_error(frappe.get_traceback(), f"Incentive backfill failed for {row.name}")

    frappe.db.commit()
    return stats


def calculate_attach_rate_bonus(company=None, payout_month=None):
    """Calculate monthly attach-rate bonus for POS executives.

    For each executive, computes their warranty/VAS/accessory attach rates
    from CH Attach Log vs total device sales. Awards bonus incentive entries
    based on POS Incentive Slab with applicable_on = 'Attach Rate'.

    Called from daily digest or month-end scheduler.
    Returns list of {executive, attach_pct, bonus_amount}.
    """
    if not payout_month:
        payout_month = str(getdate(nowdate()))[:7]  # YYYY-MM

    year, month = payout_month.split("-")
    from_date = f"{year}-{month}-01"
    to_date = str(get_last_day(getdate(from_date)))

    executives = frappe.get_all("POS Executive",
        filters={"is_active": 1, "company": company} if company else {"is_active": 1},
        fields=["name", "executive_name", "store", "company", "user"],
    )

    results = []
    for exec_doc in executives:
        user = exec_doc.user
        if not user:
            continue

        # Count device sales (items offered for attach)
        total_offered = frappe.db.count("CH Attach Log", filters={
            "offered_by": user,
            "action": "Offered",
            "offered_at": ["between", [from_date, to_date]],
        })

        if total_offered == 0:
            continue

        total_accepted = frappe.db.count("CH Attach Log", filters={
            "offered_by": user,
            "action": "Accepted",
            "offered_at": ["between", [from_date, to_date]],
        })

        attach_pct = flt(total_accepted / total_offered * 100, 1)

        # Find matching Attach Rate incentive slab
        slab = _find_incentive_slab(
            company=exec_doc.company,
            item_group="",
            brand="",
            billing_amount=attach_pct,  # Use attach % as the slab range
            transaction_type="Attach Rate",
        )

        bonus = 0
        if slab:
            if slab.incentive_type == "Percentage":
                # % of total sales amount for the month
                monthly_sales = flt(frappe.db.sql("""
                    SELECT COALESCE(SUM(si.grand_total), 0)
                    FROM `tabSales Invoice` si
                    WHERE si.docstatus = 1 AND si.is_pos = 1
                        AND si.owner = %(user)s
                        AND si.posting_date BETWEEN %(from)s AND %(to)s
                """, {"user": user, "from": from_date, "to": to_date})[0][0])
                bonus = monthly_sales * flt(slab.incentive_value) / 100
            else:
                bonus = flt(slab.incentive_value)

        if bonus <= 0:
            continue

        # Check if already created for this month
        existing = frappe.db.exists("POS Incentive Ledger", {
            "pos_executive": exec_doc.name,
            "transaction_type": "Attach Rate",
            "payout_month": payout_month,
        })
        if existing:
            continue

        ledger = frappe.new_doc("POS Incentive Ledger")
        ledger.pos_executive = exec_doc.name
        ledger.executive_name = exec_doc.executive_name
        ledger.store = exec_doc.store
        ledger.company = exec_doc.company
        ledger.posting_date = to_date
        ledger.transaction_type = "Attach Rate"
        ledger.item_code = ""
        ledger.item_name = f"Attach Rate Bonus ({attach_pct}%)"
        ledger.billing_amount = 0
        ledger.incentive_slab = slab.name
        ledger.incentive_type = slab.incentive_type
        ledger.incentive_value = flt(slab.incentive_value)
        ledger.incentive_amount = flt(bonus, 2)
        ledger.status = "Pending"
        ledger.payout_month = payout_month
        ledger.flags.ignore_permissions = True
        ledger.save()

        results.append({
            "executive": exec_doc.executive_name,
            "attach_pct": attach_pct,
            "bonus_amount": flt(bonus, 2),
        })

    return results


# ── Update Customer Details (from POS) ───────────────────────────
@frappe.whitelist()
def update_customer_details(customer, mobile_no=None, email_id=None,
                           customer_name=None, alternate_phone=None,
                           whatsapp_number=None, pan_number=None) -> dict:
    """Update customer details from POS Customer 360 view.

    Only updates fields that are explicitly passed (non-None).
    Phone number changes trigger dedup check and audit trail via
    customer.py validate hooks.
    """
    frappe.has_permission("Customer", "write", throw=True)

    if not customer or not frappe.db.exists("Customer", customer):
        frappe.throw(frappe._("Customer {0} not found").format(customer))

    cust = frappe.get_doc("Customer", customer)
    changed = False

    if customer_name is not None and customer_name.strip():
        cust.customer_name = customer_name.strip()
        changed = True

    if mobile_no is not None:
        mobile_no = mobile_no.strip()
        if mobile_no != (cust.mobile_no or ""):
            cust.mobile_no = mobile_no
            changed = True

    if email_id is not None:
        email_id = email_id.strip()
        if email_id != (cust.email_id or ""):
            cust.email_id = email_id
            changed = True

    if alternate_phone is not None:
        alternate_phone = alternate_phone.strip()
        if alternate_phone != (cust.get("ch_alternate_phone") or ""):
            cust.ch_alternate_phone = alternate_phone
            changed = True

    if whatsapp_number is not None:
        whatsapp_number = whatsapp_number.strip()
        if whatsapp_number != (cust.get("ch_whatsapp_number") or ""):
            cust.ch_whatsapp_number = whatsapp_number
            changed = True

    if pan_number is not None:
        pan_clean = pan_number.strip().upper()
        if pan_clean != (cust.get("ch_pan_number") or ""):
            cust.ch_pan_number = pan_clean
            cust.pan = pan_clean
            changed = True

    if not changed:
        return {"ok": True, "message": "No changes detected"}

    # Save triggers validate hooks: phone format, dedup, phone change tracking
    cust.flags.ignore_permissions = True
    cust.save()

    return {
        "ok": True,
        "customer": cust.name,
        "customer_name": cust.customer_name,
        "mobile_no": cust.mobile_no or "",
        "email_id": cust.email_id or "",
        "membership_id": cust.get("ch_membership_id") or "",
        "alternate_phone": cust.get("ch_alternate_phone") or "",
        "whatsapp_number": cust.get("ch_whatsapp_number") or "",
        "previous_phones": cust.get("ch_previous_phones") or "",
        "pan": cust.get("ch_pan_number") or cust.get("pan") or "",
    }

@frappe.whitelist()
def update_customer_complete(customer, payload):
    """
    Single atomic update for Customer + Billing Address + Shipping Address + Extras.
    Handles everything in ONE transaction → no deadlocks.
    """
    import json
    if isinstance(payload, str):
        payload = json.loads(payload)

    frappe.has_permission("Customer", "write", throw=True)

    if not customer or not frappe.db.exists("Customer", customer):
        frappe.throw(_("Customer {0} not found").format(customer))

    # ──────────────────────────────────────────────
    # 1. UPDATE CUSTOMER DOC
    # ──────────────────────────────────────────────
    cust = frappe.get_doc("Customer", customer)
    cust_changed = False

    # Editable customer-level fields (name/mobile/email/whatsapp are locked on frontend)
    standard_map = {
        "customer_group": "customer_group",
        "gstin": "gstin",
    }
    for src, dest in standard_map.items():
        val = payload.get(src)
        if val is not None:
            val = val.strip() if isinstance(val, str) else val
            if src == "gstin":
                val = val.upper() if val else ""
            if val != (cust.get(dest) or ""):
                cust.set(dest, val)
                cust_changed = True

    # Custom (ch_*) editable fields
    custom_map = {
        "alternate_no": "ch_alternate_phone",
    }
    for src, dest in custom_map.items():
        val = payload.get(src)
        if val is not None:
            val = val.strip() if isinstance(val, str) else val
            if val != (cust.get(dest) or ""):
                cust.set(dest, val)
                cust_changed = True

    # PAN — mirror to both fields
    if payload.get("pan") is not None:
        pan_clean = (payload["pan"] or "").strip().upper()
        if pan_clean != (cust.get("ch_pan_number") or ""):
            cust.ch_pan_number = pan_clean
            cust.pan = pan_clean
            cust_changed = True

    # 2. BILLING ADDRESS (primary)
    billing_fields = ["address_line1", "address_line2", "city", "state", "pincode"]
    has_billing_data = any(payload.get(f) for f in billing_fields)

    if has_billing_data:
        _upsert_address(
            customer=customer,
            customer_name=cust.customer_name,
            address_type="Billing",
            is_primary=1,
            is_shipping=0,
            data={
                "address_line1": payload.get("address_line1"),
                "address_line2": payload.get("address_line2"),
                "city": payload.get("city"),
                "state": payload.get("state"),
                "pincode": payload.get("pincode"),
            },
            link_field="customer_primary_address",
        )
        cust = frappe.get_doc("Customer", customer)
        for src, dest in standard_map.items():
            val = payload.get(src)
            if val is not None:
                val = val.strip() if isinstance(val, str) else val
                if src == "gstin":
                    val = val.upper() if val else ""
                if val != (cust.get(dest) or ""):
                    cust.set(dest, val)
        for src, dest in custom_map.items():
            val = payload.get(src)
            if val is not None:
                val = val.strip() if isinstance(val, str) else val
                if val != (cust.get(dest) or ""):
                    cust.set(dest, val)
        if payload.get("pan") is not None:
            pan_clean = (payload["pan"] or "").strip().upper()
            if pan_clean != (cust.get("ch_pan_number") or ""):
                cust.ch_pan_number = pan_clean
                cust.pan = pan_clean

    # 3. NOW save Customer (address exists >>>>validation passes)
    if cust_changed:
        cust.flags.ignore_permissions = True
        cust.save()

    # 4. SHIPPING ADDRESS
    ship_same = payload.get("ship_to_same_as_billing")
    
    # If "same as billing" is checked → skip shipping save (or delete old shipping)
    if ship_same:
        # Optionally remove existing separate shipping address
        pass
    else:
        shipping_fields = ["shipping_address_line1", "shipping_city", "shipping_state", "shipping_pincode"]
        has_shipping_data = any(payload.get(f) for f in shipping_fields)

        if has_shipping_data:
            _upsert_address(
                customer=customer,
                customer_name=cust.customer_name,
                address_type="Shipping",
                is_primary=0,
                is_shipping=1,
                data={
                    "address_line1": payload.get("shipping_address_line1"),
                    "address_line2": payload.get("shipping_address_line2"),
                    "city": payload.get("shipping_city"),
                    "state": payload.get("shipping_state"),
                    "pincode": payload.get("shipping_pincode"),
                },
                link_field=None,
            )

    # ... shipping address code above ...

    # ── Clear caches so next fetch returns fresh data ──
    frappe.clear_document_cache("Customer", customer)
    primary_addr = frappe.db.get_value("Customer", customer, "customer_primary_address")
    # 🔥 Update Customer's primary_address display text (this is what search shows)
    if primary_addr:
        addr_doc = frappe.db.get_value("Address", primary_addr, 
            ["address_line1", "address_line2", "city", "state", "pincode", "country"], 
            as_dict=True)
        
        if addr_doc:
            # Build the display string like Frappe does
            parts = [
                addr_doc.address_line1,
                addr_doc.address_line2,
                addr_doc.city,
                addr_doc.state,
                addr_doc.pincode,
                addr_doc.country
            ]
            display = "\n".join([p for p in parts if p])
            
            # Update Customer.primary_address with new display
            frappe.db.sql("""
                UPDATE `tabCustomer`
                SET primary_address = %s,
                    modified = NOW(),
                    modified_by = %s
                WHERE name = %s
            """, (display, frappe.session.user, customer))
            

    frappe.db.commit()
    frappe.clear_document_cache("Customer", customer)

    return {
        "ok": True,
        "customer": customer,
        "customer_name": cust.customer_name,
    }

def _upsert_address(customer, customer_name, address_type, is_primary, is_shipping, data, link_field=None):
    """
    Find/create address using DIRECT SQL to bypass all hooks/validations
    that silently revert changes.
    """
    addr_name = None

    # Find existing address
    if address_type == "Billing":
        addr_name = frappe.db.get_value("Customer", customer, "customer_primary_address")
        if addr_name and not frappe.db.exists("Address", addr_name):
            addr_name = None

    elif address_type == "Shipping":
        existing = frappe.db.sql("""
            SELECT addr.name FROM `tabAddress` addr
            INNER JOIN `tabDynamic Link` dl ON dl.parent = addr.name
            WHERE dl.link_doctype = 'Customer' AND dl.link_name = %s
              AND dl.parenttype = 'Address' AND addr.address_type = 'Shipping'
            ORDER BY addr.modified DESC LIMIT 1
        """, customer, as_dict=True)
        if existing:
            addr_name = existing[0].name

    # Get values to save
    city = (data.get("city") or "").strip()
    state = (data.get("state") or "").strip()
    pincode = (data.get("pincode") or "").strip()
    address_line1 = (data.get("address_line1") or "").strip() or "N/A"
    address_line2 = (data.get("address_line2") or "").strip()



    # Auto-create CH State if missing
    if state and not frappe.db.exists("CH State", state):
        try:
            new_state = frappe.new_doc("CH State")
            meta = frappe.get_meta("CH State")
            if meta.has_field("state_name"):
                new_state.state_name = state
            elif meta.has_field("state"):
                new_state.state = state
            new_state.flags.ignore_permissions = True
            new_state.insert(ignore_if_duplicate=True)
            print(f"Created CH State: {state}")
        except Exception as e:
            print(f"CH State create failed: {e}")

    # Auto-create CH City if missing
    if city and not frappe.db.exists("CH City", city):
        try:
            new_city = frappe.new_doc("CH City")
            meta = frappe.get_meta("CH City")
            if meta.has_field("city_name"):
                new_city.city_name = city
            elif meta.has_field("city"):
                new_city.city = city
            if state and meta.has_field("state"):
                new_city.state = state
            new_city.flags.ignore_permissions = True
            new_city.insert(ignore_if_duplicate=True)
            print(f"Created CH City: {city}")
        except Exception as e:
            print(f"CH City create failed: {e}")

    # Sync CH City state
    if city and state and frappe.db.exists("CH City", city):
        try:
            current = frappe.db.get_value("CH City", city, "state")
            if current != state:
                frappe.db.set_value("CH City", city, "state", state, update_modified=False)
        except Exception:
            pass

    # ── CASE 1: Address exists — UPDATE via direct SQL ──
    if addr_name:
        
        try:
            # Direct SQL UPDATE — bypasses ALL Frappe hooks/validations
            frappe.db.sql("""
                UPDATE `tabAddress`
                SET city = %s,
                    state = %s,
                    pincode = %s,
                    address_line1 = %s,
                    address_line2 = %s,
                    is_primary_address = %s,
                    is_shipping_address = %s,
                    modified = NOW()
                WHERE name = %s
            """, (city, state, pincode, address_line1, address_line2, 
                  is_primary, is_shipping, addr_name))
            
            frappe.db.commit()
            
            # Verify
            check = frappe.db.sql("""
                SELECT city, state, pincode FROM `tabAddress` WHERE name = %s
            """, addr_name, as_dict=True)
            print(f"Verified DB now has: {check}\n")
            
        except Exception as e:
            print(f"Direct SQL update failed: {e}")
            raise

    # ── CASE 2: New address — INSERT via Frappe ──
    else:
        addr = frappe.new_doc("Address")
        addr.address_title = f"{customer_name}-{address_type}"
        addr.address_type = address_type
        addr.address_line1 = address_line1
        addr.address_line2 = address_line2
        addr.city = city
        addr.state = state
        addr.pincode = pincode
        addr.country = "India"
        addr.is_primary_address = is_primary
        addr.is_shipping_address = is_shipping
        addr.append("links", {
            "link_doctype": "Customer",
            "link_name": customer,
        })
        
        addr.flags.ignore_permissions = True
        try:
            addr.insert()
            addr_name = addr.name
        except frappe.ValidationError as e:
            err_msg = str(e)
            if "Postal Code" in err_msg or "Postal" in err_msg:
                addr = frappe.new_doc("Address")
                addr.address_title = f"{customer_name}-{address_type}"
                addr.address_type = address_type
                addr.address_line1 = address_line1
                addr.address_line2 = address_line2
                addr.city = city
                addr.state = state
                addr.pincode = ""  # cleared
                addr.country = "India"
                addr.is_primary_address = is_primary
                addr.is_shipping_address = is_shipping
                addr.append("links", {
                    "link_doctype": "Customer",
                    "link_name": customer,
                })
                addr.flags.ignore_permissions = True
                addr.insert()
                addr_name = addr.name
            else:
                raise

    # Link as primary for billing
    if address_type == "Billing" and addr_name:
        frappe.db.set_value(
            "Customer", customer, "customer_primary_address", addr_name,
            update_modified=False
        )

    # Clear Address cache
    if addr_name:
        frappe.clear_document_cache("Address", addr_name)

    return addr_name

def _phone_suffix_10(phone_no):
    """Return normalized 10-digit Indian phone suffix, or empty string."""
    phone_no = (phone_no or "").strip()
    if not phone_no:
        return ""

    try:
        normalized = validate_indian_phone(phone_no, "Phone Number")
    except TypeError:
        normalized = validate_indian_phone(phone_no)
    except Exception:
        normalized = phone_no

    digits = "".join(ch for ch in str(normalized) if ch.isdigit())
    if len(digits) < 10:
        return ""
    return digits[-10:]


def _find_existing_customer_by_phone_suffix(phone_suffix):
    """Find existing customer by mobile/alternate/whatsapp/contact phone."""
    if not phone_suffix:
        return None

    like_value = f"%{phone_suffix}"
    row = frappe.db.sql(
        """
        SELECT name, customer_name, mobile_no, ch_alternate_phone, ch_whatsapp_number
        FROM `tabCustomer`
        WHERE (mobile_no LIKE %(like)s
            OR ch_alternate_phone LIKE %(like)s
            OR ch_whatsapp_number LIKE %(like)s)
          AND disabled = 0
        ORDER BY modified DESC
        LIMIT 1
        """,
        {"like": like_value},
        as_dict=True,
    )
    if row:
        return row[0]

    row = frappe.db.sql(
        """
        SELECT c.name, c.customer_name, c.mobile_no, c.ch_alternate_phone, c.ch_whatsapp_number
        FROM `tabContact Phone` cp
        JOIN `tabDynamic Link` dl
          ON dl.parent = cp.parent
         AND dl.parenttype = 'Contact'
         AND dl.link_doctype = 'Customer'
        JOIN `tabCustomer` c
          ON c.name = dl.link_name
        WHERE cp.phone LIKE %(like)s
          AND c.disabled = 0
        ORDER BY cp.modified DESC
        LIMIT 1
        """,
        {"like": like_value},
        as_dict=True,
    )
    return row[0] if row else None


@frappe.whitelist()
def find_existing_customer_by_phone(phone_no):
    """Check whether a customer already exists for the given phone number."""
    suffix = _phone_suffix_10(phone_no)
    if not suffix:
        return {"exists": False}

    hit = _find_existing_customer_by_phone_suffix(suffix)
    if not hit:
        return {"exists": False}

    return {
        "exists": True,
        "customer": hit.get("name"),
        "customer_name": hit.get("customer_name"),
        "mobile_no": hit.get("mobile_no") or "",
        "alternate_phone": hit.get("ch_alternate_phone") or "",
        "whatsapp_number": hit.get("ch_whatsapp_number") or "",
    }


# ── Quick Customer Creation ──────────────────────────────────────
@frappe.whitelist()
def quick_create_customer(customer_name, mobile_no="", email_id="",
                          customer_group="Individual", company=None,
                          alternate_phone="", whatsapp_number="",
                          address_line1="", address_line2="", city="",
                          state="", pincode="", area="", gstin="",
                          pan_number="",
                          same_as_billing=1,
                          shipping_address_line1="", shipping_city="",
                          shipping_state="", shipping_pincode="") -> dict:
    """Create a new Customer quickly from the POS interface."""
    frappe.has_permission("Customer", "create", throw=True)

    if not (mobile_no or "").strip():
        frappe.throw(_("Mobile Number is mandatory"), title=_("Missing Mobile Number"))
    if not (state or "").strip():
        frappe.throw(_("State is mandatory"), title=_("Missing State"))

    city, state = _resolve_pos_city_state(city, state)

    mobile_suffix = _phone_suffix_10(mobile_no)
    whatsapp_suffix = _phone_suffix_10(whatsapp_number)
    email_id = (email_id or "").strip()
    if email_id:
        email_id = validate_email_address(email_id, throw=True)

    if mobile_suffix:
        hit = _find_existing_customer_by_phone_suffix(mobile_suffix)
        if hit:
            frappe.throw(
                _("Customer already exists with mobile number {0}: <b>{1}</b> ({2}). Please select the existing customer.").format(
                    mobile_no,
                    hit.get("customer_name") or hit.get("name"),
                    hit.get("name"),
                ),
                title=_("Duplicate Customer"),
            )

    if whatsapp_suffix and whatsapp_suffix != mobile_suffix:
        hit = _find_existing_customer_by_phone_suffix(whatsapp_suffix)
        if hit:
            frappe.throw(
                _("Customer already exists with WhatsApp number {0}: <b>{1}</b> ({2}). Please select the existing customer.").format(
                    whatsapp_number,
                    hit.get("customer_name") or hit.get("name"),
                    hit.get("name"),
                ),
                title=_("Duplicate Customer"),
            )

    cust = frappe.new_doc("Customer")
    cust.customer_name = customer_name
    cust.customer_group = customer_group or "Individual"
    cust.customer_type = "Individual"
    cust.territory = frappe.db.get_single_value("Selling Settings", "territory") or "India"
    if company:
        cust.company = company
    # Set phone/email directly on Customer so dedup and lookups work
    if mobile_no:
        cust.mobile_no = mobile_no.strip()
    if email_id:
        cust.email_id = email_id
    if alternate_phone:
        cust.ch_alternate_phone = alternate_phone.strip()
    if whatsapp_number:
        cust.ch_whatsapp_number = whatsapp_number.strip()
    cust.flags.ignore_permissions = True
    cust.flags.ignore_mandatory = True
    # Store GSTIN + PAN directly on Customer master for instant lookup
    if gstin:
        cust.custom_gstin = gstin.strip().upper()
    if pan_number:
        pan_clean = pan_number.strip().upper()
        cust.pan = pan_clean
        cust.ch_pan_number = pan_clean
    cust.save()

    # Add contact details if provided
    if mobile_no or email_id:
        contact = frappe.new_doc("Contact")
        contact.first_name = customer_name
        if email_id:
            contact.append("email_ids", {"email_id": email_id, "is_primary": 1})
        if mobile_no:
            contact.append("phone_nos", {"phone": mobile_no, "is_primary_mobile_no": 1})
        contact.append("links", {"link_doctype": "Customer", "link_name": cust.name})
        contact.flags.ignore_permissions = True
        contact.save()

    # Create billing address if provided
    if address_line1 or city or pincode:
        billing = frappe.new_doc("Address")
        billing.address_title = customer_name
        billing.address_type = "Billing"
        billing.address_line1 = address_line1 or customer_name
        billing.address_line2 = address_line2
        billing.city = city or ""
        billing.state = state or ""
        billing.pincode = pincode or ""
        if area:
            billing.county = area  # Use county field for area/locality
        if gstin:
            billing.gstin = gstin
        billing.append("links", {"link_doctype": "Customer", "link_name": cust.name})
        billing.flags.ignore_permissions = True
        billing.flags.ignore_mandatory = True
        billing.save()

        # Create separate shipping address if not same as billing
        if not cint(same_as_billing) and shipping_address_line1:
            shipping = frappe.new_doc("Address")
            shipping.address_title = customer_name
            shipping.address_type = "Shipping"
            shipping.address_line1 = shipping_address_line1
            shipping.city = shipping_city or ""
            shipping.state = shipping_state or ""
            shipping.pincode = shipping_pincode or ""
            shipping.append("links", {"link_doctype": "Customer", "link_name": cust.name})
            shipping.flags.ignore_permissions = True
            shipping.flags.ignore_mandatory = True
            shipping.save()

    return cust.name


# ═══════════════════════════════════════════════════════════════════════════
# Walk-in / Footfall Counter
# ═══════════════════════════════════════════════════════════════════════════

def _get_active_session_log(pos_profile):
    """Return the active POS Session Log name for this profile, or None."""
    return frappe.db.get_value(
        "POS Session Log",
        {"pos_profile": pos_profile, "status": "Active", "docstatus": 1},
        "name",
        order_by="creation desc",
    )


@frappe.whitelist()
def log_walkin(pos_profile, source="POS Counter") -> dict:
    """Increment walk-in counter on the active session log.

    Args:
        pos_profile: Current POS Profile
        source: 'POS Counter' | 'Kiosk'
    """
    session_log = _get_active_session_log(pos_profile)
    if not session_log:
        return {"ok": False, "reason": "No active session log"}

    if source == "Kiosk":
        frappe.db.sql(
            "UPDATE `tabPOS Session Log` SET kiosk_count = kiosk_count + 1 WHERE name = %s",
            (session_log,)
        )
    else:
        frappe.db.sql(
            "UPDATE `tabPOS Session Log` SET walkin_count = walkin_count + 1 WHERE name = %s",
            (session_log,)
        )

    new_walkin = frappe.db.get_value("POS Session Log", session_log, "walkin_count") or 0
    new_kiosk = frappe.db.get_value("POS Session Log", session_log, "kiosk_count") or 0
    return {"ok": True, "session_log": session_log, "walkin_count": cint(new_walkin), "kiosk_count": cint(new_kiosk)}


@frappe.whitelist()
def increment_repair_intake_count(pos_profile) -> dict:
    """Increment repair intake counter on active session log."""
    session_log = _get_active_session_log(pos_profile)
    if session_log:
        frappe.db.sql(
            "UPDATE `tabPOS Session Log` SET repair_intake_count = repair_intake_count + 1 WHERE name = %s",
            (session_log,)
        )
    return {"ok": True}


@frappe.whitelist()
def increment_buyback_count(pos_profile) -> dict:
    """Increment buyback assessment counter on active session log."""
    session_log = _get_active_session_log(pos_profile)
    if session_log:
        frappe.db.sql(
            "UPDATE `tabPOS Session Log` SET buyback_count = buyback_count + 1 WHERE name = %s",
            (session_log,)
        )
    return {"ok": True}


@frappe.whitelist()
def get_today_footfall(pos_profile, date=None, from_date=None, to_date=None, salesman=None) -> dict:
    """Return footfall summary derived from POS Kiosk Token records.

    When the POS Profile Extension is configured for a non-Kiosk pos_mode
    (System / Tablet), kiosk-specific counters are forced to zero so that
    counter-only stores never see misleading kiosk widgets.
    """
    from_date, to_date = _dashboard_date_range(date=date, from_date=from_date, to_date=to_date)
    salesman = (salesman or "").strip()
    start_dt = f"{from_date} 00:00:00"
    end_dt = f"{to_date} 23:59:59"

    # Decouple from kiosk: when this profile is not a kiosk, zero out the
    # kiosk counters so the front-end can hide kiosk widgets entirely.
    pos_mode = frappe.db.get_value(
        "POS Profile Extension", {"pos_profile": pos_profile}, "pos_mode"
    ) or "System"
    kiosk_enabled = (pos_mode == "Kiosk")

    token_meta = frappe.get_meta("POS Kiosk Token")
    token_salesman_sql = ""
    token_params = {
        "pos_profile": pos_profile,
        "start_dt": start_dt,
        "end_dt": end_dt,
    }
    if salesman and token_meta.has_field("sales_executive"):
        token_salesman_sql = " AND sales_executive = %(salesman)s"
        token_params["salesman"] = salesman

    # Primary source: POS Kiosk Token records
    source_counts = frappe.db.sql(f"""
        SELECT IFNULL(visit_source, 'Counter') AS visit_source, COUNT(*) AS cnt
        FROM `tabPOS Kiosk Token`
        WHERE pos_profile = %(pos_profile)s
          AND creation BETWEEN %(start_dt)s AND %(end_dt)s
          AND status != 'Cancelled'
          {token_salesman_sql}
        GROUP BY visit_source
    """, token_params, as_dict=True)

    source_map = {r.visit_source: cint(r.cnt) for r in source_counts}
    walkin_count = source_map.get("Counter", 0)
    kiosk_count = source_map.get("Kiosk", 0)
    other_count = sum(v for k, v in source_map.items() if k not in ("Counter", "Kiosk"))

    purpose_counts = frappe.db.sql(f"""
        SELECT IFNULL(visit_purpose, '') AS visit_purpose, COUNT(*) AS cnt
        FROM `tabPOS Kiosk Token`
        WHERE pos_profile = %(pos_profile)s
          AND creation BETWEEN %(start_dt)s AND %(end_dt)s
          AND status != 'Cancelled'
          {token_salesman_sql}
        GROUP BY visit_purpose
    """, token_params, as_dict=True)

    purpose_map = {r.visit_purpose: cint(r.cnt) for r in purpose_counts}
    repair_intake_count = purpose_map.get("Repair", 0)
    buyback_count = purpose_map.get("Buyback", 0)

    invoice_filters = {
        "pos_profile": pos_profile,
        "posting_date": ["between", [from_date, to_date]],
        "docstatus": 1,
        "is_return": 0,
    }
    if salesman and frappe.get_meta("Sales Invoice").has_field("custom_sales_executive"):
        invoice_filters["custom_sales_executive"] = salesman
    invoices_today = frappe.db.count("Sales Invoice", invoice_filters)

    # Status counts
    status_counts = frappe.db.sql(f"""
        SELECT status, COUNT(*) AS cnt
        FROM `tabPOS Kiosk Token`
        WHERE pos_profile = %(pos_profile)s
          AND creation BETWEEN %(start_dt)s AND %(end_dt)s
          {token_salesman_sql}
        GROUP BY status
    """, token_params, as_dict=True)

    status_map = {r.status: cint(r.cnt) for r in status_counts}
    cancelled_count = cint(status_map.get("Cancelled", 0))
    dropped_count = cint(status_map.get("Dropped", 0))

    total_footfall = walkin_count + kiosk_count + other_count
    conversion_pct = round((invoices_today / total_footfall * 100) if total_footfall > 0 else 0, 1)

    if not kiosk_enabled:
        # Mask kiosk-only metrics for counter-only profiles.
        kiosk_count = 0

    return {
        "walkin_count": walkin_count,
        "kiosk_count": kiosk_count,
        "kiosk_enabled": cint(kiosk_enabled),
        "repair_intake_count": repair_intake_count,
        "buyback_count": buyback_count,
        "cancelled_count": cancelled_count,
        "dropped_count": dropped_count,
        "total_footfall": total_footfall,
        "invoices_today": invoices_today,
        "conversion_pct": conversion_pct,
        "from_date": from_date,
        "to_date": to_date,
        "salesman": salesman,
    }


@frappe.whitelist()
def flag_reprint_needed(pos_invoice, reason="Print failed") -> dict:
    """Mark an invoice as needing reprint.

    Frontend calls this when the receipt printer is offline or errors.
    Store managers can see pending reprints in the CH Store Operations workspace.
    """
    if not frappe.db.exists("Sales Invoice", pos_invoice):
        frappe.throw(frappe._("Sales Invoice {0} not found").format(pos_invoice))

    # Append to session log reprint queue if session log linked
    session_log = frappe.db.get_value("POS Session Log", {"pos_profile": frappe.db.get_value("Sales Invoice", pos_invoice, "pos_profile"), "status": ["!=", "Closed"], "docstatus": 1}, "name")

    frappe.db.sql("""
        INSERT INTO `tabPOS Reprint Queue`
          (name, parent, parenttype, parentfield, pos_invoice, reason, requested_at, status)
        VALUES (%(n)s, %(p)s, 'POS Session Log', 'reprint_queue',
                %(inv)s, %(reason)s, NOW(), 'Pending')
    """, {
        "n": frappe.generate_hash(length=10),
        "p": session_log or "",
        "inv": pos_invoice,
        "reason": (reason or "Print failed")[:200],
    })
    return {"status": "queued", "pos_invoice": pos_invoice}


@frappe.whitelist()
def get_pending_reprints(pos_profile, limit=20) -> list:
    """Return pending reprint queue items for a POS profile.

    Used by the store manager workspace shortcut.
    """
    return frappe.db.sql("""
        SELECT rq.name, rq.pos_invoice, rq.reason, rq.requested_at, rq.status
        FROM `tabPOS Reprint Queue` rq
        JOIN `tabPOS Session Log` sl ON sl.name = rq.parent
        WHERE sl.pos_profile = %(pos)s
          AND rq.status = 'Pending'
        ORDER BY rq.requested_at DESC
        LIMIT %(limit)s
    """, {"pos": pos_profile, "limit": int(limit)}, as_dict=True)


@frappe.whitelist()
def mark_reprint_done(reprint_name) -> dict:
    """Mark a reprint queue item as completed."""
    frappe.db.set_value("POS Reprint Queue", reprint_name, "status", "Done",
        update_modified=False)
    return {"status": "done"}


# ═══════════════════════════════════════════════════════════════════════════
# Buyback POS Full-Flow APIs
# ═══════════════════════════════════════════════════════════════════════════

@frappe.whitelist(methods=["GET", "POST"])
def get_pos_buyback_detail(assessment_name) -> dict:
    """Return full buyback detail for POS: assessment + linked order + diagnostics.

    Called on every stage transition so the frontend always has fresh data.
    """
    a = frappe.get_doc("Buyback Assessment", assessment_name)

    # SECURITY: this payload carries KYC (ID numbers/images) + payout bank
    # details. Raw frappe.get_doc does NOT enforce doctype permissions, so gate
    # explicitly on read permission AND the caller's store scope — an
    # authenticated user must not read buybacks belonging to another store.
    a.check_permission("read")
    from ch_pos.api.scope_guard import assert_store_scope
    assert_store_scope(
        store=a.store, warehouse=a.store, company=a.company,
        msg=frappe._("You are not entitled to view this buyback."),
    )

    # Fix status stuck at Draft when already Frappe-submitted
    if a.docstatus == 1 and a.status == "Draft":
        a.db_set("status", "Submitted")
        a.status = "Submitted"

    # Linked Buyback Order (if any)
    order = None
    order_name = frappe.db.get_value(
        "Buyback Order",
        {"buyback_assessment": assessment_name, "docstatus": ["!=", 2]},
        "name",
        order_by="creation desc",
    )
    if order_name:
        o = frappe.get_doc("Buyback Order", order_name)
        order = {
            "name": o.name,
            "status": o.status,
            "customer": o.customer or "",
            "customer_name": o.customer_name or "",
            "mobile_no": o.mobile_no or "",
            "item": o.item or "",
            "item_name": o.item_name or "",
            "brand": o.brand or "",
            "imei_serial": o.imei_serial or "",
            "condition_grade": o.condition_grade or "",
            "final_price": flt(o.final_price),
            "base_price": flt(o.base_price),
            "settlement_type": o.settlement_type or "",
            "customer_payout_mode": o.customer_payout_mode or "",
            "customer_cash_receiver_name": o.customer_cash_receiver_name or "",
            "customer_upi_id": o.customer_upi_id or "",
            "customer_bank_account_holder": o.customer_bank_account_holder or "",
            "customer_bank_account_number": o.customer_bank_account_number or "",
            "customer_bank_ifsc": o.customer_bank_ifsc or "",
            "customer_bank_name": o.customer_bank_name or "",
            "customer_payout_notes": o.customer_payout_notes or "",
            "customer_payout_updated_at": str(o.customer_payout_updated_at) if o.customer_payout_updated_at else "",
            "customer_payout_updated_by": o.customer_payout_updated_by or "",
            "customer_id_type": getattr(o, "customer_id_type", "") or "",
            "customer_id_number": getattr(o, "customer_id_number", "") or "",
            "customer_id_front": getattr(o, "customer_id_front", "") or "",
            "customer_id_back": getattr(o, "customer_id_back", "") or "",
            "customer_photo": getattr(o, "customer_photo", "") or "",
            "kyc_verified": cint(getattr(o, "kyc_verified", 0)),
            "kyc_verified_by": getattr(o, "kyc_verified_by", "") or "",
            "kyc_verified_at": (
                str(getattr(o, "kyc_verified_at", "")) if getattr(o, "kyc_verified_at", None) else ""
            ),
            "customer_approved": cint(o.customer_approved),
            "customer_approved_at": (
                str(getattr(o, "customer_approved_at", "")) if getattr(o, "customer_approved_at", None) else ""
            ),
            "customer_approval_method": getattr(o, "customer_approval_method", "") or "",
            "otp_verified": cint(o.otp_verified),
            "otp_verified_at": (
                str(getattr(o, "otp_verified_at", "")) if getattr(o, "otp_verified_at", None) else ""
            ),
            "imei_validation_status": o.imei_validation_status or "Pending",
            "imei_validation_screenshot": o.imei_validation_screenshot or "",
            "imei_validation_checked_by": o.imei_validation_checked_by or "",
            "imei_validation_checked_at": (
                str(o.imei_validation_checked_at) if o.imei_validation_checked_at else ""
            ),
            "imei_validation_remarks": o.imei_validation_remarks or "",
            "ownership_proof_type": o.ownership_proof_type or "",
            "ownership_proof_document": o.ownership_proof_document or "",
            "ownership_proof_remarks": o.ownership_proof_remarks or "",
            "account_lock_cleared": cint(o.account_lock_cleared),
            "account_lock_check_notes": o.account_lock_check_notes or "",
            "require_ownership_proof_above": flt(
                frappe.db.get_single_value("Buyback Settings", "require_ownership_proof_above")
            ),
            "payment_status": o.payment_status or "",
            "requires_approval": cint(o.requires_approval),
            "approved_by": o.approved_by or "",
            "approval_date": str(o.approval_date) if o.approval_date else "",
            "approval_remarks": o.approval_remarks or "",
            # SECURITY: the raw approval_token (and the /buyback-approval URL
            # that embeds it) are deliberately NOT returned here — a detail
            # payload must not leak a bearer credential that lets anyone
            # approve the order. Staff share the link via the dedicated
            # pos_send_approval_link endpoint instead.
            "has_approval_link": bool(o.approval_token),
            # Phase B — market-standard compliance state
            "indemnity_signed": cint(getattr(o, "indemnity_signed", 0)),
            "indemnity_signed_at": (
                str(getattr(o, "indemnity_signed_at", ""))
                if getattr(o, "indemnity_signed_at", None) else ""
            ),
            "indemnity_signature_type": getattr(o, "indemnity_signature_type", "") or "",
            "indemnity_signed_by_name": getattr(o, "indemnity_signed_by_name", "") or "",
            "indemnity_captured_by": getattr(o, "indemnity_captured_by", "") or "",
            "indemnity_attachment": getattr(o, "indemnity_attachment", "") or "",
            "latest_pickup_appointment": getattr(o, "latest_pickup_appointment", "") or "",
            "pickup_attempts_count": cint(getattr(o, "pickup_attempts_count", 0)),
            "pickup_completed_at": (
                str(getattr(o, "pickup_completed_at", ""))
                if getattr(o, "pickup_completed_at", None) else ""
            ),
            "data_wipe_certificate": getattr(o, "data_wipe_certificate", "") or "",
            "data_wipe_completed_at": (
                str(getattr(o, "data_wipe_completed_at", ""))
                if getattr(o, "data_wipe_completed_at", None) else ""
            ),
        }

    # Diagnostic test results (from mobile app or manual)
    diagnostics = []
    for d in (a.diagnostic_tests or []):
        diagnostics.append({
            "test_name": d.get("test_name") or d.get("test_code") or "",
            "result": d.get("result") or "",
            "details": d.get("details") or "",
        })

    # Assessment question responses (customer self-assessment)
    assessment_responses = []
    for r in (a.responses or []):
        assessment_responses.append({
            "question": r.get("question") or "",
            "question_code": r.get("question_code") or "",
            "question_text": r.get("question_text") or "",
            "answer_value": r.get("answer_value") or "",
            "answer_label": r.get("answer_label") or "",
            "price_impact_percent": flt(r.get("price_impact_percent")),
        })

    # Inspection data (if inspection exists)
    inspection = None
    if a.buyback_inspection:
        try:
            ins = frappe.get_doc("Buyback Inspection", a.buyback_inspection)
            # Grade options for selector
            grades = frappe.get_all(
                "Grade Master", fields=["name", "grade_name"],
                order_by="name asc",
            )
            # Inspection responses with side-by-side data
            ins_responses = []
            for ir in (ins.inspection_responses or []):
                # Fetch answer options for inspector dropdown
                options = []
                if ir.question:
                    options = frappe.get_all(
                        "Buyback Question Option",
                        filters={"parent": ir.question},
                        fields=["option_value", "option_label", "price_impact_percent"],
                        order_by="idx asc",
                    )
                ins_responses.append({
                    "question": ir.get("question") or "",
                    "question_code": ir.get("question_code") or "",
                    "question_text": ir.get("question_text") or "",
                    "assessment_answer": ir.get("assessment_answer") or "",
                    "assessment_answer_label": ir.get("assessment_answer_label") or "",
                    "assessment_impact": flt(ir.get("assessment_impact")),
                    "inspector_answer": ir.get("inspector_answer") or "",
                    "inspector_answer_label": ir.get("inspector_answer_label") or "",
                    "inspector_impact": flt(ir.get("inspector_impact")),
                    "options": [
                        {"value": o.option_value, "label": o.option_label,
                         "impact": flt(o.price_impact_percent)}
                        for o in options
                    ],
                })
            # Inspection diagnostics (automated tests)
            ins_diagnostics = []
            for id_ in (ins.inspection_diagnostics or []):
                ins_diagnostics.append({
                    "test_name": id_.get("test_name") or "",
                    "test_code": id_.get("test_code") or "",
                    "assessment_result": id_.get("assessment_result") or "",
                    "assessment_depreciation": flt(id_.get("assessment_depreciation")),
                    "inspector_result": id_.get("inspector_result") or "",
                    "inspector_depreciation": flt(id_.get("inspector_depreciation")),
                })
            inspection = {
                "name": ins.name,
                "status": ins.status or "",
                "inspector": ins.inspector or "",
                "pre_inspection_grade": ins.pre_inspection_grade or "",
                "post_inspection_grade": ins.post_inspection_grade or "",
                "condition_grade": ins.condition_grade or "",
                "estimated_price": flt(ins.estimated_price),
                "quoted_price": flt(ins.quoted_price),
                "revised_price": flt(ins.revised_price),
                "price_override_reason": ins.price_override_reason or "",
                "remarks": ins.remarks or "",
                "responses": ins_responses,
                "diagnostics": ins_diagnostics,
                "grades": [
                    {"name": g.name, "label": g.grade_name or g.name}
                    for g in grades
                ],
            }
        except frappe.DoesNotExistError:
            pass

    return {
        "name": a.name,
        "source": a.source or "",
        "status": a.status or "",
        "customer": a.customer or "",
        "customer_name": a.customer_name or "",
        "mobile_no": a.mobile_no or "",
        "item": a.item or "",
        "item_name": a.item_name or "",
        "brand": a.brand or "",
        "imei_serial": a.imei_serial or "",
        "imei_validation_status": a.imei_validation_status or "Pending",
        "imei_validation_screenshot": a.imei_validation_screenshot or "",
        "imei_validation_checked_by": a.imei_validation_checked_by or "",
        "imei_validation_checked_at": (
            str(a.imei_validation_checked_at) if a.imei_validation_checked_at else ""
        ),
        "imei_validation_remarks": a.imei_validation_remarks or "",
        "device_age_months": a.device_age_months or "",
        "warranty_status": a.warranty_status or "",
        "estimated_grade": a.estimated_grade or "",
        "estimated_price": flt(a.estimated_price),
        "quoted_price": flt(a.quoted_price),
        "remarks": a.remarks or "",
        "diagnostics": diagnostics,
        "assessment_responses": assessment_responses,
        "inspection": inspection,
        "order": order,
        "buyback_inspection": a.buyback_inspection or "",
    }


@frappe.whitelist()
def pos_start_buyback_order(assessment_name, pos_profile, final_price=None, inspector_notes=None,
                            account_lock_cleared=0, account_lock_check_notes="") -> dict:
    """Create a Buyback Order from a Buyback Assessment in POS.

    Idempotent — returns existing order if one already exists for this assessment.

    `account_lock_cleared`/`account_lock_check_notes` matter for the WALK-IN
    path (no Buyback Inspection record — see `_html_assess`'s walk-in form in
    buyback_workspace.js): that's the only POS touchpoint where staff can
    confirm FRP/iCloud lock clearance before BuybackOrder's gate requires it.
    When an Inspection IS linked, its own (already-required) value is carried
    forward instead and these params are ignored.
    """
    frappe.has_permission("Buyback Order", "create", throw=True)
    # Return existing order if already created
    existing = frappe.db.get_value(
        "Buyback Order",
        {"buyback_assessment": assessment_name, "docstatus": ["!=", 2]},
        "name",
    )
    if existing:
        if final_price:
            frappe.db.set_value("Buyback Order", existing, "final_price", flt(final_price))
        return {"order_name": existing, "created": False}

    assessment = frappe.get_doc("Buyback Assessment", assessment_name)
    warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse") or ""
    company = frappe.db.get_value("POS Profile", pos_profile, "company") or frappe.defaults.get_global_default("company")

    price = flt(final_price) or flt(assessment.quoted_price) or flt(assessment.estimated_price)

    order = frappe.new_doc("Buyback Order")
    order.buyback_assessment = assessment_name
    order.buyback_inspection = assessment.buyback_inspection or ""
    order.customer = assessment.customer or ""
    order.customer_name = assessment.customer_name or ""
    order.mobile_no = assessment.mobile_no or ""
    order.store = warehouse
    order.company = company
    order.item = assessment.item or ""
    order.item_name = assessment.item_name or ""
    order.brand = assessment.brand or ""
    order.imei_serial = assessment.imei_serial or ""
    order.warranty_status = assessment.warranty_status or ""
    order.condition_grade = assessment.estimated_grade or ""
    order.base_price = flt(assessment.estimated_price)
    order.final_price = price
    order.original_quoted_price = flt(assessment.quoted_price) or flt(assessment.estimated_price)
    if inspector_notes:
        order.remarks = str(inspector_notes)[:500]

    # Carry forward a clean Sanchar Saathi check already done at intake so
    # staff don't have to repeat it before customer approval/KYC/OTP.
    if assessment.imei_validation_status == "Verified Clean":
        order.imei_validation_status = assessment.imei_validation_status
        order.imei_validation_screenshot = assessment.imei_validation_screenshot
        order.imei_validation_checked_by = assessment.imei_validation_checked_by
        order.imei_validation_checked_at = assessment.imei_validation_checked_at
        order.imei_validation_remarks = assessment.imei_validation_remarks

    # Lock clearance: prefer a linked (completed) Inspection's value — it's
    # already mandatory there — otherwise fall back to what walk-in staff
    # confirmed directly on this call.
    inspection_lock = None
    if assessment.buyback_inspection:
        inspection_lock = frappe.db.get_value(
            "Buyback Inspection", assessment.buyback_inspection,
            ["account_lock_cleared", "account_lock_check_notes"], as_dict=True,
        )
    if inspection_lock and inspection_lock.account_lock_cleared:
        order.account_lock_cleared = inspection_lock.account_lock_cleared
        order.account_lock_check_notes = inspection_lock.account_lock_check_notes
    elif cint(account_lock_cleared):
        order.account_lock_cleared = 1
        order.account_lock_check_notes = account_lock_check_notes or ""

    order.flags.ignore_permissions = True
    try:
        order.insert()
    except frappe.UniqueValidationError:
        # Race condition: another request created an order for this assessment
        existing = frappe.db.get_value(
            "Buyback Order",
            {"buyback_assessment": assessment_name, "docstatus": ["!=", 2]},
            "name",
        )
        if existing:
            if final_price:
                frappe.db.set_value("Buyback Order", existing, "final_price", flt(final_price))
            return {"order_name": existing, "created": False}
        raise
    order.submit()

    return {"order_name": order.name, "created": True}


@frappe.whitelist()
def pos_update_buyback_price(order_name, final_price, inspector_notes=None) -> dict:
    """Update the final buyback price on an existing Buyback Order."""
    frappe.has_permission("Buyback Order", "write", throw=True)

    doc = frappe.get_doc("Buyback Order", order_name)
    if doc.docstatus == 2:
        frappe.throw(frappe._("Cannot update a cancelled order."))

    doc.final_price = flt(final_price)
    if inspector_notes:
        doc.remarks = (str(doc.remarks or "") + "\n" + str(inspector_notes))[:1000]

    doc.flags.ignore_permissions = True
    doc.save()

    return {
        "order_name": doc.name,
        "final_price": doc.final_price,
        "status": doc.status,
    }


@frappe.whitelist()
def pos_send_customer_otp(order_name) -> dict:
    """Generate and send an OTP to the customer's mobile for buyback price approval."""
    from buyback.api import _as_system_user

    doc = frappe.get_doc("Buyback Order", order_name)
    mobile_no = doc.mobile_no
    if not mobile_no:
        frappe.throw(frappe._("No mobile number on this Buyback Order."))

    # Delegate to BuybackOrder.send_otp() so the purpose, status transition, and
    # delivery logic stay in one place. Using "Buyback Customer Approval" here
    # previously caused a mismatch with BuybackOrder.verify_otp() which looks up
    # purpose="Buyback Confirmation".
    doc.flags.ignore_permissions = True
    with _as_system_user():
        doc.send_otp()

    return {
        "sent": True,
        "masked_mobile": mobile_no[:2] + "****" + mobile_no[-2:],
        "expires_in": 300,
    }


@frappe.whitelist()
def pos_verify_otp_direct(order_name: str, otp_code: str) -> dict:
    """Verify OTP entered by cashier on behalf of customer.

    Called from the POS "Awaiting OTP" stage when cashier types the OTP
    the customer received on their mobile.
    """
    from buyback.api import _as_system_user

    doc = frappe.get_doc("Buyback Order", order_name)
    with _as_system_user():
        result = doc.verify_otp(otp_code=otp_code)

    if not result.get("valid"):
        frappe.throw(result.get("message") or frappe._("Invalid or expired OTP."))

    return {"verified": True}


@frappe.whitelist()
def pos_submit_assessment_imei_validation(assessment_name: str, status: str, screenshot: str | None = None,
                                           remarks: str | None = None) -> dict:
    """POS-side wrapper for the Sanchar Saathi IMEI check at intake (Buyback Assessment).

    Optional but recommended before inspection starts — checking the device
    isn't reported lost/stolen BEFORE an inspector spends time grading it.
    `create_inspection`/`pos_create_inspection` hard-block on this.
    """
    doc = frappe.get_doc("Buyback Assessment", assessment_name)
    return doc.submit_imei_validation(status=status, screenshot=screenshot, remarks=remarks)


@frappe.whitelist()
def pos_submit_imei_validation(order_name: str, status: str, screenshot: str | None = None,
                                remarks: str | None = None) -> dict:
    """POS-side wrapper for the Sanchar Saathi IMEI validation step.

    Store staff manually check the device IMEI on ceir.sancharsaathi.gov.in
    (no public API exists) and upload a screenshot of the result via this
    endpoint. Until status="Verified Clean" is recorded, the order is
    blocked from customer approval, KYC, and OTP — the POS frontend should
    gate those screens based on `imei_validation_status` from
    `get_pos_buyback_detail()`.
    """
    doc = frappe.get_doc("Buyback Order", order_name)
    return doc.submit_imei_validation(status=status, screenshot=screenshot, remarks=remarks)


@frappe.whitelist()
def bypass_otp_instore(name: str, remarks: str | None = None) -> dict:
    """Skip OTP for in-store customer approval.

    Thin wrapper so the POS can call the BBO method via xcall
    (doctype method calls from POS require the standard frappe.call
    pattern, but xcall to a whitelisted function is simpler from JS).
    """
    doc = frappe.get_doc("Buyback Order", name)
    return doc.bypass_otp_instore(remarks=remarks)


@frappe.whitelist()
@rate_limit(limit=20, seconds=300, ip_based=True)
def pos_approve_customer_buyback(order_name, method="In-Store Signature", otp_code=None,
                                 kyc_id_type=None, kyc_id_number=None,
                                 customer_id_front=None, customer_id_back=None,
                                 customer_photo=None,
                                 settlement_type=None, payout_mode=None,
                                 upi_id=None, bank_account_holder=None,
                                 bank_account_number=None, bank_ifsc=None,
                                 bank_name=None,
                                 ownership_proof_type=None, ownership_proof_document=None,
                                 ownership_proof_remarks=None) -> dict:
    """Record customer approval of the final buyback price.

    method: "In-Store Signature" | "OTP" | "Token Link"
    If method == "OTP", otp_code is verified first.
    kyc_id_type / kyc_id_number: optional KYC data saved on the order.

    SECURITY: staff-only, store-scoped. This endpoint writes KYC + payout bank
    details onto the order, so it must never be guest-reachable — an
    unauthenticated caller could otherwise approve any order and inject a
    fraudulent payout account by passing an arbitrary ``order_name``. The
    customer-facing consent path is the separate, token-bound
    ``buyback.api.customer_approve_via_token`` / ``verify_otp`` flow.
    """
    doc = frappe.get_doc("Buyback Order", order_name)

    # Bind the operator to this order's store — a POS user may only approve
    # buybacks at a store/warehouse within their CH User Scope.
    from ch_pos.api.scope_guard import assert_store_scope
    assert_store_scope(
        store=doc.store, warehouse=doc.store, company=doc.company,
        msg=frappe._("You are not entitled to approve buybacks at this store."),
    )

    doc.flags.ignore_permissions = True

    def _normalize_kyc_type(id_type):
        raw = (id_type or "").strip()
        if not raw:
            return ""
        alias = {
            "aadhaar": "Aadhar Card",
            "aadhar": "Aadhar Card",
            "aadhaar card": "Aadhar Card",
            "aadhar card": "Aadhar Card",
            "pan": "PAN Card",
            "pan card": "PAN Card",
            "driving licence": "Driving License",
            "driving license": "Driving License",
            "voter id": "Voter ID",
            "passport": "Passport",
        }
        normalized = alias.get(raw.casefold(), raw)
        allowed = {"Aadhar Card", "PAN Card", "Driving License", "Voter ID", "Passport"}
        if normalized not in allowed:
            frappe.throw(
                frappe._("Invalid ID Proof Type. Allowed values: Aadhar Card, PAN Card, Driving License, Voter ID, Passport")
            )
        return normalized

    def _get_safe_actor() -> str:
        actor = (frappe.session.user or "").strip()
        if not actor or actor in {"Guest", "None"}:
            actor = "Administrator"
        if not frappe.db.exists("User", actor):
            actor = "Administrator"
        return actor

    safe_actor = _get_safe_actor()
    # Keep the raw caller-supplied value for OTP routing BEFORE normalization,
    # because _normalize_customer_approval_method("OTP") → "App Confirmation"
    # which would make the `if method == "OTP"` branch unreachable.
    raw_method = (method or "").strip()
    method = _normalize_customer_approval_method(method)
    is_submitted = cint(doc.docstatus) == 1
    update_after_submit = {}

    if raw_method == "OTP":
        if not otp_code:
            frappe.throw(frappe._("OTP code is required for OTP verification."))
        result = doc.verify_otp(str(otp_code))
        if not result.get("valid"):
            frappe.throw(frappe._(result.get("message", "OTP verification failed.")))
        # verify_otp() is the customer-approval event in OTP mode. Avoid
        # calling customer_approve() again because status becomes OTP Verified.
        doc.reload()

    if kyc_id_type:
        kyc_id_type = _normalize_kyc_type(kyc_id_type)
        if is_submitted:
            update_after_submit["customer_id_type"] = kyc_id_type
        else:
            doc.customer_id_type = kyc_id_type
    if kyc_id_number:
        if is_submitted:
            update_after_submit["customer_id_number"] = kyc_id_number
        else:
            doc.customer_id_number = kyc_id_number
    if customer_id_front:
        if is_submitted:
            update_after_submit["customer_id_front"] = customer_id_front
        else:
            doc.customer_id_front = customer_id_front
    if customer_id_back:
        if is_submitted:
            update_after_submit["customer_id_back"] = customer_id_back
        else:
            doc.customer_id_back = customer_id_back
    if customer_photo:
        if is_submitted:
            update_after_submit["customer_photo"] = customer_photo
        else:
            doc.customer_photo = customer_photo

    if settlement_type:
        if is_submitted:
            update_after_submit["settlement_type"] = settlement_type
        else:
            doc.settlement_type = settlement_type
    if payout_mode:
        if is_submitted:
            update_after_submit["customer_payout_mode"] = payout_mode
        else:
            doc.customer_payout_mode = payout_mode
    if upi_id:
        if is_submitted:
            update_after_submit["customer_upi_id"] = upi_id
        else:
            doc.customer_upi_id = upi_id
    if bank_account_holder:
        if is_submitted:
            update_after_submit["customer_bank_account_holder"] = bank_account_holder
        else:
            doc.customer_bank_account_holder = bank_account_holder
    if bank_account_number:
        if is_submitted:
            update_after_submit["customer_bank_account_number"] = bank_account_number
        else:
            doc.customer_bank_account_number = bank_account_number
    if bank_ifsc:
        if is_submitted:
            update_after_submit["customer_bank_ifsc"] = bank_ifsc
        else:
            doc.customer_bank_ifsc = bank_ifsc
    if bank_name:
        if is_submitted:
            update_after_submit["customer_bank_name"] = bank_name
        else:
            doc.customer_bank_name = bank_name
    if payout_mode:
        if is_submitted:
            update_after_submit["customer_payout_updated_at"] = frappe.utils.now_datetime()
            update_after_submit["customer_payout_updated_by"] = safe_actor
        else:
            doc.customer_payout_updated_at = frappe.utils.now_datetime()
            doc.customer_payout_updated_by = safe_actor

    if kyc_id_type and kyc_id_number:
        if is_submitted:
            update_after_submit["kyc_verified"] = 1
            update_after_submit["kyc_verified_by"] = safe_actor
            update_after_submit["kyc_verified_at"] = frappe.utils.now_datetime()
        else:
            doc.kyc_verified = 1
            doc.kyc_verified_by = safe_actor
            doc.kyc_verified_at = frappe.utils.now_datetime()

    if ownership_proof_type:
        if is_submitted:
            update_after_submit["ownership_proof_type"] = ownership_proof_type
        else:
            doc.ownership_proof_type = ownership_proof_type
    if ownership_proof_document:
        if is_submitted:
            update_after_submit["ownership_proof_document"] = ownership_proof_document
        else:
            doc.ownership_proof_document = ownership_proof_document
    if ownership_proof_remarks:
        if is_submitted:
            update_after_submit["ownership_proof_remarks"] = ownership_proof_remarks
        else:
            doc.ownership_proof_remarks = ownership_proof_remarks

    if is_submitted and update_after_submit:
        doc.db_set(update_after_submit, update_modified=True)
        doc.reload()

    doc.flags.ignore_permissions = True
    if raw_method != "OTP":
        if doc.status in ("Approved", "Awaiting Customer Approval"):
            doc.customer_approve(method=method)
        elif cint(doc.customer_approved):
            # Idempotent retry: order is already customer-approved in a prior call.
            pass
        else:
            frappe.throw(
                frappe._("Customer approval is only applicable in Approved or Awaiting Customer Approval status."),
                exc=frappe.ValidationError,
            )

    return {
        "order_name": doc.name,
        "status": doc.status,
        "customer_approved": cint(doc.customer_approved) or 1,
    }


@frappe.whitelist()
def pos_send_approval_link(order_name) -> dict:
    """Send a customer-facing approval link via WhatsApp + Email.

    Transitions the order to "Awaiting Customer Approval" and returns the
    masked mobile number so the UI can confirm which number was contacted.
    """
    doc = frappe.get_doc("Buyback Order", order_name)

    if not doc.mobile_no:
        frappe.throw(frappe._("No mobile number on this Buyback Order."))
    if not doc.approval_token:
        frappe.throw(frappe._("Approval token missing — please re-save the order."))

    approval_url = f"{frappe.utils.get_url()}/buyback-approval?token={doc.approval_token}"

    item_label = doc.item_name or doc.item or "your device"
    price_fmt = f"₹{flt(doc.final_price):,.0f}"
    customer_name = doc.customer_name or "Customer"

    # ── Send WhatsApp (via this order's company account) ──────────
    try:
        from ch_item_master.ch_core.whatsapp import get_whatsapp_settings, send_template_message

        settings = get_whatsapp_settings(doc.company)
        if settings and not settings.enabled:
            settings = None

        template_name = getattr(settings, "buyback_customer_approval", "") if settings else ""
        if template_name:
            send_template_message(
                phone=doc.mobile_no,
                template_name=template_name,
                body_values={
                    "1": customer_name,
                    "2": item_label,
                    "3": price_fmt,
                    "4": approval_url,
                },
                customer_name=customer_name,
                ref_doctype="Buyback Order",
                ref_name=doc.name,
                company=doc.company,
            )
            frappe.logger().info(f"[pos_send_approval_link] WhatsApp sent to {doc.mobile_no}")
        else:
            frappe.logger().warning(
                f"[pos_send_approval_link] No buyback_customer_approval template configured, skipping WhatsApp"
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Buyback approval WhatsApp failed for {doc.name}")

    # ── Send Email ───────────────────────────────────────────────
    try:
        customer_email = None
        if doc.customer:
            customer_email = frappe.db.get_value("Customer", doc.customer, "email_id")
        if not customer_email and doc.mobile_no:
            customer_email = frappe.db.get_value(
                "Customer", {"mobile_no": doc.mobile_no}, "email_id"
            )

        if customer_email:
                        subject = f"Congruence Holdings | GoGizmo Buyback Approval | {doc.name}"
                        html = f"""
                        <div style="font-family:Segoe UI,Arial,sans-serif;max-width:620px;margin:auto;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden">
                            <div style="background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600">Congruence Holdings - GoGizmo Buyback</div>
                            <div style="padding:16px">
                                <h2 style="color:#1a1a2e;margin-top:0">Buyback Offer for Your Approval</h2>
                                <p>Hi {frappe.utils.escape_html(customer_name)},</p>
                                <p>We have evaluated your <strong>{frappe.utils.escape_html(item_label)}</strong>
                                   and are offering <strong>{price_fmt}</strong>.</p>
                                <p>Please review and approve the offer by clicking the button below:</p>
                                <p style="text-align:center;margin:24px 0">
                                    <a href="{frappe.utils.escape_html(approval_url)}"
                                       style="background:#28a745;color:#fff;padding:12px 32px;
                                       text-decoration:none;border-radius:6px;font-size:16px;
                                       display:inline-block">
                                        Review &amp; Approve
                                    </a>
                                </p>
                                <p style="color:#6b7280;font-size:13px">
                                    Or copy this link: {frappe.utils.escape_html(approval_url)}
                                </p>
                                <p style="color:#6b7280;font-size:12px">
                                    Order: {doc.name} | This link is unique to your transaction.
                                </p>
                            </div>
                        </div>
                        """
                        frappe.sendmail(
                            recipients=[customer_email],
                            subject=subject,
                            message=html,
                        )
                        frappe.logger().info(f"[pos_send_approval_link] Email sent to {customer_email}")
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Buyback approval email failed for {doc.name}")

    # Advance order status so the UI reflects "waiting for customer"
    if doc.status == "Approved":
        doc.db_set("status", "Awaiting Customer Approval", notify=True)

    masked = doc.mobile_no[:2] + "****" + doc.mobile_no[-2:]
    return {
        "sent": True,
        "mobile_masked": masked,
        "approval_url": approval_url,
    }


@frappe.whitelist()
def pos_settle_buyback_cashback(order_name, payment_method="Cash") -> dict:
    """Mark a buyback order as settled via direct cashback to customer.

    Records a payment entry on the order, marks it Paid, then auto-closes.
    Idempotent — if already Paid/Closed, returns current state.
    """
    frappe.has_permission("Buyback Order", "write", throw=True)
    doc = frappe.get_doc("Buyback Order", order_name)
    payout_mode = (payment_method or "Cash").strip()
    if payout_mode not in ("Cash", "UPI", "Bank Transfer"):
        frappe.throw(frappe._("Invalid buyback payout method: {0}").format(payout_mode))
    payment_method = _resolve_buyback_payment_mode(payout_mode)

    # Idempotency: already settled
    if doc.status in ("Paid", "Closed"):
        return {
            "order_name": doc.name,
            "status": doc.status,
            "final_price": flt(doc.final_price),
            "payment_method": payment_method,
            "payout_mode": payout_mode,
        }

    if not doc.customer_approved:
        # OTP verification by the customer on their registered mobile is itself a
        # valid form of approval. Treat OTP-verified orders as approved (and
        # self-heal the flag for legacy rows where verify_otp didn't set it).
        if getattr(doc, "otp_verified", 0):
            from frappe.utils import now_datetime
            doc.customer_approved = 1
            if not doc.customer_approved_at:
                doc.customer_approved_at = doc.otp_verified_at or now_datetime()
        else:
            frappe.throw(frappe._("Customer must approve the final price before cashback settlement."))

    from frappe.utils import now_datetime
    doc.settlement_type = "Buyback"
    doc.customer_payout_mode = payout_mode
    doc.customer_payout_updated_at = now_datetime()
    doc.customer_payout_updated_by = frappe.session.user or "Administrator"

    # Avoid duplicate payments. The order may already have payment rows from
    # another flow (customer-portal payout capture, manual entry, prior call
    # of this same API on a doc that was reloaded). Add only the delta needed
    # to reach final_price; if already fully paid, skip the append entirely.
    final_price = flt(doc.final_price)
    already_paid = sum(flt(p.amount) for p in (doc.payments or []))
    remaining = flt(final_price - already_paid)

    txn_ref = f"POS-Cashback-{doc.name}"
    already_exists = any(p.transaction_reference == txn_ref for p in (doc.payments or []))
    if not already_exists and remaining > 0.01:
        doc.append("payments", {
            "payment_method": payment_method,
            "amount": remaining,
            "payment_date": now_datetime(),
            "transaction_reference": txn_ref,
        })
    doc.flags.ignore_permissions = True
    doc.save()

    # mark_paid validates payment_status == "Paid" first
    doc._calculate_payment_totals()
    if doc.payment_status == "Paid":
        doc.mark_paid()

    # Auto-close after successful cashback — triggers lifecycle update
    if doc.status == "Paid":
        try:
            doc.status = "Closed"
            doc.flags.ignore_permissions = True
            doc.save()
        except Exception:
            frappe.log_error(title=f"Buyback auto-close failed for {doc.name}")

    try:
        from ch_pos.audit import log_business_event
        log_business_event(
            event_type="Buyback Cashback",
            ref_doctype="Buyback Order", ref_name=order_name,
            before=str(doc.customer_name or doc.mobile_no),
            after=f"₹{flt(doc.final_price):,.0f} via {payment_method}",
            store=doc.store, company=doc.company,
        )
    except Exception:
        pass

    return {
        "order_name": doc.name,
        "status": doc.status,
        "final_price": flt(doc.final_price),
        "payment_method": payment_method,
        "payout_mode": payout_mode,
    }


def _resolve_buyback_payment_mode(payout_mode: str) -> str:
    """Map POS payout choices to an actual ERPNext Mode of Payment.

    Buyback Order stores customer-facing payout choices as Cash / UPI /
    Bank Transfer, but the payment child table links to Mode of Payment
    master data. Do not require every site to create a Mode of Payment
    named exactly "UPI"; use sensible configured fallbacks instead.
    """
    payout_mode = (payout_mode or "Cash").strip()
    exact = payout_mode if frappe.db.exists("Mode of Payment", payout_mode) else ""
    if exact:
        return exact

    candidates = {
        "Cash": ["Cash"],
        "UPI": ["UPI", "Wallet", "Wire Transfer", "Bank Draft", "Cheque"],
        "Bank Transfer": ["Bank Transfer", "Wire Transfer", "Bank Draft", "Cheque"],
    }.get(payout_mode, [])

    for candidate in candidates:
        if frappe.db.exists("Mode of Payment", candidate):
            return candidate

    wanted_type = "Cash" if payout_mode == "Cash" else "Bank"
    fallback = frappe.db.get_value("Mode of Payment", {"type": wanted_type}, "name", order_by="name asc")
    if fallback:
        return fallback

    frappe.throw(
        frappe._("No Mode of Payment is configured for buyback payout mode {0}.").format(
            frappe.bold(payout_mode)
        )
    )


@frappe.whitelist()
def pos_submit_assessment(assessment_name) -> dict:
    """Submit a Draft Buyback Assessment from POS."""
    doc = frappe.get_doc("Buyback Assessment", assessment_name)
    doc.check_permission("write")

    # If already Frappe-submitted (docstatus=1) but status stuck at Draft,
    # just update the status field directly.
    if doc.docstatus == 1 and doc.status == "Draft":
        doc.db_set("status", "Submitted")
        return {"name": doc.name, "status": "Submitted"}

    if doc.docstatus == 1 and doc.status != "Draft":
        return {"name": doc.name, "status": doc.status}

    doc.submit_assessment()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def pos_create_inspection(assessment_name) -> dict:
    """Create or retrieve a Buyback Inspection from an assessment.

    Idempotent — if inspection already exists, returns its current data.
    Returns pre-fill data for the inline POS inspection panel.
    """
    # Check if inspection already exists on the assessment
    existing_inspection = frappe.db.get_value(
        "Buyback Assessment", assessment_name, "buyback_inspection"
    )
    if existing_inspection:
        ins = frappe.get_doc("Buyback Inspection", existing_inspection)
    else:
        # Auto-submit if still Draft (POS quick-assessments skip manual submit)
        ba = frappe.get_doc("Buyback Assessment", assessment_name)
        if ba.docstatus == 0:
            ba.submit()
            frappe.db.commit()
        from buyback.api import create_inspection_from_assessment
        # Pick a default checklist template (first active one) so the form opens pre-filled
        default_template = frappe.db.get_value(
            "Buyback Checklist Template", {"disabled": 0}, "name", order_by="creation asc"
        )
        result = create_inspection_from_assessment(assessment_name, checklist_template=default_template)
        ins = frappe.get_doc("Buyback Inspection", result["name"])

    # Also get grade options for the inline form selector
    grades = frappe.get_all("Grade Master", fields=["name", "grade_name"], order_by="name asc")

    # Build rich response data with answer options for inspector dropdowns
    ins_responses = []
    for ir in (ins.inspection_responses or []):
        options = []
        if ir.question:
            options = frappe.get_all(
                "Buyback Question Option",
                filters={"parent": ir.question},
                fields=["option_value", "option_label", "price_impact_percent"],
                order_by="idx asc",
            )
        ins_responses.append({
            "question": ir.get("question") or "",
            "question_code": ir.get("question_code") or "",
            "question_text": ir.get("question_text") or "",
            "assessment_answer": ir.get("assessment_answer") or "",
            "assessment_answer_label": ir.get("assessment_answer_label") or "",
            "assessment_impact": flt(ir.get("assessment_impact")),
            "inspector_answer": ir.get("inspector_answer") or "",
            "inspector_answer_label": ir.get("inspector_answer_label") or "",
            "inspector_impact": flt(ir.get("inspector_impact")),
            "options": [
                {"value": o.option_value, "label": o.option_label,
                 "impact": flt(o.price_impact_percent)}
                for o in options
            ],
        })

    # Inspection diagnostics (automated test results)
    ins_diagnostics = []
    for id_ in (ins.inspection_diagnostics or []):
        ins_diagnostics.append({
            "test_name": id_.get("test_name") or "",
            "test_code": id_.get("test_code") or "",
            "assessment_result": id_.get("assessment_result") or "",
            "assessment_depreciation": flt(id_.get("assessment_depreciation")),
            "inspector_result": id_.get("inspector_result") or "",
            "inspector_depreciation": flt(id_.get("inspector_depreciation")),
        })

    return {
        "name": ins.name,
        "status": ins.status or "",
        "customer": ins.customer or "",
        "customer_name": ins.customer_name or "",
        "mobile_no": ins.mobile_no or "",
        "store": ins.store or "",
        "item": ins.item or "",
        "item_name": ins.item_name or "",
        "imei_serial": ins.imei_serial or "",
        "quoted_price": flt(ins.quoted_price),
        "revised_price": flt(ins.revised_price),
        "condition_grade": ins.condition_grade or "",
        "pre_inspection_grade": ins.pre_inspection_grade or "",
        "post_inspection_grade": ins.post_inspection_grade or "",
        "price_override_reason": ins.price_override_reason or "",
        "remarks": ins.remarks or "",
        "account_lock_cleared": cint(ins.account_lock_cleared),
        "account_lock_check_notes": ins.account_lock_check_notes or "",
        "inspector": ins.inspector or "",
        "responses": ins_responses,
        "diagnostics": ins_diagnostics,
        "grades": [{"name": g.name, "label": g.grade_name or g.name} for g in grades],
    }


@frappe.whitelist()
def pos_complete_inspection(inspection_name, condition_grade, final_price,
                            price_override_reason="", remarks="",
                            account_lock_cleared=0, account_lock_check_notes="") -> dict:
    """Complete the inline POS inspection and create a Buyback Order.

    Idempotent — if an order already exists for the assessment it is returned
    without creating a second one.
    """
    from buyback.api import complete_inspection

    # Auto-start inspection if still in Draft (POS inline flow)
    ins_status = frappe.db.get_value("Buyback Inspection", inspection_name, "status")
    if ins_status == "Draft":
        from buyback.api import start_inspection
        start_inspection(inspection_name)

    # Persist the FRP/iCloud lock-clearance checkbox before completing —
    # BuybackInspection.complete_inspection() hard-blocks without it.
    frappe.db.set_value("Buyback Inspection", inspection_name, {
        "account_lock_cleared": cint(account_lock_cleared),
        "account_lock_check_notes": account_lock_check_notes or "",
    })

    result = complete_inspection(
        inspection_name=inspection_name,
        condition_grade=condition_grade,
        revised_price=flt(final_price),
        price_override_reason=price_override_reason or None,
    )

    # Update remarks on the inspection if provided
    if remarks:
        frappe.db.set_value("Buyback Inspection", inspection_name, "remarks", str(remarks)[:1000])

    # Get the linked assessment
    assessment_name = frappe.db.get_value("Buyback Inspection", inspection_name, "buyback_assessment")

    # Create Buyback Order (idempotent — return existing if already present)
    existing_order = frappe.db.get_value(
        "Buyback Order",
        {"buyback_assessment": assessment_name, "docstatus": ["!=", 2]},
        "name",
    )
    if existing_order:
        order_name = existing_order
        order_status = frappe.db.get_value("Buyback Order", order_name, "status")
    else:
        ins = frappe.get_doc("Buyback Inspection", inspection_name)
        assessment = frappe.get_doc("Buyback Assessment", assessment_name)

        order = frappe.new_doc("Buyback Order")
        order.buyback_assessment = assessment_name
        order.buyback_inspection = inspection_name
        order.customer = ins.customer or assessment.customer or ""
        order.customer_name = ins.customer_name or assessment.customer_name or ""
        order.mobile_no = ins.mobile_no or assessment.mobile_no or ""
        order.store = ins.store or assessment.store or frappe.defaults.get_user_default("warehouse") or ""
        order.company = assessment.company or ins.company or frappe.defaults.get_global_default("company")
        order.item = ins.item or assessment.item or ""
        order.item_name = ins.item_name or assessment.item_name or ""
        order.brand = assessment.brand or ""
        order.imei_serial = ins.imei_serial or assessment.imei_serial or ""
        order.warranty_status = assessment.warranty_status or ""
        order.condition_grade = condition_grade
        order.base_price = flt(assessment.estimated_price)
        order.final_price = flt(final_price)
        order.original_quoted_price = flt(assessment.quoted_price) or flt(assessment.estimated_price)
        if remarks:
            order.remarks = str(remarks)[:500]

        # Carry forward a clean Sanchar Saathi check already done at intake.
        if assessment.imei_validation_status == "Verified Clean":
            order.imei_validation_status = assessment.imei_validation_status
            order.imei_validation_screenshot = assessment.imei_validation_screenshot
            order.imei_validation_checked_by = assessment.imei_validation_checked_by
            order.imei_validation_checked_at = assessment.imei_validation_checked_at
            order.imei_validation_remarks = assessment.imei_validation_remarks

        # Carry forward the lock-clearance just confirmed to complete this inspection
        # (complete_inspection() already required it — see buyback_inspection.py).
        if ins.account_lock_cleared:
            order.account_lock_cleared = ins.account_lock_cleared
            order.account_lock_check_notes = ins.account_lock_check_notes

        order.flags.ignore_permissions = True
        try:
            order.insert()
        except frappe.UniqueValidationError:
            # Race condition: another request created an order concurrently
            existing_order = frappe.db.get_value(
                "Buyback Order",
                {"buyback_assessment": assessment_name, "docstatus": ["!=", 2]},
                "name",
            )
            if existing_order:
                order_name = existing_order
                order_status = frappe.db.get_value("Buyback Order", order_name, "status")
                return {
                    "inspection_name": inspection_name,
                    "status": result.get("status"),
                    "order_name": order_name,
                    "order_status": order_status,
                    "assessment_name": assessment_name,
                }
            raise
        order.submit()

        order_name = order.name
        order_status = frappe.db.get_value("Buyback Order", order_name, "status")

    return {
        "inspection_name": inspection_name,
        "status": result.get("status"),
        "order_name": order_name,
        "order_status": order_status,
        "assessment_name": assessment_name,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Reprint — Today's Sales Invoices
# ═══════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_todays_invoices(pos_profile, date=None, phone=None, invoice_no=None,
                        doc_type="Invoice") -> list:
    """Return POS documents for a profile filtered by date, phone, or document number.

    Used by the Reprint dialog in the POS frontend. Company isolation is enforced
    via the POS Profile's company so the same customer's documents in another
    company (e.g. GoFix vs GoGizmo) are never exposed to this profile.

    ``doc_type`` (default ``Invoice``) selects the source:
      * ``Invoice``  → submitted Sales Invoices (POS bills + returns)
      * ``Proforma`` → submitted Quotations (POS proformas via create_pos_quotation)

    Both branches return a uniform row shape so the UI can render with one
    template; the ``__doctype`` field tells the client which print format /
    Frappe doctype to use when reprinting.
    """
    from frappe.utils import getdate

    if not pos_profile:
        frappe.throw(frappe._("POS Profile is required."))

    company = frappe.db.get_value("POS Profile", pos_profile, "company")
    if not company:
        frappe.throw(frappe._("POS Profile {0} has no Company set.").format(pos_profile))

    doc_key = (doc_type or "Invoice").strip().lower()
    if doc_key in ("proforma", "quotation"):
        return _get_proforma_quotations(company, date=date, phone=phone, invoice_no=invoice_no)
    if doc_key in ("advance receipt", "advance_receipt", "receipt", "prebooking receipt"):
        return _get_prebooking_advance_receipts(
            company=company,
            date=date,
            phone=phone,
            invoice_no=invoice_no,
        )

    if invoice_no:
        invoice_no = (invoice_no or "").strip()
        rows = frappe.db.sql("""
            SELECT
                pi.name,
                pi.customer,
                pi.customer_name,
                pi.grand_total,
                pi.posting_date,
                pi.posting_time,
                pi.is_return,
                pi.status,
                pi.custom_ch_sale_type,
                GROUP_CONCAT(DISTINCT sip.mode_of_payment ORDER BY sip.mode_of_payment SEPARATOR ', ') AS mode_of_payment,
                GROUP_CONCAT(pii.item_name ORDER BY pii.idx SEPARATOR ', ') AS items_summary
            FROM `tabSales Invoice` pi
            LEFT JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
            JOIN `tabSales Invoice Item` pii ON pii.parent = pi.name
            WHERE pi.pos_profile = %s
              AND pi.company = %s
              AND pi.name LIKE %s
              AND pi.docstatus = 1
            GROUP BY pi.name
            ORDER BY pi.posting_date DESC, pi.posting_time DESC
            LIMIT 50
        """, (pos_profile, company, f"%{invoice_no}%"), as_dict=True)
        return rows

    if phone:
        phone_clean = phone.strip()
        customers = frappe.get_all(
            "Customer",
            filters={"mobile_no": ["like", f"%{phone_clean}"]},
            pluck="name",
            limit=50,
        )
        if not customers:
            return []

        cust_placeholders = ", ".join(["%s"] * len(customers))
        rows = frappe.db.sql("""
            SELECT
                pi.name,
                pi.customer,
                pi.customer_name,
                pi.grand_total,
                pi.posting_date,
                pi.posting_time,
                pi.is_return,
                pi.status,
                pi.custom_ch_sale_type,
                GROUP_CONCAT(DISTINCT sip.mode_of_payment ORDER BY sip.mode_of_payment SEPARATOR ', ') AS mode_of_payment,
                GROUP_CONCAT(pii.item_name ORDER BY pii.idx SEPARATOR ', ') AS items_summary
            FROM `tabSales Invoice` pi
            LEFT JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
            JOIN `tabSales Invoice Item` pii ON pii.parent = pi.name
            WHERE pi.pos_profile = %s
              AND pi.company = %s
              AND pi.customer IN ({cust_placeholders})
              AND pi.docstatus = 1
            GROUP BY pi.name
            ORDER BY pi.posting_date DESC, pi.posting_time DESC
            LIMIT 50
        """.format(cust_placeholders=cust_placeholders), [pos_profile, company] + customers, as_dict=True)  # noqa: UP032
        return rows

    filter_date = getdate(date) if date else getdate(nowdate())

    rows = frappe.db.sql("""
        SELECT
            pi.name,
            pi.customer,
            pi.customer_name,
            pi.grand_total,
            pi.posting_date,
            pi.posting_time,
            pi.is_return,
            pi.status,
            pi.custom_ch_sale_type,
            GROUP_CONCAT(DISTINCT sip.mode_of_payment ORDER BY sip.mode_of_payment SEPARATOR ', ') AS mode_of_payment,
            GROUP_CONCAT(pii.item_name ORDER BY pii.idx SEPARATOR ', ') AS items_summary
        FROM `tabSales Invoice` pi
        LEFT JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
        JOIN `tabSales Invoice Item` pii ON pii.parent = pi.name
        WHERE pi.pos_profile = %s
          AND pi.company = %s
          AND pi.posting_date = %s
          AND pi.docstatus = 1
        GROUP BY pi.name
        ORDER BY pi.posting_time DESC
    """, (pos_profile, company, filter_date), as_dict=True)

    return rows


def _get_prebooking_advance_receipts(company, date=None, phone=None, invoice_no=None) -> list:
    """Return pre-booking advance receipts (Payment Entries) for Reprint.

    Only customer-receive Payment Entries linked to Sales Orders are returned,
    so the dialog shows true pre-booking advance receipts, not unrelated PEs.
    """
    from frappe.utils import getdate

    select_cols = """
        pe.name,
        pe.party AS customer,
        pe.party_name AS customer_name,
        pe.paid_amount AS grand_total,
        pe.posting_date,
        NULL AS posting_time,
        0 AS is_return,
        CASE WHEN pe.docstatus = 1 THEN 'Submitted' ELSE 'Draft' END AS status,
        NULL AS custom_ch_sale_type,
        pe.mode_of_payment,
        GROUP_CONCAT(DISTINCT per.reference_name ORDER BY per.reference_name SEPARATOR ', ') AS linked_sales_orders,
        GROUP_CONCAT(DISTINCT per.reference_name ORDER BY per.reference_name SEPARATOR ', ') AS items_summary,
        'Payment Entry' AS __doctype,
        'Standard' AS __print_format,
        pe.docstatus AS __docstatus,
        CASE WHEN pe.docstatus = 1 THEN 'Final' ELSE 'Draft' END AS receipt_state
    """
    from_clause = """
        FROM `tabPayment Entry` pe
        JOIN `tabPayment Entry Reference` per
          ON per.parent = pe.name
         AND per.reference_doctype = 'Sales Order'
    """
    where_base = """
        WHERE pe.company = %(company)s
          AND pe.docstatus != 2
          AND pe.payment_type = 'Receive'
          AND pe.party_type = 'Customer'
    """

    if invoice_no:
        return frappe.db.sql(
            "SELECT" + select_cols + from_clause + where_base + """
                AND pe.name LIKE %(needle)s
                GROUP BY pe.name
                ORDER BY pe.posting_date DESC, pe.creation DESC
                LIMIT 50
            """,
            {"company": company, "needle": f"%{(invoice_no or '').strip()}%"},
            as_dict=True,
        )

    if phone:
        customers = frappe.get_all(
            "Customer",
            filters={"mobile_no": ["like", f"%{phone.strip()}"]},
            pluck="name",
            limit=50,
        )
        if not customers:
            return []
        cust_placeholders = ", ".join(["%s"] * len(customers))
        sql = (
            "SELECT" + select_cols + from_clause + where_base
            + f" AND pe.party IN ({cust_placeholders}) "
            + "GROUP BY pe.name ORDER BY pe.posting_date DESC, pe.creation DESC LIMIT 50"
        )
        return frappe.db.sql(sql, [company] + customers, as_dict=True)

    filter_date = getdate(date) if date else getdate(nowdate())
    return frappe.db.sql(
        "SELECT" + select_cols + from_clause + where_base + """
            AND pe.posting_date = %(d)s
            GROUP BY pe.name
            ORDER BY pe.creation DESC
        """,
        {"company": company, "d": filter_date},
        as_dict=True,
    )


def _get_proforma_quotations(company, date=None, phone=None, invoice_no=None) -> list:
    """Return submitted Quotations (POS proformas) for the Reprint dialog.

    Returns the same row shape as Sales-Invoice rows so the UI template can be
    shared; ``__doctype`` is set to ``"Quotation"`` so the print button knows
    which doctype + print format to request.
    """
    from frappe.utils import getdate

    select_cols = """
        q.name,
        q.party_name AS customer,
        q.customer_name,
        q.grand_total,
        q.transaction_date AS posting_date,
        NULL AS posting_time,
        0 AS is_return,
        q.status,
        NULL AS custom_ch_sale_type,
        NULL AS mode_of_payment,
        GROUP_CONCAT(qi.item_name ORDER BY qi.idx SEPARATOR ', ') AS items_summary,
        'Quotation' AS __doctype,
        'Proforma Invoice' AS __print_format
    """
    from_clause = """
        FROM `tabQuotation` q
        JOIN `tabQuotation Item` qi ON qi.parent = q.name
    """
    where_base = """
        WHERE q.company = %(company)s
          AND q.docstatus = 1
          AND q.quotation_to = 'Customer'
    """

    if invoice_no:
        rows = frappe.db.sql(
            select_cols.join(["SELECT", from_clause]) + where_base + """
                AND q.name LIKE %(needle)s
                GROUP BY q.name
                ORDER BY q.transaction_date DESC, q.creation DESC
                LIMIT 50
            """,
            {"company": company, "needle": f"%{(invoice_no or '').strip()}%"},
            as_dict=True,
        )
        return rows

    if phone:
        customers = frappe.get_all(
            "Customer",
            filters={"mobile_no": ["like", f"%{phone.strip()}"]},
            pluck="name",
            limit=50,
        )
        if not customers:
            return []
        cust_placeholders = ", ".join(["%s"] * len(customers))
        sql = (
            "SELECT" + select_cols + from_clause + where_base
            + f" AND q.party_name IN ({cust_placeholders}) "
            + "GROUP BY q.name ORDER BY q.transaction_date DESC, q.creation DESC LIMIT 50"
        )
        rows = frappe.db.sql(sql, [company] + customers, as_dict=True)
        return rows

    filter_date = getdate(date) if date else getdate(nowdate())
    rows = frappe.db.sql(
        "SELECT" + select_cols + from_clause + where_base + """
            AND q.transaction_date = %(d)s
            GROUP BY q.name
            ORDER BY q.creation DESC
        """,
        {"company": company, "d": filter_date},
        as_dict=True,
    )
    return rows


# ═══════════════════════════════════════════════════════════════════════════
# FIFO Enforcement
# ═══════════════════════════════════════════════════════════════════════════

def _get_oldest_fifo_serial(item_code, warehouse):
    """Return (serial_no, received_date) for the oldest FIFO serial in the warehouse.

    Uses SNBB net-balance to determine what is currently in stock, then finds
    the one whose **most recent** inward into THIS warehouse is the earliest.

    TC_017 — for a buy-back unit that is re-onboarded into the Sellable
    warehouse, the FIFO clock must restart from the date it re-entered the
    warehouse. Previously this query took ``MIN(posting_datetime)`` over every
    inward SNBB ever recorded against the serial, which meant a
    bought-back-then-resold unit kept its original purchase date and was
    perpetually flagged as the "oldest" FIFO serial — blocking sale of fresh
    stock with the same item_code. We now scope inwards to the current
    warehouse and use the latest such inward as the received-date, then take
    the minimum across serials to find the FIFO leader.
    """
    rows = frappe.db.sql("""
        SELECT
            available.serial_no,
            MAX(DATE(sbb_in.posting_datetime)) AS received_date
        FROM (
            SELECT sbe.serial_no
            FROM `tabSerial and Batch Entry` sbe
            JOIN `tabSerial and Batch Bundle` sbb ON sbe.parent = sbb.name
            WHERE sbb.item_code = %s
              AND sbb.warehouse = %s
              AND sbb.docstatus = 1
            GROUP BY sbe.serial_no
            HAVING SUM(sbe.qty) > 0
        ) available
        JOIN `tabSerial and Batch Entry` sbe_in ON sbe_in.serial_no = available.serial_no
        JOIN `tabSerial and Batch Bundle` sbb_in
            ON sbe_in.parent = sbb_in.name
            AND sbb_in.type_of_transaction = 'Inward'
            AND sbb_in.docstatus = 1
            AND sbb_in.warehouse = %s
        GROUP BY available.serial_no
        ORDER BY received_date ASC, available.serial_no ASC
        LIMIT 1
    """, (item_code, warehouse, warehouse), as_dict=True)

    if rows:
        return rows[0].serial_no, rows[0].received_date

    # Fallback: use Serial No document creation timestamp on sites that do not
    # have the legacy purchase_document_date field.
    fallback = frappe.db.sql("""
        SELECT name, creation
        FROM `tabSerial No`
        WHERE item_code = %s AND warehouse = %s AND status = 'Active'
        ORDER BY creation ASC
        LIMIT 1
    """, (item_code, warehouse), as_dict=True)

    if fallback:
        return fallback[0].name, fallback[0].creation

    return None, None


def _send_fifo_violation_alert(item_code, warehouse, selected_serial, oldest_serial, cashier):
    """Create Notification Log entries for RSM/ASM/Stock Managers on FIFO violation."""
    subject = frappe._("FIFO Violation: Serial {0} selected out of order").format(selected_serial)
    message = frappe._(
        "FIFO violation at <b>{warehouse}</b>: cashier <b>{cashier}</b> is attempting to sell "
        "serial <b>{selected}</b> for item <b>{item}</b>, but the oldest available serial is "
        "<b>{oldest}</b>. Please investigate."
    ).format(
        warehouse=warehouse,
        cashier=cashier,
        selected=selected_serial,
        item=item_code,
        oldest=oldest_serial,
    )

    # Notify users with RSM / ASM / Stock Manager / System Manager roles
    alert_roles = ["RSM", "ASM", "Stock Manager", "System Manager", "Purchase Manager"]
    notified = set()

    for role in alert_roles:
        users = frappe.db.sql(
            "SELECT DISTINCT parent FROM `tabHas Role` WHERE role = %s AND parenttype = 'User'",
            role, as_dict=True
        )
        for u in users:
            uid = u.parent
            if uid in notified or uid == "Administrator":
                continue
            notified.add(uid)
            try:
                frappe.get_doc({
                    "doctype": "Notification Log",
                    "subject": subject,
                    "email_content": message,
                    "type": "Alert",
                    "document_type": "Sales Invoice",
                    "document_name": "",
                    "from_user": frappe.session.user,
                    "for_user": uid,
                    "read": 0,
                }).insert(ignore_permissions=True)
            except Exception:
                pass

    frappe.log_error(
        message=f"FIFO Violation — warehouse={warehouse} cashier={cashier} "
                f"selected={selected_serial} oldest={oldest_serial} item={item_code}",
        title="POS FIFO Violation",
    )


# ── Bundle / Free Items ──────────────────────────────────────────
def resolve_bundle_parent(item_code: str) -> str | None:
    """Return the Item code whose active Product Bundle applies to ``item_code``.

    ERPNext lets retailers link a Product Bundle to either a specific SKU or
    the parent template. Our catalogue models each colour/storage as a
    separate variant Item (e.g. ``I02793 = Apple iPhone 13 Pro 512GB Silver``),
    with the template (``Apple iPhone 13 Pro``, ``has_variants=1``) holding
    the retail-side attributes. Cashiers scan the variant, so an exact
    ``new_item_code = variant`` lookup misses when the bundle is defined
    against the template.

    Resolution order:
      1. Direct: active Product Bundle whose ``new_item_code`` equals
         ``item_code``.
      2. Fallback: if the item is a variant (``variant_of`` is set), check
         for an active bundle against the template.

    Returns the matching parent item code, or None. Used by both
    :func:`get_bundle_items` (client popup) and
    ``pos_invoice._validate_bundle_pricing`` (submit-time guard) so the
    fallback stays consistent across the read and validate paths.
    """
    if not item_code:
        return None
    if frappe.db.exists("Product Bundle", {"new_item_code": item_code, "disabled": 0}):
        return item_code
    variant_of = frappe.db.get_value("Item", item_code, "variant_of")
    if variant_of and frappe.db.exists(
        "Product Bundle", {"new_item_code": variant_of, "disabled": 0}
    ):
        return variant_of
    return None


@frappe.whitelist()
def get_bundle_items(item_code, warehouse=None, channel="POS") -> list:
    """Return free/bundled accessory items for a parent item.

    Looks up an active Product Bundle for ``item_code`` (with a variant ->
    template fallback via :func:`resolve_bundle_parent`) and returns the
    child items (excluding the parent itself) with pricing and stock info
    so the POS frontend can show a "Select free items" popup.
    """
    parent = resolve_bundle_parent(item_code)
    if not parent:
        return []

    bundle = frappe.get_doc("Product Bundle", {"new_item_code": parent, "disabled": 0})
    result = []
    for row in bundle.items:
        # Skip either the scanned variant itself or the resolved template.
        if row.item_code in (item_code, parent):
            continue

        item_fields = ["item_name", "image", "item_group", "stock_uom", "has_serial_no"]
        if frappe.db.has_column("Item", "ch_item_type"):
            item_fields.append("ch_item_type")
        if frappe.db.has_column("Item", "ch_allow_zero_rate"):
            item_fields.append("ch_allow_zero_rate")

        item = frappe.db.get_value(
            "Item", row.item_code,
            item_fields,
            as_dict=True,
        )
        if not item:
            continue

        # Pricing
        ch_price = frappe.db.get_value(
            "CH Item Price",
            {"item_code": row.item_code, "channel": channel, "status": "Active"},
            ["selling_price", "mrp"],
            as_dict=True,
        )
        selling_price = flt(ch_price.selling_price) if ch_price else 0
        mrp = flt(ch_price.mrp) if ch_price else 0

        # Stock
        stock_qty = 0
        if warehouse:
            stock_qty = flt(frappe.db.get_value(
                "Bin", {"item_code": row.item_code, "warehouse": warehouse}, "actual_qty"
            ))

        result.append({
            "item_code": row.item_code,
            "item_name": item.item_name,
            "image": item.image,
            "item_group": item.item_group,
            "stock_uom": item.stock_uom,
            "has_serial_no": cint(item.has_serial_no),
            "ch_item_type": (item.get("ch_item_type") or "") if item else "",
            "ch_allow_zero_rate": cint(item.get("ch_allow_zero_rate")) if item else 0,
            "selling_price": selling_price,
            "mrp": mrp,
            "stock_qty": stock_qty,
            "bundle_qty": flt(row.qty),
            "is_free_bundle_item": 1,
        })

    return result


def _post_free_sale_write_off(inv) -> None:
    """Reclassify COGS of free-sale items as Promotional Expense.

    ERPNext already debits Cost of Goods Sold when the SI with update_stock=1
    is submitted.  This JE moves that cost into a distinct Promotional Expense
    account so Finance can see the true P&L impact of free promotions.

    Silently skips (logs warning) if accounts are not configured.
    """
    settings = frappe.get_cached_doc("CH POS Control Settings")
    promo_account = settings.get("promotional_expense_account")
    if not promo_account:
        frappe.log_error(
            f"Free sale write-off GL skipped for {inv.name}: "
            "'Promotional Expense Account' not set in CH POS Control Settings → GL Accounts.",
            "Free Sale GL"
        )
        return

    company = inv.company
    cost_center = frappe.db.get_value("Company", company, "cost_center")
    total_cost = flt(0)
    je_accounts = []

    for item in inv.items:
        if flt(item.qty) <= 0:
            continue
        # Get the valuation rate from the Bin (current cost) as approximation
        # Stock Ledger Entry incoming_rate would be more precise but requires SLE query
        val_rate = flt(frappe.db.get_value(
            "Bin",
            {"item_code": item.item_code, "warehouse": item.warehouse or inv.set_warehouse},
            "valuation_rate",
        ) or 0)
        if val_rate <= 0:
            continue

        item_cost = flt(val_rate * flt(item.qty), 2)
        total_cost += item_cost

        # Credit: expense_account on item (the COGS account ERPNext used)
        cogs_account = item.expense_account or frappe.db.get_value(
            "Item", item.item_code, "expense_account"
        ) or frappe.db.get_value("Company", company, "default_expense_account")

        if cogs_account:
            je_accounts.append({
                "account": cogs_account,
                "credit_in_account_currency": item_cost,
                "cost_center": cost_center,
                "reference_type": "Sales Invoice",
                "reference_name": inv.name,
            })

    if total_cost <= 0 or not je_accounts:
        return

    # Debit side: single Promotional Expense line for the total
    je_accounts.insert(0, {
        "account": promo_account,
        "debit_in_account_currency": total_cost,
        "cost_center": cost_center,
        "reference_type": "Sales Invoice",
        "reference_name": inv.name,
    })

    try:
        je = frappe.new_doc("Journal Entry")
        je.update({
            "voucher_type": "Journal Entry",
            "company": company,
            "posting_date": inv.posting_date or frappe.utils.today(),
            "cheque_no": inv.name,
            "cheque_date": inv.posting_date or frappe.utils.today(),
            "remark": frappe._("Free sale promotional write-off — {0}").format(inv.name),
            "accounts": je_accounts,
        })
        je.flags.ignore_permissions = True
        je.insert(ignore_permissions=True)
        je.submit()
        frappe.db.set_value(
            "Sales Invoice", inv.name,
            "custom_promo_write_off_je", je.name,
            update_modified=False,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(),
                         f"Free sale write-off GL failed for {inv.name}")


# ──────────────────────────────────────────────────────────────────────────
# Pickup Queue — Convert Pre-Booking (Sales Order) → POS Sales Invoice
# ──────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def list_pickup_prebookings(pos_profile, search=None, days_ahead=30,
                            overdue_only=0, limit=100):
    """Return submitted, pickup-pending Pre-Bookings (Sales Orders) for this POS profile.

    A row is considered pickup-pending when:
      - docstatus = 1 (submitted)
      - per_billed < 100 (not yet fully invoiced)
      - status NOT IN ('Closed', 'Cancelled', 'Completed')
      - company matches the POS Profile's company
    """
    frappe.has_permission("Sales Order", "read", throw=True)

    if not pos_profile:
        frappe.throw(_("POS Profile is required"))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    company = profile.company
    overdue_only = cint(overdue_only)
    try:
        days_ahead = int(days_ahead)
    except (TypeError, ValueError):
        days_ahead = 30
    limit = min(max(cint(limit) or 100, 1), 500)

    conditions = [
        "so.docstatus = 1",
        "so.company = %(company)s",
        "so.per_billed < 100",
        "so.status NOT IN ('Closed', 'Cancelled', 'Completed', 'On Hold')",
    ]
    params = {"company": company}

    if overdue_only:
        conditions.append("so.delivery_date <= %(today)s")
        params["today"] = nowdate()
    elif days_ahead:
        conditions.append("so.delivery_date <= %(horizon)s")
        params["horizon"] = frappe.utils.add_days(nowdate(), days_ahead)

    if search:
        conditions.append(
            "(so.name LIKE %(q)s OR so.customer LIKE %(q)s "
            "OR so.customer_name LIKE %(q)s OR so.tracking_number LIKE %(q)s)"
        )
        params["q"] = f"%{search}%"

    rows = frappe.db.sql(
        f"""
        SELECT so.name, so.customer, so.customer_name, so.transaction_date,
               so.delivery_date, so.grand_total, so.advance_paid, so.currency,
               so.status, so.per_billed, so.per_delivered,
               COALESCE(so.reserve_stock, 0) AS reserve_stock,
               so.tracking_number, so.contact_mobile, so.contact_email
          FROM `tabSales Order` so
         WHERE {' AND '.join(conditions)}
         ORDER BY so.delivery_date ASC, so.transaction_date ASC
         LIMIT {limit}
        """,
        params, as_dict=True,
    )
    if not rows:
        return []

    so_names = [r.name for r in rows]
    soi_meta = frappe.get_meta("Sales Order Item")
    has_custom_serial_col = soi_meta.has_field("custom_serial_no")
    serial_select = ", soi.custom_serial_no AS reserved_serials" if has_custom_serial_col else ""
    items = frappe.db.sql(
        f"""SELECT soi.parent, soi.item_code, soi.item_name, soi.qty,
                   soi.delivered_qty, soi.billed_amt, soi.rate, soi.amount,
                   soi.warehouse, soi.delivery_date
                   {serial_select}
             FROM `tabSales Order Item` soi
            WHERE soi.parent IN %(p)s
            ORDER BY soi.idx""",
        {"p": tuple(so_names)}, as_dict=True,
    )

    def _split_serials(raw):
        if not raw:
            return []
        return [s.strip() for s in str(raw).replace("\r", "").split("\n") if s.strip()]

    items_by_parent = {}
    serials_by_parent = {}
    for it in items:
        items_by_parent.setdefault(it.parent, []).append(it)
        serials = _split_serials(it.get("reserved_serials"))
        if serials:
            it["reserved_serials"] = serials
            serials_by_parent.setdefault(it.parent, []).extend(serials)
        else:
            it["reserved_serials"] = []

    today = getdate(nowdate())
    out = []
    for r in rows:
        bal = flt(r.grand_total) - flt(r.advance_paid)
        dd = getdate(r.delivery_date) if r.delivery_date else None
        days = (dd - today).days if dd else None
        reserved = serials_by_parent.get(r.name, [])
        out.append({
            "name": r.name,
            "customer": r.customer,
            "customer_name": r.customer_name or r.customer,
            "transaction_date": str(r.transaction_date) if r.transaction_date else None,
            "delivery_date": str(r.delivery_date) if r.delivery_date else None,
            "days_to_delivery": days,
            "is_overdue": bool(dd and dd < today),
            "grand_total": flt(r.grand_total),
            "advance_paid": flt(r.advance_paid),
            "balance_due": bal if bal > 0 else 0,
            "currency": r.currency,
            "status": r.status,
            "per_billed": flt(r.per_billed),
            "per_delivered": flt(r.per_delivered),
            "reserve_stock": cint(r.reserve_stock),
            "tracking_number": r.tracking_number,
            "contact_mobile": r.contact_mobile,
            "contact_email": r.contact_email,
            "items": items_by_parent.get(r.name, []),
            "reserved_serials": reserved,
            "reserved_serial_count": len(reserved),
        })
    return out


@frappe.whitelist()
def list_reserved_serials(pos_profile, search=None, limit=300):
    """Return a flat list of IMEIs/serials currently reserved on open
    pre-bookings for this POS profile.

    Each row carries enough context (item, customer, SO, due date) for the
    cashier to verify physical pickup against the reservation — mirrors the
    Reserved Items view in SAP Retail / Oracle Xstore / Tally Shoper POS.
    """
    frappe.has_permission("Sales Order", "read", throw=True)
    if not pos_profile:
        frappe.throw(_("POS Profile is required"))

    soi_meta = frappe.get_meta("Sales Order Item")
    if not soi_meta.has_field("custom_serial_no"):
        return []

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    company = profile.company
    limit = min(max(cint(limit) or 300, 1), 1000)

    conditions = [
        "so.docstatus = 1",
        "so.company = %(company)s",
        "so.per_billed < 100",
        "so.status NOT IN ('Closed', 'Cancelled', 'Completed', 'On Hold')",
        "COALESCE(so.reserve_stock, 0) = 1",
        "IFNULL(soi.custom_serial_no, '') != ''",
    ]
    params = {"company": company}
    if search:
        conditions.append(
            "(soi.custom_serial_no LIKE %(q)s OR so.customer LIKE %(q)s "
            "OR so.customer_name LIKE %(q)s OR soi.item_code LIKE %(q)s "
            "OR soi.item_name LIKE %(q)s OR so.name LIKE %(q)s)"
        )
        params["q"] = f"%{search}%"

    rows = frappe.db.sql(
        f"""
        SELECT so.name AS sales_order, so.customer, so.customer_name,
               so.transaction_date, so.delivery_date, so.per_billed,
               soi.item_code, soi.item_name, soi.qty, soi.warehouse,
               soi.custom_serial_no AS serial_blob
          FROM `tabSales Order` so
          JOIN `tabSales Order Item` soi ON soi.parent = so.name
         WHERE {' AND '.join(conditions)}
         ORDER BY so.delivery_date ASC, so.name ASC
         LIMIT {limit}
        """,
        params, as_dict=True,
    )

    today = getdate(nowdate())
    out = []
    for r in rows:
        for serial in [s.strip() for s in str(r.serial_blob or "").replace("\r", "").split("\n") if s.strip()]:
            dd = getdate(r.delivery_date) if r.delivery_date else None
            out.append({
                "serial_no": serial,
                "item_code": r.item_code,
                "item_name": r.item_name,
                "warehouse": r.warehouse,
                "qty": flt(r.qty),
                "sales_order": r.sales_order,
                "customer": r.customer,
                "customer_name": r.customer_name or r.customer,
                "transaction_date": str(r.transaction_date) if r.transaction_date else None,
                "delivery_date": str(r.delivery_date) if r.delivery_date else None,
                "is_overdue": bool(dd and dd < today),
                "per_billed": flt(r.per_billed),
            })
    return out


@frappe.whitelist()
def get_prebook_pickup_kpis(pos_profile, days=7):
    """KPI snapshot for the cashier-facing Pre-Book and Pickup workspaces.

    Returns counters store staff need to answer questions like:
      - How many proformas did we raise today / this week?
      - How many are still open vs converted / expired / lost?
      - How many pre-bookings are still pending pickup, overdue, billed today?
      - How many IMEIs/serials are currently reserved?

    Mirrors the cashier dashboards in SAP Retail, Oracle Xstore Office and
    GoFrugal POS, kept lightweight so it can be refreshed on every tab open.
    """
    frappe.has_permission("Quotation", "read", throw=True)
    frappe.has_permission("Sales Order", "read", throw=True)

    if not pos_profile:
        frappe.throw(_("POS Profile is required"))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    company = profile.company
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(days, 90))
    today = nowdate()
    window_start = frappe.utils.add_days(today, -days)

    # ── Proforma (Quotation) ─────────────────────────────────────────
    qtn_today = frappe.db.sql(
        """SELECT COUNT(*) AS n, COALESCE(SUM(grand_total), 0) AS v
             FROM `tabQuotation`
            WHERE company = %(company)s AND docstatus = 1
              AND transaction_date = %(today)s""",
        {"company": company, "today": today}, as_dict=True,
    )[0]
    qtn_window = frappe.db.sql(
        """SELECT status, COUNT(*) AS n, COALESCE(SUM(grand_total), 0) AS v
             FROM `tabQuotation`
            WHERE company = %(company)s AND docstatus = 1
              AND transaction_date >= %(start)s
         GROUP BY status""",
        {"company": company, "start": window_start}, as_dict=True,
    )
    qtn_status = {row.status: {"count": cint(row.n), "value": flt(row.v)} for row in qtn_window}

    # ── Pre-Booking (Sales Order) ────────────────────────────────────
    so_open = frappe.db.sql(
        """SELECT COUNT(*) AS n, COALESCE(SUM(grand_total - advance_paid), 0) AS v
             FROM `tabSales Order`
            WHERE company = %(company)s AND docstatus = 1
              AND per_billed < 100
              AND status NOT IN ('Closed', 'Cancelled', 'Completed', 'On Hold')""",
        {"company": company}, as_dict=True,
    )[0]
    so_overdue = frappe.db.sql(
        """SELECT COUNT(*) AS n
             FROM `tabSales Order`
            WHERE company = %(company)s AND docstatus = 1
              AND per_billed < 100
              AND status NOT IN ('Closed', 'Cancelled', 'Completed', 'On Hold')
              AND delivery_date < %(today)s""",
        {"company": company, "today": today}, as_dict=True,
    )[0]
    so_billed_today = frappe.db.sql(
        """SELECT COUNT(DISTINCT si.name) AS n,
                  COALESCE(SUM(si.grand_total), 0) AS v
             FROM `tabSales Invoice` si
             JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
            WHERE si.company = %(company)s AND si.docstatus = 1
              AND si.posting_date = %(today)s
              AND IFNULL(sii.sales_order, '') != ''""",
        {"company": company, "today": today}, as_dict=True,
    )[0]

    # ── Reserved Serials ─────────────────────────────────────────────
    reserved_count = 0
    soi_meta = frappe.get_meta("Sales Order Item")
    if soi_meta.has_field("custom_serial_no"):
        reserved_rows = frappe.db.sql(
            """SELECT IFNULL(soi.custom_serial_no, '') AS serial_blob
                 FROM `tabSales Order` so
                 JOIN `tabSales Order Item` soi ON soi.parent = so.name
                WHERE so.company = %(company)s AND so.docstatus = 1
                  AND so.per_billed < 100
                  AND so.status NOT IN ('Closed', 'Cancelled', 'Completed', 'On Hold')
                  AND COALESCE(so.reserve_stock, 0) = 1
                  AND IFNULL(soi.custom_serial_no, '') != ''""",
            {"company": company}, as_dict=True,
        )
        for r in reserved_rows:
            reserved_count += sum(
                1 for s in str(r.serial_blob or "").replace("\r", "").split("\n") if s.strip()
            )

    return {
        "company": company,
        "window_days": days,
        "today": today,
        "proforma": {
            "today_count": cint(qtn_today.n),
            "today_value": flt(qtn_today.v),
            "by_status": qtn_status,
            "open_count": cint(qtn_status.get("Open", {}).get("count", 0)),
            "open_value": flt(qtn_status.get("Open", {}).get("value", 0)),
            "ordered_count": cint(qtn_status.get("Ordered", {}).get("count", 0)),
            "lost_count": cint(qtn_status.get("Lost", {}).get("count", 0)),
            "expired_count": cint(qtn_status.get("Expired", {}).get("count", 0)),
        },
        "prebook": {
            "open_count": cint(so_open.n),
            "open_balance": flt(so_open.v),
            "overdue_count": cint(so_overdue.n),
            "billed_today_count": cint(so_billed_today.n),
            "billed_today_value": flt(so_billed_today.v),
        },
        "reserved_serials": reserved_count,
    }


@frappe.whitelist()
def list_my_proformas(pos_profile, status=None, search=None,
                      days=30, only_mine=1, limit=100):
    """List Proforma Quotations raised from POS, so store staff can see
    open / converted / lost / expired counts at a glance.

    Statuses follow ERPNext Quotation: Open, Ordered, Lost, Expired.
    By default only the current user's quotations are returned so each
    cashier sees what they personally raised — matching cashier-centric
    dashboards in retail POS suites.
    """
    frappe.has_permission("Quotation", "read", throw=True)
    if not pos_profile:
        frappe.throw(_("POS Profile is required"))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    company = profile.company
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 365))
    limit = min(max(cint(limit) or 100, 1), 500)

    conditions = [
        "q.docstatus = 1",
        "q.company = %(company)s",
        "q.transaction_date >= %(start)s",
    ]
    params = {
        "company": company,
        "start": frappe.utils.add_days(nowdate(), -days),
    }

    if status and status != "All":
        conditions.append("q.status = %(status)s")
        params["status"] = status
    if cint(only_mine):
        conditions.append("q.owner = %(me)s")
        params["me"] = frappe.session.user
    if search:
        conditions.append(
            "(q.name LIKE %(q)s OR q.party_name LIKE %(q)s "
            "OR q.customer_name LIKE %(q)s)"
        )
        params["q"] = f"%{search}%"

    rows = frappe.db.sql(
        f"""
        SELECT q.name, q.party_name, q.customer_name, q.transaction_date,
               q.valid_till, q.grand_total, q.status, q.owner,
               q.contact_mobile, q.contact_email
          FROM `tabQuotation` q
         WHERE {' AND '.join(conditions)}
         ORDER BY q.transaction_date DESC, q.creation DESC
         LIMIT {limit}
        """,
        params, as_dict=True,
    )
    if not rows:
        return []

    qtn_names = [r.name for r in rows]
    items = frappe.db.sql(
        """SELECT parent, item_code, item_name, qty, rate, amount
             FROM `tabQuotation Item`
            WHERE parent IN %(p)s
            ORDER BY idx""",
        {"p": tuple(qtn_names)}, as_dict=True,
    )
    items_by_parent = {}
    for it in items:
        items_by_parent.setdefault(it.parent, []).append(it)

    today = getdate(nowdate())
    out = []
    for r in rows:
        vt = getdate(r.valid_till) if r.valid_till else None
        days_left = (vt - today).days if vt else None
        advance = 0.0
        try:
            advance = flt(frappe.db.get_value(
                "Quotation", r.name, "custom_advance_received"
            ) or 0)
        except Exception:
            advance = 0.0
        out.append({
            "name": r.name,
            "customer": r.party_name,
            "customer_name": r.customer_name or r.party_name,
            "transaction_date": str(r.transaction_date) if r.transaction_date else None,
            "valid_till": str(r.valid_till) if r.valid_till else None,
            "days_left": days_left,
            "is_expiring_soon": bool(days_left is not None and 0 <= days_left <= 3),
            "is_expired": bool(vt and vt < today and r.status == "Open"),
            "grand_total": flt(r.grand_total),
            "advance_received": advance,
            "balance_due": max(flt(r.grand_total) - advance, 0),
            "status": r.status,
            "owner": r.owner,
            "contact_mobile": r.contact_mobile,
            "contact_email": r.contact_email,
            "items": items_by_parent.get(r.name, []),
            "print_format": "Proforma Invoice",
            "print_url": "/printview?doctype=Quotation"
                         f"&name={frappe.utils.escape_html(r.name)}"
                         f"&format=Proforma+Invoice&no_letterhead=0",
        })
    return out


@frappe.whitelist()
def load_sales_order_to_cart(pos_profile, sales_order):
    """Load a submitted pre-booking Sales Order into POS cart-item shape.

    Returns the data the cart panel needs to bill the SO inline (right-panel
    flow): cart items, customer, sale_type, advance, balance due, IMEI
    reservations. The POS payment dialog then submits via ``create_pos_invoice``
    with ``sales_order`` set; the backend re-links each item to its SO row via
    ``so_detail`` so ``per_billed`` / advance allocation / stock reservation
    behave exactly like ``make_sales_invoice(source=SO)``.
    """
    frappe.has_permission("Sales Order", "read", throw=True)
    if not pos_profile:
        frappe.throw(_("POS Profile is required"))
    if not sales_order:
        frappe.throw(_("Sales Order is required"))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    so = frappe.get_doc("Sales Order", sales_order)

    if so.docstatus != 1:
        frappe.throw(_("Sales Order {0} is not submitted").format(sales_order))
    if so.company != profile.company:
        frappe.throw(
            _("Sales Order company ({0}) does not match POS Profile company ({1})").format(
                so.company, profile.company
            )
        )
    if flt(so.per_billed) >= 100:
        frappe.throw(_("Sales Order {0} is already fully billed").format(sales_order))

    # Sale type carried on the SO (custom field) — replayed onto cart so the
    # cashier sees the same context before billing.
    sale_type = (
        so.get("custom_sale_type")
        or so.get("ch_sale_type")
        or so.get("sale_type")
        or None
    )

    # Per-item cache for item-level flags the cart panel needs.
    item_codes = list({d.item_code for d in (so.items or [])})
    item_meta = {}
    if item_codes:
        # ``must_be_whole_number`` is optional on many sites (custom field).
        # Query it only when present to keep this API migration-safe.
        item_meta_doc = frappe.get_meta("Item")
        has_whole_col = bool(item_meta_doc.get_field("must_be_whole_number"))
        select_cols = (
            "name AS item_code, item_name, stock_uom, has_serial_no, "
            "ch_item_type, ch_allow_zero_rate"
        )
        if has_whole_col:
            select_cols += ", must_be_whole_number"
        rows = frappe.db.sql(
            f"""SELECT {select_cols}
                 FROM `tabItem`
                WHERE name IN %(p)s""",
            {"p": tuple(item_codes)},
            as_dict=True,
        )
        item_meta = {r.item_code: r for r in rows}

    def _split_serials(raw):
        if not raw:
            return []
        return [s.strip() for s in str(raw).replace("\r", "").split("\n") if s.strip()]

    cart_items = []
    all_reserved = []
    for d in (so.items or []):
        # Skip rows already fully billed (defensive).
        billed_qty = flt(d.qty) * (flt(d.billed_amt) / flt(d.amount) if flt(d.amount) else 0)
        remaining_qty = max(flt(d.qty) - billed_qty, 0)
        if remaining_qty <= 0:
            continue

        meta = item_meta.get(d.item_code) or {}
        has_serial = cint(meta.get("has_serial_no") or 0)
        reserved = _split_serials(d.get("custom_serial_no"))
        all_reserved.extend(reserved)

        rate = flt(d.rate)
        price_list_rate = flt(d.price_list_rate or d.rate)
        discount_amount = max(0, price_list_rate - rate)
        discount_pct = (discount_amount / price_list_rate * 100) if price_list_rate > 0 else 0

        base_row = {
            "item_code": d.item_code,
            "item_name": d.item_name or meta.get("item_name") or d.item_code,
            "rate": rate,
            "price_list_rate": price_list_rate,
            "mrp": price_list_rate,
            "uom": d.uom or meta.get("stock_uom") or "Nos",
            "discount_percentage": flt(discount_pct),
            "discount_amount": flt(discount_amount),
            "offers": [],
            "applied_offer": None,
            "warranty_plan": None,
            "is_warranty": False,
            "is_vas": False,
            "has_serial_no": cint(has_serial),
            "ch_item_type": meta.get("ch_item_type") or "",
            "ch_allow_zero_rate": cint(meta.get("ch_allow_zero_rate") or 0),
            "must_be_whole_number": cint(meta.get("must_be_whole_number") or 0),
            "stock_qty": flt(remaining_qty),
            # Sales-order linkage — passed back to ``create_pos_invoice`` so the
            # generated Sales Invoice Item lands with proper SO references and
            # ``per_billed`` updates on submit.
            "sales_order": so.name,
            "so_detail": d.name,
            "from_sales_order": True,
        }

        # Serial-backed items: expand one cart row per reserved IMEI so the
        # cashier can see them individually (matches the rest of the cart UX
        # where serial items are always qty=1 per row).
        if has_serial and reserved:
            for sn in reserved:
                row = dict(base_row)
                row["qty"] = 1
                row["serial_no"] = sn
                cart_items.append(row)
            # If reserved count < SO qty, fill remainder as unserialised slots
            # the cashier must scan.
            extra = int(remaining_qty) - len(reserved)
            for _ in range(max(extra, 0)):
                row = dict(base_row)
                row["qty"] = 1
                row["serial_no"] = ""
                cart_items.append(row)
        elif has_serial:
            for _ in range(int(remaining_qty) or 1):
                row = dict(base_row)
                row["qty"] = 1
                row["serial_no"] = ""
                cart_items.append(row)
        else:
            row = dict(base_row)
            row["qty"] = flt(remaining_qty)
            row["serial_no"] = ""
            cart_items.append(row)

    advance = flt(so.advance_paid)
    grand_total = flt(so.grand_total)
    balance_due = max(grand_total - advance, 0)

    return {
        "sales_order": so.name,
        "customer": so.customer,
        "customer_name": so.customer_name or so.customer,
        "sale_type": sale_type,
        "grand_total": grand_total,
        "advance_paid": advance,
        "balance_due": balance_due,
        "currency": so.currency,
        "delivery_date": str(so.delivery_date) if so.delivery_date else None,
        "reserved_serials": all_reserved,
        "items": cart_items,
        "item_count": len(cart_items),
    }


@frappe.whitelist()
def load_quotation_to_cart(pos_profile, quotation):
    """Load a submitted Quotation (POS Proforma) into POS cart-item shape.

    Mirrors :py:func:`load_sales_order_to_cart` but sources from a Quotation
    so the cashier can:

      * **Convert → Sale**: seed the Sell cart with proforma items, then
        scan IMEIs, add accessories/VAS, change qty, and press PAY normally.
        The resulting Sales Invoice is linked back via
        ``custom_source_quotation`` for audit (no field write on the
        Quotation here — its status auto-updates to ``Ordered`` when a
        downstream SO/SI references its items the standard ERPNext way).

      * **Convert → Pre-Booking**: seed the Pre-Booking dialog with the
        same items + customer; cashier picks delivery date, collects
        advance via split-tender, and the Sales Order is created. The
        Quotation status flips to ``Ordered`` via the standard mapper.

    Market parity:
      * SAP SD F2 with reference to F5/F8
      * Oracle Xstore Quote → Order (Create with Reference)
      * MS D365 Retail Quote → Sales Order
      * Zoho / Odoo / GoFrugal / Tally — "Convert to Invoice" / "Make Bill"
      * ERPNext core ``make_sales_invoice`` / ``make_sales_order`` mappers
    """
    frappe.has_permission("Quotation", "read", throw=True)
    if not pos_profile:
        frappe.throw(_("POS Profile is required"))
    if not quotation:
        frappe.throw(_("Quotation is required"))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    qtn = frappe.get_doc("Quotation", quotation)

    if qtn.docstatus != 1:
        frappe.throw(_("Quotation {0} is not submitted").format(quotation))
    if qtn.company != profile.company:
        frappe.throw(
            _("Quotation company ({0}) does not match POS Profile company ({1})").format(
                qtn.company, profile.company
            )
        )
    if (qtn.status or "").lower() in ("lost", "cancelled"):
        frappe.throw(_("Quotation {0} is {1} — cannot convert").format(quotation, qtn.status))
    if qtn.valid_till and getdate(qtn.valid_till) < getdate(nowdate()):
        # Soft warning rather than hard throw — cashiers may still want to
        # re-quote an expired proforma. Frontend surfaces this as a confirm.
        warning = _("Quotation {0} expired on {1}").format(quotation, qtn.valid_till)
    else:
        warning = None

    # Per-item cache for cart flags
    item_codes = list({d.item_code for d in (qtn.items or [])})
    item_meta = {}
    if item_codes:
        # NOTE: ``must_be_whole_number`` is a ch_pos JS-side qty-step flag,
        # not a guaranteed schema column on tabItem (older installs lack the
        # custom field). Pull it conditionally via Item meta so the loader
        # works on every site; default to 0 (allow decimal qty) when absent.
        item_meta_doc = frappe.get_meta("Item")
        has_whole_col = bool(item_meta_doc.get_field("must_be_whole_number"))
        select_cols = (
            "name AS item_code, item_name, stock_uom, has_serial_no, "
            "ch_item_type, ch_allow_zero_rate"
        )
        if has_whole_col:
            select_cols += ", must_be_whole_number"
        rows = frappe.db.sql(
            f"""SELECT {select_cols}
                 FROM `tabItem`
                WHERE name IN %(p)s""",
            {"p": tuple(item_codes)},
            as_dict=True,
        )
        item_meta = {r.item_code: r for r in rows}

    cart_items = []
    for d in (qtn.items or []):
        meta = item_meta.get(d.item_code) or {}
        has_serial = cint(meta.get("has_serial_no") or 0)
        rate = flt(d.rate)
        price_list_rate = flt(d.price_list_rate or d.rate)
        discount_amount = max(0, price_list_rate - rate)
        discount_pct = (discount_amount / price_list_rate * 100) if price_list_rate > 0 else 0

        base_row = {
            "item_code": d.item_code,
            "item_name": d.item_name or meta.get("item_name") or d.item_code,
            "rate": rate,
            "price_list_rate": price_list_rate,
            "mrp": price_list_rate,
            "uom": d.uom or meta.get("stock_uom") or "Nos",
            "discount_percentage": flt(discount_pct),
            "discount_amount": flt(discount_amount),
            "offers": [],
            "applied_offer": None,
            "warranty_plan": None,
            "is_warranty": False,
            "is_vas": False,
            "has_serial_no": cint(has_serial),
            "ch_item_type": meta.get("ch_item_type") or "",
            "ch_allow_zero_rate": cint(meta.get("ch_allow_zero_rate") or 0),
            "must_be_whole_number": cint(meta.get("must_be_whole_number") or 0),
            "stock_qty": flt(d.qty),
            # Quotation linkage — kept on each row so the eventual Sales
            # Invoice / Sales Order can stamp prevdoc_docname for audit
            # parity with the ERPNext "Make → Sales Invoice" mapper.
            "source_quotation": qtn.name,
            "quotation_item": d.name,
            "from_quotation": True,
        }

        # Serial-backed items: expand one row per qty so the cashier scans
        # an IMEI per unit at billing time (proforma never carries IMEIs).
        if has_serial:
            for _ in range(int(flt(d.qty)) or 1):
                row = dict(base_row)
                row["qty"] = 1
                row["serial_no"] = ""
                cart_items.append(row)
        else:
            row = dict(base_row)
            row["qty"] = flt(d.qty)
            row["serial_no"] = ""
            cart_items.append(row)

    return {
        "quotation": qtn.name,
        "customer": qtn.party_name,
        "customer_name": qtn.customer_name or qtn.party_name,
        "sale_type": None,           # Cashier picks at billing time
        "grand_total": flt(qtn.grand_total),
        "currency": qtn.currency,
        "valid_till": str(qtn.valid_till) if qtn.valid_till else None,
        "status": qtn.status,
        "items": cart_items,
        "item_count": len(cart_items),
        "warning": warning,
    }


@frappe.whitelist()
def convert_prebooking_to_invoice(pos_profile, sales_order,
                                  mode_of_payment=None, paid_amount=None,
                                  apply_advance=1, client_request_id=None,
                                  scanned_serials=None):
    """Create & submit a POS Sales Invoice from a pre-booking (Sales Order).

    Uses ERPNext's standard `make_sales_invoice` mapper, then flips it to a
    POS invoice (is_pos=1) tied to the supplied POS Profile. Optionally takes
    a single payment at pickup time. Advance already collected on the SO is
    pulled in automatically via the SO link.
    """
    frappe.has_permission("Sales Invoice", "create", throw=True)

    if not pos_profile:
        frappe.throw(_("POS Profile is required"))
    if not sales_order:
        frappe.throw(_("Sales Order is required"))

    # Session guard
    from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_active_session
    active = get_active_session(pos_profile)
    if not active:
        frappe.throw(_("No active POS session. Open a session before billing."))

    # Duplicate-submit guard (same pattern as create_pos_invoice)
    if client_request_id:
        existing = frappe.db.sql(
            """SELECT name FROM `tabSales Invoice`
                WHERE custom_client_request_id = %(crid)s
                  AND docstatus != 2
                  AND creation >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)
                LIMIT 1""",
            {"crid": str(client_request_id)[:140]},
            as_dict=True,
        )
        if existing:
            inv = frappe.get_doc("Sales Invoice", existing[0].name)
            return _pickup_invoice_response(inv, status="duplicate_prevented")

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    so = frappe.get_doc("Sales Order", sales_order)

    if so.docstatus != 1:
        frappe.throw(_("Sales Order {0} is not submitted").format(sales_order))
    if so.company != profile.company:
        frappe.throw(_("Sales Order company ({0}) does not match POS Profile company ({1})").format(
            so.company, profile.company))
    if flt(so.per_billed) >= 100:
        frappe.throw(_("Sales Order {0} is already fully billed").format(sales_order))

    # ── IMEI scan-confirmation (goods-issue verification) ──────────────────
    # Every IMEI reserved on the pre-booking must be physically scanned at
    # pickup before billing — confirming the exact device handed over matches
    # what was reserved. Mirrors SAP/Oracle pick-confirm against reservation.
    _confirm_prebook_serials(so, scanned_serials)

    # Reuse ERPNext's standard mapper — preserves taxes, advances, item mapping
    from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
    inv = make_sales_invoice(source_name=sales_order, target_doc=None, ignore_permissions=False)

    # Strip rows that are already fully billed (defensive)
    inv.items = [d for d in inv.items if flt(d.qty) > 0]
    if not inv.items:
        frappe.throw(_("Nothing left to bill on Sales Order {0}").format(sales_order))

    # ── POS flip ──────────────────────────────────────────────
    inv.is_pos = 1
    inv.update_stock = 1
    inv.pos_profile = pos_profile
    inv.custom_ch_pos_session = active.get("name")
    inv.posting_date = str(active.get("business_date")) if active.get("business_date") else nowdate()
    if profile.warehouse:
        inv.set_warehouse = profile.warehouse
        for it in inv.items:
            if not it.warehouse:
                it.warehouse = profile.warehouse

    # ── Serial/Batch reservation bundles ──────────────────────
    # ERPNext's make_sales_invoice mapper (unlike make_delivery_note) does NOT
    # build Serial and Batch Bundles for SO rows backed by a Stock Reservation
    # Entry. With update_stock=1, selling_controller.update_stock_reservation_entries
    # then does frappe.get_doc("Serial and Batch Bundle", item.serial_and_batch_bundle)
    # and blows up with "Serial and Batch Bundle None not found" when serials
    # were reserved on the SO. Mirror the DN mapper's behaviour here.
    try:
        from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
            get_sre_details_for_voucher,
            get_ssb_bundle_for_voucher,
        )

        sre_list = get_sre_details_for_voucher("Sales Order", sales_order) or []
        if sre_list:
            sre_by_detail = {s.voucher_detail_no: s for s in sre_list}
            for it in inv.items:
                if it.get("serial_and_batch_bundle"):
                    continue
                sre = sre_by_detail.get(it.so_detail)
                if not sre:
                    continue
                if (
                    sre.reservation_based_on == "Serial and Batch"
                    and (sre.has_serial_no or sre.has_batch_no)
                ):
                    bundle = get_ssb_bundle_for_voucher(sre)
                    if bundle:
                        it.serial_and_batch_bundle = (
                            bundle.name if hasattr(bundle, "name") else bundle
                        )
                        if sre.warehouse and not it.warehouse:
                            it.warehouse = sre.warehouse
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Pickup: serial/batch bundle hydration failed for SO {sales_order}",
        )

    # Tax Category — derived from company vs customer billing-address state.
    # Customer master tax category is no longer consulted (item-level GST
    # migration). Pickup invoices that already carry tax_category from the
    # source SO retain their value.
    if not inv.get("tax_category"):
        from ch_erp15.ch_erp15.custom.sales_invoice import get_gst_template_for_customer
        _resolved = get_gst_template_for_customer(inv.customer or "", inv.company) or {}
        inv.tax_category = _resolved.get("tax_category") or "In-State"
        if _resolved.get("template") and not inv.get("taxes_and_charges"):
            inv.taxes_and_charges = _resolved["template"]

    if client_request_id and inv.meta.has_field("custom_client_request_id"):
        inv.custom_client_request_id = str(client_request_id)[:140]

    # Apply advance already collected on the SO (idempotent — set_advances() handles this)
    if cint(apply_advance):
        try:
            inv.set_advances()
        except Exception:
            frappe.log_error(frappe.get_traceback(),
                             f"Pickup: set_advances failed for SO {sales_order}")

    # Build payments table from POS Profile defaults
    from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account
    inv.set("payments", [])
    for row in (profile.payments or []):
        acct = get_bank_cash_account(row.mode_of_payment, profile.company).get("account")
        inv.append("payments", {
            "mode_of_payment": row.mode_of_payment,
            "account": acct,
            "default": row.default,
            "amount": 0,
        })

    # Single payment at pickup (optional — staff may choose to collect later)
    if mode_of_payment and flt(paid_amount or 0) > 0:
        matched = next((p for p in inv.payments if p.mode_of_payment == mode_of_payment), None)
        if matched:
            matched.amount = flt(paid_amount)
        else:
            inv.append("payments", {
                "mode_of_payment": mode_of_payment,
                "amount": flt(paid_amount),
            })

    inv.flags.ignore_permissions = False
    inv.insert()
    inv.submit()

    return _pickup_invoice_response(inv, status="ok")


def _pickup_invoice_response(inv, status="ok"):
    from urllib.parse import quote
    print_format = "Custom Sales Invoice"
    try:
        # Keep pickup flow print behavior aligned with the main POS payment/share flow.
        from ch_pos.api.share_api import _resolve_print_format

        print_format = _resolve_print_format(inv.name) or print_format
    except Exception:
        if getattr(inv, "custom_gofix_service_request", None):
            print_format = "GoFix Service Invoice"
    return {
        "status": status,
        "name": inv.name,
        "docstatus": inv.docstatus,
        "grand_total": flt(inv.grand_total),
        "outstanding_amount": flt(inv.outstanding_amount),
        "paid_amount": flt(inv.paid_amount),
        "customer": inv.customer,
        "customer_name": inv.customer_name,
        "print_format": print_format,
        "print_url": "/printview?doctype=Sales%20Invoice"
                     f"&name={quote(inv.name)}"
                     f"&format={quote(print_format)}&no_letterhead=0",
    }


@frappe.whitelist()
def get_customer_full_details(customer, **kwargs):
    """Fetch fresh customer details with robust address lookup."""
    if not customer:
        return {}

    # ⚡ Clear cache first to guarantee fresh data
    frappe.clear_document_cache("Customer", customer)

    # Use db.get_value to read directly from DB (no cache)
    cust_data = frappe.db.get_value("Customer", customer, [
        "name", "customer_name", "customer_group", "customer_type",
        "email_id", "mobile_no",
        "ch_whatsapp_number", "ch_alternate_phone",
        "pan", "ch_pan_number", "gstin", "tax_id",
        "customer_primary_address"
    ], as_dict=True)

    if not cust_data:
        return {}

    result = {
        "name": cust_data.name,
        "customer_name": cust_data.customer_name,
        "customer_group": cust_data.customer_group,
        "customer_type": cust_data.customer_type,
        "email_id": cust_data.email_id,
        "mobile_no": cust_data.mobile_no,
        "whatsapp_no": cust_data.ch_whatsapp_number,
        "alternate_no": cust_data.ch_alternate_phone,
        "pan": cust_data.pan or cust_data.ch_pan_number,
        "gstin": cust_data.gstin or cust_data.tax_id,
    }

    # ── BILLING ADDRESS: Try customer_primary_address first ──
    billing_addr_name = cust_data.customer_primary_address

    # Fallback: Find any billing address linked to customer
    if not billing_addr_name:
        existing = frappe.db.sql("""
            SELECT addr.name
            FROM `tabAddress` addr
            INNER JOIN `tabDynamic Link` dl ON dl.parent = addr.name
            WHERE dl.link_doctype = 'Customer'
              AND dl.link_name = %s
              AND dl.parenttype = 'Address'
              AND addr.address_type = 'Billing'
            ORDER BY addr.is_primary_address DESC, addr.creation DESC
            LIMIT 1
        """, customer, as_dict=True)
        if existing:
            billing_addr_name = existing[0].name
            # Also set as customer_primary_address for next time
            frappe.db.set_value(
                "Customer", customer, "customer_primary_address", billing_addr_name,
                update_modified=False
            )

    if billing_addr_name and frappe.db.exists("Address", billing_addr_name):
        # Read directly from DB to bypass cache
        billing = frappe.db.get_value("Address", billing_addr_name, [
            "address_line1", "address_line2", "city", "state", "pincode", "country"
        ], as_dict=True)
        if billing:
            result.update({
                "address_line1": billing.address_line1,
                "address_line2": billing.address_line2,
                "city": billing.city,
                "state": billing.state,
                "pincode": billing.pincode,
                "country": billing.country,
                "billing_address_name": billing_addr_name,
            })

    # ── SHIPPING ADDRESS ──
    shipping_existing = frappe.db.sql("""
        SELECT addr.name
        FROM `tabAddress` addr
        INNER JOIN `tabDynamic Link` dl ON dl.parent = addr.name
        WHERE dl.link_doctype = 'Customer'
          AND dl.link_name = %s
          AND dl.parenttype = 'Address'
          AND addr.address_type = 'Shipping'
        ORDER BY addr.modified DESC
        LIMIT 1
    """, customer, as_dict=True)

    if shipping_existing:
        shipping = frappe.db.get_value("Address", shipping_existing[0].name, [
            "address_line1", "address_line2", "city", "state", "pincode"
        ], as_dict=True)
        if shipping:
            result.update({
                "shipping_address_line1": shipping.address_line1,
                "shipping_address_line2": shipping.address_line2,
                "shipping_city": shipping.city,
                "shipping_state": shipping.state,
                "shipping_pincode": shipping.pincode,
                "shipping_address_name": shipping_existing[0].name,
                "ship_to_same_as_billing": 0,
            })
        else:
            result["ship_to_same_as_billing"] = 1
    else:
        result["ship_to_same_as_billing"] = 1

    return result
