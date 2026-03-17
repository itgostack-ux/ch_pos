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
# Helpers
# ---------------------------------------------------------------------------

_INDIAN_PHONE_RE = re.compile(r"^[6-9]\d{9}$")

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


# ---------------------------------------------------------------------------
# Guest API — Kiosk
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def get_store_config(pos_profile: str):
    """
    Returns store configuration for the kiosk page dropdowns.
    Called on kiosk load with ?store=<pos_profile>.
    """
    profile = frappe.db.get_value(
        "POS Profile",
        pos_profile,
        ["name", "company", "warehouse"],
        as_dict=True,
    )
    if not profile:
        frappe.throw(_("Store not found"), frappe.DoesNotExistError)

    company = frappe.db.get_value("Company", profile.company, ["name", "abbr"], as_dict=True)

    # Device brands — pulled from Brand doctype, top-level only (no sub-brands)
    # Sub-brands have ch_parent_brand set (e.g. Galaxy → Samsung)
    _raw_brands = frappe.db.sql(
        """SELECT name FROM `tabBrand`
           WHERE ch_disabled = 0
             AND (ch_parent_brand IS NULL OR ch_parent_brand = '')
             AND name != 'Test Brand'
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
def get_brand_models(brand: str):
    """
    Return distinct device model names for a given brand from Item Master.
    Uses Brand doctype as single source of truth:
    - Includes items tagged with the brand itself
    - Includes items tagged with sub-brands (where ch_parent_brand = brand)
    """
    # Single source of truth: query Brand doctype for the brand + all sub-brands
    brand_rows = frappe.db.sql(
        "SELECT name FROM `tabBrand` WHERE name = %s OR ch_parent_brand = %s",
        (brand, brand),
        as_dict=True,
    )
    db_brands = [r.name for r in brand_rows] or [brand]

    brand_placeholders = ", ".join(["%s"] * len(db_brands))

    rows = frappe.db.sql(
        f"""SELECT DISTINCT item_name FROM `tabItem`
            WHERE brand IN ({brand_placeholders})
              AND disabled = 0
            ORDER BY item_name ASC
            LIMIT 200""",
        tuple(db_brands),
        as_dict=True,
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
):
    """
    Create a new queue token. Called from the kiosk (no login required).
    Returns the token display number and doc name.
    """
    # Input validation
    if not customer_name or not customer_phone:
        frappe.throw(_("Customer name and phone are required"))
    if not pos_profile:
        frappe.throw(_("Store is required"))
    customer_phone = _validate_indian_phone(customer_phone)  # normalize + validate

    profile = frappe.db.get_value(
        "POS Profile", pos_profile, ["name", "company", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw(_("Invalid POS Profile"))

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
def get_queue(pos_profile: str = None, status: str = None, date_filter: str = "today"):
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
def get_store_users(pos_profile: str = None, role: str = None):
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
def assign_token(token_name: str, technician: str):
    """Assign a technician to a token. Status → In Progress."""
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    doc.flags.ignore_permissions = True
    doc.technician = technician
    doc.assigned_at = now_datetime()
    if doc.status == "Waiting":
        doc.status = "In Progress"
    doc.save()
    return {"status": "ok", "token_status": doc.status}


@frappe.whitelist()
def start_token(token_name: str):
    """Mark service as started."""
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    doc.flags.ignore_permissions = True
    doc.started_at = now_datetime()
    doc.status = "In Progress"
    doc.save()
    return {"status": "ok"}


@frappe.whitelist()
def complete_token(token_name: str):
    """Mark token as completed."""
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    doc.flags.ignore_permissions = True
    doc.completed_at = now_datetime()
    doc.status = "Completed"
    doc.save()
    return {"status": "ok"}


@frappe.whitelist()
def cancel_token(token_name: str):
    """Cancel a token (typically Waiting status)."""
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    if doc.status in ("Completed", "Cancelled", "Converted"):
        frappe.throw(_("Cannot cancel a {0} token").format(doc.status))
    doc.flags.ignore_permissions = True
    doc.status = "Cancelled"
    doc.save()
    return {"status": "ok"}


@frappe.whitelist()
def drop_token(token_name: str):
    """Mark a token as Dropped (customer left / no-show)."""
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    if doc.status in ("Completed", "Cancelled", "Converted", "Dropped"):
        frappe.throw(_("Cannot drop a {0} token").format(doc.status))
    doc.flags.ignore_permissions = True
    doc.status = "Dropped"
    doc.save()
    return {"status": "ok"}


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
):
    """
    Create a minimal POS Kiosk Token for a direct-counter walk-in.
    This replaces the old log_walkin counter-only approach.
    Returns token name and display number.
    """
    profile = frappe.db.get_value(
        "POS Profile", pos_profile, ["name", "company", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw(_("Invalid POS Profile"))

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
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))

    return {
        "status": "ok",
        "token": token_display,
        "name": doc.name,
        "visit_purpose": visit_purpose,
    }


@frappe.whitelist()
def get_dashboard_stats(pos_profile: str = None, date_filter: str = "today"):
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
def get_technician_tokens(technician: str = None):
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
def get_reports(pos_profile: str = None, days: int = 7):
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
def get_pos_profiles():
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
def get_pos_waiting_tokens(pos_profile: str):
    """
    Returns waiting/in-progress tokens for the given POS store.
    Called by the POS Queue panel on load and after each action.
    """
    today = frappe.utils.today()
    tokens = frappe.db.sql(
        """SELECT name, token_display, customer_name, customer_phone,
                  device_type, device_brand, device_model,
                  issue_category, issue_description, status,
                  technician, creation
           FROM `tabPOS Kiosk Token`
           WHERE pos_profile = %s
             AND status IN ('Waiting', 'In Progress')
             AND DATE(creation) = %s
           ORDER BY creation ASC""",
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
                            data_disclaimer: int = 0):
    """
    Convert a POS Kiosk Token into a GoFix Service Request.
    - Pulls all device/issue info from the token
    - Creates the Service Request doc
    - Marks the token as Converted with a link back
    Returns the new Service Request name.
    """
    token = frappe.get_doc("POS Kiosk Token", token_name)
    if token.status == "Converted":
        frappe.throw(_("This token has already been converted to a GoFix request."))

    profile = frappe.db.get_value(
        "POS Profile", pos_profile,
        ["company", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw(_("Invalid POS Profile"))

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
    sr.flags.ignore_mandatory = True
    sr.insert()

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

