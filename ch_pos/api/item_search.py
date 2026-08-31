import frappe
from frappe.utils import cint


@frappe.whitelist()
def search_items_by_name(doctype, txt, searchfield, start, page_len, filters):
    """
    Custom Item search for POS walk-in dialog.
    - Returns item_code as the stored value (required by Link field)
    - Returns item_name as the visible label in the dropdown
    - Optional filters.ch_model narrows to items of that CH Model, once the
      walk-in dialog's Brand -> Model -> Item cascade has a model selected.
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
    start = max(0, cint(start))
    page_len = max(1, min(cint(page_len) or 20, 100))
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabBrand`
        WHERE name LIKE %(txt)s
        ORDER BY name ASC
        LIMIT %(start)s, %(page_len)s
        """,
        {"txt": f"%{txt or ''}%", "start": start, "page_len": page_len},
    )
    return [(name, "") for (name,) in rows]


@frappe.whitelist()
def autocomplete_ch_models(txt="", brand=None):
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
