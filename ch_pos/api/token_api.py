"""
CH Queue — Token System API
All public-facing endpoints for the kiosk and management dashboard.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, add_days, get_datetime, time_diff_in_hours


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Return the next sequential number for this store today."""
    today = frappe.utils.today()
    count = frappe.db.count(
        "POS Kiosk Token",
        filters={
            "pos_profile": pos_profile,
            "creation": [">=", today + " 00:00:00"],
        },
    )
    return count + 1


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

    # Device brands — pull from Item Attribute or a simple hardcoded list
    brands = [
        "Apple", "Samsung", "OnePlus", "Xiaomi", "Realme", "Oppo", "Vivo",
        "Motorola", "Nokia", "Google", "Lenovo", "Asus", "HP", "Dell",
        "Acer", "Huawei", "Nothing", "Other",
    ]

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

    profile = frappe.db.get_value(
        "POS Profile", pos_profile, ["name", "company", "warehouse"], as_dict=True
    )
    if not profile:
        frappe.throw(_("Invalid POS Profile"))

    company_abbr = frappe.db.get_value("Company", profile.company, "abbr") or "CH"
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
            "expires_at": frappe.utils.add_days(now_datetime(), 1),
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert()

    return {
        "token": token_display,
        "name": doc.name,
        "customer_name": doc.customer_name,
        "store": pos_profile,
        "device": f"{device_brand} {device_model}".strip(),
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
        t["device"] = f"{t.get('device_brand', '')} {t.get('device_model', '')}".strip()
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
def assign_token(token_name: str, technician: str):
    """Assign a technician to a token. Status → In Progress."""
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    doc.flags.ignore_permissions = True
    doc.technician = technician
    doc.assigned_at = now_datetime()
    if doc.status == "Waiting":
        doc.status = "In Progress"
    doc.save()
    frappe.db.commit()
    return {"status": "ok", "token_status": doc.status}


@frappe.whitelist()
def start_token(token_name: str):
    """Mark service as started."""
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    doc.flags.ignore_permissions = True
    doc.started_at = now_datetime()
    doc.status = "In Progress"
    doc.save()
    frappe.db.commit()
    return {"status": "ok"}


@frappe.whitelist()
def complete_token(token_name: str):
    """Mark token as completed."""
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    doc.flags.ignore_permissions = True
    doc.completed_at = now_datetime()
    doc.status = "Completed"
    doc.save()
    frappe.db.commit()
    return {"status": "ok"}


@frappe.whitelist()
def cancel_token(token_name: str):
    """Cancel a waiting token."""
    doc = frappe.get_doc("POS Kiosk Token", token_name)
    doc.flags.ignore_permissions = True
    doc.status = "Cancelled"
    doc.save()
    frappe.db.commit()
    return {"status": "ok"}


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
        t["device"] = f"{t.get('device_brand', '')} {t.get('device_model', '')}".strip()

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
