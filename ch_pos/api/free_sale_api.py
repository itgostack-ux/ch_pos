"""CH POS — Free Sale Approval API.

Handles category-manager based approval flow for free (zero-value) sales.
Each category has its own manager; if the cart spans multiple categories,
ALL category managers must approve before the free sale can proceed.
"""

import hashlib
import hmac
import json
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, get_url, getdate, now_datetime, nowdate

from ch_pos.rate_limits import clear_fixed_window, increment_fixed_window


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _manager_link_sig(token: str, manager: str, action: str) -> str:
    """Per-manager signature binding one approval link to one manager row.

    Keyed on the site's server-only encryption key so that a party who holds
    only the (secret, shared-per-approval) approval token still cannot forge
    a link for a different manager by editing the ``manager`` query param.
    """
    from frappe.utils.password import get_encryption_key

    key = get_encryption_key()
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(
        key,
        f"{token}:{manager}:{action}".encode(),
        hashlib.sha256).hexdigest()


def _validate_signed_link(token: str, manager: str, action: str, sig: str | None) -> bool:
    token = str(token or "").strip()
    manager = str(manager or "").strip()
    action = str(action or "").strip()
    sig = str(sig or "").strip()
    if len(token) > 256 or len(manager) > 254 or len(action) > 16 or len(sig) > 128:
        frappe.respond_as_web_page(
            _("Invalid Request"),
            _("Approval link parameters exceed the allowed size."),
            indicator_color="red")
        return False
    if action not in ("approve", "reject"):
        frappe.respond_as_web_page(
            _("Invalid Request"),
            _("Action must be 'approve' or 'reject'."),
            indicator_color="red")
        return False
    if not token or len(token) < 20:
        frappe.respond_as_web_page(
            _("Invalid Request"),
            _("Missing or invalid approval token."),
            indicator_color="red")
        return False
    if not manager:
        frappe.respond_as_web_page(
            _("Invalid Link"),
            _("This approval link is missing its intended manager."),
            indicator_color="red")
        return False
    expected = _manager_link_sig(token, manager, action)
    if not sig or not hmac.compare_digest(expected, sig):
        frappe.respond_as_web_page(
            _("Invalid Link"),
            _("This approval link is not valid for the specified action and manager."),
            indicator_color="red")
        return False
    return True

def _rate_limit_token_attempt(token: str) -> None:
    """Raise PermissionError if this token has been attempted too many times.

    Tracks attempts in Redis with a 15-minute TTL.  After 10 failed
    attempts the endpoint returns a locked response.  Successful
    responses clear the counter (see respond_to_approval).
    """
    attempts = increment_fixed_window("free-sale-approval", token, 900)
    if attempts > 10:
        frappe.respond_as_web_page(
            _("Too Many Attempts"),
            _("This approval link has been accessed too many times. "
              "Please contact your administrator."),
            indicator_color="red")
        raise frappe.PermissionError


_VAS_PLAN_TYPES = frozenset({"Value Added Service", "Protection Plan"})
_WARRANTY_PLAN_TYPES = frozenset(
    {"Own Warranty", "Extended Warranty", "Post-Repair Warranty"}
)


def canonicalize_cart_items(
    items,
    company: str | None = None,
    original_invoice: str | None = None,
    customer: str | None = None) -> list[dict]:
    """Return cart rows with classification and billable values normalized.

    ``is_vas`` and ``is_warranty`` are presentation hints from the browser,
    never authorization inputs. A special row must name an active CH Warranty
    Plan whose configured service item matches the billed Item. Any supplied
    hint that disagrees with that master data is rejected.
    """
    if isinstance(items, str):
        items = frappe.parse_json(items)
    if not isinstance(items, list):
        frappe.throw(_("Items must be a list."))

    from ch_pos.config import get_control_setting

    max_items = max(
        1,
        min(int(get_control_setting("free_sale_max_cart_items", 100) or 100), 500))
    if len(items) > max_items:
        frappe.throw(_("A free-sale request may contain at most {0} items.").format(max_items))

    plan_names = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            frappe.throw(_("Cart row {0} must be an object.").format(index))
        plan_name = str(item.get("warranty_plan") or "").strip()
        if len(plan_name) > 140:
            frappe.throw(_("Cart row {0} has an invalid warranty plan.").format(index))
        if plan_name:
            plan_names.add(plan_name)

    plans = {}
    if plan_names:
        plans = {
            row.name: row
            for row in frappe.get_all(
                "CH Warranty Plan",
                filters={"name": ("in", sorted(plan_names))},
                fields=[
                    "name", "company", "status", "plan_type", "service_item",
                    "is_sellable", "pricing_mode", "price", "percentage_value",
                    "valid_from", "valid_to", "min_device_price", "max_device_price",
                    "coverage_availability", "allow_external_device",
                    "external_device_price", "allow_zero_external_price",
                ],
                limit_page_length=len(plan_names))
        }

    normalized = []
    for index, source in enumerate(items, start=1):
        item = dict(source)
        item_code = str(item.get("item_code") or "").strip()
        if not item_code or len(item_code) > 140:
            frappe.throw(_("Cart row {0} has an invalid item code.").format(index))

        qty = flt(item.get("qty") if item.get("qty") is not None else 1)
        if qty <= 0:
            frappe.throw(_("Cart row {0} must have a positive quantity.").format(index))

        supplied_rate = item.get("rate")
        if supplied_rate is None:
            supplied_rate = item.get("price")
        rate = flt(supplied_rate)
        amount_supplied = item.get("amount") not in (None, "")
        amount = flt(item.get("amount")) if amount_supplied else None
        if supplied_rate in (None, "") and amount_supplied:
            rate = flt(amount / qty)
        elif amount_supplied and abs(amount - (rate * qty)) > 0.01:
            frappe.throw(
                _("Cart row {0} has conflicting rate and amount values.").format(index)
            )
        if rate < 0:
            frappe.throw(_("Cart row {0} cannot have a negative rate.").format(index))

        plan_name = str(item.get("warranty_plan") or "").strip()
        plan = plans.get(plan_name) if plan_name else None
        if plan_name and not plan:
            frappe.throw(_("Warranty plan {0} does not exist.").format(plan_name))

        authoritative_vas = 0
        authoritative_warranty = 0
        if plan:
            if plan.status != "Active" or not cint(plan.is_sellable):
                frappe.throw(_("Warranty plan {0} is not active.").format(plan_name))
            today = getdate(nowdate())
            if (plan.valid_from and getdate(plan.valid_from) > today) or (
                plan.valid_to and getdate(plan.valid_to) < today
            ):
                frappe.throw(_("Warranty plan {0} is outside its validity period.").format(plan_name))
            if company and plan.company and plan.company != company:
                frappe.throw(
                    _("Warranty plan {0} belongs to another company.").format(plan_name),
                    frappe.PermissionError)
            if not plan.service_item or plan.service_item != item_code:
                frappe.throw(
                    _("Warranty plan {0} does not authorize item {1}.").format(
                        plan_name, item_code
                    ),
                    frappe.PermissionError)
            if plan.plan_type in _VAS_PLAN_TYPES:
                authoritative_vas = 1
            elif plan.plan_type in _WARRANTY_PLAN_TYPES:
                authoritative_warranty = 1
            else:
                frappe.throw(_("Warranty plan {0} has an unsupported plan type.").format(plan_name))

        for fieldname, authoritative in (
            ("is_vas", authoritative_vas),
            ("is_warranty", authoritative_warranty)):
            if fieldname in item and cint(item.get(fieldname)) != authoritative:
                frappe.throw(
                    _("Cart row {0} has a forged or stale {1} classification.").format(
                        index, fieldname
                    ),
                    frappe.PermissionError)

        item.update(
            {
                "item_code": item_code,
                "qty": qty,
                "rate": rate,
                "warranty_plan": plan_name or None,
                "is_vas": authoritative_vas,
                "is_warranty": authoritative_warranty,
            }
        )
        if item.get("customer_imei") and not item.get("for_serial_no"):
            item["for_serial_no"] = str(item["customer_imei"])
        normalized.append(item)

    max_device_price = max(
        (
            flt(item.get("rate"))
            for item in normalized
            if not item.get("warranty_plan")
        ),
        default=0)

    # A VAS-only follow-up bill has no device row in the current cart. Resolve
    # the authoritative device value from the customer's original submitted
    # invoice instead of trusting the browser-supplied VAS rate.
    historical_prices_by_serial = {}
    historical_prices_by_item = {}
    if original_invoice and customer and company:
        valid_source = frappe.db.get_value(
            "Sales Invoice",
            {
                "name": original_invoice,
                "customer": customer,
                "company": company,
                "docstatus": 1,
                "is_return": 0,
            },
            "name")
        if valid_source:
            source_rows = frappe.get_all(
                "Sales Invoice Item",
                filters={"parent": valid_source, "parenttype": "Sales Invoice"},
                fields=["item_code", "serial_no", "rate", "amount", "qty"],
                order_by="idx asc")
            for source_row in source_rows:
                source_rate = flt(source_row.rate)
                if not source_rate and flt(source_row.qty):
                    source_rate = flt(source_row.amount) / flt(source_row.qty)
                if source_rate <= 0:
                    continue
                historical_prices_by_item.setdefault(source_row.item_code, source_rate)
                for serial_no in (source_row.serial_no or "").split("\n"):
                    serial_no = serial_no.strip()
                    if serial_no:
                        historical_prices_by_serial[serial_no] = source_rate

    for index, item in enumerate(normalized, start=1):
        plan_name = item.get("warranty_plan")
        if not plan_name:
            continue
        plan = plans[plan_name]
        external_intent = bool(str(item.get("customer_imei") or "").strip())
        availability = plan.coverage_availability or (
            "Both" if plan.allow_external_device else "In-Store Only"
        )
        if external_intent and availability not in ("External Only", "Both"):
            frappe.throw(_("Warranty plan {0} is not available for external devices.").format(plan_name))
        if not external_intent and availability == "External Only":
            frappe.throw(_("Warranty plan {0} is available only for customer-provided devices.").format(plan_name))

        covered_device_price = max_device_price
        if not external_intent and historical_prices_by_serial:
            covered_serial = str(item.get("for_serial_no") or "").strip()
            covered_item = str(item.get("for_item_code") or "").strip()
            covered_device_price = (
                historical_prices_by_serial.get(covered_serial)
                or historical_prices_by_item.get(covered_item)
                or max_device_price
            )

        if external_intent:
            expected_rate = flt(plan.external_device_price)
            if expected_rate == 0 and not cint(plan.allow_zero_external_price):
                frappe.throw(
                    _("Warranty plan {0} has no authorized external-device price.").format(plan_name)
                )
        elif plan.pricing_mode == "Percentage of Device Price":
            expected_rate = round(
                covered_device_price * flt(plan.percentage_value) / 100,
                2)
        else:
            expected_rate = flt(plan.price)
        if expected_rate < 0 or (expected_rate == 0 and not external_intent):
            frappe.throw(
                _("Warranty plan {0} has no computable server price.").format(plan_name)
            )
        if abs(flt(item.get("rate")) - expected_rate) > 0.01:
            frappe.throw(
                _("Cart row {0} does not match the configured price for plan {1}.").format(
                    index, plan_name
                ),
                frappe.PermissionError)
        if not external_intent and plan.min_device_price and covered_device_price < flt(plan.min_device_price):
            frappe.throw(_("Warranty plan {0} is not valid for this device value.").format(plan_name))
        if not external_intent and plan.max_device_price and covered_device_price > flt(plan.max_device_price):
            frappe.throw(_("Warranty plan {0} is not valid for this device value.").format(plan_name))
    return normalized


def _cart_hash_rows(items) -> list[dict]:
    rows = [
        {
            "item_code": str(item.get("item_code") or ""),
            "qty": round(flt(item.get("qty")), 3),
            "rate": round(flt(item.get("rate")), 2),
            "warranty_plan": str(item.get("warranty_plan") or ""),
            "for_item_code": str(item.get("for_item_code") or ""),
            "serial_no": str(item.get("serial_no") or ""),
            "for_serial_no": str(item.get("for_serial_no") or ""),
            "is_warranty": cint(item.get("is_warranty")),
            "is_vas": cint(item.get("is_vas")),
        }
        for item in items
    ]
    return sorted(
        rows,
        key=lambda row: tuple(str(row[field]) for field in sorted(row)))


def compute_cart_total(items) -> float:
    """Recompute the billable cart subtotal from canonical server rows."""
    return round(sum(flt(item.get("qty")) * flt(item.get("rate")) for item in items), 2)


def compute_cart_hash(
    customer,
    items,
    *,
    company: str | None = None,
    canonical: bool = False) -> str:
    """Return a stable SHA-256 over every canonical billed row."""
    normalized = list(items) if canonical else canonicalize_cart_items(items, company=company)
    payload = json.dumps(
        {
            "customer": str(customer or ""),
            "items": _cart_hash_rows(normalized),
            "billable_total": compute_cart_total(normalized),
        },
        sort_keys=True,
        separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_category_managers_for_canonical_cart(items) -> list:
    item_codes = sorted(
        {
            item["item_code"]
            for item in items
            if not item.get("is_warranty") and not item.get("is_vas")
        }
    )
    if not item_codes:
        return []

    item_categories = frappe.get_all(
        "Item",
        filters={"name": ("in", item_codes), "ch_category": ("is", "set")},
        fields=["name as item_code", "ch_category"])
    categories = sorted({row["ch_category"] for row in item_categories})
    if not categories:
        return []

    cat_managers = frappe.get_all(
        "CH Category",
        filters={"name": ("in", categories), "category_manager": ("is", "set")},
        fields=["name as category", "category_name", "category_manager as manager"])
    manager_ids = sorted({row["manager"] for row in cat_managers if row.get("manager")})
    manager_names = {
        row.name: row.full_name or row.name
        for row in frappe.get_all(
            "User",
            filters={"name": ("in", manager_ids)},
            fields=["name", "full_name"],
            limit_page_length=len(manager_ids))
    } if manager_ids else {}
    for manager in cat_managers:
        manager["manager_name"] = manager_names.get(manager["manager"], manager["manager"])

    seen_managers = set()
    unique_managers = []
    for manager in cat_managers:
        if manager["manager"] not in seen_managers:
            seen_managers.add(manager["manager"])
            unique_managers.append(manager)
    return unique_managers


@frappe.whitelist()
def get_category_managers_for_cart(items, company=None) -> list:
    """Given cart items, return the unique category managers required.

    Args:
        items: JSON string or list of dicts with at least {item_code}

    Returns:
        list of {category, category_name, manager, manager_name}
    """
    frappe.has_permission("Sales Invoice", "create", throw=True)
    frappe.has_permission("Item", "read", throw=True)
    frappe.has_permission("CH Category", "read", throw=True)
    canonical_items = canonicalize_cart_items(items, company=company)
    return _get_category_managers_for_canonical_cart(canonical_items)


@frappe.whitelist(methods=["POST"])
def request_free_sale_approval(reason, customer, items, grand_total,
                                store=None, company=None) -> dict:
    """Create a CH Free Sale Approval request and email category managers.

    Args:
        reason: Why the free sale is needed
        customer: Customer name
        items: JSON string of cart items
        grand_total: Total value being given free
        store: CH Store name
        company: Company name

    Returns:
        {approval_name, managers: [{category, manager, manager_name, status}]}
    """
    frappe.has_permission("Sales Invoice", "create", throw=True)
    from ch_pos.api.scope_guard import assert_store_scope
    from ch_pos.config import require_configured_roles


    if not store:
        frappe.throw(_("Store is required for a free-sale approval request."))
    store_company = frappe.db.get_value("CH Store", store, "company")
    if not store_company or store_company != company:
        frappe.throw(_("Store and company do not match."), frappe.PermissionError)
    assert_store_scope(store=store, company=company)

    canonical_items = canonicalize_cart_items(items, company=company)
    managers = _get_category_managers_for_canonical_cart(canonical_items)
    if not managers:
        frappe.throw(_(
            "No category managers found for the items in this cart. "
            "Please assign category managers in CH Category."
        ))

    doc = frappe.get_doc({
        "doctype": "CH Free Sale Approval",
        "status": "Pending",
        "requested_by": frappe.session.user,
        "store": store or None,
        "company": company or None,
        "customer": customer or None,
        "reason": reason,
        "grand_total": compute_cart_total(canonical_items),
        "cart_snapshot": json.dumps(canonical_items, default=str),
        "cart_hash": compute_cart_hash(customer, canonical_items, canonical=True),
        "approvals": [
            {
                "category": m["category"],
                "manager": m["manager"],
                "manager_name": m["manager_name"],
                "status": "Pending",
            }
            for m in managers
        ],
    })
    doc.flags.ch_server_issued = True
    doc.insert(ignore_permissions=True)
    token = doc.approval_token

    # Send email to each manager
    for m in managers:
        _send_approval_email(doc, m, token)

    return {
        "approval_name": doc.name,
        "managers": [
            {
                "category": m["category"],
                "manager": m["manager"],
                "manager_name": m["manager_name"],
                "status": "Pending",
            }
            for m in managers
        ],
    }


def _send_approval_email(approval_doc, manager_info, token):
    """Send approval request email to a category manager."""
    base_url = "/api/method/ch_pos.api.free_sale_api.preview_approval"
    approve_params = {
        "token": token,
        "manager": manager_info["manager"],
        "action": "approve",
        "sig": _manager_link_sig(token, manager_info["manager"], "approve"),
    }
    reject_params = {
        "token": token,
        "manager": manager_info["manager"],
        "action": "reject",
        "sig": _manager_link_sig(token, manager_info["manager"], "reject"),
    }
    approve_url = get_url(f"{base_url}?{urlencode(approve_params)}")
    reject_url = get_url(f"{base_url}?{urlencode(reject_params)}")

    # Build items summary for email
    items = json.loads(approval_doc.cart_snapshot or "[]")
    items_html = "".join(
        f"<tr><td>{frappe.utils.escape_html(i.get('item_name', i.get('item_code', '')))}</td>"
        f"<td style='text-align:center'>{i.get('qty', 1)}</td>"
        f"<td style='text-align:right'>₹{frappe.utils.fmt_money(i.get('rate', 0) * i.get('qty', 1))}</td></tr>"
        for i in items
        if not i.get("is_warranty") and not i.get("is_vas")
    )

    company_label = (
        frappe.get_cached_value("Company", approval_doc.company, "company_name")
        or approval_doc.company
        or _("Our Store")
    )
    company_subject = str(company_label).replace("\r", " ").replace("\n", " ")
    company_html = frappe.utils.escape_html(company_label)
    subject = _("{0} | Free Sale Approval | {1}").format(company_subject, approval_doc.name)

    message = f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden">
        <div style="background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600">{company_html} - POS Approval Desk</div>
        <div style="padding:16px">
        <h3 style="color:#111827;margin-top:0">Free Sale Approval Required</h3>
        <p>A free sale has been requested and needs your approval as
        <b>{frappe.utils.escape_html(manager_info['category'])}</b> Category Manager.</p>

        <table style="width:100%;border-collapse:collapse;margin:16px 0" border="1" cellpadding="8">
            <tr style="background:#f8f9fa">
                <th style="text-align:left">Item</th>
                <th style="text-align:center">Qty</th>
                <th style="text-align:right">Value</th>
            </tr>
            {items_html}
            <tr style="background:#f8f9fa;font-weight:bold">
                <td colspan="2">Total Value</td>
                <td style="text-align:right">₹{frappe.utils.fmt_money(approval_doc.grand_total)}</td>
            </tr>
        </table>

        <p><b>Reason:</b> {frappe.utils.escape_html(approval_doc.reason)}</p>
        <p><b>Customer:</b> {frappe.utils.escape_html(approval_doc.customer_name or approval_doc.customer or 'Walk-in')}</p>
        <p><b>Store:</b> {frappe.utils.escape_html(approval_doc.store or 'N/A')}</p>
        <p><b>Requested By:</b> {frappe.utils.escape_html(approval_doc.requested_by_name or approval_doc.requested_by)}</p>

        <div style="margin:24px 0;text-align:center">
            <a href="{approve_url}"
               style="display:inline-block;padding:12px 24px;background:#16a34a;color:#fff;
                      text-decoration:none;border-radius:6px;font-weight:600;margin-right:12px">
                Approve
            </a>
            <a href="{reject_url}"
               style="display:inline-block;padding:12px 24px;background:#dc2626;color:#fff;
                      text-decoration:none;border-radius:6px;font-weight:600">
                Reject
            </a>
        </div>

        <p class="text-muted" style="font-size:12px;color:#6b7280">
            Approval request: {approval_doc.name} | Store: {frappe.utils.escape_html(approval_doc.store or approval_doc.company or '')}
        </p>
        </div>
    </div>
    """

    # Keep legacy context in subject text for quick mailbox scanning.
    subject = subject + _(" — {0} — ₹{1}").format(
        approval_doc.store or approval_doc.company or "",
        frappe.utils.fmt_money(approval_doc.grand_total))

    frappe.sendmail(
        recipients=[manager_info["manager"]],
        subject=subject,
        message=message,
        now=True)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=30, seconds=300, methods=["GET"], ip_based=True)
def preview_approval(token: str, manager: str, action: str, sig: str | None = None) -> None:
    token = str(token or "").strip()
    manager = str(manager or "").strip()
    action = str(action or "").strip()
    sig = str(sig or "").strip()
    if not _validate_signed_link(token, manager, action, sig):
        return

    from frappe.sessions import get_csrf_token

    escape = frappe.utils.escape_html
    verb = _("Approve") if action == "approve" else _("Reject")
    color = "#16a34a" if action == "approve" else "#dc2626"
    form = f"""
        <p>{_("Please confirm that you want to {0} this free-sale request.").format(verb.lower())}</p>
        <form method="post" action="/api/method/ch_pos.api.free_sale_api.respond_to_approval">
            <input type="hidden" name="csrf_token" value="{escape(get_csrf_token())}">
            <input type="hidden" name="token" value="{escape(token)}">
            <input type="hidden" name="manager" value="{escape(manager)}">
            <input type="hidden" name="action" value="{escape(action)}">
            <input type="hidden" name="sig" value="{escape(sig)}">
            <button type="submit" style="border:0;border-radius:6px;padding:10px 20px;color:#fff;background:{color};font-weight:600">
                {verb}
            </button>
        </form>
    """
    frappe.respond_as_web_page(
        _("Confirm Free Sale Response"),
        form,
        indicator_color="orange")


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=300, methods=["POST"], ip_based=True)
def respond_to_approval(token: str, manager: str, action: str, sig: str | None = None) -> None:
    """Handle manager's response from email link.

    Token-based authentication — the cryptographic token (32+ chars) proves
    the request came from the correct email recipient.  This endpoint must
    remain guest-accessible because managers click links in email without
    being logged into Frappe.

    Brute-force protection: max 10 attempts per token per 15 minutes via
    Redis cache.  Tokens older than 24 h are automatically expired.

    Args:
        token: Approval token (cryptographic, ≥32 chars)
        manager: Manager's Frappe user/email
        action: 'approve' or 'reject'
    """
    token = str(token or "").strip()
    manager = str(manager or "").strip()
    action = str(action or "").strip()
    sig = str(sig or "").strip()
    if not _validate_signed_link(token, manager, action, sig):
        return

    # Rate-limit: max 10 attempts per token in a 15-minute window
    _rate_limit_token_attempt(token)

    approval = frappe.db.get_value(
        "CH Free Sale Approval",
        {"approval_token": token, "status": "Pending"},
        ["name", "creation"],
        as_dict=True,
        for_update=True)
    if not approval:
        frappe.respond_as_web_page(
            _("Invalid or Expired"),
            _("This approval request is no longer valid or has already been processed."),
            indicator_color="red")
        return

    # Token TTL — reject links older than configured hours (default 24)
    from frappe.utils import time_diff_in_hours
    ttl_hours = frappe.db.get_single_value("CH POS Control Settings", "approval_token_ttl_hours") or 24
    age_hours = time_diff_in_hours(now_datetime(), approval.creation)
    if age_hours > ttl_hours:
        frappe.respond_as_web_page(
            _("Expired"),
            _("This approval link has expired (valid for {0} hours). "
              "Please request a new approval.").format(int(ttl_hours)),
            indicator_color="red")
        return

    doc = frappe.get_doc("CH Free Sale Approval", approval.name)

    # Verify the manager matches a pending row — prevents token reuse by a different person
    target_row = None
    for row in doc.approvals:
        if row.manager == manager and row.status == "Pending":
            target_row = row
            break

    if not target_row:
        frappe.respond_as_web_page(
            _("Already Responded"),
            _("You have already responded to this approval request."),
            indicator_color="orange")
        return

    new_status = "Approved" if action == "approve" else "Rejected"
    frappe.db.set_value(
        "CH Free Sale Approval Detail",
        target_row.name,
        {"status": new_status, "responded_at": now_datetime()},
        update_modified=False)
    statuses = [new_status if row.name == target_row.name else row.status for row in doc.approvals]
    parent_status = (
        "Rejected"
        if "Rejected" in statuses
        else "Approved"
        if statuses and all(status == "Approved" for status in statuses)
        else "Pending"
    )
    frappe.db.set_value(
        "CH Free Sale Approval",
        doc.name,
        "status",
        parent_status,
        update_modified=True)

    clear_fixed_window("free-sale-approval", token)

    # Show confirmation page
    if action == "approve":
        frappe.respond_as_web_page(
            _("Approved"),
            _("You have approved the free sale request {0}. "
              "Value: ₹{1}").format(doc.name, frappe.utils.fmt_money(doc.grand_total)),
            indicator_color="green")
    else:
        frappe.respond_as_web_page(
            _("Rejected"),
            _("You have rejected the free sale request {0}.").format(doc.name),
            indicator_color="red")


@frappe.whitelist()
def check_approval_status(approval_name) -> dict:
    """Check current status of a free sale approval request.

    Args:
        approval_name: CH Free Sale Approval name

    Returns:
        {status, approvals: [{category, manager, manager_name, status}]}
    """
    doc = frappe.get_doc("CH Free Sale Approval", approval_name)
    from ch_pos.api.scope_guard import assert_store_scope
    from ch_pos.config import is_privileged_user, require_configured_roles

    assert_store_scope(store=doc.store, company=doc.company)
    managers = {row.manager for row in (doc.approvals or []) if row.manager}
    if frappe.session.user != doc.requested_by and frappe.session.user not in managers:
        if not is_privileged_user():
            require_configured_roles(
                "free_sale_review_roles",
                action=_("review another user's free-sale approval"))
    return {
        "status": doc.status,
        "approvals": [
            {
                "category": r.category,
                "manager": r.manager,
                "manager_name": r.manager_name,
                "status": r.status,
                "responded_at": str(r.responded_at) if r.responded_at else None,
            }
            for r in doc.approvals
        ],
    }
