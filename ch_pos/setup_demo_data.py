"""
CH POS — Demo Data Setup

Creates POS Executive records, Incentive Slabs, sets Item POS mappings,
and fixes store warehouse links — enabling full testing of:
  - Executive access control (GoGizmo vs GoFix)
  - Company switcher in POS
  - Item filtering by company
  - Incentive calculation on billing
"""
import frappe
from frappe.utils import today


def execute():
    """Run all demo data setup steps."""
    _fix_store_warehouses()
    _create_test_users()
    _create_pos_executives()
    _create_incentive_slabs()
    _set_item_company_mappings()
    _seed_stock_and_prices()
    frappe.db.commit()
    print("✅ Demo data setup complete!")


# ── 1. Fix Store Warehouses ──────────────────────────────
def _fix_store_warehouses():
    """Link CH Store warehouses to match the actual POS warehouse."""
    store_wh = {
        "QA-VEL": "QA Velachery - GGR",
        "QA-ANN": "QA Anna Nagar - GGR",
        "QA-KIL": "QA Kilpauk - GGR",
    }
    for store_code, warehouse in store_wh.items():
        if frappe.db.exists("CH Store", store_code):
            frappe.db.set_value("CH Store", store_code, "warehouse", warehouse)
            print(f"  Store {store_code} → warehouse {warehouse}")


# ── 2. Create Test Users ─────────────────────────────────
def _create_test_users():
    """Create GoFix and GoGizmo test user accounts if they don't exist."""
    users = [
        {"email": "gofix_exec@test.com", "first_name": "Ravi", "last_name": "Kumar",
         "roles": ["Sales User"]},
        {"email": "gofix_mgr@test.com", "first_name": "Priya", "last_name": "Sharma",
         "roles": ["Sales User", "Sales Manager"]},
        {"email": "gogizmo_exec@test.com", "first_name": "Arun", "last_name": "Mohan",
         "roles": ["Sales User"]},
        {"email": "gogizmo_exec2@test.com", "first_name": "Deepa", "last_name": "Nair",
         "roles": ["Sales User"]},
    ]
    for u in users:
        if not frappe.db.exists("User", u["email"]):
            user = frappe.new_doc("User")
            user.email = u["email"]
            user.first_name = u["first_name"]
            user.last_name = u["last_name"]
            user.send_welcome_email = 0
            user.new_password = "Pos@Test#2026"
            for role in u["roles"]:
                user.append("roles", {"role": role})
            user.flags.ignore_password_policy = True
            user.insert(ignore_permissions=True)
            print(f"  Created user {u['email']}")
        else:
            print(f"  User {u['email']} already exists")


# ── 3. Create POS Executives ─────────────────────────────
def _create_pos_executives():
    """Create executive records at QA-VEL store for multiple companies."""
    executives = [
        # GoGizmo executives
        {
            "executive_name": "Arun Mohan",
            "user": "gogizmo_exec@test.com",
            "store": "QA-VEL",
            "company": "GoGizmo Retail Pvt Ltd",
            "role": "Executive",
            "can_give_discount": 1,
            "max_discount_pct": 5,
        },
        {
            "executive_name": "Deepa Nair",
            "user": "gogizmo_exec2@test.com",
            "store": "QA-VEL",
            "company": "GoGizmo Retail Pvt Ltd",
            "role": "Senior Executive",
            "can_give_discount": 1,
            "max_discount_pct": 10,
        },
        # GoFix executives
        {
            "executive_name": "Ravi Kumar",
            "user": "gofix_exec@test.com",
            "store": "QA-VEL",
            "company": "GoFix Services Pvt Ltd",
            "role": "Executive",
            "can_give_discount": 1,
            "max_discount_pct": 5,
        },
        # Manager — access to BOTH companies (collab store)
        {
            "executive_name": "Priya Sharma",
            "user": "gofix_mgr@test.com",
            "store": "QA-VEL",
            "company": "GoFix Services Pvt Ltd",
            "role": "Manager",
            "can_give_discount": 1,
            "max_discount_pct": 20,
        },
        {
            "executive_name": "Priya Sharma",
            "user": "gofix_mgr@test.com",
            "store": "QA-VEL",
            "company": "GoGizmo Retail Pvt Ltd",
            "role": "Manager",
            "can_give_discount": 1,
            "max_discount_pct": 20,
        },
        # Administrator — for testing (both companies)
        {
            "executive_name": "Admin User",
            "user": "Administrator",
            "store": "QA-VEL",
            "company": "GoGizmo Retail Pvt Ltd",
            "role": "Manager",
            "can_give_discount": 1,
            "max_discount_pct": 100,
        },
        {
            "executive_name": "Admin User",
            "user": "Administrator",
            "store": "QA-VEL",
            "company": "GoFix Services Pvt Ltd",
            "role": "Manager",
            "can_give_discount": 1,
            "max_discount_pct": 100,
        },
    ]

    for ex in executives:
        existing = frappe.db.exists("POS Executive", {
            "user": ex["user"],
            "store": ex["store"],
            "company": ex["company"],
        })
        if existing:
            print(f"  POS Executive already exists: {ex['executive_name']} @ {ex['company']}")
            continue

        doc = frappe.new_doc("POS Executive")
        for k, v in ex.items():
            doc.set(k, v)
        doc.is_active = 1
        doc.insert(ignore_permissions=True)
        print(f"  Created POS Executive: {doc.name} — {ex['executive_name']} ({ex['role']}) @ {ex['company']}")


# ── 4. Create Incentive Slabs ────────────────────────────
def _create_incentive_slabs():
    """Create sample incentive slabs for GoGizmo and GoFix."""
    if frappe.db.count("POS Incentive Slab", {"is_active": 1}) > 0:
        print("  Incentive slabs already exist, skipping")
        return

    slabs = [
        # GoGizmo — Smartphones (high-value)
        {
            "company": "GoGizmo Retail Pvt Ltd",
            "item_group": "Smartphones",
            "applicable_on": "Sale",
            "priority": 10,
            "from_amount": 0,
            "to_amount": 15000,
            "incentive_type": "Fixed Amount",
            "incentive_value": 100,
            "description": "Smartphone sale up to ₹15K",
        },
        {
            "company": "GoGizmo Retail Pvt Ltd",
            "item_group": "Smartphones",
            "applicable_on": "Sale",
            "priority": 10,
            "from_amount": 15001,
            "to_amount": 50000,
            "incentive_type": "Fixed Amount",
            "incentive_value": 250,
            "description": "Smartphone sale ₹15K–₹50K",
        },
        {
            "company": "GoGizmo Retail Pvt Ltd",
            "item_group": "Smartphones",
            "applicable_on": "Sale",
            "priority": 10,
            "from_amount": 50001,
            "to_amount": 200000,
            "incentive_type": "Percentage",
            "incentive_value": 0.5,
            "description": "Smartphone sale above ₹50K",
        },
        # GoGizmo — Accessories (lower incentive)
        {
            "company": "GoGizmo Retail Pvt Ltd",
            "item_group": "Accessories",
            "applicable_on": "Sale",
            "priority": 10,
            "from_amount": 0,
            "to_amount": 99999,
            "incentive_type": "Percentage",
            "incentive_value": 2,
            "description": "Accessory sale — 2%",
        },
        # GoGizmo — VAS incentive
        {
            "company": "GoGizmo Retail Pvt Ltd",
            "applicable_on": "VAS",
            "priority": 5,
            "from_amount": 0,
            "to_amount": 99999,
            "incentive_type": "Percentage",
            "incentive_value": 5,
            "description": "VAS plan — 5% incentive",
        },
        # GoFix — Repair Services
        {
            "company": "GoFix Services Pvt Ltd",
            "item_group": "Repair Services",
            "applicable_on": "Sale",
            "priority": 10,
            "from_amount": 0,
            "to_amount": 5000,
            "incentive_type": "Fixed Amount",
            "incentive_value": 50,
            "description": "Repair up to ₹5K",
        },
        {
            "company": "GoFix Services Pvt Ltd",
            "item_group": "Repair Services",
            "applicable_on": "Sale",
            "priority": 10,
            "from_amount": 5001,
            "to_amount": 50000,
            "incentive_type": "Percentage",
            "incentive_value": 1.5,
            "description": "Repair above ₹5K",
        },
        # GoFix — Accessory sales (GoFix can sell accessories too)
        {
            "company": "GoFix Services Pvt Ltd",
            "item_group": "Accessories",
            "applicable_on": "Sale",
            "priority": 10,
            "from_amount": 0,
            "to_amount": 99999,
            "incentive_type": "Percentage",
            "incentive_value": 3,
            "description": "GoFix accessory sale — 3%",
        },
        # Return clawback for both companies
        {
            "company": "GoGizmo Retail Pvt Ltd",
            "applicable_on": "Return",
            "priority": 1,
            "from_amount": 0,
            "to_amount": 999999,
            "incentive_type": "Percentage",
            "incentive_value": 0.5,
            "description": "Return clawback — GoGizmo",
        },
        {
            "company": "GoFix Services Pvt Ltd",
            "applicable_on": "Return",
            "priority": 1,
            "from_amount": 0,
            "to_amount": 999999,
            "incentive_type": "Percentage",
            "incentive_value": 1,
            "description": "Return clawback — GoFix",
        },
    ]

    for slab in slabs:
        doc = frappe.new_doc("POS Incentive Slab")
        for k, v in slab.items():
            doc.set(k, v)
        doc.is_active = 1
        doc.insert(ignore_permissions=True)
        print(f"  Created Incentive Slab: {slab['description']}")


# ── 5. Set Item POS Company Mappings ─────────────────────
def _set_item_company_mappings():
    """Set custom_pos_usage and custom_pos_allowed_companies on items.

    Rules:
    - Smartphones, Tablets, Laptops, Mobiles, Devices → GoGizmo only, Sale
    - Accessories → Both companies, Sale and Repair
    - Mobile Parts → GoFix only, some Sale and Repair, some Repair Only
    - Repair Services → GoFix only, Sale (billed via POS)
    """
    print("  Setting item POS mappings...")

    # GoGizmo-only items: phones, laptops, tablets
    gogizmo_groups = ["Smartphones", "Tablets", "Laptops", "Mobiles", "Devices"]
    gogizmo_items = frappe.get_all(
        "Item",
        filters={"item_group": ("in", gogizmo_groups), "disabled": 0, "has_variants": 0},
        pluck="name",
    )
    for item_code in gogizmo_items:
        _set_item_pos(item_code, "Sale", ["GoGizmo Retail Pvt Ltd"])

    print(f"    {len(gogizmo_items)} items → GoGizmo only (Sale)")

    # Accessories → both companies
    acc_items = frappe.get_all(
        "Item",
        filters={"item_group": "Accessories", "disabled": 0, "has_variants": 0},
        pluck="name",
    )
    for item_code in acc_items:
        _set_item_pos(item_code, "Sale and Repair", ["GoGizmo Retail Pvt Ltd", "GoFix Services Pvt Ltd"])

    print(f"    {len(acc_items)} accessories → Both companies (Sale and Repair)")

    # Mobile Parts → GoFix only, Repair Only
    parts_items = frappe.get_all(
        "Item",
        filters={"item_group": "Mobile Parts", "disabled": 0, "has_variants": 0},
        pluck="name",
    )
    for item_code in parts_items:
        _set_item_pos(item_code, "Repair Only", ["GoFix Services Pvt Ltd"])

    print(f"    {len(parts_items)} parts → GoFix only (Repair Only)")

    # Repair Services → GoFix only, Sale (billedthrough POS)
    svc_items = frappe.get_all(
        "Item",
        filters={"item_group": "Repair Services", "disabled": 0, "has_variants": 0},
        pluck="name",
    )
    for item_code in svc_items:
        _set_item_pos(item_code, "Sale and Repair", ["GoFix Services Pvt Ltd"])

    print(f"    {len(svc_items)} repair services → GoFix only (Sale and Repair)")


def _set_item_pos(item_code, usage, companies):
    """Set POS usage and allowed companies on an Item."""
    item = frappe.get_doc("Item", item_code)
    item.custom_pos_usage = usage

    # Clear existing allowed companies
    item.custom_pos_allowed_companies = []
    for comp in companies:
        item.append("custom_pos_allowed_companies", {"company": comp})

    item.flags.ignore_validate = True
    item.flags.ignore_mandatory = True
    item.save(ignore_permissions=True)


# ── 6. Seed Stock and Prices ─────────────────────────────
def _seed_stock_and_prices():
    """Create CH Item Price records and stock entries for QA Velachery."""
    warehouse = "QA Velachery - GGR"

    # Check if prices already seeded
    existing_prices = frappe.db.count("CH Item Price", {"channel": "POS", "status": "Active"})
    if existing_prices > 10:
        print(f"  {existing_prices} prices already exist, skipping price seed")
    else:
        _create_sample_prices()

    # Check stock
    stock_count = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabBin` WHERE warehouse=%s AND actual_qty > 0",
        warehouse,
    )[0][0]
    if stock_count > 5:
        print(f"  {stock_count} items in stock at {warehouse}, skipping stock seed")
    else:
        _create_stock_entries(warehouse)


def _create_sample_prices():
    """Create CH Item Price records for all active items."""
    import random

    print("  Creating CH Item Price records...")

    price_ranges = {
        "Smartphones": (8999, 149999),
        "Tablets": (15999, 89999),
        "Laptops": (29999, 199999),
        "Mobiles": (4999, 29999),
        "Devices": (4999, 79999),
        "Accessories": (199, 4999),
        "Mobile Parts": (299, 2999),
        "Repair Services": (499, 15999),
    }

    items = frappe.get_all(
        "Item",
        filters={"disabled": 0, "has_variants": 0},
        fields=["name", "item_group"],
    )

    count = 0
    for item in items:
        if frappe.db.exists("CH Item Price", {"item_code": item.name, "channel": "POS", "status": "Active"}):
            continue

        lo, hi = price_ranges.get(item.item_group, (999, 9999))
        selling = round(random.randint(lo, hi), -1)  # round to nearest 10
        mrp = round(selling * random.uniform(1.0, 1.15), -1)

        doc = frappe.new_doc("CH Item Price")
        doc.item_code = item.name
        doc.channel = "POS"
        doc.selling_price = selling
        doc.mrp = mrp
        doc.mop = selling
        doc.status = "Active"
        doc.effective_from = today()
        try:
            doc.insert(ignore_permissions=True)
            count += 1
        except Exception as e:
            print(f"    Price skip {item.name}: {e}")

    print(f"    Created {count} CH Item Prices")


def _create_stock_entries(warehouse):
    """Create stock entries to seed inventory."""
    import random

    print(f"  Seeding stock at {warehouse}...")

    # Get serialized items (phones, laptops, tablets)
    serial_items = frappe.get_all(
        "Item",
        filters={
            "disabled": 0,
            "has_variants": 0,
            "has_serial_no": 1,
            "item_group": ("in", ["Smartphones", "Tablets", "Laptops", "Mobiles"]),
        },
        fields=["name", "item_name", "item_group"],
        limit=20,
    )

    # Get non-serial items (accessories, parts, services)
    nonserial_items = frappe.get_all(
        "Item",
        filters={
            "disabled": 0,
            "has_variants": 0,
            "has_serial_no": 0,
            "is_stock_item": 1,
            "item_group": ("in", ["Accessories", "Mobile Parts", "Devices"]),
        },
        fields=["name", "item_name"],
        limit=20,
    )

    company = "GoGizmo Retail Pvt Ltd"

    # Stock Entry for serialized items — 2–4 units each
    for item in serial_items:
        qty = random.randint(2, 4)
        serials = []
        for i in range(qty):
            serial = f"SR-{item.name[-8:]}-{random.randint(10000, 99999)}"
            serials.append(serial)

        try:
            se = frappe.new_doc("Stock Entry")
            se.stock_entry_type = "Material Receipt"
            se.company = company
            se.to_warehouse = warehouse
            se.append("items", {
                "item_code": item.name,
                "qty": qty,
                "serial_no": "\n".join(serials),
                "t_warehouse": warehouse,
                "basic_rate": 1000,
            })
            se.insert(ignore_permissions=True)
            se.submit()
            print(f"    Stocked {qty}x {item.name} (serials: {', '.join(serials[:2])}...)")
        except Exception as e:
            print(f"    Stock skip {item.name}: {e}")

    # Stock Entry for non-serial items — 10–50 units each
    if nonserial_items:
        try:
            se = frappe.new_doc("Stock Entry")
            se.stock_entry_type = "Material Receipt"
            se.company = company
            for item in nonserial_items:
                qty = random.randint(10, 50)
                se.append("items", {
                    "item_code": item.name,
                    "qty": qty,
                    "t_warehouse": warehouse,
                    "basic_rate": 500,
                })
            se.insert(ignore_permissions=True)
            se.submit()
            print(f"    Stocked {len(nonserial_items)} non-serial items")
        except Exception as e:
            print(f"    Non-serial stock error: {e}")

    frappe.db.commit()
