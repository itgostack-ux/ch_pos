"""CH POS — Free Sale Approval API.

Handles category-manager based approval flow for free (zero-value) sales.
Each category has its own manager; if the cart spans multiple categories,
ALL category managers must approve before the free sale can proceed.
"""

import json
import secrets

import frappe
from frappe import _
from frappe.utils import now_datetime, get_url


@frappe.whitelist()
def get_category_managers_for_cart(items) -> list:
    """Given cart items, return the unique category managers required.

    Args:
        items: JSON string or list of dicts with at least {item_code}

    Returns:
        list of {category, category_name, manager, manager_name}
    """
    if isinstance(items, str):
        items = json.loads(items)

    # Collect unique item codes (exclude warranty/VAS service items)
    item_codes = list({
        i["item_code"]
        for i in items
        if not i.get("is_warranty") and not i.get("is_vas")
    })

    if not item_codes:
        return []

    # Get ch_category for each item
    item_categories = frappe.get_all(
        "Item",
        filters={"name": ("in", item_codes), "ch_category": ("is", "set")},
        fields=["name as item_code", "ch_category"],
    )

    # Unique categories
    categories = list({ic["ch_category"] for ic in item_categories})
    if not categories:
        return []

    # Get category managers
    cat_managers = frappe.get_all(
        "CH Category",
        filters={"name": ("in", categories), "category_manager": ("is", "set")},
        fields=["name as category", "category_name", "category_manager as manager"],
    )

    # Resolve manager names
    for cm in cat_managers:
        cm["manager_name"] = frappe.db.get_value("User", cm["manager"], "full_name") or cm["manager"]

    return cat_managers


@frappe.whitelist()
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
    if isinstance(items, str):
        items = json.loads(items)

    managers = get_category_managers_for_cart(items)
    if not managers:
        frappe.throw(_(
            "No category managers found for the items in this cart. "
            "Please assign category managers in CH Category master."
        ))

    # Generate a unique token for email approval links
    token = secrets.token_urlsafe(32)

    doc = frappe.get_doc({
        "doctype": "CH Free Sale Approval",
        "status": "Pending",
        "requested_by": frappe.session.user,
        "store": store or None,
        "company": company or None,
        "customer": customer or None,
        "reason": reason,
        "grand_total": frappe.utils.flt(grand_total),
        "cart_snapshot": json.dumps(items, default=str),
        "approval_token": token,
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
    doc.insert(ignore_permissions=True)

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
    approve_url = get_url(
        f"/api/method/ch_pos.api.free_sale_api.respond_to_approval"
        f"?token={token}&manager={manager_info['manager']}&action=approve"
    )
    reject_url = get_url(
        f"/api/method/ch_pos.api.free_sale_api.respond_to_approval"
        f"?token={token}&manager={manager_info['manager']}&action=reject"
    )

    # Build items summary for email
    items = json.loads(approval_doc.cart_snapshot or "[]")
    items_html = "".join(
        f"<tr><td>{frappe.utils.escape_html(i.get('item_name', i.get('item_code', '')))}</td>"
        f"<td style='text-align:center'>{i.get('qty', 1)}</td>"
        f"<td style='text-align:right'>₹{frappe.utils.fmt_money(i.get('rate', 0) * i.get('qty', 1))}</td></tr>"
        for i in items
        if not i.get("is_warranty") and not i.get("is_vas")
    )

    subject = _("Free Sale Approval Required — {0} — ₹{1}").format(
        approval_doc.store or approval_doc.company or "",
        frappe.utils.fmt_money(approval_doc.grand_total),
    )

    message = f"""
    <div style="font-family:sans-serif;max-width:600px">
        <h3 style="color:#7c3aed">🎁 Free Sale Approval Required</h3>
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
               style="display:inline-block;padding:12px 32px;background:#16a34a;color:#fff;
                      text-decoration:none;border-radius:6px;font-weight:bold;margin-right:12px">
                ✅ Approve
            </a>
            <a href="{reject_url}"
               style="display:inline-block;padding:12px 32px;background:#dc2626;color:#fff;
                      text-decoration:none;border-radius:6px;font-weight:bold">
                ❌ Reject
            </a>
        </div>

        <p class="text-muted" style="font-size:12px;color:#6b7280">
            Approval request: {approval_doc.name}
        </p>
    </div>
    """

    frappe.sendmail(
        recipients=[manager_info["manager"]],
        subject=subject,
        message=message,
        now=True,
    )


@frappe.whitelist(allow_guest=True)
def respond_to_approval(token, manager, action) -> None:
    """Handle manager's response from email link.

    Uses token-based authentication — the cryptographic token proves
    the request came from the correct email recipient.

    Args:
        token: Approval token (cryptographic, 32 bytes)
        manager: Manager's user/email
        action: 'approve' or 'reject'
    """
    if action not in ("approve", "reject"):
        frappe.throw(_("Invalid action"), title=_("API Error"))

    if not token or len(token) < 20:
        frappe.respond_as_web_page(
            _("Invalid Request"),
            _("Missing or invalid approval token."),
            indicator_color="red",
        )
        return

    approval = frappe.get_all(
        "CH Free Sale Approval",
        filters={"approval_token": token, "status": "Pending"},
        fields=["name", "creation"],
        limit=1,
    )
    if not approval:
        frappe.respond_as_web_page(
            _("Invalid or Expired"),
            _("This approval request is no longer valid or has already been processed."),
            indicator_color="red",
        )
        return

    # POS-5 fix: Token expiry — reject tokens older than 24 hours
    from frappe.utils import time_diff_in_hours
    age_hours = time_diff_in_hours(now_datetime(), approval[0].creation)
    if age_hours > 24:
        frappe.respond_as_web_page(
            _("Expired"),
            _("This approval link has expired (valid for 24 hours). "
              "Please request a new approval."),
            indicator_color="red",
        )
        return

    doc = frappe.get_doc("CH Free Sale Approval", approval[0].name)

    # Verify the manager email matches a row in this approval
    # This prevents token reuse with a different manager email
    found = False
    for row in doc.approvals:
        if row.manager == manager and row.status == "Pending":
            row.status = "Approved" if action == "approve" else "Rejected"
            row.responded_at = now_datetime()
            found = True
            break

    if not found:
        frappe.respond_as_web_page(
            _("Already Responded"),
            _("You have already responded to this approval request."),
            indicator_color="orange",
        )
        return

    doc.update_status()

    # Show confirmation page
    if action == "approve":
        frappe.respond_as_web_page(
            _("Approved"),
            _("You have approved the free sale request {0}. "
              "Value: ₹{1}").format(doc.name, frappe.utils.fmt_money(doc.grand_total)),
            indicator_color="green",
        )
    else:
        frappe.respond_as_web_page(
            _("Rejected"),
            _("You have rejected the free sale request {0}.").format(doc.name),
            indicator_color="red",
        )


@frappe.whitelist()
def check_approval_status(approval_name) -> dict:
    """Check current status of a free sale approval request.

    Args:
        approval_name: CH Free Sale Approval name

    Returns:
        {status, approvals: [{category, manager, manager_name, status}]}
    """
    doc = frappe.get_doc("CH Free Sale Approval", approval_name)
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
