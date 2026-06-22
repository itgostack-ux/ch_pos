import frappe


@frappe.whitelist()
def search_items_by_name(doctype, txt, searchfield, start, page_len, filters):
    """
    Custom Item search for POS walk-in dialog.
    - Returns item_code as the stored value (required by Link field)
    - Returns item_name as the visible label in the dropdown
    """
    txt_like = f"%{txt or ''}%"

    items = frappe.db.sql(
        """
        SELECT
            item_code,
            item_name
        FROM `tabItem`
        WHERE disabled = 0
            AND (item_name LIKE %(txt)s OR item_code LIKE %(txt)s)
        ORDER BY
            CASE WHEN item_name LIKE %(txt)s THEN 0 ELSE 1 END,
            item_name ASC
        LIMIT %(start)s, %(page_len)s
        """,
        {
            "txt": txt_like,
            "start": int(start or 0),
            "page_len": int(page_len or 20),
        },
    )

  
    return [(item_code, item_name, "") for item_code, item_name in items]