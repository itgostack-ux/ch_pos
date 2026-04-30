"""
CH Queue — Token System API
All public-facing endpoints for the kiosk and management dashboard.
"""

import re

import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime

from buyback.utils import normalize_indian_phone, validate_indian_phone


# ---------------------------------------------------------------------------
# Authority gate (Tier-1 — see ch_erp15.ch_erp15.auth.authority)
# ---------------------------------------------------------------------------

def _ensure_can_operate_token() -> None:
    """Raise PermissionError unless the user has Operate authority on tokens.

    Falls back to legacy role check if ch_erp15 isn't installed (defensive —
    ch_pos can run standalone in test envs).
    """
    try:
        from ch_erp15.ch_erp15.auth import authority as auth
    except ImportError:
        if not (set(frappe.get_roles()) & {"POS User", "POS Manager"}):
            frappe.throw(_("Not permitted"), frappe.PermissionError, title=_("API Error"))
        return
    if auth.can("Operate", "POS Kiosk Token") or auth.can("Override", "POS Kiosk Token"):
        return
    frappe.throw(_("Not permitted"), frappe.PermissionError, title=_("API Error"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INDIAN_PHONE_RE = re.compile(r"^[6-9]\d{9}$")

# Simple in-memory rate limiter for guest endpoints
_RATE_LIMIT_CACHE = {}  # key → list of timestamps
_RATE_LIMIT_MAX = 10  # max requests per window
_RATE_LIMIT_WINDOW = 3600  # 1 hour in seconds


def _check_rate_limit(key: str):
    """Raise if rate limit exceeded for this key (IP/store combo)."""
    import time
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW
    hits = _RATE_LIMIT_CACHE.get(key, [])
    hits = [t for t in hits if t > window_start]
    if len(hits) >= _RATE_LIMIT_MAX:
        frappe.throw(_("Too many requests. Please try again later."), frappe.RateLimitExceededError, title=_("API Error"))
    hits.append(now)
    _RATE_LIMIT_CACHE[key] = hits

def _normalize_phone(raw: str) -> str:
    """Alias for backward compatibility — delegates to shared utility."""
    return normalize_indian_phone(raw)

def _validate_indian_phone(raw: str) -> str:
    """Alias for backward compatibility — delegates to shared utility."""
    return validate_indian_phone(raw, "Phone number")

def _device_label(brand: str, model: str) -> str:
    """Return a clean device label, avoiding repeating the brand if model already starts with it."""
    brand = (brand or "").strip()
    model = (model or "").strip()
    if not model:
        return brand
    # If model already starts with brand name (case-insensitive), show model only
    if brand and model.lower().startswith(brand.lower()):
        return model
    return f"{brand} {model}".strip() if brand else model


def _get_store_code(pos_profile_name: str) -> str:
    """Generate a short store code from the POS Profile name."""
    # e.g. "QA Velachery POS" → "VELPOS", "T Nagar" → "TNAGAR"
    parts = pos_profile_name.upper().split()
    # Drop common noise words
    noise = {"POS", "QA", "THE", "AND", "&"}
    meaningful = [p for p in parts if p not in noise] or parts
    code = "".join(p[:3] for p in meaningful[:2])
    return code[:6]


def _next_daily_seq(pos_profile: str) -> int:
    """Return the next sequential number for this store today (race-safe)."""
    today = frappe.utils.today()
    result = frappe.db.sql(
        """SELECT COUNT(*) + 1
           FROM `tabPOS Kiosk Token`
           WHERE pos_profile = %s
             AND DATE(creation) = %s
        """,
        (pos_profile, today),
    )
    return int(result[0][0]) if result else 1


def _generate_token_display(pos_profile: str, company_abbr: str) -> str:
    """Generate human-readable token like  GGR-VEL-001."""
    store_code = _get_store_code(pos_profile)
    seq = _next_daily_seq(pos_profile)
    abbr = (company_abbr or "CH")[:4].upper()
    return f"{abbr}-{store_code}-{seq:03d}"


def _resolve_pos_profile(identifier: str) -> dict | None:
    """Resolve a store identifier to a POS Profile row.

    Accepts:
    - Exact POS Profile name
    - CH Store identifiers (name/store_code/store_name/linked pos_profile)
    - Fuzzy POS Profile match as last resort
    """
    if not identifier:
        return None

    # 1) Exact POS Profile name
    profile = frappe.db.get_value(
        "POS Profile",
        identifier,
        ["name", "company", "warehouse"],
        as_dict=True,
    )
    if profile:
        return profile

    # 2) CH Store mapping (if available)
    if frappe.db.exists("DocType", "CH Store"):
        store_candidates = frappe.get_all(
            "CH Store",
            filters={"disabled": 0},
            or_filters=[
                ["name", "=", identifier],
                ["store_code", "=", identifier],
                ["store_name", "=", identifier],
                ["store_name", "like", f"%{identifier}%"],
                ["pos_profile", "=", identifier],
            ],
            fields=["pos_profile"],
            limit_page_length=5,
        )
        mapped_profiles = sorted({(r.get("pos_profile") or "").strip() for r in store_candidates if r.get("pos_profile")})
        if len(mapped_profiles) == 1:
            profile = frappe.db.get_value(
                "POS Profile",
                mapped_profiles[0],
                ["name", "company", "warehouse"],
                as_dict=True,
            )
            if profile:
                return profile

    # 3) Fuzzy POS Profile fallback (must be unambiguous)
    candidates = frappe.get_all(
        "POS Profile",
        filters={"name": ["like", f"%{identifier}%"]},
        fields=["name", "company", "warehouse"],
        limit_page_length=5,
    )
    if len(candidates) == 1:
        return candidates[0]

    return None


# ---------------------------------------------------------------------------
# Guest API — Kiosk
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def get_store_config(pos_profile: str) -> dict:
    """
    Returns store configuration for the kiosk page dropdowns.
    Called on kiosk load with ?store=<pos_profile>.
    """
    profile = _resolve_pos_profile(pos_profile)

    if not profile:
        frappe.throw(_("Store not found"), frappe.DoesNotExistError, title=_("API Error"))

    company = frappe.db.get_value("Company", profile.company, ["name", "abbr"], as_dict=True)

    # Device brands — pulled from Brand doctype, top-level only (no sub-brands)
    # Sub-brands have ch_parent_brand set (e.g. Galaxy → Samsung)
    # Some environments may not yet have custom Brand fields during rollout.
    # Fall back to a plain Brand list instead of failing with SQL 1054.
    if frappe.db.has_column("Brand", "ch_disabled") and frappe.db.has_column("Brand", "ch_parent_brand"):
        _raw_brands = frappe.db.sql(
            """SELECT name FROM `tabBrand`
               WHERE ch_disabled = 0
                 AND (ch_parent_brand IS NULL OR ch_parent_brand = '')
                 AND name != 'Test Brand'
               ORDER BY name ASC""",
            as_dict=True,
        )
    else:
        _raw_brands = frappe.db.sql(
            """SELECT name FROM `tabBrand`
               WHERE name != 'Test Brand'
               ORDER BY name ASC""",
            as_dict=True,
        )
    brands = [b.name for b in _raw_brands]
    if "Other" not in brands:
        brands.append("Other")

    # Issue categories
    issues = [
        {"key": "Screen Replacement", "icon": "📱"},
        {"key": "Screen Repair", "icon": "🔧"},
        {"key": "Battery Replacement", "icon": "🔋"},
        {"key": "Charging Port", "icon": "⚡"},
        {"key": "Camera Repair", "icon": "📷"},
        {"key": "Water Damage", "icon": "💧"},
        {"key": "Software Issue", "icon": "💾"},
        {"key": "Speaker / Mic", "icon": "🔊"},
        {"key": "Back Panel", "icon": "🪟"},
        {"key": "Other", "icon": "🛠️"},
    ]

    return {
        "store_name": profile.name,
        "company": profile.company,
        "company_abbr": company.abbr if company else "CH",
        "brands": brands,
        "issues": issues,
    }


@frappe.whitelist(allow_guest=True)
def get_brand_models(brand: str) -> dict:
    """
    Return distinct device model names for a given brand from Item Master.
    Uses Brand doctype as single source of truth:
    - Includes items tagged with the brand itself
    - Includes items tagged with sub-brands (where ch_parent_brand = brand)
    """
    # Single source of truth: query Brand doctype for the brand + all sub-brands.
    # Some environments may not yet have ch_parent_brand custom field.
    if frappe.db.has_column("Brand", "ch_parent_brand"):
        brand_rows = frappe.db.sql(
            "SELECT name FROM `tabBrand` WHERE name = %s OR ch_parent_brand = %s",
            (brand, brand),
            as_dict=True,
        )
    else:
        brand_rows = frappe.db.sql(
            "SELECT name FROM `tabBrand` WHERE name = %s",
            (brand,),
            as_dict=True,
        )
    db_brands = [r.name for r in brand_rows] or [brand]

    rows = frappe.get_all(
        "Item",
        filters={"brand": ("in", db_brands), "disabled": 0},
        fields=["distinct item_name as item_name"],
        order_by="item_name asc",
        limit_page_length=200,
    )

    seen = set()
    models = []
    for r in rows:
        name = r.item_name.strip()
        if name not in seen:
            seen.add(name)
            models.append(name)

    return models


@frappe.whitelist(allow_guest=True)
def create_token(
    pos_profile: str,
    customer_name: str,
    customer_phone: str,
    device_type: str,
    device_brand: str,
    device_model: str,
    issue_category: str,
    issue_description: str = "",
) -> dict:
    """
    Create a new queue token. Called from the kiosk (no login required).
    Returns the token display number and doc name.
    """
    # Rate limit by IP
    client_ip = frappe.local.request.remote_addr if hasattr(frappe.local, "request") and frappe.local.request else "unknown"
    _check_rate_limit(f"token_{client_ip}_{pos_profile}")

    # Input validation
    if not customer_name or not customer_phone:
        frappe.throw(_("Customer name and phone are required"), title=_("API Error"))
    if not pos_profile:
        frappe.throw(_("Store is required"), title=_("API Error"))
    customer_phone = _validate_indian_phone(customer_phone)  # normalize + validate

    profile = _resolve_pos_profile(pos_profile)
    if not profile:
        frappe.throw(_("Invalid POS Profile"), title=_("API Error"))

    company_abbr = frappe.db.get_value("Company", profile.company, "abbr") or "CH"

    # Acquire a per-store advisory lock so concurrent kiosk submissions
    # get unique sequential numbers for the day.
    today = frappe.utils.today()
    lock_key = f"pos_seq_{pos_profile}_{today}"
    frappe.db.sql("SELECT GET_LOCK(%s, 10)", (lock_key,))
    try:
        token_display = _generate_token_display(pos_profile, company_abbr)
        doc = frappe.get_doc(
            {
                "doctype": "POS Kiosk Token",
                "pos_profile": pos_profile,
                "company": profile.company,
                "store": profile.warehouse,
                "status": "Waiting",
                "token_display": token_display,
                "customer_name": customer_name.strip(),
                "customer_phone": customer_phone.strip(),
                "device_type": device_type,
                "device_brand": device_brand,
                "device_model": device_model,
                "issue_category": issue_category,
                "issue_description": issue_description,
                "visit_source": "Kiosk",
                "visit_purpose": "Repair",
                "expires_at": frappe.utils.add_days(now_datetime(), 1),
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert()
        doc.submit()
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))

    return {
        "token": token_display,
        "name": doc.name,
        "customer_name": doc.customer_name,
        "store": pos_profile,
        "device": device_model if device_model else device_brand,
        "issue": issue_category,
        "created_at": str(doc.creation),
    }


# ---------------------------------------------------------------------------
# Authenticated API — Management Dashboard
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_queue(pos_profile: str = None, status: str = None, date_filter: str = "today") -> dict:
    """
    Returns token queue for manager/admin view.
    date_filter: today | yesterday | this_week | all
    """
    filters = {}
    if pos_profile:
        filters["pos_profile"] = pos_profile
    if status and status != "All":
        filters["status"] = status

    now = frappe.utils.now_datetime()
    if date_filter == "today":
        filters["creation"] = [">=", frappe.utils.today() + " 00:00:00"]
    elif date_filter == "yesterday":
        yesterday = frappe.utils.add_days(frappe.utils.today(), -1)
        filters["creation"] = ["between", [
            yesterday + " 00:00:00",
            yesterday + " 23:59:59",
        ]]
    elif date_filter == "this_week":
        week_start = frappe.utils.add_days(frappe.utils.today(), -6)
        filters["creation"] = [">=", week_start + " 00:00:00"]
    # date_filter == "all" → no date filter applied

    tokens = frappe.get_all(
        "POS Kiosk Token",
        filters=filters,
        fields=[
            "name", "token_display", "creation", "status",
            "customer_name", "customer_phone",
            "device_type", "device_brand", "device_model",
            "issue_category", "issue_description",
            "technician", "assigned_at", "started_at", "completed_at",
            "pos_profile", "company",
        ],
        order_by="creation desc",
        limit=200,
    )

    for t in tokens:
        t["device"] = _device_label(t.get('device_brand', ''), t.get('device_model', ''))
        # Compute wait time in minutes
        created = get_datetime(t["creation"])
        end_time = get_datetime(t["completed_at"]) if t.get("completed_at") else now
        delta_minutes = int((end_time - created).total_seconds() / 60)
        t["wait_minutes"] = delta_minutes
        t["technician_name"] = (
            frappe.db.get_value("User", t["technician"], "full_name")
            if t.get("technician")
            else None
        )

    return tokens


@frappe.whitelist()
def get_store_users(pos_profile: str = None, role: str = None) -> dict:
    """
    Return users mapped to a store via CH Store.store_users child table.
    Filtered by pos_profile (looks up CH Store via pos_profile field).
    Optionally filtered by role (Technician / Store Executive / Store Manager).
    Falls back to all enabled non-guest users if no mapping exists.
    """
    users = []
    if pos_profile:
        # Find the CH Store linked to this POS Profile
        store_name = frappe.db.get_value("CH Store", {"pos_profile": pos_profile, "disabled": 0}, "name")
        if store_name:
            filters = {"parent": store_name, "parenttype": "CH Store"}
            if role:
                filters["role"] = role
            rows = frappe.db.get_all(
                "CH Store User",
                filters=filters,
                fields=["user", "full_name", "role"],
                order_by="full_name",
            )
            # Enrich with live full_name in case fetch_from hasn't run
            for r in rows:
                if not r.full_name:
                    r.full_name = frappe.db.get_value("User", r.user, "full_name") or r.user
            users = rows

    if not users:
        # Fallback: all enabled System Users
        rows = frappe.db.get_all(
            "User",
            filters={"enabled": 1, "user_type": "System User", "name": ("not in", ["Administrator", "Guest"])},
            fields=["name as user", "full_name"],
            order_by="full_name",
        )
        for r in rows:
            r["role"] = ""
        users = rows

    return users


@frappe.whitelist()
def assign_token(token_name: str, technician: str) -> dict:
    """Assign a technician to a token. Status → In Progress."""
    _ensure_can_operate_token()
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    updates = {"technician": technician, "assigned_at": now_datetime()}
    if doc.status == "Waiting":
        updates["status"] = "In Progress"
    frappe.db.set_value("POS Kiosk Token", token_name, updates)
    return {"status": "ok", "token_status": updates.get("status", doc.status)}


@frappe.whitelist()
def start_token(token_name: str) -> dict:
    """Mark service as started."""
    _ensure_can_operate_token()
    frappe.db.set_value("POS Kiosk Token", token_name, {
        "started_at": now_datetime(),
        "status": "In Progress",
    })
    return {"status": "ok"}


@frappe.whitelist()
def complete_token(token_name: str) -> dict:
    """Mark token as completed."""
    _ensure_can_operate_token()
    frappe.db.set_value("POS Kiosk Token", token_name, {
        "completed_at": now_datetime(),
        "status": "Completed",
    })
    return {"status": "ok"}


@frappe.whitelist()
def cancel_token(token_name: str) -> dict:
    """Cancel a token (typically Waiting status)."""
    _ensure_can_operate_token()
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    if doc.status in ("Completed", "Cancelled", "Converted"):
        frappe.throw(_("Cannot cancel a {0} token").format(doc.status), title=_("API Error"))
    frappe.db.set_value("POS Kiosk Token", token_name, "status", "Cancelled")
    return {"status": "ok"}


@frappe.whitelist()
def drop_token(token_name: str, drop_reason: str = "", drop_sub_reason: str = "", drop_remarks: str = "") -> dict:
    """Mark a token as Dropped (customer left / no-show) with mandatory reason capture."""
    user_roles = frappe.get_roles()
    if "POS User" not in user_roles and "POS Manager" not in user_roles:
        frappe.throw(_("Not permitted"), frappe.PermissionError, title=_("API Error"))
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    if doc.status in ("Completed", "Cancelled", "Converted", "Dropped"):
        frappe.throw(_("Cannot drop a {0} token").format(doc.status), title=_("API Error"))
    if not drop_reason:
        frappe.throw(_("Drop reason is mandatory when marking a token as Dropped"), title=_("API Error"))
    frappe.db.set_value("POS Kiosk Token", token_name, {
        "status": "Dropped",
        "drop_reason": drop_reason,
        "drop_sub_reason": drop_sub_reason,
        "drop_remarks": drop_remarks,
        "exit_at": now_datetime(),
    })
    return {"status": "ok", "drop_reason": drop_reason}


@frappe.whitelist()
def engage_token(token_name: str, sales_executive: str = "") -> dict:
    """Mark a token as Engaged — staff has started interacting with the customer."""
    user_roles = frappe.get_roles()
    if "POS User" not in user_roles and "POS Manager" not in user_roles:
        frappe.throw(_("Not permitted"), frappe.PermissionError, title=_("API Error"))
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    if doc.status not in ("Waiting",):
        frappe.throw(_("Can only engage a Waiting token, current status is {0}").format(doc.status), title=_("API Error"))
    updates = {
        "status": "Engaged",
        "engaged_at": now_datetime(),
    }
    if sales_executive:
        updates["sales_executive"] = sales_executive
    elif not doc.technician:
        updates["technician"] = frappe.session.user
    frappe.db.set_value("POS Kiosk Token", token_name, updates)
    return {"status": "ok", "token_status": "Engaged"}


@frappe.whitelist()
def quick_walkin(
    pos_profile: str,
    visit_purpose: str = "Sales",
    category_interest: str = "",
    brand_interest: str = "",
    budget_range: str = "",
    customer_name: str = "",
    customer_phone: str = "",
    sales_executive: str = "",
) -> dict:
    """
    2-second retail walk-in entry — button-driven, no typing needed.
    Creates a token already in Engaged state with retail interest fields populated.
    """
    profile = frappe.db.get_value(
        "POS Profile", pos_profile, ["name", "company", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw(_("Invalid POS Profile"), title=_("API Error"))

    company_abbr = frappe.db.get_value("Company", profile.company, "abbr") or "CH"

    if customer_phone and customer_phone.strip():
        customer_phone = validate_indian_phone(customer_phone.strip(), "Phone number")

    today = frappe.utils.today()
    lock_key = f"pos_seq_{pos_profile}_{today}"
    frappe.db.sql("SELECT GET_LOCK(%s, 10)", (lock_key,))
    try:
        token_display = _generate_token_display(pos_profile, company_abbr)
        doc = frappe.get_doc({
            "doctype": "POS Kiosk Token",
            "pos_profile": pos_profile,
            "company": profile.company,
            "store": profile.warehouse,
            "status": "Engaged",
            "token_display": token_display,
            "customer_name": customer_name.strip() or "Walk-in",
            "customer_phone": customer_phone.strip() or "",
            "visit_source": "Counter",
            "visit_purpose": visit_purpose,
            "category_interest": category_interest,
            "brand_interest": brand_interest,
            "budget_range": budget_range,
            "sales_executive": sales_executive or "",
            "engaged_at": now_datetime(),
            "technician": frappe.session.user,
            "expires_at": frappe.utils.add_days(now_datetime(), 1),
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        doc.submit()
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))

    return {
        "status": "ok",
        "token": token_display,
        "name": doc.name,
        "visit_purpose": visit_purpose,
    }


@frappe.whitelist()
def audit_orphan_invoices(pos_profile: str = "", date: str = "") -> dict:
    """
    Daily audit: find POS invoices that have no linked kiosk token.
    Returns list of orphan invoices for compliance review.
    """
    target_date = date or frappe.utils.today()
    filters = {
        "is_pos": 1,
        "docstatus": 1,
        "posting_date": target_date,
        "custom_kiosk_token": ("in", ["", None]),
    }
    if pos_profile:
        filters["pos_profile"] = pos_profile

    orphans = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=["name", "pos_profile", "customer_name", "grand_total", "posting_date", "owner"],
        order_by="creation asc",
    )
    return {
        "date": target_date,
        "total_orphans": len(orphans),
        "invoices": orphans,
    }


@frappe.whitelist()
def get_walkin_insights(pos_profile: str = "", days: int = 30) -> dict:
    """
    AI-style insights derived from token data — actionable observations for store managers.
    Returns structured insights with severity and recommendations.
    """
    from frappe.utils import getdate, add_days
    end_date = frappe.utils.today()
    start_date = str(add_days(getdate(end_date), -(int(days) - 1)))

    base_filters = {"creation": [">=", start_date + " 00:00:00"]}
    if pos_profile:
        base_filters["pos_profile"] = pos_profile

    tokens = frappe.get_all(
        "POS Kiosk Token",
        filters=base_filters,
        fields=[
            "name", "status", "visit_purpose", "visit_source",
            "category_interest", "brand_interest", "budget_range",
            "drop_reason", "sales_executive", "handling_duration",
            "creation", "engaged_at", "exit_at", "converted_invoice",
            "pos_profile",
        ],
    )

    if not tokens:
        return {"insights": [], "summary": "No token data for the selected period."}

    total = len(tokens)
    converted = [t for t in tokens if t.status == "Converted"]
    dropped = [t for t in tokens if t.status == "Dropped"]
    waiting = [t for t in tokens if t.status in ("Waiting", "Expired")]

    conversion_rate = round(len(converted) / total * 100, 1) if total else 0
    drop_rate = round(len(dropped) / total * 100, 1) if total else 0

    insights = []

    # 1. Conversion rate alert
    if conversion_rate < 30:
        insights.append({
            "type": "warning",
            "title": "Low Conversion Rate",
            "metric": f"{conversion_rate}%",
            "detail": f"Only {len(converted)} of {total} walk-ins converted to sales. Industry benchmark is 35-45%.",
            "action": "Review drop reasons and staff training. Check if high-demand products are in stock.",
        })
    elif conversion_rate > 50:
        insights.append({
            "type": "success",
            "title": "Strong Conversion",
            "metric": f"{conversion_rate}%",
            "detail": f"{len(converted)} of {total} walk-ins converted — above benchmark.",
            "action": "Maintain momentum. Consider upsell training to increase basket size.",
        })

    # 2. Top drop reasons
    drop_reasons = {}
    for t in dropped:
        r = t.drop_reason or "Not Specified"
        drop_reasons[r] = drop_reasons.get(r, 0) + 1
    if drop_reasons:
        top_reason = max(drop_reasons, key=drop_reasons.get)
        top_count = drop_reasons[top_reason]
        insights.append({
            "type": "info",
            "title": "Top Drop Reason",
            "metric": f"{top_reason} ({top_count}x)",
            "detail": f"'{top_reason}' is the #1 reason customers leave without buying ({round(top_count / len(dropped) * 100)}% of drops).",
            "action": {
                "Price Too High": "Review pricing vs. competitors. Push finance/EMI options.",
                "Product Not Available": "Check stock availability for requested items. Improve procurement.",
                "Competitor Better Deal": "Activate price match or bundle offers.",
                "Just Browsing": "Train staff on engagement techniques to convert browsers.",
            }.get(top_reason, "Investigate and address the root cause."),
        })

    # 3. Unengaged visitors (went from Waiting to Expired without engagement)
    unengaged = [t for t in waiting if not t.engaged_at]
    if unengaged and len(unengaged) > total * 0.15:
        insights.append({
            "type": "warning",
            "title": "High Unengaged Walk-ins",
            "metric": f"{len(unengaged)} ({round(len(unengaged) / total * 100)}%)",
            "detail": f"{len(unengaged)} customers left without any staff interaction.",
            "action": "Ensure adequate floor staff during peak hours. Consider greeting protocol within 60 seconds.",
        })

    # 4. Brand demand without sales
    brand_demand = {}
    brand_converted = set()
    for t in tokens:
        if t.brand_interest:
            brand_demand[t.brand_interest] = brand_demand.get(t.brand_interest, 0) + 1
        if t.status == "Converted" and t.brand_interest:
            brand_converted.add(t.brand_interest)
    missed_brands = {b: c for b, c in brand_demand.items() if b not in brand_converted and c >= 3}
    if missed_brands:
        top_missed = max(missed_brands, key=missed_brands.get)
        insights.append({
            "type": "opportunity",
            "title": "Missed Brand Opportunity",
            "metric": f"{top_missed} ({missed_brands[top_missed]} requests, 0 sales)",
            "detail": f"Customers asked for {top_missed} {missed_brands[top_missed]} times but none converted.",
            "action": f"Check {top_missed} stock levels and pricing. Consider adding models if not stocked.",
        })

    # 5. Staff performance variance
    exec_data = {}
    for t in tokens:
        ex = t.sales_executive or t.get("technician") or ""
        if not ex:
            continue
        if ex not in exec_data:
            exec_data[ex] = {"total": 0, "converted": 0}
        exec_data[ex]["total"] += 1
        if t.status == "Converted":
            exec_data[ex]["converted"] += 1
    if len(exec_data) >= 2:
        rates = {e: round(d["converted"] / d["total"] * 100, 1) for e, d in exec_data.items() if d["total"] >= 5}
        if rates:
            best = max(rates, key=rates.get)
            worst = min(rates, key=rates.get)
            if rates[best] - rates[worst] > 20:
                best_name = frappe.db.get_value("User", best, "full_name") or best
                worst_name = frappe.db.get_value("User", worst, "full_name") or worst
                insights.append({
                    "type": "info",
                    "title": "Staff Conversion Gap",
                    "metric": f"{rates[best]}% vs {rates[worst]}%",
                    "detail": f"{best_name} converts at {rates[best]}% while {worst_name} is at {rates[worst]}%.",
                    "action": "Pair low-performers with high-performers for shadowing. Review approach differences.",
                })

    # 6. Budget range analysis
    budget_counts = {}
    for t in tokens:
        if t.budget_range:
            budget_counts[t.budget_range] = budget_counts.get(t.budget_range, 0) + 1
    if budget_counts:
        top_budget = max(budget_counts, key=budget_counts.get)
        insights.append({
            "type": "info",
            "title": "Most Requested Budget Segment",
            "metric": top_budget,
            "detail": f"{budget_counts[top_budget]} walk-ins asked for {top_budget} range ({round(budget_counts[top_budget] / total * 100)}%).",
            "action": f"Ensure strong assortment and display in the {top_budget} range.",
        })

    return {
        "insights": insights,
        "summary": {
            "period_days": days,
            "total_footfall": total,
            "converted": len(converted),
            "dropped": len(dropped),
            "conversion_rate": conversion_rate,
            "drop_rate": drop_rate,
            "drop_reasons": drop_reasons,
            "top_categories": dict(sorted(
                {t.category_interest: 0 for t in tokens if t.category_interest}.items()
            )),
        },
    }


# ---------------------------------------------------------------------------
# Customer Lookup by Phone
# ---------------------------------------------------------------------------

@frappe.whitelist()
def find_customer_by_phone(phone: str) -> dict:
    """Return the ERPNext Customer name matching this phone number, or None."""
    if not phone or not phone.strip():
        return None
    phone = normalize_indian_phone(phone.strip())
    # Try mobile_no on Customer directly
    name = frappe.db.get_value("Customer", {"mobile_no": phone}, "name")
    if name:
        return name
    # Try Dynamic Link on Contact
    contact = frappe.db.sql(
        """SELECT dl.link_name
           FROM `tabContact Phone` cp
           JOIN `tabDynamic Link` dl ON dl.parent = cp.parent AND dl.parenttype = 'Contact'
           WHERE cp.phone = %s AND dl.link_doctype = 'Customer'
           LIMIT 1""",
        (phone,),
        as_dict=True,
    )
    if contact:
        return contact[0].link_name
    return None


# ---------------------------------------------------------------------------
# Counter Walk-in — creates a lightweight token from POS app
# ---------------------------------------------------------------------------

@frappe.whitelist()
def log_counter_walkin(
    pos_profile: str,
    visit_purpose: str = "Enquiry",
    customer_name: str = "",
    customer_phone: str = "",
    remarks: str = "",
) -> dict:
    """
    Create a minimal POS Kiosk Token for a direct-counter walk-in.
    This replaces the old log_walkin counter-only approach.
    Returns token name and display number.
    """
    profile = frappe.db.get_value(
        "POS Profile", pos_profile, ["name", "company", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw(_("Invalid POS Profile"), title=_("API Error"))

    company_abbr = frappe.db.get_value("Company", profile.company, "abbr") or "CH"

    # Validate phone if provided (walk-ins don't always have a phone)
    if customer_phone and customer_phone.strip():
        customer_phone = validate_indian_phone(customer_phone.strip(), "Phone number")

    today = frappe.utils.today()
    lock_key = f"pos_seq_{pos_profile}_{today}"
    frappe.db.sql("SELECT GET_LOCK(%s, 10)", (lock_key,))
    try:
        token_display = _generate_token_display(pos_profile, company_abbr)
        doc = frappe.get_doc({
            "doctype": "POS Kiosk Token",
            "pos_profile": pos_profile,
            "company": profile.company,
            "store": profile.warehouse,
            "status": "In Progress",
            "token_display": token_display,
            "customer_name": customer_name.strip() or "Walk-in",
            "customer_phone": customer_phone.strip() or "",
            "visit_source": "Counter",
            "visit_purpose": visit_purpose,
            "issue_description": remarks,
            "started_at": now_datetime(),
            "technician": frappe.session.user,
            "expires_at": frappe.utils.add_days(now_datetime(), 1),
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        doc.submit()
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))

    return {
        "status": "ok",
        "token": token_display,
        "name": doc.name,
        "visit_purpose": visit_purpose,
    }


@frappe.whitelist()
def get_dashboard_stats(pos_profile: str = None, date_filter: str = "today") -> dict:
    """
    Returns aggregate metrics for the dashboard cards.
    """
    filters = {}
    if pos_profile:
        filters["pos_profile"] = pos_profile

    today = frappe.utils.today()
    if date_filter == "today":
        filters["creation"] = [">=", today + " 00:00:00"]
    elif date_filter == "yesterday":
        yesterday = frappe.utils.add_days(today, -1)
        filters["creation"] = ["between", [yesterday + " 00:00:00", yesterday + " 23:59:59"]]
    elif date_filter == "this_week":
        filters["creation"] = [">=", frappe.utils.add_days(today, -6) + " 00:00:00"]
    # date_filter == "all" → no date filter

    all_tokens = frappe.get_all(
        "POS Kiosk Token",
        filters=filters,
        fields=["status", "creation", "completed_at", "pos_profile"],
    )

    total = len(all_tokens)
    waiting = sum(1 for t in all_tokens if t.status == "Waiting")
    in_progress = sum(1 for t in all_tokens if t.status == "In Progress")
    completed = sum(1 for t in all_tokens if t.status == "Completed")
    cancelled = sum(1 for t in all_tokens if t.status in ("Cancelled", "Expired"))
    dropped = sum(1 for t in all_tokens if t.status == "Dropped")

    # Completion rate: completed / (completed + waiting + in_progress) — excludes cancelled
    serviceable = completed + waiting + in_progress
    completion_rate = round(completed / serviceable * 100) if serviceable else 0

    # Average wait time (creation → completed_at) for completed tokens
    completed_tokens = [t for t in all_tokens if t.status == "Completed" and t.completed_at]
    if completed_tokens:
        total_mins = sum(
            int((get_datetime(t["completed_at"]) - get_datetime(t["creation"])).total_seconds() / 60)
            for t in completed_tokens
        )
        avg_wait = round(total_mins / len(completed_tokens))
    else:
        avg_wait = 0

    # Per-store breakdown (for admin)
    store_breakdown = {}
    for t in all_tokens:
        p = t.pos_profile or "Unknown"
        if p not in store_breakdown:
            store_breakdown[p] = {"store": p, "total": 0, "waiting": 0, "in_progress": 0, "completed": 0}
        store_breakdown[p]["total"] += 1
        status_key = t.status.lower().replace(" ", "_")
        if status_key in store_breakdown[p]:
            store_breakdown[p][status_key] += 1

    return {
        "total": total,
        "waiting": waiting,
        "in_progress": in_progress,
        "completed": completed,
        "cancelled": cancelled,
        "dropped": dropped,
        "avg_wait_minutes": avg_wait,
        "completion_rate": completion_rate,
        "store_breakdown": list(store_breakdown.values()),
    }


@frappe.whitelist()
def get_technician_tokens(technician: str = None) -> dict:
    """
    Returns tokens assigned to a specific technician (defaults to logged-in user).
    """
    tech = technician or frappe.session.user
    today = frappe.utils.today()

    tokens = frappe.get_all(
        "POS Kiosk Token",
        filters={
            "technician": tech,
            "creation": [">=", today + " 00:00:00"],
        },
        fields=[
            "name", "token_display", "creation", "status",
            "customer_name", "customer_phone",
            "device_type", "device_brand", "device_model",
            "issue_category", "assigned_at", "started_at", "completed_at",
            "pos_profile",
        ],
        order_by="creation desc",
    )

    for t in tokens:
        t["device"] = _device_label(t.get('device_brand', ''), t.get('device_model', ''))

    return tokens


@frappe.whitelist()
def get_reports(pos_profile: str = None, days: int = 7) -> dict:
    """
    Returns daily breakdown and technician performance for the manager Reports tab.
    """
    from datetime import timedelta
    import datetime

    today = frappe.utils.today()
    start_date = frappe.utils.add_days(today, -(int(days) - 1))

    base_filters = {"creation": [">=", start_date + " 00:00:00"]}
    if pos_profile:
        base_filters["pos_profile"] = pos_profile

    all_tokens = frappe.get_all(
        "POS Kiosk Token",
        filters=base_filters,
        fields=["name", "status", "creation", "completed_at", "technician"],
    )

    # Daily breakdown
    daily_map = {}
    for t in all_tokens:
        day = str(get_datetime(t["creation"]).date())
        if day not in daily_map:
            daily_map[day] = {"date": day, "created": 0, "completed": 0, "cancelled": 0, "wait_sum": 0, "wait_count": 0}
        daily_map[day]["created"] += 1
        if t.status == "Completed":
            daily_map[day]["completed"] += 1
            if t.completed_at:
                mins = int((get_datetime(t["completed_at"]) - get_datetime(t["creation"])).total_seconds() / 60)
                daily_map[day]["wait_sum"] += mins
                daily_map[day]["wait_count"] += 1
        elif t.status == "Cancelled":
            daily_map[day]["cancelled"] += 1

    daily_breakdown = []
    for day, data in sorted(daily_map.items(), reverse=True):
        avg = round(data["wait_sum"] / data["wait_count"]) if data["wait_count"] else 0
        daily_breakdown.append({
            "date": data["date"],
            "created": data["created"],
            "completed": data["completed"],
            "cancelled": data["cancelled"],
            "avg_wait": avg,
        })

    # Technician performance
    tech_map = {}
    for t in all_tokens:
        if not t.technician:
            continue
        tech = t.technician
        if tech not in tech_map:
            tech_map[tech] = {"technician": tech, "name": frappe.db.get_value("User", tech, "full_name") or tech,
                              "total": 0, "completed": 0, "time_sum": 0, "time_count": 0}
        tech_map[tech]["total"] += 1
        if t.status == "Completed":
            tech_map[tech]["completed"] += 1
            if t.completed_at:
                mins = int((get_datetime(t["completed_at"]) - get_datetime(t["creation"])).total_seconds() / 60)
                tech_map[tech]["time_sum"] += mins
                tech_map[tech]["time_count"] += 1

    tech_performance = []
    for tech_data in sorted(tech_map.values(), key=lambda x: x["completed"], reverse=True):
        avg = round(tech_data["time_sum"] / tech_data["time_count"]) if tech_data["time_count"] else 0
        tech_performance.append({
            "technician": tech_data["technician"],
            "name": tech_data["name"],
            "total": tech_data["total"],
            "completed": tech_data["completed"],
            "avg_time": avg,
        })

    return {
        "daily_breakdown": daily_breakdown,
        "tech_performance": tech_performance,
    }


@frappe.whitelist(allow_guest=True)
def get_pos_profiles() -> dict:
    """Returns list of active POS Profiles for store selector (management side)."""
    profiles = frappe.get_all(
        "POS Profile",
        filters={"disabled": 0},
        fields=["name", "company", "warehouse"],
        order_by="name asc",
    )
    return profiles


# ---------------------------------------------------------------------------
# POS Integration APIs
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_pos_waiting_tokens(pos_profile: str) -> dict:
    """
    Returns waiting/in-progress tokens for the given POS store.
    Called by the POS Queue panel on load and after each action.
    """
    today = frappe.utils.today()
    tokens = frappe.db.sql(
        """SELECT name, token_display, customer_name, customer_phone,
                  device_type, device_brand, device_model,
                  issue_category, issue_description, status,
                  visit_purpose, category_interest, brand_interest,
                  budget_range, sales_executive, engaged_at,
                  technician, creation
           FROM `tabPOS Kiosk Token`
           WHERE pos_profile = %s
             AND status IN ('Waiting', 'Engaged', 'In Progress')
             AND DATE(creation) = %s
           ORDER BY FIELD(status, 'Waiting', 'Engaged', 'In Progress'), creation ASC""",
        (pos_profile, today),
        as_dict=True,
    )
    return tokens


@frappe.whitelist()
def convert_token_to_gofix(token_name: str, pos_profile: str,
                            customer: str = None, device_item: str = None,
                            device_condition: str = "Good",
                            accessories: str = "",
                            warranty_status: str = "Out of Warranty",
                            data_disclaimer: int = 0) -> dict:
    """
    Convert a POS Kiosk Token into a GoFix Service Request.
    - Pulls all device/issue info from the token
    - Creates the Service Request doc
    - Marks the token as Converted with a link back
    Returns the new Service Request name.
    """
    _ensure_can_operate_token()
    token = frappe.get_doc("POS Kiosk Token", token_name)
    if token.status == "Converted":
        frappe.throw(_("This token has already been converted to a GoFix request."), title=_("API Error"))

    profile = frappe.db.get_value(
        "POS Profile", pos_profile,
        ["company", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw(_("Invalid POS Profile"), title=_("API Error"))

    # Resolve issue category — must match GoFix Issue Category doctype
    issue_cat = None
    if token.issue_category:
        if frappe.db.exists("Issue Category", token.issue_category):
            issue_cat = token.issue_category
        else:
            # Try a case-insensitive match
            match = frappe.db.get_value(
                "Issue Category", {"category_name": token.issue_category}, "name"
            )
            issue_cat = match

    sr = frappe.get_doc({
        "doctype": "Service Request",
        "customer": customer or None,
        "customer_name": token.customer_name,
        "contact_number": token.customer_phone,
        "company": profile.company,
        "source_warehouse": profile.warehouse,
        "walkin_source": "Walk-in",    # Walkin Source master record named 'Walk-in'
        "decision": "Accepted",        # Customer is present — accepting the device
        "device_item": device_item or None,
        "device_item_name": _device_label(token.device_brand, token.device_model) if not device_item else None,
        "brand": token.device_brand,
        "device_condition": device_condition,
        "accessories_received": accessories,
        "warranty_status": warranty_status,
        "issue_category": issue_cat,
        "issue_description": token.issue_description or token.issue_category,
        "data_backup_disclaimer": data_disclaimer,
        "mode_of_service": "Walk-in",
        "priority": "Medium",
        "internal_remarks": f"Created from CH Queue token {token.token_display}",
        # Store back-reference
        "referral_code": token.name,
    })
    sr.flags.ignore_permissions = True
    sr.insert()
    sr.submit()

    # Mark token as Converted and link back to SR
    frappe.db.set_value("POS Kiosk Token", token_name, {
        "status": "Converted",
        "technician": frappe.session.user_fullname or frappe.session.user,
        "linked_service_request": sr.name,
    })

    return {
        "service_request": sr.name,
        "token": token.token_display,
        "customer_name": token.customer_name,
    }

