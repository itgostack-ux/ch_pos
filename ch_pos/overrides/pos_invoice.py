import frappe
from frappe.utils import flt, cint
from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice


def _get_serial_nos_from_item(item, parent_doc=None):
    """Extract serial numbers from item row, checking serial_no field first,
    then falling back to Serial and Batch Bundle (ERPNext v15 clears serial_no
    on returns after bundle creation).

    If parent_doc has _cached_serial_nos (set during on_cancel before delink),
    use that instead — the bundle reference may already be cleared.
    """
    if parent_doc and hasattr(parent_doc, "_cached_serial_nos"):
        cached = parent_doc._cached_serial_nos.get(item.name, [])
        if cached:
            return cached

    serial_nos = (item.serial_no or "").strip()
    if serial_nos:
        return [s.strip() for s in serial_nos.split("\n") if s.strip()]

    if item.serial_and_batch_bundle:
        from erpnext.stock.serial_batch_bundle import get_serial_nos
        return get_serial_nos(item.serial_and_batch_bundle) or []

    return []


def _update_serial_status(serial_no, new_status, company=None, warehouse=None, remarks=None, **kwargs):
    """Call the centralized CH Serial Lifecycle API for status transitions."""
    from ch_item_master.ch_item_master.doctype.ch_serial_lifecycle.ch_serial_lifecycle import (
        update_lifecycle_status,
    )
    return update_lifecycle_status(
        serial_no=serial_no,
        new_status=new_status,
        company=company,
        warehouse=warehouse,
        remarks=remarks,
        **kwargs,
    )


def _create_or_update_device(serial_no, customer, **kwargs):
    """Call the centralized CH Customer Device API for device registration."""
    from ch_item_master.ch_customer_master.doctype.ch_customer_device.ch_customer_device import (
        CHCustomerDevice,
    )
    return CHCustomerDevice.create_or_update_for_serial(serial_no, customer, **kwargs)


class CustomPOSInvoice(POSInvoice):
    """Extends POS Invoice for margin scheme GST on selling side.

    Also overrides on_submit/on_cancel to create SLE/GL entries at submit time.
    Standard ERPNext v15 POS Invoice defers stock ledger and GL entry creation
    to POS Closing Entry → Consolidated Sales Invoice.  GoGizmo needs real-time
    stock and accounting for IMEI serial tracking, so we invoke those methods
    directly.
    """
    # POS Invoice DocType lacks this Sales Invoice field; accounts_controller
    # accesses it during make_precision_loss_gl_entry.
    use_company_roundoff_cost_center = 0
    # POS Invoice DocType lacks this Sales Invoice field; used in
    # make_customer_gl_entry and make_tax_gl_entries for return invoices.
    update_outstanding_for_self = 0
    def validate(self):
        # When loyalty points are redeemed, paid_amount must include loyalty_amount
        # so ERPNext's validate_full_payment (paid_amount >= invoice_total) passes.
        # ERPNext's set_paid_amount() only sums payment rows, not loyalty — we fix that here.
        if self.redeem_loyalty_points and flt(self.loyalty_amount) > 0:
            payments_sum = sum(flt(p.amount) for p in self.get("payments", []))
            self.paid_amount = payments_sum + flt(self.loyalty_amount)
        super().validate()
        _apply_margin_scheme(self)

    def on_submit(self):
        # Run standard POS Invoice on_submit (loyalty, phone payments, serial
        # batch bundles, coupon code).
        super().on_submit()

        # Create SLE and GL entries that POS Invoice normally skips.
        if self.update_stock == 1:
            self.update_stock_ledger()

        self.make_gl_entries()

        if self.update_stock == 1:
            self.repost_future_sle_and_gle()

    def on_cancel(self):
        # Reimplements POS Invoice on_cancel with SLE/GL reversal inserted
        # before serial bundle delinking to preserve bundle references.
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

        self.ignore_linked_doctypes = (
            "GL Entry",
            "Stock Ledger Entry",
            "Repost Item Valuation",
            "Repost Payment Ledger",
            "Repost Payment Ledger Items",
            "Repost Accounting Ledger",
            "Repost Accounting Ledger Items",
            "Payment Ledger Entry",
            "Serial and Batch Bundle",
        )

        # SellingController chain (same pattern as POSInvoice.on_cancel)
        super(SalesInvoice, self).on_cancel()

        # Loyalty points cleanup (from POS Invoice.on_cancel)
        if not self.is_return and self.loyalty_program:
            self.delete_loyalty_point_entry()
        elif self.is_return and self.return_against and self.loyalty_program:
            against_psi_doc = frappe.get_doc("POS Invoice", self.return_against)
            against_psi_doc.delete_loyalty_point_entry()
            against_psi_doc.make_loyalty_point_entry()

        # Reverse SLE/GL entries BEFORE delinking serial bundles
        if self.update_stock == 1:
            self.update_stock_ledger()

        self.make_gl_entries_on_cancel()

        if self.update_stock == 1:
            self.repost_future_sle_and_gle()

        self.db_set("status", "Cancelled")

        if self.coupon_code:
            from erpnext.accounts.doctype.pricing_rule.utils import update_coupon_code_count

            update_coupon_code_count(self.coupon_code, "cancelled")

        # Cache serial numbers per item BEFORE delink clears the references.
        # Doc_events (reverse_serial_lifecycle) fire after on_cancel returns,
        # by which time serial_and_batch_bundle is already cleared.
        self._cached_serial_nos = {}
        for item in self.items:
            self._cached_serial_nos[item.name] = _get_serial_nos_from_item(item)

        # Delink serial bundles AFTER reversing stock/GL
        self.delink_serial_and_batch_bundle()

    def delink_serial_and_batch_bundle(self):
        """Override: skip cancel if the bundle was already cancelled by SLE reversal."""
        for row in self.items:
            if row.serial_and_batch_bundle:
                bundle_docstatus = frappe.db.get_value(
                    "Serial and Batch Bundle", row.serial_and_batch_bundle, "docstatus"
                )
                if not self.consolidated_invoice:
                    frappe.db.set_value(
                        "Serial and Batch Bundle",
                        row.serial_and_batch_bundle,
                        {"is_cancelled": 1, "voucher_no": ""},
                    )
                if bundle_docstatus == 1:
                    frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle).cancel()
                row.db_set("serial_and_batch_bundle", None)

    def make_discount_gl_entries(self, gl_entries):
        """Override: accounts_controller only handles Sales/Purchase Invoice.

        POS Invoice is a selling document, so we read Selling Settings and
        then delegate to the parent implementation with doctype temporarily
        set so the if/elif branches match.
        """
        from erpnext.accounts.doctype.account.account import get_account_currency

        enable_discount_accounting = cint(
            frappe.db.get_single_value("Selling Settings", "enable_discount_accounting")
        )

        dr_or_cr = "debit"
        rev_dr_cr = "credit"
        supplier_or_customer = self.customer

        if enable_discount_accounting:
            for item in self.get("items"):
                if item.get("discount_amount") and item.get("discount_account"):
                    discount_amount = item.discount_amount * item.qty
                    income_or_expense_account = (
                        item.income_account
                        if (not item.get("enable_deferred_revenue") or self.is_return)
                        else item.get("deferred_revenue_account")
                    )

                    account_currency = get_account_currency(item.discount_account)
                    gl_entries.append(
                        self.get_gl_dict(
                            {
                                "account": item.discount_account,
                                "against": supplier_or_customer,
                                dr_or_cr: flt(
                                    discount_amount * self.get("conversion_rate"),
                                    item.precision("discount_amount"),
                                ),
                                dr_or_cr + "_in_transaction_currency": flt(
                                    discount_amount, item.precision("discount_amount")
                                ),
                                "cost_center": item.cost_center,
                                "project": item.project,
                            },
                            account_currency,
                            item=item,
                        )
                    )

                    account_currency = get_account_currency(income_or_expense_account)
                    gl_entries.append(
                        self.get_gl_dict(
                            {
                                "account": income_or_expense_account,
                                "against": supplier_or_customer,
                                rev_dr_cr: flt(
                                    discount_amount * self.get("conversion_rate"),
                                    item.precision("discount_amount"),
                                ),
                                rev_dr_cr + "_in_transaction_currency": flt(
                                    discount_amount, item.precision("discount_amount")
                                ),
                                "cost_center": item.cost_center,
                                "project": item.project or self.project,
                            },
                            account_currency,
                            item=item,
                        )
                    )

        if (
            (enable_discount_accounting or self.get("is_cash_or_non_trade_discount"))
            and self.get("additional_discount_account")
            and self.get("discount_amount")
        ):
            import erpnext

            gl_entries.append(
                self.get_gl_dict(
                    {
                        "account": self.additional_discount_account,
                        "against": supplier_or_customer,
                        dr_or_cr: self.base_discount_amount,
                        "cost_center": self.cost_center or erpnext.get_default_cost_center(self.company),
                    },
                    item=self,
                )
            )

    def get_gl_entries(self, warehouse_account=None):
        # accounts_controller.make_discount_gl_entries only handles
        # "Sales Invoice"/"Purchase Invoice" doctypes.  Override below
        # ensures POS Invoice is handled like Sales Invoice for discounts.
        gl_entries = super().get_gl_entries(warehouse_account)
        if not cint(self.get("custom_is_margin_scheme")):
            return gl_entries

        # For mixed carts: reduce GST GL amounts by the margin-item GST saving
        margin_gst = flt(self.get("custom_margin_gst"))
        if margin_gst <= 0:
            return gl_entries

        # Calculate what GST SHOULD have been on margin items at full value
        total_margin_amount = sum(
            flt(item.amount) for item in self.items if cint(item.get("custom_is_margin_item"))
        )
        total_margin_taxable = sum(
            flt(item.get("custom_taxable_value")) for item in self.items
            if cint(item.get("custom_is_margin_item"))
        )
        # GST saving = GST that would have been charged on full amount minus actual margin GST
        gst_saving = 0
        for tax in self.taxes:
            if tax.charge_type != "On Net Total":
                continue
            full_margin_gst = (total_margin_amount * flt(tax.rate)) / 100
            actual_margin_gst = (total_margin_taxable * flt(tax.rate)) / 100
            gst_saving += full_margin_gst - actual_margin_gst

        if gst_saving <= 0:
            return gl_entries

        # Adjust GST GL entries downward by the saving amount
        adjusted = []
        for gl in gl_entries:
            if "gst" in (gl.account or "").lower():
                if flt(gl.debit) > 0:
                    gl.debit = max(0, flt(gl.debit) - gst_saving / 2)
                    gl.debit_in_account_currency = gl.debit
                if flt(gl.credit) > 0:
                    gl.credit = max(0, flt(gl.credit) - gst_saving / 2)
                    gl.credit_in_account_currency = gl.credit
                if flt(gl.debit) == 0 and flt(gl.credit) == 0:
                    continue
            adjusted.append(gl)
        return adjusted


# ── doc_event handlers (called via hooks.py) ─────────────────────


def validate_margin_scheme(doc, method=None):
    """Hook: validate — apply margin scheme GST calculation if applicable."""
    _apply_margin_scheme(doc)


def create_customer_device_records(doc, method=None):
    """Hook: on_submit — register sold devices under customer's account.

    Delegates to ch_item_master's CHCustomerDevice.create_or_update_for_serial()
    which handles deduplication, warranty syncing, and field population.
    """
    for item in doc.items:
        for sn in _get_serial_nos_from_item(item):

            device_kwargs = {
                "company": doc.company,
                "item_code": item.item_code,
                "item_name": item.item_name,
                "brand": item.brand or frappe.db.get_value("Item", item.item_code, "brand"),
                "purchase_date": doc.posting_date,
                # purchase_invoice links to Sales Invoice — POS Invoice is a separate flow
                # Store the POS Invoice name in a custom field if available, else skip
                "purchase_price": flt(item.rate),
                # purchase_store is a Link to Warehouse; use item.warehouse (already validated)
                "purchase_store": item.warehouse or frappe.db.get_value(
                    "POS Profile", doc.pos_profile, "warehouse"),
                "current_status": "Owned",
            }
            # Only set purchase_invoice if CH Customer Device has a pos_invoice field
            # (to avoid Link validation errors against tabSales Invoice)
            from ch_item_master.ch_customer_master.doctype.ch_customer_device.ch_customer_device import CHCustomerDevice
            cd_meta_fields = {f.fieldname for f in frappe.get_meta("CH Customer Device").fields}
            if "pos_invoice" in cd_meta_fields:
                device_kwargs["pos_invoice"] = doc.name
            elif "purchase_invoice" in cd_meta_fields:
                # Only set if the Link points to POS Invoice (not Sales Invoice)
                pf = frappe.get_meta("CH Customer Device").get_field("purchase_invoice")
                if pf and pf.options == "POS Invoice":
                    device_kwargs["purchase_invoice"] = doc.name

            # Attach warranty plan if selected
            if item.get("custom_warranty_plan"):
                plan = frappe.get_cached_doc("CH Warranty Plan", item.custom_warranty_plan)
                device_kwargs.update({
                    "active_warranty_plan": plan.name,
                    "warranty_plan_name": plan.plan_name,
                    "warranty_months": plan.duration_months,
                    "warranty_expiry": frappe.utils.add_months(doc.posting_date, plan.duration_months),
                    "warranty_status": "Active",
                })

            _create_or_update_device(sn, doc.customer, **device_kwargs)


def update_serial_lifecycle(doc, method=None):
    """Hook: on_submit — update CH Serial Lifecycle status.

    For sales: In Stock → Sold.
    For returns (is_return=1): Sold → Returned → In Stock.
    Delegates to ch_item_master's update_lifecycle_status() which enforces
    valid state transitions and writes audit log entries.
    """
    if doc.is_return:
        _return_serial_lifecycle(doc)
        return

    for item in doc.items:
        for sn in _get_serial_nos_from_item(item):
            if not frappe.db.exists("CH Serial Lifecycle", sn):
                continue

            _update_serial_status(
                serial_no=sn,
                new_status="Sold",
                company=doc.company,
                warehouse=doc.get("set_warehouse") or doc.items[0].warehouse if doc.items else None,
                remarks=f"Sold via POS Invoice {doc.name}",
                sale_date=doc.posting_date,
                sale_document=doc.name,
                sale_rate=flt(item.rate),
                customer=doc.customer,
                customer_name=doc.customer_name,
            )


def _return_serial_lifecycle(doc):
    """Handle serial lifecycle for return invoices (is_return=1).

    Sold → Returned → In Stock (item returned to store).
    """
    for item in doc.items:
        for sn in _get_serial_nos_from_item(item):
            if not frappe.db.exists("CH Serial Lifecycle", sn):
                continue

            current_status = frappe.db.get_value("CH Serial Lifecycle", sn, "lifecycle_status")
            if current_status == "Sold":
                _update_serial_status(
                    serial_no=sn,
                    new_status="Returned",
                    company=doc.company,
                    remarks=f"Returned via POS Invoice {doc.name} (return against {doc.return_against})",
                    sale_date=None,
                    sale_document=None,
                    sale_rate=0,
                    customer=None,
                    customer_name=None,
                )
                _update_serial_status(
                    serial_no=sn,
                    new_status="In Stock",
                    company=doc.company,
                    warehouse=item.warehouse,
                    remarks=f"Returned to stock — POS Invoice {doc.name}",
                )


def reverse_serial_lifecycle(doc, method=None):
    """Hook: on_cancel — revert serial lifecycle status.

    For sales: Sold → Returned → In Stock.
    For returns: In Stock → Sold (re-mark as sold since the return is voided).
    """
    # Require a cancellation reason from non-system users
    if frappe.session.user != "Administrator":
        if not (doc.get("custom_cancel_reason") or "").strip():
            frappe.throw(
                frappe._("A cancellation reason is required to cancel POS Invoice {0}").format(doc.name)
            )

    # Emit audit log for the cancellation
    try:
        from ch_pos.audit import log_business_event
        log_business_event(
            event_type="POS Invoice Cancelled",
            ref_doctype="POS Invoice",
            ref_name=doc.name,
            before="Submitted",
            after="Cancelled",
            remarks=doc.get("custom_cancel_reason") or "",
            company=doc.company,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Audit log failed on POS Invoice cancel")

    if doc.is_return:
        _cancel_return_serial_lifecycle(doc)
        return

    for item in doc.items:
        for sn in _get_serial_nos_from_item(item, parent_doc=doc):
            if not frappe.db.exists("CH Serial Lifecycle", sn):
                continue

            current_status = frappe.db.get_value("CH Serial Lifecycle", sn, "lifecycle_status")
            # Sold → Returned is a valid transition; then Returned → In Stock
            if current_status == "Sold":
                _update_serial_status(
                    serial_no=sn,
                    new_status="Returned",
                    company=doc.company,
                    remarks=f"POS Invoice {doc.name} cancelled",
                    sale_date=None,
                    sale_document=None,
                    sale_rate=0,
                    customer=None,
                    customer_name=None,
                )
                # Returned → In Stock
                _update_serial_status(
                    serial_no=sn,
                    new_status="In Stock",
                    company=doc.company,
                    warehouse=item.warehouse,
                    remarks=f"Returned to stock — POS Invoice {doc.name} cancelled",
                )


def _cancel_return_serial_lifecycle(doc):
    """Handle serial lifecycle when a return invoice is cancelled.

    Cancelling a return means the original sale stands — re-mark as Sold.
    In Stock → Sold (with original sale details from return_against).
    """
    orig_inv = doc.return_against
    for item in doc.items:
        for sn in _get_serial_nos_from_item(item, parent_doc=doc):
            if not frappe.db.exists("CH Serial Lifecycle", sn):
                continue

            current_status = frappe.db.get_value("CH Serial Lifecycle", sn, "lifecycle_status")
            if current_status == "In Stock":
                # Look up original sale details
                sale_date = None
                sale_rate = 0
                customer = doc.customer
                customer_name = doc.customer_name
                if orig_inv:
                    sale_date = frappe.db.get_value("POS Invoice", orig_inv, "posting_date")
                    # Find rate from original invoice item
                    orig_items = frappe.db.get_all("POS Invoice Item",
                        filters={"parent": orig_inv, "serial_no": ["like", f"%{sn}%"]},
                        fields=["rate"], limit=1)
                    if orig_items:
                        sale_rate = flt(orig_items[0].rate)
                _update_serial_status(
                    serial_no=sn,
                    new_status="Sold",
                    company=doc.company,
                    remarks=f"Return {doc.name} cancelled — sale {orig_inv} reinstated",
                    sale_date=sale_date,
                    sale_document=orig_inv,
                    sale_rate=sale_rate,
                    customer=customer,
                    customer_name=customer_name,
                )


def update_kiosk_token_status(doc, method=None):
    """Hook: on_submit — mark kiosk token as Converted if linked."""
    token = doc.get("custom_kiosk_token")
    if token:
        frappe.db.set_value("POS Kiosk Token", token, {
            "status": "Converted",
            "converted_invoice": doc.name,
        })


def deactivate_customer_devices(doc, method=None):
    """Hook: on_cancel — deactivate customer device records created by this invoice."""
    for item in doc.items:
        for sn in _get_serial_nos_from_item(item, parent_doc=doc):

            device = frappe.db.get_value(
                "CH Customer Device",
                {"serial_no": sn, "customer": doc.customer, "purchase_invoice": doc.name},
                "name",
            )
            if device:
                frappe.db.set_value("CH Customer Device", device, "current_status", "Inactive")


def revert_kiosk_token_status(doc, method=None):
    """Hook: on_cancel — revert kiosk token from Converted back to Expired.

    We set 'Expired' rather than 'Active' since the token has likely passed its
    expiry time by now. If the user needs to re-use it, they should create a new token.
    """
    token = doc.get("custom_kiosk_token")
    if token:
        frappe.db.set_value("POS Kiosk Token", token, {
            "status": "Expired",
            "converted_invoice": None,
        })


# ── Margin Scheme GST (selling side) ────────────────────────────


def _apply_margin_scheme(doc):
    """Apply margin scheme GST calculation for used/refurbished items.

    Mirrors ch_erp15 purchase-side logic:
    - Taxable margin = selling price - incoming rate (purchase cost)
    - GST applies only on the margin, not the full selling price
    - Exempted value = full amount - margin - GST on margin
    """
    has_margin = False

    for item in doc.items:
        if not item.get("custom_is_margin_item"):
            continue

        has_margin = True
        qty = flt(item.qty)
        rate = flt(item.rate)
        if qty <= 0 or rate <= 0:
            continue

        # Incoming rate (purchase cost) from serial no or valuation
        incoming = _get_incoming_rate(item)
        margin_per_unit = max(0, rate - incoming)

        item.custom_taxable_value = margin_per_unit * qty
        item.custom_exempted_value = 0  # calculated after tax pass

    if not has_margin:
        doc.custom_is_margin_scheme = 0
        return

    doc.custom_is_margin_scheme = 1

    # Sum up margin taxable across all margin items
    total_margin_taxable = sum(
        flt(item.custom_taxable_value) for item in doc.items if item.get("custom_is_margin_item")
    )
    total_non_margin = sum(
        flt(item.amount) for item in doc.items if not item.get("custom_is_margin_item")
    )

    # Recalculate tax rows for margin items
    total_gst = 0
    for tax in doc.taxes:
        if tax.charge_type != "On Net Total":
            continue

        # Tax on margin items only
        margin_tax = (total_margin_taxable * flt(tax.rate)) / 100
        # Tax on non-margin items (normal)
        non_margin_tax = (total_non_margin * flt(tax.rate)) / 100

        combined = margin_tax + non_margin_tax
        tax.tax_amount = combined
        tax.tax_amount_after_discount_amount = combined
        tax.base_tax_amount = combined
        tax.base_tax_amount_after_discount_amount = combined

        total_gst += margin_tax

    # Calculate exempted value per margin item
    total_exempted = 0
    for item in doc.items:
        if not item.get("custom_is_margin_item"):
            continue

        qty = flt(item.qty)
        rate = flt(item.rate)
        item_amount = rate * qty
        margin_taxable = flt(item.custom_taxable_value)

        # Proportional GST for this item
        item_gst = 0
        if total_margin_taxable > 0:
            item_gst = (margin_taxable / total_margin_taxable) * total_gst

        exempted = item_amount - margin_taxable - item_gst
        if exempted < 0:
            frappe.throw(f"Exempted value cannot be negative for item {item.item_code}")
        item.custom_exempted_value = exempted
        total_exempted += exempted

    # Populate header-level margin summary fields
    doc.custom_margin_taxable = total_margin_taxable
    doc.custom_margin_gst = total_gst
    doc.custom_margin_exempted = total_exempted


def _get_incoming_rate(item):
    """Get purchase cost (incoming rate) for a POS Invoice item."""
    # Try from serial no first
    serial = (item.serial_no or "").strip().split("\n")[0].strip()
    if serial:
        rate = frappe.db.get_value("CH Serial Lifecycle", {"serial_no": serial}, "purchase_rate")
        if rate:
            return flt(rate)

    # Fallback to item valuation rate
    return flt(item.get("incoming_rate") or item.get("valuation_rate") or 0)
