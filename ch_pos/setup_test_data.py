"""
CH POS — Test Master Data Setup
Run: bench --site erpnext.local execute ch_pos.setup_test_data.run

Creates / ensures all required master records for the scenario test suite:
  • CH Discount Reason  — Test Clearance
  • CH Item Offer       — UPI Cashback, Credit Card Bank Offer, Flat Item Offer
  • Pricing Rule + Coupon Code — for S3 coupon test
"""
import frappe
from frappe.utils import today, add_days


def _replenish_stock_if_needed():
    """Top-up stock for test items when POS reserved qty leaves < 5 available."""
    from frappe.utils import nowdate

    company = frappe.db.get_single_value("Global Defaults", "default_company") or "GoGizmo Retail Pvt Ltd"
    warehouse = frappe.db.get_value("POS Profile", {"disabled": 0}, "warehouse") or "QA Velachery - GGR"
    # Use the company from the warehouse itself to avoid cross-company validation errors
    wh_company = frappe.db.get_value("Warehouse", warehouse, "company")
    if wh_company:
        company = wh_company

    # Account needed for Material Receipt
    stock_adj_account = (
        frappe.db.get_value("Account",
            {"account_type": "Stock Adjustment", "company": company, "is_group": 0}, "name")
        or frappe.db.get_value("Account",
            {"account_name": ["like", "%Temporary%"], "company": company, "is_group": 0}, "name")
    )

    items_to_replenish = [
        {"item_code": "CSV000001-BLA-Lightning", "target_available": 20, "has_serial": False},
        {"item_code": "QAP000001-R", "target_available": 5, "has_serial": True,
         "serial_prefix": "RFB-IPH15"},
    ]

    for entry in items_to_replenish:
        item_code = entry["item_code"]
        target = entry["target_available"]

        if not frappe.db.exists("Item", item_code):
            print(f"  ✗ Item {item_code} not found — skipping")
            continue

        # current_available = bin_qty - pos_reserved_qty
        bin_qty = frappe.db.get_value("Bin",
            {"item_code": item_code, "warehouse": warehouse}, "actual_qty") or 0
        pos_reserved = frappe.db.sql("""
            SELECT IFNULL(SUM(pi.stock_qty), 0) as qty
            FROM `tabSales Invoice Item` pi
            JOIN `tabSales Invoice` p ON pi.parent = p.name
            WHERE pi.item_code = %s AND pi.warehouse = %s
              AND p.docstatus = 1 AND p.is_return = 0
              AND IFNULL(p.consolidated_invoice, '') = ''
        """, (item_code, warehouse), as_dict=True)
        reserved = frappe.utils.flt((pos_reserved[0].qty if pos_reserved else 0))
        available = frappe.utils.flt(bin_qty) - reserved

        if available >= target:
            print(f"  ✓ {item_code}: available={available:.0f} (bin={bin_qty:.0f}, reserved={reserved:.0f}) — no top-up needed")
            continue

        add_qty = int(target - available)
        print(f"  ! {item_code}: available={available:.0f} < {target}, adding {add_qty} units via Material Receipt")

        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Material Receipt"
        se.company = company
        se.posting_date = nowdate()

        item_row = se.append("items", {
            "item_code": item_code,
            "qty": add_qty,
            "t_warehouse": warehouse,
            "basic_rate": frappe.db.get_value("Item Price",
                {"item_code": item_code, "selling": 1}, "price_list_rate") or 1,
        })

        # For serial items, assign new unique serial numbers
        if entry.get("has_serial"):
            prefix = entry.get("serial_prefix", item_code[:10])
            # Find next available serial number suffix
            existing = frappe.db.sql("""
                SELECT MAX(CAST(SUBSTRING_INDEX(name, '-', -1) AS UNSIGNED)) as max_n
                FROM `tabSerial No` WHERE name LIKE %s
            """, (f"{prefix}-%",), as_dict=True)
            start_n = int((existing[0].max_n or 0)) + 1
            new_serials = [f"{prefix}-{str(n).zfill(3)}" for n in range(start_n, start_n + add_qty)]
            item_row.use_serial_batch_fields = 1
            item_row.serial_no = "\n".join(new_serials)

        se.flags.ignore_permissions = True
        se.flags.ignore_mandatory = True
        try:
            se.insert()
            se.submit()
            frappe.db.commit()
            extra = f" (serials: {', '.join(new_serials)})" if entry.get("has_serial") else ""
            print(f"  + Stock Entry {se.name}: +{add_qty} units of {item_code}{extra}")
        except Exception as exc:
            print(f"  ✗ Failed to create stock entry for {item_code}: {exc}")
            frappe.db.rollback()


def _ensure(doctype, name, data, label=None):
    """Insert if not exists (by name or by offer_name for CH Item Offer), print status."""
    label = label or name
    # For CH Item Offer, check by offer_name since naming series overrides name
    if doctype == "CH Item Offer":
        existing = frappe.db.get_value(doctype, {"offer_name": data.get("offer_name")}, "name")
        if existing:
            print(f"  ✓ {doctype}: '{label}' already exists ({existing})")
            return frappe.get_doc(doctype, existing)
        # Remove the 'name' key so naming series is used
        data = {k: v for k, v in data.items() if k != "name"}
    elif frappe.db.exists(doctype, name):
        print(f"  ✓ {doctype}: '{label}' already exists")
        return frappe.get_doc(doctype, name)
    doc = frappe.new_doc(doctype)
    doc.update(data)
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.insert()
    frappe.db.commit()
    print(f"  + Created {doctype}: '{label}'")
    return doc


def run():
    frappe.set_user("Administrator")
    print("\n══════════════════════════════════════════")
    print("  CH POS — Setup Test Master Data")
    print("══════════════════════════════════════════")

    # ── 1. CH Discount Reasons ────────────────────────────────────────────────
    print("\n[1] CH Discount Reasons")
    # Names MUST match the Select options on Sales Invoice.custom_discount_reason
    _ensure("CH Discount Reason", "Customer Negotiation", {
        "name": "Customer Negotiation",
        "reason_name": "Customer Negotiation",
        "enabled": 1,
        "discount_type": "Percentage",
        "discount_value": 10,
        "allow_manual_entry": 1,
        "max_manual_percent": 15,
    })
    _ensure("CH Discount Reason", "Store Manager Discretion", {
        "name": "Store Manager Discretion",
        "reason_name": "Store Manager Discretion",
        "enabled": 1,
        "discount_type": "Percentage",
        "discount_value": 20,
        "allow_manual_entry": 1,
        "max_manual_percent": 30,
    })
    _ensure("CH Discount Reason", "Test Clearance", {
        "name": "Test Clearance",
        "reason_name": "Test Clearance",
        "enabled": 1,
        "discount_type": "Percentage",
        "discount_value": 10,
        "allow_manual_entry": 1,
        "max_manual_percent": 20,
    })

    # ── 2. CH Item Offers — payment discounts ─────────────────────────────────
    print("\n[2] CH Item Offers (Payment Discounts)")

    # Resolve company + item group for offers
    company = frappe.db.get_single_value("Global Defaults", "default_company") or "GoGizmo Retail Pvt Ltd"
    item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"

    start = today()
    end   = add_days(today(), 365)

    # 2a. UPI Cashback — 5% off on UPI payment, Bill level
    _ensure("CH Item Offer", "TEST-OFFER-UPI-CASHBACK", {
        "name": "TEST-OFFER-UPI-CASHBACK",
        "offer_name": "UPI Cashback 5%",
        "status": "Active",
        "approval_status": "Approved",
        "offer_type": "Cashback",
        "offer_level": "Bill",
        "value_type": "Percentage",
        "value": 5,
        "channel": "POS",
        "payment_mode": "UPI",
        "min_bill_amount": 1000,
        "company": company,
        "start_date": start,
        "end_date": end,
        "priority": 5,
        "stackable": 0,
    }, "UPI Cashback 5%")

    # 2b. Credit Card Bank Offer — ₹500 off on HDFC Credit Card, bill ≥ ₹10,000
    _ensure("CH Item Offer", "TEST-OFFER-CC-HDFC", {
        "name": "TEST-OFFER-CC-HDFC",
        "offer_name": "HDFC Credit Card ₹500 Off",
        "status": "Active",
        "approval_status": "Approved",
        "offer_type": "Bank Offer",
        "offer_level": "Bill",
        "value_type": "Amount",
        "value": 500,
        "channel": "POS",
        "payment_mode": "Credit Card",
        "bank_name": "HDFC",
        "card_type": "Credit",
        "min_bill_amount": 10000,
        "company": company,
        "start_date": start,
        "end_date": end,
        "priority": 3,
        "stackable": 0,
    }, "HDFC Credit Card ₹500 Off")

    # 2c. Flat discount — ₹200 off any item in the store (Item level), no MOP restriction
    _ensure("CH Item Offer", "TEST-OFFER-FLAT-200", {
        "name": "TEST-OFFER-FLAT-200",
        "offer_name": "Store Flat ₹200 Off",
        "status": "Active",
        "approval_status": "Approved",
        "offer_type": "Flat Discount",
        "offer_level": "Item",
        "apply_on": "Item Group",
        "target_item_group": item_group,
        "value_type": "Amount",
        "value": 200,
        "channel": "POS",
        "min_bill_amount": 2000,
        "company": company,
        "start_date": start,
        "end_date": end,
        "priority": 10,
        "stackable": 1,
    }, "Store Flat ₹200 Off")

    # ── 3. Pricing Rule + Coupon Code for S3 ─────────────────────────────────
    print("\n[3] Pricing Rule + Coupon Code (S3 coupon test)")

    pr_title = "Test Coupon 10% Off"
    existing_pr = frappe.db.get_value("Pricing Rule", {"title": pr_title}, "name")
    if not existing_pr:
        pr = frappe.new_doc("Pricing Rule")
        pr.title = pr_title
        pr.apply_on = "Transaction"
        pr.price_or_product_discount = "Price"
        pr.selling = 1
        pr.buying = 0
        pr.discount_percentage = 10
        pr.coupon_code_based = 1
        pr.valid_from = start
        pr.valid_upto = end
        pr.company = company
        pr.flags.ignore_permissions = True
        pr.flags.ignore_mandatory = True
        pr.insert()
        frappe.db.commit()
        existing_pr = pr.name
        print(f"  + Created Pricing Rule: '{existing_pr}' (title={pr_title})")
    else:
        print(f"  ✓ Pricing Rule: '{existing_pr}' (title={pr_title}) already exists")

    coupon_doc_name = "Test 10% Off Coupon"   # Coupon Code uses coupon_name as pk
    if not frappe.db.exists("Coupon Code", coupon_doc_name):
        cc = frappe.new_doc("Coupon Code")
        cc.coupon_code = "TESTCOUPON10"
        cc.coupon_name = coupon_doc_name
        cc.coupon_type = "Promotional"
        cc.pricing_rule = existing_pr
        cc.valid_from = start
        cc.valid_upto = end
        cc.maximum_use = 100
        cc.used = 0
        cc.flags.ignore_permissions = True
        cc.insert()
        frappe.db.commit()
        print(f"  + Created Coupon Code: 'TESTCOUPON10' ({coupon_doc_name})")
    else:
        print(f"  ✓ Coupon Code: 'TESTCOUPON10' ({coupon_doc_name}) already exists")
        # Reset used count so it's reusable for tests
        frappe.db.set_value("Coupon Code", coupon_doc_name, "used", 0)
        frappe.db.commit()

    # ── 5. Stock replenishment ────────────────────────────────────────────────
    # Ensure the test items always have enough stock.
    # Sales Invoice reserved qty reduces available stock without reducing Bin qty
    # (until POS Closing Entry). We top-up when availability drops below 5.
    print("\n[5] Stock Replenishment")
    _replenish_stock_if_needed()

    # Ensure QA loyalty program has accounting configuration for redemption tests
    if frappe.db.exists("Loyalty Program", "QA Buyback Loyalty"):
        if not frappe.db.get_value("Loyalty Program", "QA Buyback Loyalty", "expense_account"):
            frappe.db.set_value(
                "Loyalty Program",
                "QA Buyback Loyalty",
                "expense_account",
                "Sales Expenses - GGR",
                update_modified=False,
            )
            frappe.db.commit()
            print("  + Updated QA Buyback Loyalty expense account: Sales Expenses - GGR")

    # ── 6. Summary ────────────────────────────────────────────────────────────
    print("\n[6] Verification")
    offers = frappe.get_all("CH Item Offer",
        filters={"status": "Active", "name": ["like", "TEST-OFFER-%"]},
        fields=["name", "offer_name", "offer_type", "payment_mode", "value", "value_type"])
    for o in offers:
        print(f"  ✓ Offer: {o.name} — {o.offer_name} ({o.offer_type}, {o.value_type}={o.value}, MOP={o.payment_mode})")

    lp = frappe.get_all("Loyalty Program",
        fields=["name", "loyalty_program_type", "conversion_factor", "expiry_duration"], limit=3)
    print(f"  ✓ Loyalty Programs: {[l.name for l in lp]}")

    print("\n══════════════════════════════════════════")
    print("  Done. Test master data is ready.")
    print("══════════════════════════════════════════\n")
