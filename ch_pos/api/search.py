import frappe
from frappe.utils import cint, flt


@frappe.whitelist()
def pos_item_search(
    search_term="",
    pos_profile=None,
    filters=None,
    page=0,
    page_size=20,
    company=None,
    usage_context="sale",
):
    """Unified POS item search across item_code, item_name, barcode, model, brand."""
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)
    filters = filters or {}
    page = cint(page)
    page_size = min(cint(page_size) or 20, 100)

    profile_doc = frappe.get_cached_doc("POS Profile", pos_profile) if pos_profile else None
    warehouse = profile_doc.warehouse if profile_doc else None

    conditions = ["i.disabled = 0", "i.is_sales_item = 1", "i.has_variants = 0"]
    values = {}

    if search_term:
        conditions.append(
            "(i.name LIKE %(search)s OR i.item_name LIKE %(search)s"
            " OR i.brand LIKE %(search)s"
            " OR EXISTS (SELECT 1 FROM `tabItem Barcode` ib"
            "   WHERE ib.parent = i.name AND ib.barcode LIKE %(search)s))"
        )
        values["search"] = f"%{search_term}%"

    if filters.get("item_group"):
        conditions.append("i.item_group = %(item_group)s")
        values["item_group"] = filters["item_group"]

    if filters.get("brand"):
        conditions.append("i.brand = %(brand)s")
        values["brand"] = filters["brand"]

    if filters.get("in_stock_only") and warehouse:
        conditions.append(
            "EXISTS (SELECT 1 FROM `tabBin` b"
            " WHERE b.item_code = i.name AND b.warehouse = %(wh)s AND b.actual_qty > 0)"
        )
        values["wh"] = warehouse

    # Company filter: only show items allowed for the active company
    if company:
        conditions.append(
            "(NOT EXISTS (SELECT 1 FROM `tabPOS Allowed Company` pac"
            "   WHERE pac.parenttype = 'Item' AND pac.parent = i.name)"
            " OR EXISTS (SELECT 1 FROM `tabPOS Allowed Company` pac"
            "   WHERE pac.parenttype = 'Item' AND pac.parent = i.name"
            "   AND pac.company = %(pos_company)s))"
        )
        values["pos_company"] = company

    # Usage context filter: sale, repair, or both
    usage_context = (usage_context or "sale").lower()
    if usage_context == "sale":
        conditions.append(
            "(IFNULL(i.custom_pos_usage, '') IN ('', 'Sale', 'Sale and Repair'))"
        )
    elif usage_context == "repair":
        conditions.append(
            "(IFNULL(i.custom_pos_usage, '') IN ('', 'Repair Only', 'Sale and Repair'))"
        )

    where = " AND ".join(conditions)

    total = frappe.db.sql(
        f"SELECT COUNT(*) FROM `tabItem` i WHERE {where}",
        values,
    )[0][0]

    items_raw = frappe.db.sql(
        f"""SELECT i.name as item_code, i.item_name, i.image, i.brand,
                   i.item_group, i.stock_uom, i.has_serial_no,
                   i.variant_of, i.ch_default_warranty_months, i.ch_item_type,
                   IFNULL(i.custom_pos_usage, '') as pos_usage
            FROM `tabItem` i
            WHERE {where}
            ORDER BY i.item_name
            LIMIT %(limit)s OFFSET %(offset)s""",
        {**values, "limit": page_size, "offset": page * page_size},
        as_dict=True,
    )

    items = []
    nearby_warehouses = _get_nearby_warehouses(pos_profile) if pos_profile else []
    for row in items_raw:
        enriched = _enrich_item(row, warehouse, profile_doc, nearby_warehouses)
        items.append(enriched)

    return {"items": items, "total": total}


def _enrich_item(row, warehouse, profile_doc, nearby_warehouses=None):
    """Add pricing, stock, offer, variant attributes, and nearby-store data to an item row."""

    # Variant attributes (Colour, Storage, RAM, etc.)
    row.attributes = frappe.db.get_all(
        "Item Variant Attribute",
        filters={"parent": row.item_code},
        fields=["attribute", "attribute_value"],
        order_by="idx",
    )

    # Use the ch_item_type field from Item master (set by ch_item_master)
    # Only fall back to name-based heuristic if the field is not set
    if not row.get("ch_item_type"):
        name_lower = (row.item_name or "").lower()
        if "refurb" in name_lower or "renewed" in name_lower:
            row.ch_item_type = "Refurbished"
        elif "display" in name_lower or "demo" in name_lower:
            row.ch_item_type = "Display"
        elif "pre-owned" in name_lower or "preowned" in name_lower or "used" in name_lower:
            row.ch_item_type = "Pre-Owned"

    # Condition grade — detect from item name for Refurbished / Pre-Owned items
    row.condition_grade = ""
    if row.get("ch_item_type") in ("Refurbished", "Pre-Owned"):
        name_lower = (row.item_name or "").lower()
        for grade in ["Superb", "Good", "Fair", "Excellent"]:
            if grade.lower() in name_lower:
                row.condition_grade = grade
                break

    # CH Item Price (POS channel)
    ch_price = frappe.db.get_value(
        "CH Item Price",
        {"item_code": row.item_code, "channel": "POS", "status": "Active"},
        ["selling_price", "mrp", "mop"],
        as_dict=True,
    )
    row.selling_price = flt(ch_price.selling_price) if ch_price else 0
    row.mrp = flt(ch_price.mrp) if ch_price else 0

    # Stock qty
    if warehouse:
        row.stock_qty = flt(
            frappe.db.get_value("Bin", {"item_code": row.item_code, "warehouse": warehouse}, "actual_qty")
        )
    else:
        row.stock_qty = 0

    # Active offers
    row.offers = _get_item_offers(row.item_code)

    # Nearby store stock (only for out-of-stock or low-stock items)
    row.nearby_stores = []
    if nearby_warehouses and row.stock_qty <= 0:
        for ns in nearby_warehouses:
            qty = flt(frappe.db.get_value(
                "Bin", {"item_code": row.item_code, "warehouse": ns["warehouse"]}, "actual_qty"
            ))
            if qty > 0:
                row.nearby_stores.append({
                    "store_name": ns["store_name"],
                    "store_code": ns["store_code"],
                    "city": ns["city"],
                    "qty": qty,
                })

    return row


def _get_item_offers(item_code):
    today = frappe.utils.today()
    return frappe.db.get_all(
        "CH Item Offer",
        filters={
            "item_code": item_code,
            "channel": "POS",
            "status": "Active",
            "start_date": ["<=", today],
            "end_date": [">=", today],
        },
        fields=["name", "offer_name", "offer_type", "value_type", "value"],
        order_by="priority asc",
    )


def _get_nearby_warehouses(pos_profile):
    """Return nearby store warehouses based on city/pincode of the current store."""
    ext = frappe.db.get_value(
        "POS Profile Extension", pos_profile, ["store"], as_dict=True
    )
    if not ext or not ext.store:
        return []

    current = frappe.db.get_value(
        "CH Store", ext.store,
        ["store_code", "city", "pincode", "warehouse"],
        as_dict=True,
    )
    if not current or not current.warehouse:
        return []

    # Find other stores in same city (or same pincode prefix for nearby)
    conditions = ["s.disabled = 0", "s.warehouse IS NOT NULL", "s.store_code != %(cur)s"]
    values = {"cur": current.store_code}

    if current.city:
        conditions.append("s.city = %(city)s")
        values["city"] = current.city
    elif current.pincode:
        # Same pincode prefix (first 3 digits = same region)
        conditions.append("s.pincode LIKE %(pin_prefix)s")
        values["pin_prefix"] = current.pincode[:3] + "%"
    else:
        return []

    where = " AND ".join(conditions)
    stores = frappe.db.sql(
        f"""SELECT s.store_code, s.store_name, s.city, s.pincode, s.warehouse
            FROM `tabCH Store` s
            WHERE {where}
            ORDER BY
                CASE WHEN s.pincode = %(cur_pin)s THEN 0
                     WHEN s.pincode LIKE %(pin3)s THEN 1
                     ELSE 2 END,
                s.store_name
            LIMIT 10""",
        {**values, "cur_pin": current.pincode or "", "pin3": (current.pincode or "")[:3] + "%"},
        as_dict=True,
    )
    return stores


@frappe.whitelist()
def get_nearby_stock(item_code, pos_profile):
    """Full nearby-store stock check for a single item (for item detail popup)."""
    stores = _get_nearby_warehouses(pos_profile)
    result = []
    for s in stores:
        qty = flt(frappe.db.get_value(
            "Bin", {"item_code": item_code, "warehouse": s.warehouse}, "actual_qty"
        ))
        result.append({
            "store_name": s.store_name,
            "store_code": s.store_code,
            "city": s.city,
            "pincode": s.pincode,
            "qty": qty,
        })
    return result


@frappe.whitelist()
def get_available_serials(item_code, warehouse):
    """Return list of available serial numbers for an item in a warehouse."""
    serials = frappe.db.sql(
        """SELECT sn.name as serial_no, sn.warranty_expiry_date
           FROM `tabSerial No` sn
           WHERE sn.item_code = %(item_code)s
             AND sn.warehouse = %(warehouse)s
             AND sn.status = 'Active'
           ORDER BY sn.name""",
        {"item_code": item_code, "warehouse": warehouse},
        as_dict=True,
    )
    return serials


@frappe.whitelist()
def load_kiosk_token(token):
    """Load a POS Kiosk Token and return its items for cart population."""
    doc = frappe.get_doc("POS Kiosk Token", token)
    if doc.status != "Active":
        frappe.throw(f"Token {token} is {doc.status}.")
    if doc.expires_at and frappe.utils.now_datetime() > doc.expires_at:
        doc.db_set("status", "Expired")
        frappe.throw(f"Token {token} has expired.")

    return {
        "items": [
            {
                "item_code": d.item_code,
                "item_name": d.item_name,
                "qty": d.qty,
                "rate": d.rate,
                "amount": d.amount,
                "offer_applied": d.offer_applied,
            }
            for d in doc.items
        ],
        "total_estimate": doc.total_estimate,
    }


@frappe.whitelist()
def get_item_detail_for_pos(item_code, warehouse=None, price_list=None):
    """Detailed item info for POS item detail panel."""
    item = frappe.get_cached_doc("Item", item_code)

    # Specs from CH Model
    specs = {}
    model_name = frappe.db.get_value("Item", item_code, "ch_model")
    if model_name:
        model_doc = frappe.get_cached_doc("CH Model", model_name)
        specs = {sv.specification: sv.value for sv in (model_doc.spec_values or [])}

    # CH Item Price
    ch_price = frappe.db.get_value(
        "CH Item Price",
        {"item_code": item_code, "channel": "POS", "status": "Active"},
        ["selling_price", "mrp", "mop", "cost_price"],
        as_dict=True,
    ) or {}

    # Stock
    stock_qty = flt(
        frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
    ) if warehouse else 0

    # Warranty plans
    warranty_plans = frappe.db.get_all(
        "CH Warranty Plan",
        filters={
            "status": "Active",
            "brand": item.brand,
            "plan_type": ["in", ["Own Warranty", "Extended Warranty"]],
        },
        fields=["name", "plan_name", "price", "duration_months"],
    )

    return {
        "item_code": item_code,
        "item_name": item.item_name,
        "image": item.image,
        "brand": item.brand,
        "item_group": item.item_group,
        "specs": specs,
        "selling_price": flt(ch_price.get("selling_price")),
        "mrp": flt(ch_price.get("mrp")),
        "stock_qty": stock_qty,
        "offers": _get_item_offers(item_code),
        "warranty_plans": warranty_plans,
    }
