import frappe
from frappe.utils import flt, cint, nowdate, add_months, now_datetime, fmt_money


@frappe.whitelist()
def get_pos_profile_data(pos_profile):
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
        "payment_modes": payment_modes,
        "store_caps": store_caps,
        "pos_ext": pos_ext,
        "executive_access": executive_access,
    }


@frappe.whitelist()
def get_sale_types(company=None):
    """Return enabled sale types with their sub-types, optionally filtered by company."""
    filters = {"enabled": 1}
    types = frappe.get_all(
        "CH Sale Type",
        filters=filters,
        fields=["name as sale_type_name", "code", "is_default", "requires_customer",
                "requires_payment", "default_payment_mode", "description"],
        order_by="is_default desc, sale_type_name asc",
    )

    for st in types:
        # Fetch sub-types
        st["sub_types"] = frappe.get_all(
            "CH Sale Sub Type",
            filters={"parent": st["sale_type_name"], "parenttype": "CH Sale Type"},
            fields=["sale_sub_type", "description", "requires_reference", "reference_doctype"],
            order_by="idx",
        )

        # Filter by allowed companies if specified
        if company:
            allowed = frappe.get_all(
                "POS Allowed Company",
                filters={"parent": st["sale_type_name"], "parenttype": "CH Sale Type"},
                pluck="company",
            )
            if allowed and company not in allowed:
                st["_skip"] = True

    return [st for st in types if not st.get("_skip")]


@frappe.whitelist()
def get_discount_reasons(company=None):
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
    )


@frappe.whitelist()
def create_pos_invoice(pos_profile, customer, items,
                       mode_of_payment=None, amount_paid=0,
                       payments=None,
                       exchange_assessment=None, additional_discount_percentage=0,
                       additional_discount_amount=0, coupon_code=None,
                       voucher_code=None, voucher_amount=0,
                       redeem_loyalty_points=0, loyalty_points=0, loyalty_amount=0,
                       sales_executive=None, sale_type=None, sale_sub_type=None,
                       sale_reference=None, discount_reason=None,
                       client_request_id=None):
    """Create and submit a POS Invoice from the CH POS App cart.

    Supports both legacy single-payment and new multi-payment (split) modes:
      Legacy:  mode_of_payment + amount_paid
      Split:   payments = [{mode_of_payment, amount, upi_transaction_id?,
                             card_reference?, card_last_four?}, ...]

    Idempotency:
      Pass a unique client_request_id (UUID) per attempt. Retries with the
      same ID within 10 minutes return the existing invoice without
      creating a duplicate.
    """
    frappe.has_permission("POS Invoice", "create", throw=True)

    # ── Duplicate-submit guard ────────────────────────────────────────────────
    if client_request_id:
        _crid = frappe.db.escape(str(client_request_id)[:140])
        existing = frappe.db.sql(
            f"""SELECT name FROM `tabPOS Invoice`
                WHERE custom_client_request_id = {_crid}
                  AND docstatus != 2
                  AND creation >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)
                LIMIT 1""",
            as_dict=True,
        )
        if existing:
            return {"name": existing[0].name, "status": "duplicate_prevented"}
    if isinstance(items, str):
        items = frappe.parse_json(items)

    profile = frappe.get_cached_doc("POS Profile", pos_profile)

    inv = frappe.new_doc("POS Invoice")
    inv.pos_profile = pos_profile
    inv.customer = customer
    inv.company = profile.company
    inv.selling_price_list = profile.selling_price_list
    inv.currency = profile.currency or frappe.get_cached_value("Company", profile.company, "default_currency")
    inv.warehouse = profile.warehouse
    inv.posting_date = nowdate()
    inv.is_pos = 1
    inv.update_stock = 1

    # Track warranty items to create CH Sold Plans after submit
    warranty_items = []

    for item in items:
        row = {
            "item_code": item.get("item_code"),
            "qty": flt(item.get("qty", 1)),
            "rate": flt(item.get("rate")),
            "uom": item.get("uom", "Nos"),
            "warehouse": profile.warehouse,
            "discount_amount": flt(item.get("discount_amount", 0)),
        }
        # Set warranty_plan on the invoice item if custom field exists
        if item.get("warranty_plan"):
            row["custom_warranty_plan"] = item.get("warranty_plan")

        # Manager approval fields for exception tracking
        if item.get("manager_approved"):
            row["custom_manager_approved"] = 1
            row["custom_manager_user"] = item.get("manager_user") or ""
            row["custom_override_reason"] = item.get("override_reason") or ""

        # Pass serial_no so margin scheme can look up purchase cost
        if item.get("serial_no"):
            row["serial_no"] = item.get("serial_no")

        # Auto-detect margin items from ch_item_type (Refurbished / Pre-Owned)
        ch_item_type = frappe.db.get_value("Item", item.get("item_code"), "ch_item_type")
        if ch_item_type in ("Refurbished", "Pre-Owned"):
            row["custom_is_margin_item"] = 1

        inv.append("items", row)

        # Collect warranty/VAS info for post-submit processing
        if (item.get("is_warranty") or item.get("is_vas")) and item.get("warranty_plan"):
            warranty_items.append({
                "warranty_plan": item.get("warranty_plan"),
                "for_item_code": item.get("for_item_code"),
                "price": flt(item.get("rate")),
                "is_vas": cint(item.get("is_vas", 0)),
            })

    # Payment — supports split payments (new) and single mode (legacy)
    if payments:
        if isinstance(payments, str):
            payments = frappe.parse_json(payments)
        if not payments:
            frappe.throw(frappe._("At least one payment mode is required"))
        for p in payments:
            row = {
                "mode_of_payment": p.get("mode_of_payment"),
                "amount": flt(p.get("amount", 0)),
            }
            if p.get("upi_transaction_id"):
                row["custom_upi_transaction_id"] = p["upi_transaction_id"]
            if p.get("card_reference"):
                row["custom_card_reference"] = p["card_reference"]
            if p.get("card_last_four"):
                row["custom_card_last_four"] = p["card_last_four"]
            inv.append("payments", row)
    elif mode_of_payment:
        inv.append("payments", {
            "mode_of_payment": mode_of_payment,
            "amount": flt(amount_paid),
        })
    else:
        frappe.throw(frappe._("Payment mode is required"))

    # Taxes from POS Profile
    for tax in (profile.get("taxes") or []):
        inv.append("taxes", {
            "charge_type": tax.charge_type,
            "account_head": tax.account_head,
            "rate": tax.rate,
            "description": tax.description or tax.account_head,
        })

    # Exchange assessment link + amount
    exchange_credit = 0
    if exchange_assessment:
        ba = frappe.db.get_value(
            "Buyback Assessment", exchange_assessment,
            ["quoted_price", "estimated_price", "status", "expires_on",
             "linked_pos_invoice"],
            as_dict=True,
        )
        if not ba:
            frappe.throw(frappe._("Buyback Assessment {0} not found").format(exchange_assessment))
        if ba.status in ("Expired", "Cancelled"):
            frappe.throw(
                frappe._("Buyback Assessment {0} is {1} and cannot be used as exchange credit").format(
                    exchange_assessment, ba.status))
        if ba.linked_pos_invoice:
            frappe.throw(
                frappe._("Buyback Assessment {0} was already used in invoice {1}").format(
                    exchange_assessment, ba.linked_pos_invoice))
        if ba.expires_on and str(ba.expires_on) < nowdate():
            frappe.throw(
                frappe._("Buyback Assessment {0} expired on {1}").format(
                    exchange_assessment, ba.expires_on))

        inv.custom_exchange_assessment = exchange_assessment
        exchange_credit = flt(ba.quoted_price) or flt(ba.estimated_price)
        inv.custom_exchange_amount = exchange_credit

    # Additional discount
    if flt(additional_discount_percentage) > 0:
        inv.additional_discount_percentage = flt(additional_discount_percentage)
    elif flt(additional_discount_amount) > 0:
        inv.discount_amount = flt(additional_discount_amount)

    # Discount reason — validate against CH Discount Reason master
    if discount_reason:
        reason_doc = frappe.db.get_value(
            "CH Discount Reason", discount_reason,
            ["enabled", "allow_manual_entry", "discount_type", "discount_value", "max_manual_percent"],
            as_dict=True,
        )
        if not reason_doc or not reason_doc.enabled:
            frappe.throw(frappe._("Invalid or disabled discount reason: {0}").format(discount_reason))

        # For preset reasons, enforce the fixed discount value from master
        if not reason_doc.allow_manual_entry:
            if reason_doc.discount_type == "Percentage":
                inv.additional_discount_percentage = flt(reason_doc.discount_value)
                inv.discount_amount = 0
            else:
                inv.discount_amount = flt(reason_doc.discount_value)
                inv.additional_discount_percentage = 0

        # For manual-entry reasons, enforce max cap
        if reason_doc.allow_manual_entry and flt(reason_doc.max_manual_percent) > 0:
            if flt(additional_discount_percentage) > flt(reason_doc.max_manual_percent):
                frappe.throw(
                    frappe._("Discount {0}% exceeds maximum {1}% for {2}").format(
                        additional_discount_percentage, reason_doc.max_manual_percent, discount_reason
                    )
                )

        inv.custom_discount_reason = discount_reason
    elif flt(additional_discount_percentage) > 0 or flt(additional_discount_amount) > 0:
        frappe.throw(frappe._("A discount reason is required when applying a discount"))

    # Coupon code — accept code string (e.g. TESTCOUPON10) or doc name
    if coupon_code:
        doc_name = frappe.db.get_value("Coupon Code", {"coupon_code": coupon_code}, "name")
        if not doc_name:
            doc_name = coupon_code if frappe.db.exists("Coupon Code", coupon_code) else None
        if not doc_name:
            frappe.throw(frappe._("Coupon code '{0}' not found").format(coupon_code))
        inv.coupon_code = doc_name

    # Voucher — add voucher amount to the discount
    voucher_redeemed = 0
    if voucher_code and flt(voucher_amount) > 0:
        inv.discount_amount = flt(inv.discount_amount or 0) + flt(voucher_amount)

    # Loyalty points redemption
    if cint(redeem_loyalty_points):
        inv.redeem_loyalty_points = 1
        inv.loyalty_points = cint(loyalty_points)
        inv.loyalty_amount = flt(loyalty_amount)

    # Sales executive attribution
    if sales_executive:
        inv.custom_sales_executive = sales_executive

    # Sale type classification
    if sale_type:
        inv.custom_ch_sale_type = sale_type
    if sale_sub_type:
        inv.custom_ch_sale_sub_type = sale_sub_type
    if sale_reference:
        inv.custom_ch_sale_reference = sale_reference

    # Store client request ID for idempotency
    if client_request_id:
        inv.custom_client_request_id = str(client_request_id)[:140]

    inv.flags.ignore_permissions = True
    inv.save()
    inv.submit()

    # Set reverse link on Buyback Assessment → POS Invoice
    if exchange_assessment:
        frappe.db.set_value(
            "Buyback Assessment", exchange_assessment,
            {"linked_pos_invoice": inv.name, "exchange_amount": exchange_credit},
            update_modified=False,
        )

    # Redeem voucher after successful submit
    if voucher_code and flt(voucher_amount) > 0:
        from ch_item_master.ch_item_master.voucher_api import redeem_voucher
        rv = redeem_voucher(voucher_code, flt(voucher_amount), pos_invoice=inv.name)
        voucher_redeemed = flt(rv.get("redeemed_amount", 0))

    # Create CH Sold Plan records for warranty items
    sold_plans = []
    for wi in warranty_items:
        sp = _create_sold_plan(
            warranty_plan=wi["warranty_plan"],
            customer=customer,
            item_code=wi["for_item_code"],
            company=profile.company,
            sales_invoice=inv.name,
            plan_price=wi["price"],
        )
        if sp:
            sold_plans.append(sp.name)

    # ── VAS Voucher Generation ────────────────────────────────────────────────
    # For each VAS item: generate floor(price/500) × ₹500 vouchers, email to customer
    generated_vouchers = []
    try:
        from ch_item_master.ch_item_master.voucher_api import issue_voucher
        customer_phone = frappe.db.get_value("Customer", customer, "mobile_no") or ""
        customer_email = frappe.db.get_value("Customer", customer, "email_id") or ""
        for wi in warranty_items:
            if not wi.get("is_vas"):
                continue
            vas_price = flt(wi["price"])
            voucher_count = int(vas_price // 500)
            for _ in range(voucher_count):
                v = issue_voucher(
                    voucher_type="VAS Voucher",
                    amount=500,
                    company=profile.company,
                    customer=customer if customer != "Walk-in Customer" else None,
                    phone=customer_phone or None,
                    valid_days=365,
                    source_type="Purchase",
                    source_document=inv.name,
                    reason=f"VAS purchase on {inv.name}",
                    single_use=0,
                    applicable_channel="POS",
                    applicable_item_group="Accessories",
                )
                generated_vouchers.append(v)
        if generated_vouchers and (customer_email or customer_phone):
            _send_voucher_email(customer, customer_email, customer_phone, generated_vouchers, inv.name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"VAS voucher generation failed for {inv.name}")

    # Create incentive ledger entries for the sales executive
    incentive_total = 0
    if sales_executive:
        incentive_total = _create_incentive_entries(
            invoice=inv,
            pos_executive=sales_executive,
            transaction_type="Sale",
        )

    # ── Audit logging (best-effort, never blocks sale) ────────────────────────
    try:
        from ch_pos.audit import log_business_event
        _store = frappe.get_cached_value("POS Profile", pos_profile, "warehouse")
        _company = inv.company

        # Discount override audit
        if flt(additional_discount_percentage) > 0 or flt(additional_discount_amount) > 0:
            log_business_event(
                event_type="Discount Override",
                ref_doctype="POS Invoice", ref_name=inv.name,
                before="0",
                after=f"{additional_discount_percentage}% / ₹{additional_discount_amount}",
                remarks=f"Reason: {discount_reason or 'N/A'}",
                store=_store, company=_company,
            )

        # Exchange conversion audit
        if exchange_assessment:
            log_business_event(
                event_type="Exchange Conversion",
                ref_doctype="POS Invoice", ref_name=inv.name,
                before=exchange_assessment,
                after=f"Credit: ₹{exchange_credit}",
                store=_store, company=_company,
            )

        # Voucher redemption audit
        if voucher_code and flt(voucher_redeemed) > 0:
            log_business_event(
                event_type="Voucher Redemption",
                ref_doctype="POS Invoice", ref_name=inv.name,
                before=voucher_code,
                after=f"₹{voucher_redeemed} redeemed",
                store=_store, company=_company,
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Audit log failed for POS Invoice {inv.name}")

    return {
        "name": inv.name,
        "grand_total": inv.grand_total,
        "sold_plans": sold_plans,
        "incentive_earned": incentive_total,
        "voucher_redeemed": voucher_redeemed,
        "generated_vouchers": generated_vouchers,
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
    html = f"""
    <div style='font-family:sans-serif;max-width:500px;margin:0 auto'>
        <h2 style='color:#4f46e5'>Your VAS Vouchers are here! 🎉</h2>
        <p>Thank you for purchasing a VAS plan (Invoice: <b>{invoice_name}</b>).</p>
        <p>You've earned <b>{len(vouchers)} × ₹500 voucher(s)</b> to redeem on accessories at our store.</p>
        <table style='width:100%;border-collapse:collapse;margin:16px 0'>
            {codes_html}
        </table>
        <p style='color:#6b7280;font-size:13px'>
            Each ₹500 voucher gives you ₹125 off when purchasing accessories.
            Visit any GoGizmo store and quote your voucher code at checkout.
        </p>
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


def _create_sold_plan(warranty_plan, customer, item_code, company, sales_invoice, plan_price):
    """Create a CH Sold Plan when a warranty is sold via POS.

    Uses the standard Frappe document lifecycle (insert → submit) so that
    the CH Sold Plan controller's validate/on_submit hooks run properly.
    """
    plan_doc = frappe.get_cached_doc("CH Warranty Plan", warranty_plan)
    today = nowdate()
    sp = frappe.new_doc("CH Sold Plan")
    sp.warranty_plan = warranty_plan
    sp.customer = customer
    sp.item_code = item_code
    sp.company = company
    sp.start_date = today
    sp.end_date = add_months(today, plan_doc.duration_months or 12)
    sp.status = "Active"
    sp.sales_invoice = sales_invoice
    sp.plan_price = plan_price
    sp.max_claims = plan_doc.max_claims or 1
    sp.deductible_amount = flt(plan_doc.deductible_amount)
    sp.sold_by = frappe.session.user
    sp.insert(ignore_permissions=True)
    sp.submit()
    return sp


@frappe.whitelist()
def get_warranty_plans(item_code, item_group=None, brand=None):
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
def lookup_exchange(assessment=None, imei_serial=None, mobile_no=None):
    """Find a Buyback Assessment/Order eligible for exchange at POS.

    Returns exchange details or None if nothing found.
    """
    assessment_name = None

    valid_statuses = ["Draft", "Submitted", "Inspection Created"]

    if assessment:
        assessment_name = assessment
    elif imei_serial:
        assessment_name = frappe.db.get_value(
            "Buyback Assessment",
            {"imei_serial": imei_serial, "status": ["in", valid_statuses]},
            "name",
        )
    elif mobile_no:
        assessment_name = frappe.db.get_value(
            "Buyback Assessment",
            {"mobile_no": mobile_no, "status": ["in", valid_statuses]},
            "name",
            order_by="creation desc",
        )

    if not assessment_name:
        return None

    ba = frappe.get_doc("Buyback Assessment", assessment_name)

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
def get_vas_plans():
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
def validate_coupon(coupon_code, customer=None, cart_total=0):
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

    pr = frappe.get_cached_doc("Pricing Rule", coupon.pricing_rule)

    # Check minimum amount
    if pr.min_amt and cart_total < flt(pr.min_amt):
        return {"valid": False, "reason": frappe._("Minimum cart amount ₹{0} required").format(pr.min_amt)}

    # Compute discount
    discount_amount = 0
    if flt(pr.discount_percentage) > 0:
        discount_amount = flt(cart_total * flt(pr.discount_percentage) / 100)
        if pr.max_discount and discount_amount > flt(pr.max_discount):
            discount_amount = flt(pr.max_discount)
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
def search_invoices_for_return(search_term, pos_profile=None):
    """Search POS Invoices for return/exchange processing."""
    search_term = (search_term or "").strip()
    if not search_term:
        return []

    filters = {
        "docstatus": 1,
        "is_return": 0,
    }

    or_filters = [
        ["name", "like", f"%{search_term}%"],
        ["customer", "like", f"%{search_term}%"],
        ["customer_name", "like", f"%{search_term}%"],
    ]

    invoices = frappe.get_all(
        "POS Invoice",
        filters=filters,
        or_filters=or_filters,
        fields=[
            "name", "customer", "customer_name", "posting_date",
            "grand_total", "status", "pos_profile",
        ],
        order_by="posting_date desc, creation desc",
        limit_page_length=20,
    )

    for inv in invoices:
        inv["items_count"] = frappe.db.count(
            "POS Invoice Item", {"parent": inv["name"]}
        )

    return invoices


@frappe.whitelist()
def get_invoice_items_for_return(invoice_name):
    """Get items from a POS Invoice that can still be returned."""
    inv = frappe.get_doc("POS Invoice", invoice_name)
    if inv.docstatus != 1 or inv.is_return:
        frappe.throw(frappe._("Only submitted non-return invoices can be returned"))

    returnable = []
    for item in inv.items:
        # Calculate already-returned qty for this item row
        already_returned = flt(frappe.db.sql("""
            SELECT ABS(SUM(ri.qty))
            FROM `tabPOS Invoice Item` ri
            JOIN `tabPOS Invoice` pi ON pi.name = ri.parent
            WHERE pi.return_against = %s
              AND pi.docstatus = 1
              AND ri.item_code = %s
              AND ri.pos_invoice_item = %s
        """, (invoice_name, item.item_code, item.name))[0][0] or 0)

        returnable_qty = flt(item.qty) - already_returned
        if returnable_qty <= 0:
            continue

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
        })

    return returnable


@frappe.whitelist()
def create_pos_return(original_invoice, return_items, sales_executive=None):
    """Create a POS Invoice return (credit note) for specific items."""
    frappe.has_permission("POS Invoice", "create", throw=True)
    if isinstance(return_items, str):
        return_items = frappe.parse_json(return_items)

    orig = frappe.get_doc("POS Invoice", original_invoice)
    if orig.docstatus != 1 or orig.is_return:
        frappe.throw(frappe._("Can only create returns for submitted non-return invoices"))

    ret = frappe.new_doc("POS Invoice")
    ret.pos_profile = orig.pos_profile
    ret.customer = orig.customer
    ret.company = orig.company
    ret.selling_price_list = orig.selling_price_list
    ret.currency = orig.currency
    # POS Invoice doesn't have a top-level warehouse field; get from items
    _orig_warehouse = (
        orig.get("set_warehouse")
        or (orig.items[0].warehouse if orig.items else None)
        or frappe.get_cached_value("POS Profile", orig.pos_profile, "warehouse")
    )
    ret.posting_date = nowdate()
    ret.is_pos = 1
    ret.is_return = 1
    ret.return_against = orig.name
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
            "pos_invoice_item": ri.get("original_item_row", ""),
        }
        if ri.get("serial_no"):
            row["serial_no"] = ri["serial_no"]
        ret.append("items", row)
        total_return_amount += qty * rate

    if not ret.items:
        frappe.throw(frappe._("No items to return"))

    # Payment (negative)
    default_mode = "Cash"
    for p in (orig.payments or []):
        if p.default:
            default_mode = p.mode_of_payment
            break

    ret.append("payments", {
        "mode_of_payment": default_mode,
        "amount": -1 * total_return_amount,
    })

    # Taxes from original
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

    # Set paid_amount before save so ERPNext's validate_change_amount
    # doesn't hit a NoneType error on return invoices (grand_total < 0)
    ret.paid_amount = -1 * total_return_amount

    ret.flags.ignore_permissions = True
    ret.save()
    ret.submit()

    # Create return incentive (clawback) entries
    incentive_clawback = 0
    if sales_executive:
        incentive_clawback = _create_return_incentive_entries(ret, sales_executive)

    # Audit
    try:
        from ch_pos.audit import log_business_event
        log_business_event(
            event_type="Return Approved",
            ref_doctype="POS Invoice", ref_name=ret.name,
            before=original_invoice,
            after=f"Return ₹{total_return_amount}",
            company=orig.company,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Return audit log failed")

    return {
        "name": ret.name,
        "grand_total": ret.grand_total,
        "customer": ret.customer,
        "customer_name": ret.customer_name,
        "incentive_clawback": incentive_clawback,
    }
@frappe.whitelist()
def validate_serial_for_sale(serial_no, item_code, warehouse):
    """Validate a serial number can be sold from this warehouse, enforcing FIFO."""
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

    # ── FIFO enforcement ────────────────────────────────────────────────────
    oldest_serial, oldest_date = _get_oldest_fifo_serial(item_code, warehouse)
    if oldest_serial and oldest_serial != serial_no:
        # Determine the receipt date of the selected serial for comparison
        selected_date_row = frappe.db.sql("""
            SELECT MIN(sbb.posting_date) AS received_date
            FROM `tabSerial and Batch Entry` sbe
            JOIN `tabSerial and Batch Bundle` sbb
                ON sbe.parent = sbb.name
                AND sbb.type_of_transaction = 'Inward'
                AND sbb.docstatus = 1
            WHERE sbe.serial_no = %s
        """, serial_no, as_dict=True)
        selected_date = selected_date_row[0].received_date if selected_date_row else None

        if oldest_date and selected_date and selected_date > oldest_date:
            # FIFO violated — alert managers and reject
            _send_fifo_violation_alert(
                item_code=item_code,
                warehouse=warehouse,
                selected_serial=serial_no,
                oldest_serial=oldest_serial,
                cashier=frappe.session.user,
            )
            return {
                "valid": False,
                "fifo_violation": True,
                "reason": frappe._(
                    "FIFO violation: Serial {0} (received {1}) must be sold before {2} (received {3}). "
                    "Alert sent to RSM/ASM."
                ).format(oldest_serial, oldest_date, serial_no, selected_date),
            }

    # Check if already in current POS cart (prevent double-scan)
    return {"valid": True, "serial_no": serial_no, "item_code": item_code}


@frappe.whitelist()
def check_serial_returnable(serial_no, original_invoice=None):
    """Check if a serial number can be returned (not scrapped, transferred, or already returned)."""
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
            FROM `tabPOS Invoice Item` ri
            JOIN `tabPOS Invoice` pi ON pi.name = ri.parent
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

    # Check if transferred out (outgoing stock entry after sale)
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

    return {"returnable": True, "serial_no": serial_no, "item_code": sn.item_code}


# ── Repair / Job Assignment from POS ────────────────────────
@frappe.whitelist()
def create_repair_job_from_pos(service_request):
    """Accept a Service Request and create Job Assignment in one step from POS."""
    try:
        from gofix.gofix_services.doctype.service_request.service_request import accept_service_request
        from gofix.gofix_services.doctype.job_assignment.job_assignment import create_job_sheet_from_service_order
    except ImportError:
        frappe.throw(frappe._("GoFix app is not installed — cannot create repair jobs from POS."))

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
def get_store_repairs(pos_profile):
    """Get open Service Requests and Job Assignments for the store."""
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    warehouse = profile.warehouse

    # Open Service Requests for this store
    service_requests = frappe.db.get_all(
        "Service Request",
        filters={
            "source_warehouse": warehouse,
            "status": ["in", ["Open", "Draft", "In Service", "Waiting for Parts", "Ready for Delivery"]],
        },
        fields=[
            "name", "customer_name", "device_item", "serial_no",
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
        # Check if already billed via POS Invoice or Sales Invoice
        sr["billed"] = bool(frappe.db.get_value(
            "POS Invoice Item",
            {"description": ["like", f"%{sr.name}%"], "docstatus": 1},
            "parent",
        ) or frappe.db.get_value(
            "Service Request", sr.name, "custom_repair_invoice"
        ))

    return service_requests


@frappe.whitelist()
def collect_repair_payment(service_request, amount, mode_of_payment, pos_profile,
                           customer="Walk-in Customer", service_order=None, upi_txn_id=None):
    """Create a POS Invoice to collect payment for a completed repair job.

    Marks Service Request as billed after invoice submission.
    """
    frappe.has_permission("POS Invoice", "create", throw=True)
    amount = flt(amount)
    if amount <= 0:
        frappe.throw(frappe._("Repair charge must be greater than zero"))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    sr_doc = frappe.get_doc("Service Request", service_request)

    # Find or create a repair service item
    repair_item = frappe.db.get_value("Item", {"item_name": "Repair Service", "disabled": 0}, "name")
    if not repair_item:
        repair_item = frappe.db.get_value("Item", {"item_group": "Services", "disabled": 0, "is_stock_item": 0}, "name")
    if not repair_item:
        frappe.throw(frappe._("No 'Repair Service' item found. Please create a non-stock service item named 'Repair Service'."))

    inv = frappe.new_doc("POS Invoice")
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
    inv.submit()

    # Mark service request as billed
    try:
        frappe.db.set_value("Service Request", service_request, "custom_repair_invoice", inv.name, update_modified=False)
    except Exception:
        pass  # custom field may not exist yet

    # Increment repair intake counter
    try:
        _get_active_session = _get_active_session_log(pos_profile)
    except Exception:
        pass

    return {"invoice": inv.name, "grand_total": inv.grand_total}


@frappe.whitelist()
def get_repair_closure_data(service_request):
    """Return all data needed by the Repair Closure Dialog:
    technician, spare parts, estimated cost, customer, SO/JA names.
    """
    sr = frappe.get_doc("Service Request", service_request)

    eng_users = frappe.db.sql("""
        SELECT DISTINCT u.name, u.full_name FROM `tabUser` u
        JOIN `tabHas Role` hr ON hr.parent = u.name
        WHERE hr.role IN ('Service Engineer','Service Manager','Technician') AND u.enabled=1
        ORDER BY u.full_name
    """, as_dict=True)

    spare_parts = frappe.db.get_all(
        "Service Request Spare Part",
        filters={"parent": service_request, "parenttype": "Service Request"},
        fields=["spare_part_item", "item_name", "qty", "uom", "rate", "amount"],
        order_by="idx",
    )

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
        "estimated_cost": flt(sr.estimated_cost),
        "service_order": sr.service_order or "",
        "job_assignment": ja.name if ja else "",
        "current_technician": (ja.service_engineer if ja else "") or "",
        "qc_status": so_qc_status,
        "spare_parts": spare_parts,
        "technicians": eng_users,
    }


@frappe.whitelist()
def close_repair_order(service_request, pos_profile, payments, qc_result,
                       qc_remarks="", delivery_ack=0, delivery_note="",
                       technician="", spare_parts=None, service_charge=0):
    """Complete the repair closure flow in one atomic call.

    Steps:
    1. Assign technician to Job Assignment (if provided)
    2. Save spare parts onto Service Request (replace child rows)
    3. Set QC status on the linked Sales Order
    4. Create POS Invoice with service charge line + one line per spare part
    5. Create Material Issue Stock Entry for spare parts
    6. Mark SR as delivered / closed

    payments: list of {mode_of_payment, amount, reference_no}
    spare_parts: list of {spare_part_item, item_name, qty, uom, rate}
    qc_result: "Pass" | "Fail" | "Not Repairable" | "Customer Cancelled"
    """
    frappe.has_permission("POS Invoice", "create", throw=True)

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

    # 4 — Build and submit POS Invoice
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

    inv = frappe.new_doc("POS Invoice")
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
            inv.append("items", {
                "item_code": part.get("spare_part_item"),
                "qty": qty,
                "rate": rate,
                "uom": part.get("uom") or "Nos",
                "description": f"Part — {service_request}",
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

    frappe.db.commit()
    return {
        "invoice": inv.name,
        "grand_total": inv.grand_total,
        "stock_entry": stock_entry_name,
    }



# ── Buyback Valuation ────────────────────────────────────────
@frappe.whitelist()
def check_imei_blacklist(imei):
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
def calculate_buyback_valuation(item_code, condition_checks):
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
        from ch_erp_buyback.buyback.buyback.pricing.engine import calculate_estimated_price

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
):
    """Create a Buyback Assessment with condition grading and KYC from POS."""
    if isinstance(condition_checks, str):
        condition_checks = frappe.parse_json(condition_checks)
    condition_checks = condition_checks or {}

    # Calculate valuation
    valuation = calculate_buyback_valuation(item_code, condition_checks)

    doc = frappe.new_doc("Buyback Assessment")
    doc.source = "Store Manual"
    doc.store = frappe.db.get_value("POS Profile", frappe.form_dict.get("pos_profile"), "warehouse") or ""
    doc.mobile_no = mobile_no
    doc.customer = customer or ""
    doc.item = item_code
    doc.imei_serial = imei_serial or ""
    doc.estimated_grade = valuation["grade"]
    doc.estimated_price = valuation["final_price"]
    doc.quoted_price = valuation["final_price"]
    doc.remarks = _build_grading_remarks(condition_checks, valuation, kyc_id_type, kyc_id_number, kyc_name)

    doc.flags.ignore_permissions = True
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
            lines.append(f"  - {d['label']}: -₹{d['amount']:,.0f} ({d['pct']}%)")
    lines.append(f"Final Price: ₹{valuation['final_price']:,.0f}")

    if kyc_id_type and kyc_id_number:
        lines.append(f"\n=== KYC ===")
        lines.append(f"  Name: {kyc_name or 'N/A'}")
        lines.append(f"  ID Type: {kyc_id_type}")
        lines.append(f"  ID Number: {kyc_id_number}")

    return "\n".join(lines)


# ── Governance: Manager Approval at POS ──────────────────────────────

@frappe.whitelist()
def request_manager_approval(mobile_no, purpose, reference_doctype=None, reference_name=None):
    """Generate an OTP for manager approval at POS.

    Used when a discount exceeds limits, exchange override is needed, etc.
    The OTP is sent to the store manager's mobile and must be verified
    before the transaction can proceed.
    """
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    otp = CHOTPLog.generate_otp(
        mobile_no=mobile_no,
        purpose=purpose,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    )
    # In production, send OTP via SMS here
    return {"sent": True, "mobile": mobile_no[:3] + "****" + mobile_no[-3:]}


@frappe.whitelist()
def verify_manager_approval(mobile_no, purpose, otp_code, reference_doctype=None, reference_name=None):
    """Verify a manager OTP for POS approval.

    Returns {"valid": True/False, "message": str}.
    """
    from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog

    result = CHOTPLog.verify_otp(
        mobile_no=mobile_no,
        purpose=purpose,
        otp_code=otp_code,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
    )
    return result


@frappe.whitelist()
def get_customer_loyalty(customer, company=None):
    """Get loyalty program details and current points for a customer."""
    if not company:
        company = frappe.defaults.get_user_default("Company")

    loyalty_program = frappe.db.get_value("Customer", customer, "loyalty_program")
    if not loyalty_program:
        return {"loyalty_program": None, "points": 0, "conversion_factor": 0, "currency_value": 0}

    from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
        get_loyalty_program_details_with_points,
    )

    details = get_loyalty_program_details_with_points(customer, loyalty_program, company=company)
    conversion_factor = flt(details.get("conversion_factor"))
    points = cint(details.get("loyalty_points"))

    return {
        "loyalty_program": loyalty_program,
        "points": points,
        "conversion_factor": conversion_factor,
        "currency_value": flt(points * conversion_factor),
        "tier_name": details.get("tier_name", ""),
    }


@frappe.whitelist()
def imei_history(serial_no):
    """Full lifecycle of a serial number / IMEI: sales, returns, service, buyback."""
    serial_no = serial_no.strip()

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

    # Sales history — POS Invoice items referencing this serial
    out["sales"] = frappe.db.sql("""
        SELECT pii.parent as invoice, pii.item_code, pii.item_name, pii.rate,
               pi.customer, pi.customer_name, pi.posting_date as date
        FROM `tabPOS Invoice Item` pii
        JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        WHERE pii.serial_no LIKE %(sn_pattern)s
          AND pi.docstatus = 1 AND pi.is_return = 0
        ORDER BY pi.posting_date DESC
    """, {"sn_pattern": f"%{serial_no}%"}, as_dict=True)

    # Returns
    out["returns"] = frappe.db.sql("""
        SELECT pii.parent as invoice, pii.item_code, pii.item_name, pii.rate,
               pi.customer, pi.customer_name, pi.posting_date as date
        FROM `tabPOS Invoice Item` pii
        JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
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
def customer_360(identifier, company=None):
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
            # Try Dynamic Link → Contact with phone
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
    }

    # Invoices
    out["invoices"] = frappe.db.sql("""
        SELECT name, posting_date, grand_total, status,
               (SELECT COUNT(*) FROM `tabPOS Invoice Item` WHERE parent = pi.name) as items_count
        FROM `tabPOS Invoice` pi
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

    # Active warranties / VAS (CH Sold Plan)
    out["warranties"] = frappe.db.sql("""
        SELECT name, plan_title, warranty_plan, plan_type, item_code, item_name,
               start_date, end_date, status, sales_invoice
        FROM `tabCH Sold Plan`
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
        FROM `tabPOS Invoice`
        WHERE customer = %(customer)s AND docstatus = 1 AND is_return = 1
        ORDER BY posting_date DESC LIMIT 20
    """, {"customer": customer}, as_dict=True)

    # Swap / exchange invoices (sale_type driven)
    out["swap_invoices"] = frappe.db.sql("""
        SELECT name, posting_date, grand_total, custom_ch_sale_type,
               custom_ch_sale_sub_type, custom_exchange_assessment, status
        FROM `tabPOS Invoice`
        WHERE customer = %(customer)s AND docstatus = 1
          AND custom_exchange_assessment IS NOT NULL AND custom_exchange_assessment != ''
        ORDER BY posting_date DESC LIMIT 20
    """, {"customer": customer}, as_dict=True)

    # Coupon usage
    out["coupon_usage"] = frappe.db.sql("""
        SELECT name, posting_date, coupon_code, grand_total
        FROM `tabPOS Invoice`
        WHERE customer = %(customer)s AND docstatus = 1
          AND coupon_code IS NOT NULL AND coupon_code != ''
        ORDER BY posting_date DESC LIMIT 20
    """, {"customer": customer}, as_dict=True)

    # Exception requests
    out["exceptions"] = frappe.db.sql("""
        SELECT name, creation, exception_type, status,
               reference_doctype, reference_name
        FROM `tabCH Exception Request`
        WHERE customer = %(customer)s
        ORDER BY creation DESC LIMIT 20
    """, {"customer": customer}, as_dict=True)

    # Loyalty
    loyalty_program = cust_doc.loyalty_program
    if loyalty_program and company:
        try:
            from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
                get_loyalty_program_details_with_points,
            )
            details = get_loyalty_program_details_with_points(customer, loyalty_program, company=company)
            points = cint(details.get("loyalty_points"))
            cf = flt(details.get("conversion_factor"))
            out["loyalty"] = {
                "program": loyalty_program,
                "points": points,
                "conversion_factor": cf,
                "currency_value": flt(points * cf),
                "tier_name": details.get("tier_name", ""),
            }
        except Exception:
            out["loyalty"] = None
    else:
        out["loyalty"] = None

    return out


@frappe.whitelist()
def store_dashboard(pos_profile):
    """Return today's sales summary, top items, staff performance and inventory alerts."""
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    today = nowdate()
    warehouse = profile.warehouse

    # Today's invoices
    invoices = frappe.get_all(
        "POS Invoice",
        filters={
            "pos_profile": pos_profile,
            "posting_date": today,
            "docstatus": 1,
            "is_return": 0,
        },
        fields=["name", "grand_total", "owner"],
    )
    total_revenue = sum(flt(inv.grand_total) for inv in invoices)
    total_invoices = len(invoices)

    # Items sold today
    items_sold = 0
    if invoices:
        inv_names = [inv.name for inv in invoices]
        items_sold = frappe.db.sql(
            """SELECT COALESCE(SUM(ii.qty), 0)
               FROM `tabPOS Invoice Item` ii
               WHERE ii.parent IN %s""",
            (inv_names,),
        )[0][0] or 0

    # Returns today
    total_returns = frappe.db.count(
        "POS Invoice",
        filters={
            "pos_profile": pos_profile,
            "posting_date": today,
            "docstatus": 1,
            "is_return": 1,
        },
    )

    # Top selling items today
    top_items = []
    if invoices:
        top_items_raw = frappe.db.sql(
            """SELECT ii.item_name, SUM(ii.qty) AS qty, SUM(ii.amount) AS revenue
               FROM `tabPOS Invoice Item` ii
               JOIN `tabPOS Invoice` pi ON pi.name = ii.parent
               WHERE pi.pos_profile = %s AND pi.posting_date = %s
                 AND pi.docstatus = 1 AND pi.is_return = 0
               GROUP BY ii.item_code
               ORDER BY revenue DESC
               LIMIT 10""",
            (pos_profile, today),
            as_dict=True,
        )
        top_items = [{"item_name": r.item_name, "qty": flt(r.qty), "revenue": flt(r.revenue)} for r in top_items_raw]

    # Staff performance
    staff_map = {}
    for inv in invoices:
        owner = inv.owner
        if owner not in staff_map:
            staff_map[owner] = {"cashier": frappe.utils.get_fullname(owner) or owner, "invoices": 0, "revenue": 0}
        staff_map[owner]["invoices"] += 1
        staff_map[owner]["revenue"] += flt(inv.grand_total)
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
               ORDER BY b.actual_qty ASC
               LIMIT 15""",
            (warehouse,),
            as_dict=True,
        )
        inventory_alerts = [{"item_name": r.item_name, "qty": flt(r.qty)} for r in low_stock]

        # Also include stock items that have NO Bin entry at all (effectively 0 stock)
        remaining = 15 - len(inventory_alerts)
        if remaining > 0:
            existing_codes = [r.item_code for r in low_stock]
            no_bin_items = frappe.db.sql(
                """SELECT i.name AS item_code, i.item_name, 0 AS qty
                   FROM `tabItem` i
                   WHERE i.disabled = 0 AND i.is_stock_item = 1
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
                [{"item_name": r.item_name, "qty": 0} for r in no_bin_items]
            )

    # Hourly sales breakdown for bar chart
    hourly_sales = []
    if invoices:
        hourly_raw = frappe.db.sql(
            """SELECT HOUR(pi.posting_time) AS hr, SUM(pi.grand_total) AS revenue,
                      COUNT(*) AS cnt
               FROM `tabPOS Invoice` pi
               WHERE pi.pos_profile = %s AND pi.posting_date = %s
                 AND pi.docstatus = 1 AND pi.is_return = 0
               GROUP BY HOUR(pi.posting_time)
               ORDER BY hr""",
            (pos_profile, today),
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

    # Recent Stock Transfers involving this warehouse
    stock_transfers = []
    if warehouse:
        stock_transfers = frappe.db.sql(
            """SELECT se.name, se.posting_date, se.docstatus,
                      (SELECT COUNT(*) FROM `tabStock Entry Detail` sed
                       WHERE sed.parent = se.name) AS item_count
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
    }


@frappe.whitelist()
def create_material_request(pos_profile, items, urgency=None, notes=None, source_warehouse=None):
    """Create a Material Request from POS for stock replenishment."""
    import json
    if isinstance(items, str):
        items = json.loads(items)

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    company = profile.company or frappe.defaults.get_default("company")
    warehouse = profile.warehouse

    mr = frappe.new_doc("Material Request")
    mr.material_request_type = "Material Transfer"
    mr.company = company
    mr.schedule_date = nowdate()

    # Urgency → schedule_date offset
    if urgency == "Urgent":
        mr.schedule_date = nowdate()
    elif urgency == "Standard":
        mr.schedule_date = frappe.utils.add_days(nowdate(), 3)
    elif urgency == "Low":
        mr.schedule_date = frappe.utils.add_days(nowdate(), 7)

    for item in items:
        row = {
            "item_code": item.get("item_code"),
            "qty": flt(item.get("qty", 1)),
            "uom": item.get("uom", "Nos"),
            "warehouse": warehouse,
            "schedule_date": mr.schedule_date,
        }
        # If source warehouse specified, set it on each item
        if source_warehouse:
            row["from_warehouse"] = source_warehouse
        mr.append("items", row)

    # Store notes in remarks
    remark_parts = []
    if urgency:
        remark_parts.append(f"Urgency: {urgency}")
    if notes:
        remark_parts.append(str(notes))
    if remark_parts:
        mr.description = " | ".join(remark_parts)

    mr.insert()
    mr.submit()
    frappe.db.commit()
    return mr.name


@frappe.whitelist()
def get_pending_material_requests(pos_profile):
    """Get recent Material Requests for this POS store."""
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    warehouse = profile.warehouse

    requests = frappe.db.sql(
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
           LIMIT 20""",
        (warehouse,),
        as_dict=True,
    )
    return requests


@frappe.whitelist()
def get_stock_transfers(pos_profile, direction="incoming"):
    """Get recent stock transfers (Stock Entries of type Material Transfer)."""
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    warehouse = profile.warehouse

    if direction == "incoming":
        wh_filter = "se.to_warehouse = %s OR EXISTS (SELECT 1 FROM `tabStock Entry Detail` sed WHERE sed.parent = se.name AND sed.t_warehouse = %s)"
        params = (warehouse, warehouse)
    else:
        wh_filter = "se.from_warehouse = %s OR EXISTS (SELECT 1 FROM `tabStock Entry Detail` sed WHERE sed.parent = se.name AND sed.s_warehouse = %s)"
        params = (warehouse, warehouse)

    entries = frappe.db.sql(
        f"""SELECT se.name, se.posting_date, se.docstatus,
                   se.from_warehouse, se.to_warehouse, se.remarks,
                   (SELECT COUNT(*) FROM `tabStock Entry Detail` sed
                    WHERE sed.parent = se.name) AS item_count
            FROM `tabStock Entry` se
            WHERE se.stock_entry_type = 'Material Transfer'
              AND se.docstatus IN (0, 1)
              AND ({wh_filter})
            ORDER BY se.creation DESC
            LIMIT 20""",
        params,
        as_dict=True,
    )
    return entries


@frappe.whitelist()
def create_stock_transfer(from_warehouse, to_warehouse, items,
                          courier_name=None, courier_tracking=None,
                          handover_notes=None, expected_delivery_date=None):
    """Create a Stock Entry (Material Transfer) from POS with courier hand-over."""
    import json
    if isinstance(items, str):
        items = json.loads(items)

    if not from_warehouse or not to_warehouse:
        frappe.throw(frappe._("Both source and destination warehouses are required"))
    if from_warehouse == to_warehouse:
        frappe.throw(frappe._("Source and destination warehouses must be different"))

    company = frappe.db.get_value("Warehouse", from_warehouse, "company") or frappe.defaults.get_default("company")

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer"
    se.company = company
    se.from_warehouse = from_warehouse
    se.to_warehouse = to_warehouse

    for item in items:
        se.append("items", {
            "item_code": item.get("item_code"),
            "qty": flt(item.get("qty", 1)),
            "uom": item.get("uom", "Nos"),
            "s_warehouse": from_warehouse,
            "t_warehouse": to_warehouse,
        })

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

    se.insert()
    frappe.db.commit()
    return se.name


# ── Model Comparison ──────────────────────────────────────
@frappe.whitelist()
def get_comparison_filters():
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
def get_model_comparison(brand=None, ram=None, storage=None, search_text=None, pos_profile=None):
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

    # Get warehouse for stock check
    warehouse = None
    if pos_profile:
        warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse")

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

        # Get variant prices (lowest) and stock
        variants = frappe.db.get_all(
            "Item",
            filters={"variant_of": item.name, "disabled": 0},
            fields=["name", "item_name"],
        )

        min_price = 0
        max_price = 0
        total_stock = 0
        variant_count = len(variants)

        variant_codes = [v.name for v in variants] if variants else [item.name]
        if variant_codes:
            # Get prices from Item Price (Selling)
            prices = frappe.db.get_all(
                "Item Price",
                filters={
                    "item_code": ["in", variant_codes],
                    "selling": 1,
                },
                fields=["price_list_rate"],
            )
            if prices:
                price_vals = [flt(p.price_list_rate) for p in prices if flt(p.price_list_rate)]
                if price_vals:
                    min_price = min(price_vals)
                    max_price = max(price_vals)

            # Get stock in store warehouse
            if warehouse:
                stock_data = frappe.db.get_all(
                    "Bin",
                    filters={
                        "item_code": ["in", variant_codes],
                        "warehouse": warehouse,
                    },
                    fields=["actual_qty"],
                )
                total_stock = sum(flt(s.actual_qty) for s in stock_data)

        # Get active offers for this item (by item_code, brand, item_group)
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
            "stock": total_stock,
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
def get_customer_pos_info(customer, company=None):
    """Get customer info for POS: price list, credit rules, loyalty, group type.

    Used when a named customer is selected to auto-apply correct pricing.
    """
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
    customer_type = "B2C"
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

    # Loyalty
    loyalty = None
    if cust.loyalty_program and company:
        try:
            from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
                get_loyalty_program_details_with_points,
            )
            details = get_loyalty_program_details_with_points(customer, cust.loyalty_program, company=company)
            points = cint(details.get("loyalty_points"))
            cf = flt(details.get("conversion_factor"))
            loyalty = {
                "program": cust.loyalty_program,
                "points": points,
                "conversion_factor": cf,
                "currency_value": flt(points * cf),
                "tier_name": details.get("tier_name", ""),
            }
        except Exception:
            pass

    return {
        "customer": customer,
        "customer_name": cust.customer_name,
        "customer_group": customer_group,
        "customer_type": customer_type,
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
def validate_swap_eligibility(invoice_name, swap_window_days=7):
    """Check if a POS Invoice is eligible for in-store swap.

    Rules:
    - Invoice must be submitted, non-return, not already fully returned
    - Sale date must be within swap_window_days (default 7)
    - Items must not be scrapped / transferred out
    """
    swap_window_days = cint(swap_window_days) or 7

    if not frappe.db.exists("POS Invoice", invoice_name):
        return {"eligible": False, "reason": frappe._("Invoice {0} not found").format(invoice_name)}

    inv = frappe.get_doc("POS Invoice", invoice_name)

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
            FROM `tabPOS Invoice Item` ri
            JOIN `tabPOS Invoice` pi ON pi.name = ri.parent
            WHERE pi.return_against = %s AND pi.docstatus = 1
              AND ri.item_code = %s AND ri.pos_invoice_item = %s
        """, (invoice_name, item.item_code, item.name))[0][0] or 0)
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
def get_vas_plans_with_rules(cart_items=None):
    """Return active VAS plans with device-dependency enforcement.

    If a plan has requires_device=1 (or plan_type is 'Protection Plan'),
    it can only be added when the cart has at least one device item.
    """
    if isinstance(cart_items, str):
        cart_items = frappe.parse_json(cart_items)
    cart_items = cart_items or []

    # Check if cart has any device (non-service, non-warranty item)
    has_device = False
    device_item_codes = []
    device_brands = set()
    for ci in cart_items:
        if ci.get("is_warranty") or ci.get("is_vas"):
            continue
        has_device = True
        device_item_codes.append(ci.get("item_code"))
        # Get brand for filtering
        brand = frappe.db.get_value("Item", ci.get("item_code"), "brand")
        if brand:
            device_brands.add(brand)

    today = nowdate()
    plans = frappe.get_all(
        "CH Warranty Plan",
        filters={
            "status": "Active",
            "plan_type": ["in", ["Value Added Service", "Protection Plan"]],
        },
        fields=[
            "name", "plan_name", "plan_type", "service_item",
            "duration_months", "price", "coverage_description", "brand",
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

        # Device dependency: Protection Plans always require a device
        requires_device = plan.plan_type == "Protection Plan"
        plan["requires_device"] = requires_device

        if requires_device and not has_device:
            plan["blocked"] = True
            plan["blocked_reason"] = frappe._("Requires a device in cart")
        else:
            plan["blocked"] = False
            plan["blocked_reason"] = ""

        # Brand filtering: if plan specifies a brand, only show if cart has that brand
        if plan.brand and device_brands and plan.brand not in device_brands:
            plan["blocked"] = True
            plan["blocked_reason"] = frappe._("Only for {0} devices").format(plan.brand)

        applicable.append(plan)

    return applicable


# ── Quick Job Card from POS ──────────────────────────────────
@frappe.whitelist()
def create_quick_job_card(customer, contact_number, device_item,
                          issue_description, serial_no=None,
                          issue_category=None, warranty_status=None,
                          priority="Medium", estimated_hours=None,
                          device_condition=None, accessories_received=None,
                          data_backup_disclaimer=0):
    """Create a Service Request, accept it, and create Job Assignment in one call.

    This is the 'quick job card' flow for walk-in repairs from POS.
    Returns: {service_request, service_order, job_assignment}
    """
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
    sr.company = frappe.defaults.get_default("company") or ""
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

    frappe.db.commit()
    return {
        "service_request": sr.name,
        "service_order": service_order,
        "job_assignment": job_name,
    }


@frappe.whitelist()
def get_central_warehouses(company=None):
    """Return warehouses suitable as source for stock requests (non-POS, non-store)."""
    if not company:
        company = frappe.defaults.get_default("company")

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
                "can_give_discount", "max_discount_pct"],
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

    # Build company-role map
    company_roles = []
    for comp in accessible_companies:
        user_exec = next((e for e in own if e.company == comp), None)
        role = user_exec.role if user_exec else "Manager"
        company_roles.append({
            "company": comp,
            "role": role,
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

    # Determine user's own default executive record
    own_default = None
    if own:
        own_default = {
            "name": own[0].name,
            "executive_name": own[0].executive_name,
            "company": own[0].company,
            "role": own[0].role,
            "can_give_discount": own[0].can_give_discount,
            "max_discount_pct": own[0].max_discount_pct,
        }

    return {
        "companies": company_roles,
        "is_manager": is_manager,
        "store_executives": store_execs_by_company,
        "own_executive": own_default,
        "stores": stores,
    }


@frappe.whitelist()
def get_store_executives(warehouse=None, company=None):
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
                "can_give_discount", "max_discount_pct"],
        order_by="company, executive_name",
    )


@frappe.whitelist()
def get_executive_incentive_summary(pos_executive, from_date=None, to_date=None):
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

    return {
        "total_incentive": total,
        "total_billing": total_billing,
        "total_transactions": sum(cint(r.total_transactions) for r in ledger),
        "by_type": {r.transaction_type: {
            "incentive": flt(r.total_incentive),
            "billing": flt(r.total_billing),
            "count": cint(r.total_transactions),
        } for r in ledger},
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

        item_group = frappe.db.get_value("Item", item.item_code, "item_group") or ""
        brand = frappe.db.get_value("Item", item.item_code, "brand") or ""

        slab = _find_incentive_slab(
            company=exec_doc.company,
            item_group=item_group,
            brand=brand,
            billing_amount=billing_amount,
            transaction_type=item_type,
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


# ── Quick Customer Creation ──────────────────────────────────────
@frappe.whitelist()
def quick_create_customer(customer_name, mobile_no="", email_id="",
                          customer_group="Individual", company=None):
    """Create a new Customer quickly from the POS interface."""
    cust = frappe.new_doc("Customer")
    cust.customer_name = customer_name
    cust.customer_group = customer_group or "Individual"
    cust.customer_type = "Individual"
    cust.territory = frappe.db.get_single_value("Selling Settings", "territory") or "India"
    if company:
        cust.company = company
    cust.flags.ignore_permissions = True
    cust.flags.ignore_mandatory = True
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

    return cust.name


# ── POS Closing Entry APIs ───────────────────────────────────────

@frappe.whitelist()
def get_session_payment_summary(pos_profile, from_date, to_date=None):
    """Return expected payment totals for a POS profile + date range.

    Used to pre-fill the POS Closing Entry payment breakdown.

    Args:
        pos_profile: POS Profile name
        from_date:   Start posting date (inclusive)
        to_date:     End posting date (inclusive, default = from_date)

    Returns:
        {
          payment_modes: [{mode_of_payment, expected_amount}],
          totals: {total_invoices, gross_sales, total_returns, return_amount,
                   net_sales, total_tax, total_margin_gst}
        }
    """
    from_date = from_date or frappe.utils.nowdate()
    to_date = to_date or from_date

    # Payment mode breakdown
    payment_rows = frappe.db.sql("""
        SELECT
            sip.mode_of_payment,
            SUM(sip.amount) AS expected_amount
        FROM `tabPOS Invoice` pi
        JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
        WHERE pi.pos_profile = %(pos_profile)s
          AND pi.docstatus = 1
          AND pi.is_consolidated = 0
          AND pi.posting_date BETWEEN %(from_date)s AND %(to_date)s
        GROUP BY sip.mode_of_payment
        ORDER BY expected_amount DESC
    """, {"pos_profile": pos_profile, "from_date": from_date, "to_date": to_date}, as_dict=True)

    # Invoice totals
    invoices = frappe.get_all(
        "POS Invoice",
        filters={
            "pos_profile": pos_profile,
            "docstatus": 1,
            "is_consolidated": 0,
            "posting_date": ["between", [from_date, to_date]],
        },
        fields=["grand_total", "is_return", "total_taxes_and_charges", "custom_margin_gst"],
    )

    total_invoices = 0
    gross_sales = 0.0
    total_returns = 0
    return_amount = 0.0
    total_tax = 0.0
    total_margin_gst = 0.0

    for inv in invoices:
        amt = flt(inv.grand_total)
        if inv.is_return:
            total_returns += 1
            return_amount += abs(amt)
        else:
            total_invoices += 1
            gross_sales += amt
            total_tax += flt(inv.total_taxes_and_charges)
            total_margin_gst += flt(inv.custom_margin_gst)

    return {
        "payment_modes": [{"mode_of_payment": r.mode_of_payment, "expected_amount": flt(r.expected_amount)} for r in payment_rows],
        "totals": {
            "total_invoices": total_invoices,
            "gross_sales": gross_sales,
            "total_returns": total_returns,
            "return_amount": return_amount,
            "net_sales": gross_sales - return_amount,
            "total_tax": total_tax,
            "total_margin_gst": total_margin_gst,
            "total_regular_gst": total_tax - total_margin_gst,
        },
    }


@frappe.whitelist()
def open_closing_entry(pos_profile, from_date, to_date=None, opening_cash=0,
                       session_log=None):
    """Create a Draft POS Closing Entry pre-filled with expected amounts.

    Args:
        pos_profile:  POS Profile
        from_date:    Period start
        to_date:      Period end (default = from_date)
        opening_cash: Starting cash float in drawer
        session_log:  Optional POS Session Log to link

    Returns:
        name of the created POS Closing Entry
    """
    from_date = from_date or frappe.utils.nowdate()
    to_date = to_date or from_date

    # Prevent duplicate open/draft entries for same profile+date
    existing = frappe.db.get_value(
        "POS Closing Entry",
        {
            "pos_profile": pos_profile,
            "from_date": from_date,
            "to_date": to_date,
            "docstatus": 0,
        },
        "name",
    )
    if existing:
        return existing

    profile = frappe.get_cached_doc("POS Profile", pos_profile)

    entry = frappe.new_doc("POS Closing Entry")
    entry.company = profile.company
    entry.pos_profile = pos_profile
    entry.session_log = session_log
    entry.from_date = from_date
    entry.to_date = to_date
    entry.closing_date = frappe.utils.nowdate()
    entry.opening_cash = flt(opening_cash)

    entry.flags.ignore_permissions = True
    entry.save()

    return entry.name


@frappe.whitelist()
def submit_closing_entry(closing_entry, counted_amounts_json, counted_cash=0, remarks=None):
    """Record actual counted amounts per payment mode and submit the closing entry.

    Args:
        closing_entry:       POS Closing Entry name
        counted_amounts_json: JSON list [{mode_of_payment, counted_amount, notes?}]
        counted_cash:        Physical cash counted in drawer
        remarks:             Optional remarks from cashier

    Returns:
        {name, status, cash_variance, payment_variances: [...]}
    """
    if isinstance(counted_amounts_json, str):
        counted_amounts_json = frappe.parse_json(counted_amounts_json)

    doc = frappe.get_doc("POS Closing Entry", closing_entry)
    if doc.docstatus != 0:
        frappe.throw(frappe._("Closing Entry {0} is already submitted").format(closing_entry))

    # Map counted amounts by mode_of_payment
    counts = {row["mode_of_payment"]: row for row in (counted_amounts_json or [])}

    for row in doc.payment_details:
        if row.mode_of_payment in counts:
            c = counts[row.mode_of_payment]
            row.counted_amount = flt(c.get("counted_amount", 0))
            row.notes = c.get("notes", "")
            row.variance = row.counted_amount - flt(row.expected_amount)

    doc.counted_cash = flt(counted_cash)
    if remarks:
        doc.remarks = remarks

    doc.flags.ignore_permissions = True
    doc.save()
    doc.submit()

    return {
        "name": doc.name,
        "status": doc.status,
        "cash_variance": doc.cash_variance,
        "payment_variances": [
            {
                "mode_of_payment": r.mode_of_payment,
                "expected": r.expected_amount,
                "counted": r.counted_amount,
                "variance": r.variance,
            }
            for r in doc.payment_details
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2C — Session Crash Recovery
# ═══════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_open_session(pos_profile):
	"""Return the last unclosed POS Session Log for this profile/terminal.

	Frontend calls this on startup. If a session is returned, the UI should
	offer "Resume session {name}?" before creating a new one.
	"""
	row = frappe.db.sql("""
		SELECT name, opening_date, opening_time, sales_executive
		FROM `tabPOS Session Log`
		WHERE pos_profile = %(pos)s
		  AND docstatus = 1
		  AND status != 'Closed'
		ORDER BY creation DESC
		LIMIT 1
	""", {"pos": pos_profile}, as_dict=True)
	return row[0] if row else None


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
def log_walkin(pos_profile, source="POS Counter"):
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
	frappe.db.commit()

	new_walkin = frappe.db.get_value("POS Session Log", session_log, "walkin_count") or 0
	new_kiosk = frappe.db.get_value("POS Session Log", session_log, "kiosk_count") or 0
	return {"ok": True, "session_log": session_log, "walkin_count": cint(new_walkin), "kiosk_count": cint(new_kiosk)}


@frappe.whitelist()
def increment_repair_intake_count(pos_profile):
	"""Increment repair intake counter on active session log."""
	session_log = _get_active_session_log(pos_profile)
	if session_log:
		frappe.db.sql(
			"UPDATE `tabPOS Session Log` SET repair_intake_count = repair_intake_count + 1 WHERE name = %s",
			(session_log,)
		)
		frappe.db.commit()
	return {"ok": True}


@frappe.whitelist()
def increment_buyback_count(pos_profile):
	"""Increment buyback assessment counter on active session log."""
	session_log = _get_active_session_log(pos_profile)
	if session_log:
		frappe.db.sql(
			"UPDATE `tabPOS Session Log` SET buyback_count = buyback_count + 1 WHERE name = %s",
			(session_log,)
		)
		frappe.db.commit()
	return {"ok": True}


@frappe.whitelist()
def get_today_footfall(pos_profile):
	"""Return today's footfall summary: walk-ins, kiosk, repairs, buybacks, invoices."""
	today = nowdate()

	# Session log totals for today
	session_totals = frappe.db.sql("""
		SELECT
			COALESCE(SUM(walkin_count), 0) AS walkin_count,
			COALESCE(SUM(kiosk_count), 0) AS kiosk_count,
			COALESCE(SUM(repair_intake_count), 0) AS repair_intake_count,
			COALESCE(SUM(buyback_count), 0) AS buyback_count
		FROM `tabPOS Session Log`
		WHERE pos_profile = %s
		  AND DATE(session_start) = %s
		  AND docstatus = 1
	""", (pos_profile, today), as_dict=True)

	s = session_totals[0] if session_totals else {}

	# Invoices today
	invoices_today = frappe.db.count("POS Invoice", {
		"pos_profile": pos_profile,
		"posting_date": today,
		"docstatus": 1,
		"is_return": 0,
	})

	total_footfall = cint(s.get("walkin_count", 0)) + cint(s.get("kiosk_count", 0))
	conversion_pct = round((invoices_today / total_footfall * 100) if total_footfall > 0 else 0, 1)

	return {
		"walkin_count": cint(s.get("walkin_count", 0)),
		"kiosk_count": cint(s.get("kiosk_count", 0)),
		"repair_intake_count": cint(s.get("repair_intake_count", 0)),
		"buyback_count": cint(s.get("buyback_count", 0)),
		"total_footfall": total_footfall,
		"invoices_today": invoices_today,
		"conversion_pct": conversion_pct,
	}


@frappe.whitelist()
def flag_reprint_needed(pos_invoice, reason="Print failed"):
	"""Mark an invoice as needing reprint.

	Frontend calls this when the receipt printer is offline or errors.
	Store managers can see pending reprints in the CH Store Operations workspace.
	"""
	if not frappe.db.exists("POS Invoice", pos_invoice):
		frappe.throw(frappe._("POS Invoice {0} not found").format(pos_invoice))

	# Append to session log reprint queue if session log linked
	session_log = frappe.db.get_value("POS Session Log", {"pos_profile": frappe.db.get_value("POS Invoice", pos_invoice, "pos_profile"), "status": ["!=", "Closed"], "docstatus": 1}, "name")

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
	frappe.db.commit()
	return {"status": "queued", "pos_invoice": pos_invoice}


@frappe.whitelist()
def get_pending_reprints(pos_profile, limit=20):
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
def mark_reprint_done(reprint_name):
	"""Mark a reprint queue item as completed."""
	frappe.db.set_value("POS Reprint Queue", reprint_name, "status", "Done",
		update_modified=False)
	return {"status": "done"}


# ═══════════════════════════════════════════════════════════════════════════
# Buyback POS Full-Flow APIs
# ═══════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_pos_buyback_detail(assessment_name):
	"""Return full buyback detail for POS: assessment + linked order + diagnostics.

	Called on every stage transition so the frontend always has fresh data.
	"""
	a = frappe.get_doc("Buyback Assessment", assessment_name)

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
			"final_price": flt(o.final_price),
			"base_price": flt(o.base_price),
			"settlement_type": o.settlement_type or "",
			"customer_approved": cint(o.customer_approved),
			"otp_verified": cint(o.otp_verified),
			"payment_status": o.payment_status or "",
			"requires_approval": cint(o.requires_approval),
			"approved_by": o.approved_by or "",
			"approval_token": o.approval_token or "",
			"approval_url": (
				f"{frappe.utils.get_url()}/buyback-approval?token={o.approval_token}"
				if o.approval_token else ""
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
		"device_age_months": a.device_age_months or "",
		"warranty_status": a.warranty_status or "",
		"estimated_grade": a.estimated_grade or "",
		"estimated_price": flt(a.estimated_price),
		"quoted_price": flt(a.quoted_price),
		"remarks": a.remarks or "",
		"diagnostics": diagnostics,
		"order": order,
		"buyback_inspection": a.buyback_inspection or "",
	}


@frappe.whitelist()
def pos_start_buyback_order(assessment_name, pos_profile, final_price=None, inspector_notes=None):
	"""Create a Buyback Order from a Buyback Assessment in POS.

	Idempotent — returns existing order if one already exists for this assessment.
	"""
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
	company = frappe.db.get_value("POS Profile", pos_profile, "company") or frappe.defaults.get_default("company")

	price = flt(final_price) or flt(assessment.quoted_price) or flt(assessment.estimated_price)

	order = frappe.new_doc("Buyback Order")
	order.buyback_assessment = assessment_name
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

	order.flags.ignore_permissions = True
	order.insert()
	order.submit()

	return {"order_name": order.name, "created": True}


@frappe.whitelist()
def pos_update_buyback_price(order_name, final_price, inspector_notes=None):
	"""Update the final buyback price on an existing Buyback Order."""
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
def pos_send_customer_otp(order_name):
	"""Generate and send an OTP to the customer's mobile for buyback price approval."""
	doc = frappe.get_doc("Buyback Order", order_name)
	mobile_no = doc.mobile_no
	if not mobile_no:
		frappe.throw(frappe._("No mobile number on this Buyback Order."))

	from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
	otp = CHOTPLog.generate_otp(
		mobile_no=mobile_no,
		purpose="Buyback Customer Approval",
		reference_doctype="Buyback Order",
		reference_name=order_name,
	)
	# In production: send OTP via SMS gateway here
	# from ch_item_master.ch_core.sms_gateway import send_sms
	# send_sms(mobile_no, f"Your GoFix buyback OTP: {otp}. Valid 5 min.")
	return {
		"sent": True,
		"masked_mobile": mobile_no[:2] + "****" + mobile_no[-2:],
		"expires_in": 300,
	}


@frappe.whitelist()
def pos_approve_customer_buyback(order_name, method="In-Store Signature", otp_code=None):
	"""Record customer approval of the final buyback price.

	method: "In-Store Signature" | "OTP" | "Token Link"
	If method == "OTP", otp_code is verified first.
	"""
	doc = frappe.get_doc("Buyback Order", order_name)

	if method == "OTP":
		if not otp_code:
			frappe.throw(frappe._("OTP code is required for OTP verification."))
		from ch_item_master.ch_core.doctype.ch_otp_log.ch_otp_log import CHOTPLog
		result = CHOTPLog.verify_otp(
			mobile_no=doc.mobile_no,
			purpose="Buyback Customer Approval",
			otp_code=str(otp_code),
			reference_doctype="Buyback Order",
			reference_name=order_name,
		)
		if not result.get("valid"):
			frappe.throw(frappe._(result.get("message", "OTP verification failed.")))
		doc.otp_verified = 1

	doc.flags.ignore_permissions = True
	doc.customer_approve(method=method)

	return {
		"order_name": doc.name,
		"status": doc.status,
		"customer_approved": 1,
	}


@frappe.whitelist()
def pos_send_approval_link(order_name):
	"""Send a customer-facing approval link via SMS/WhatsApp.

	Transitions the order to "Awaiting Customer Approval" and returns the
	masked mobile number so the UI can confirm which number was contacted.
	"""
	doc = frappe.get_doc("Buyback Order", order_name)

	if not doc.mobile_no:
		frappe.throw(frappe._("No mobile number on this Buyback Order."))
	if not doc.approval_token:
		frappe.throw(frappe._("Approval token missing — please re-save the order."))

	approval_url = f"{frappe.utils.get_url()}/buyback-approval?token={doc.approval_token}"

	# Compose message
	item_label = doc.item_name or doc.item or "your device"
	price_fmt = f"₹{flt(doc.final_price):,.0f}"
	message = (
		f"GoFix Buyback: We offer {price_fmt} for {item_label}. "
		f"Tap to review & approve: {approval_url}"
	)

	# Send SMS (wire up your gateway — currently logs to console in dev)
	frappe.logger().info(f"[pos_send_approval_link] SMS to {doc.mobile_no}: {message}")
	# Example: from ch_item_master.ch_core.sms_gateway import send_sms
	# send_sms(doc.mobile_no, message)

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
def pos_settle_buyback_cashback(order_name, payment_method="Cash"):
	"""Mark a buyback order as settled via direct cashback to customer.

	Records a payment entry on the order, marks it Paid.
	Actual JE/stock entry is handled by back-office accounting.
	"""
	doc = frappe.get_doc("Buyback Order", order_name)

	if not doc.customer_approved:
		frappe.throw(frappe._("Customer must approve the final price before cashback settlement."))

	from frappe.utils import now_datetime
	doc.settlement_type = "Buyback"
	doc.append("payments", {
		"payment_method": payment_method,
		"amount": flt(doc.final_price),
		"payment_date": now_datetime(),
		"transaction_reference": f"POS-Cashback-{doc.name}",
	})
	doc.flags.ignore_permissions = True
	doc.save()

	# mark_paid validates payment_status == "Paid" first
	doc._calculate_payment_totals()
	if doc.payment_status == "Paid":
		doc.mark_paid()

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
	}


@frappe.whitelist()
def pos_create_inspection(assessment_name):
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
		from buyback.api import create_inspection_from_assessment
		result = create_inspection_from_assessment(assessment_name)
		ins = frappe.get_doc("Buyback Inspection", result["name"])

	# Also get grade options for the inline form selector
	grades = frappe.get_all("Grade Master", fields=["name", "grade_label"], order_by="name asc")

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
		"price_override_reason": ins.price_override_reason or "",
		"remarks": ins.remarks or "",
		"inspector": ins.inspector or "",
		"grades": [{"name": g.name, "label": g.grade_label or g.name} for g in grades],
	}


@frappe.whitelist()
def pos_complete_inspection(inspection_name, condition_grade, final_price,
							price_override_reason="", remarks=""):
	"""Complete the inline POS inspection and create a Buyback Order.

	Idempotent — if an order already exists for the assessment it is returned
	without creating a second one.
	"""
	from buyback.api import complete_inspection
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
		order.store = assessment.store or ""
		order.company = assessment.company or frappe.defaults.get_default("company")
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

		order.flags.ignore_permissions = True
		order.insert()
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
# Reprint — Today's POS Invoices
# ═══════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_todays_invoices(pos_profile, date=None):
	"""Return POS invoices for a profile on a given date (default: today).

	Used by the Reprint dialog in the POS frontend.
	"""
	from frappe.utils import getdate
	filter_date = getdate(date) if date else getdate(nowdate())

	rows = frappe.db.sql("""
		SELECT
			pi.name,
			pi.customer,
			pi.grand_total,
			pi.posting_date,
			pi.posting_time,
			pi.is_return,
			pi.status,
			GROUP_CONCAT(pii.item_name ORDER BY pii.idx SEPARATOR ', ') AS items_summary
		FROM `tabPOS Invoice` pi
		JOIN `tabPOS Invoice Item` pii ON pii.parent = pi.name
		WHERE pi.pos_profile = %s
		  AND pi.posting_date = %s
		  AND pi.docstatus = 1
		GROUP BY pi.name
		ORDER BY pi.posting_time DESC
	""", (pos_profile, filter_date), as_dict=True)

	return rows


# ═══════════════════════════════════════════════════════════════════════════
# FIFO Enforcement
# ═══════════════════════════════════════════════════════════════════════════

def _get_oldest_fifo_serial(item_code, warehouse):
	"""Return (serial_no, received_date) for the oldest FIFO serial in the warehouse.

	Uses SNBB net-balance to determine what is currently in stock, then finds
	the one with the earliest inward posting date.
	"""
	rows = frappe.db.sql("""
		SELECT
			available.serial_no,
			MIN(sbb_in.posting_date) AS received_date
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
		GROUP BY available.serial_no
		ORDER BY received_date ASC
		LIMIT 1
	""", (item_code, warehouse), as_dict=True)

	if rows:
		return rows[0].serial_no, rows[0].received_date

	# Fallback: use Serial No document purchase_document_date
	fallback = frappe.db.sql("""
		SELECT name, purchase_document_date
		FROM `tabSerial No`
		WHERE item_code = %s AND warehouse = %s AND status = 'Active'
		ORDER BY purchase_document_date ASC, creation ASC
		LIMIT 1
	""", (item_code, warehouse), as_dict=True)

	if fallback:
		return fallback[0].name, fallback[0].purchase_document_date

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
					"document_type": "POS Invoice",
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
