import frappe
from frappe.utils import cint


@frappe.whitelist()
def search_items_by_name(doctype, txt, searchfield, start, page_len, filters):
    """
    Custom Item search for POS walk-in dialog.
    - Returns item_code as the stored value (required by Link field)
    - Returns item_name as the visible label in the dropdown
    - Optional filters.ch_model / filters.ch_category narrow the list as the
      walk-in dialog's Category -> Brand -> Model -> Item cascade is filled in.

    Deliberately NOT filtered by stock, sellability or lifecycle. A walk-in is
    a record of what the customer ASKED FOR, and the most valuable rows are the
    ones we could not sell -- an item filtered out for being out of stock is
    exactly the demand signal this form exists to capture.
    """
    frappe.has_permission("Item", "read", throw=True)
    filters = frappe.parse_json(filters) or {}
    txt_like = f"%{txt or ''}%"
    start = max(0, cint(start))
    page_len = max(1, min(cint(page_len) or 20, 100))

    conditions = ["disabled = 0", "(item_name LIKE %(txt)s OR item_code LIKE %(txt)s)"]
    values = {"txt": txt_like, "start": start, "page_len": page_len}
    ch_model = filters.get("ch_model")
    if ch_model:
        conditions.append("ch_model = %(ch_model)s")
        values["ch_model"] = ch_model
    ch_category = filters.get("ch_category")
    if ch_category:
        conditions.append("ch_category = %(ch_category)s")
        values["ch_category"] = ch_category

    items = frappe.db.sql(
        f"""
        SELECT
            item_code,
            item_name
        FROM `tabItem`
        WHERE {' AND '.join(conditions)}
        ORDER BY
            CASE WHEN item_name LIKE %(txt)s THEN 0 ELSE 1 END,
            item_name ASC
        LIMIT %(start)s, %(page_len)s
        """,
        values,
    )

    return [(item_code, item_name, "") for item_code, item_name in items]


@frappe.whitelist()
def search_brands(doctype, txt, searchfield, start, page_len, filters):
    """Brand search for the POS walk-in dialog — the standard Link-field
    default search silently returns nothing inside this SPA's dialog
    context (unlike a full Desk form), the same way it did for Item until
    search_items_by_name above was added as an explicit custom query. A
    plain raw-SQL search sidesteps whatever that default path needs and
    doesn't have here.
    """
    frappe.has_permission("Brand", "read", throw=True)
    filters = frappe.parse_json(filters) or {}
    start = max(0, cint(start))
    page_len = max(1, min(cint(page_len) or 20, 100))
    values = {"txt": f"%{txt or ''}%", "start": start, "page_len": page_len}

    # With a category chosen, offer only brands that actually sell into it.
    # Brand carries no category of its own, so the link is made through Item --
    # existence only, with no stock or sellability condition, so a brand we
    # stock nothing from is still offerable.
    category_join = ""
    if filters.get("ch_category"):
        category_join = """
          AND EXISTS (
            SELECT 1 FROM `tabItem` i
            WHERE i.brand = b.name AND i.disabled = 0
              AND i.ch_category = %(ch_category)s
          )"""
        values["ch_category"] = filters["ch_category"]

    rows = frappe.db.sql(
        f"""
        SELECT b.name
        FROM `tabBrand` b
        WHERE b.name LIKE %(txt)s {category_join}
        ORDER BY b.name ASC
        LIMIT %(start)s, %(page_len)s
        """,
        values,
    )
    return [(name, "") for (name,) in rows]


@frappe.whitelist()
def autocomplete_ch_models(txt="", brand=None, ch_category=None):
    """CH Model source for the walk-in dialog's Model field, which uses
    fieldtype Autocomplete rather than Link. CH Model's own name is an
    internal compound key, and CH Model has show_title_field_in_link on so
    a Link field's own display SHOULD show model_name instead — but that
    depends on Frappe's client-side link-title cache/timing, which raced
    against selection here and left the field blank after picking a model.
    Autocomplete has no such race: {value, label} pairs it returns are
    looked up locally (see ControlAutocomplete.format_for_input/
    get_input_value in frappe's autocomplete.js), so the label always
    displays deterministically and the raw name is still what gets stored.
    """
    frappe.has_permission("CH Model", "read", throw=True)
    conditions = ["disabled = 0", "model_name LIKE %(txt)s"]
    values = {"txt": f"%{txt or ''}%"}
    if brand:
        conditions.append("brand = %(brand)s")
        values["brand"] = brand
    if ch_category:
        # CH Model has no category column -- only brand and model_name -- so a
        # model belongs to a category through the items built on it.
        conditions.append(
            """EXISTS (
                SELECT 1 FROM `tabItem` i
                WHERE i.ch_model = `tabCH Model`.name AND i.disabled = 0
                  AND i.ch_category = %(ch_category)s
            )"""
        )
        values["ch_category"] = ch_category

    rows = frappe.db.sql(
        f"""
        SELECT name, model_name
        FROM `tabCH Model`
        WHERE {' AND '.join(conditions)}
        ORDER BY model_name ASC
        LIMIT 20
        """,
        values,
    )
    return [{"value": name, "label": model_name} for name, model_name in rows]


@frappe.whitelist()
def search_ch_models(doctype, txt, searchfield, start, page_len, filters):
    """CH Model as a Link-field query, for Desk forms.

    autocomplete_ch_models above answers the POS walk-in dialog, whose Model
    field is an Autocomplete and so takes {value, label} pairs. A Desk Link
    field calls its query with Frappe's positional search signature and expects
    rows, so the two cannot share one function. The filtering is the same, and
    the second column is model_name so the dropdown shows the readable name
    rather than CH Model's internal compound key.
    """
    frappe.has_permission("CH Model", "read", throw=True)
    filters = filters or {}
    conditions = ["disabled = 0", "(model_name LIKE %(txt)s OR name LIKE %(txt)s)"]
    values = {
        "txt": f"%{txt or ''}%",
        "start": int(start or 0),
        "page_len": int(page_len or 20),
    }
    if filters.get("brand"):
        conditions.append("brand = %(brand)s")
        values["brand"] = filters["brand"]
    if filters.get("ch_category"):
        # CH Model has no category column, so a model belongs to a category
        # through the items built on it -- same reasoning as above.
        conditions.append(
            """EXISTS (
                SELECT 1 FROM `tabItem` i
                WHERE i.ch_model = `tabCH Model`.name AND i.disabled = 0
                  AND i.ch_category = %(ch_category)s
            )"""
        )
        values["ch_category"] = filters["ch_category"]

    return frappe.db.sql(
        f"""
        SELECT name, model_name
        FROM `tabCH Model`
        WHERE {' AND '.join(conditions)}
        ORDER BY model_name ASC
        LIMIT %(page_len)s OFFSET %(start)s
        """,
        values,
    )


@frappe.whitelist()
def search_categories(doctype, txt, searchfield, start, page_len, filters):
    """CH Category source for the walk-in dialog's Category field.

    Uses an explicit query for the same reason Brand and Item do: the default
    Link search returns nothing inside this SPA's dialog context.

    Only categories that actually carry items are offered -- an empty category
    is noise at the counter -- but item stock is deliberately not consulted, so
    a category we are completely out of still appears. Recording that a
    customer asked for it is the entire point.
    """
    frappe.has_permission("CH Category", "read", throw=True)
    start = max(0, cint(start))
    page_len = max(1, min(cint(page_len) or 20, 100))
    rows = frappe.db.sql(
        """
        SELECT c.name
        FROM `tabCH Category` c
        WHERE c.name LIKE %(txt)s
          AND EXISTS (
            SELECT 1 FROM `tabItem` i
            WHERE i.ch_category = c.name AND i.disabled = 0
          )
        ORDER BY c.name ASC
        LIMIT %(start)s, %(page_len)s
        """,
        {"txt": f"%{txt or ''}%", "start": start, "page_len": page_len},
    )
    return [(name, "") for (name,) in rows]
