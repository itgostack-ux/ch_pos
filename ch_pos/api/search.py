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
        # For serial-tracked items check Active serials; for others check Bin qty
        conditions.append(
            "(CASE WHEN i.has_serial_no = 1 THEN"
            " EXISTS (SELECT 1 FROM `tabSerial No` sn"
            "   WHERE sn.item_code = i.name AND sn.warehouse = %(wh)s AND sn.status = 'Active')"
            " ELSE"
            " EXISTS (SELECT 1 FROM `tabBin` b"
            "   WHERE b.item_code = i.name AND b.warehouse = %(wh)s AND b.actual_qty > 0)"
            " END)"
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

    if items_raw:
        item_codes = [r.item_code for r in items_raw]

        # Batch-fetch variant attributes
        all_attrs = frappe.get_all(
            "Item Variant Attribute",
            filters={"parent": ("in", item_codes)},
            fields=["parent", "attribute", "attribute_value", "idx"],
            order_by="parent, idx",
        )
        attrs_map = {}
        for a in all_attrs:
            attrs_map.setdefault(a.parent, []).append(
                {"attribute": a.attribute, "attribute_value": a.attribute_value}
            )

        # Batch-fetch CH Item Prices (POS channel)
        all_prices = frappe.get_all(
            "CH Item Price",
            filters={"item_code": ("in", item_codes), "channel": "POS", "status": "Active"},
            fields=["item_code", "selling_price", "mrp", "mop"],
        )
        price_map = {p.item_code: p for p in all_prices}

        # Batch-fetch stock from Bin
        all_bins = []
        if warehouse:
            all_bins = frappe.get_all(
                "Bin",
                filters={"item_code": ("in", item_codes), "warehouse": warehouse},
                fields=["item_code", "actual_qty"],
            )
        stock_map = {b.item_code: flt(b.actual_qty) for b in all_bins}

        # For serial-tracked items, use count of Active serials (stays in sync with IMEI picker)
        if warehouse:
            serial_item_codes = [r.item_code for r in items_raw if r.has_serial_no]
            if serial_item_codes:
                serial_counts = frappe.db.sql(
                    """SELECT item_code, COUNT(*) as cnt
                       FROM `tabSerial No`
                       WHERE item_code IN %(item_codes)s
                         AND warehouse = %(warehouse)s
                         AND status = 'Active'
                       GROUP BY item_code""",
                    {"item_codes": serial_item_codes, "warehouse": warehouse},
                    as_dict=True,
                )
                for sc in serial_counts:
                    stock_map[sc.item_code] = sc.cnt

        # Batch-fetch active offers
        today = frappe.utils.today()
        all_offers = frappe.get_all(
            "CH Item Offer",
            filters={
                "item_code": ("in", item_codes),
                "channel": "POS",
                "status": "Active",
                "start_date": ("<=", today),
                "end_date": (">=", today),
            },
            fields=["item_code", "name", "offer_name", "offer_type", "value_type", "value", "priority"],
            order_by="priority asc",
        )
        offers_map = {}
        for o in all_offers:
            offers_map.setdefault(o.item_code, []).append({
                "name": o.name, "offer_name": o.offer_name,
                "offer_type": o.offer_type, "value_type": o.value_type, "value": o.value,
            })

        # Batch-fetch nearby store stock (only needed item_codes)
        nearby_wh_names = [ns["warehouse"] for ns in nearby_warehouses] if nearby_warehouses else []
        nearby_stock_map = {}
        if nearby_wh_names:
            out_of_stock_items = [ic for ic in item_codes if stock_map.get(ic, 0) <= 0]
            if out_of_stock_items:
                nearby_bins = frappe.get_all(
                    "Bin",
                    filters={
                        "item_code": ("in", out_of_stock_items),
                        "warehouse": ("in", nearby_wh_names),
                        "actual_qty": (">", 0),
                    },
                    fields=["item_code", "warehouse", "actual_qty"],
                )
                for nb in nearby_bins:
                    nearby_stock_map.setdefault(nb.item_code, []).append(
                        {"warehouse": nb.warehouse, "qty": flt(nb.actual_qty)}
                    )

        # Build a warehouse → store info lookup
        wh_store_map = {}
        if nearby_warehouses:
            for ns in nearby_warehouses:
                wh_store_map[ns["warehouse"]] = ns

        for row in items_raw:
            row.attributes = attrs_map.get(row.item_code, [])

            # ch_item_type fallback heuristic
            if not row.get("ch_item_type"):
                name_lower = (row.item_name or "").lower()
                if "refurb" in name_lower or "renewed" in name_lower:
                    row.ch_item_type = "Refurbished"
                elif "display" in name_lower or "demo" in name_lower:
                    row.ch_item_type = "Display"
                elif "pre-owned" in name_lower or "preowned" in name_lower or "used" in name_lower:
                    row.ch_item_type = "Pre-Owned"

            # Condition grade
            row.condition_grade = ""
            if row.get("ch_item_type") in ("Refurbished", "Pre-Owned"):
                name_lower = (row.item_name or "").lower()
                for grade in ["Superb", "Good", "Fair", "Excellent"]:
                    if grade.lower() in name_lower:
                        row.condition_grade = grade
                        break

            # Pricing
            ch_price = price_map.get(row.item_code)
            row.selling_price = flt(ch_price.selling_price) if ch_price else 0
            row.mrp = flt(ch_price.mrp) if ch_price else 0

            # Stock
            row.stock_qty = stock_map.get(row.item_code, 0)

            # Offers
            row.offers = offers_map.get(row.item_code, [])

            # Nearby stores
            row.nearby_stores = []
            if nearby_warehouses and row.stock_qty <= 0:
                for ns_bin in nearby_stock_map.get(row.item_code, []):
                    store_info = wh_store_map.get(ns_bin["warehouse"])
                    if store_info:
                        row.nearby_stores.append({
                            "store_name": store_info["store_name"],
                            "store_code": store_info["store_code"],
                            "city": store_info["city"],
                            "qty": ns_bin["qty"],
                        })

            items.append(row)

    return {"items": items, "total": total}


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
    """Return list of available serial numbers for an item in a warehouse, FIFO ordered."""
    # Primary: use SNBB inward dates for true FIFO order
    rows = frappe.db.sql("""
        SELECT
            sn.name AS serial_no,
            sn.warranty_expiry_date,
            MIN(sbb.posting_date) AS inward_date
        FROM `tabSerial No` sn
        LEFT JOIN `tabSerial and Batch Entry` sbe ON sbe.serial_no = sn.name
        LEFT JOIN `tabSerial and Batch Bundle` sbb
            ON sbe.parent = sbb.name
            AND sbb.type_of_transaction = 'Inward'
            AND sbb.docstatus = 1
        WHERE sn.item_code = %(item_code)s
          AND sn.warehouse = %(warehouse)s
          AND sn.status = 'Active'
        GROUP BY sn.name, sn.warranty_expiry_date
        ORDER BY inward_date ASC, sn.name ASC
    """, {"item_code": item_code, "warehouse": warehouse}, as_dict=True)

    # Tag the first (oldest) serial so the UI can show a "Sell First" badge
    for i, r in enumerate(rows):
        r["is_oldest"] = 1 if i == 0 else 0
    return rows


@frappe.whitelist()
def load_kiosk_token(token, pos_profile=None):
    """Load a POS Kiosk Token and return its items for cart population.
    
    Also increments the kiosk walk-in counter on the active session log.
    """
    doc = frappe.get_doc("POS Kiosk Token", token)
    if doc.status != "Active":
        frappe.throw(f"Token {token} is {doc.status}.")
    if doc.expires_at and frappe.utils.now_datetime() > doc.expires_at:
        doc.db_set("status", "Expired")
        frappe.throw(f"Token {token} has expired.")

    # Note: kiosk walk-in is already tracked via POS Kiosk Token (created by create_token).
    # Legacy session-log counter bump removed — footfall now derived from token records.

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
        "customer_name": doc.customer_name,
        "customer_phone": doc.customer_phone,
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
