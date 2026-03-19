"""
CH POS Settlement — per-session end-of-day cash reconciliation and sign-off.

Settlement is always per company + device + session.
System calculates expected closing cash from actual posted transactions.
User enters actual physical cash count.
Manager approval required above configured threshold.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class CHPOSSettlement(Document):
    def validate(self):
        self._validate_session()
        self._validate_no_duplicate()
        self._validate_company_match()
        self._compute_denomination_total()

    def before_submit(self):
        self._validate_signoff()
        self._validate_variance_approval()
        self.settlement_status = "Submitted"

    def on_submit(self):
        self.db_set("settlement_status", "Submitted")

    def _validate_session(self):
        """Settlement must reference a valid session."""
        if not self.session:
            frappe.throw(_("POS Session is required."))
        status = frappe.db.get_value("CH POS Session", self.session, "status")
        if status == "Closed":
            frappe.throw(
                _("Session {0} is already closed. Settlement cannot be modified.").format(self.session)
            )

    def _validate_no_duplicate(self):
        """No duplicate settlement for one session."""
        existing = frappe.db.get_value(
            "CH POS Settlement",
            {
                "session": self.session,
                "docstatus": ("!=", 2),
                "name": ("!=", self.name),
            },
            "name",
        )
        if existing:
            frappe.throw(
                _("A settlement {0} already exists for session {1}.").format(existing, self.session)
            )

    def _validate_company_match(self):
        if self.session and self.company:
            session_company = frappe.db.get_value("CH POS Session", self.session, "company")
            if session_company and session_company != self.company:
                frappe.throw(
                    _("Settlement company {0} does not match session company {1}.").format(
                        self.company, session_company
                    )
                )

    def _compute_denomination_total(self):
        """Compute actual_closing_cash from denomination count if provided."""
        if self.denomination_details:
            total = 0
            for row in self.denomination_details:
                row.amount = flt(row.denomination) * (row.count or 0)
                total += flt(row.amount)
            if total > 0:
                self.actual_closing_cash = total

        self.variance_amount = flt(self.actual_closing_cash) - flt(self.expected_closing_cash)

    def _validate_signoff(self):
        """Cashier signoff mandatory before submission."""
        if not self.signoff_by_user:
            frappe.throw(_("Cashier sign-off is mandatory before submitting settlement."))

    def _validate_variance_approval(self):
        """Manager approval needed if variance exceeds threshold."""
        threshold = flt(frappe.db.get_single_value("CH POS Control Settings", "variance_approval_threshold") or 100)
        if abs(flt(self.variance_amount)) > threshold:
            if not self.variance_reason:
                frappe.throw(
                    _("Variance is ₹{0}. Reason is mandatory when variance exceeds ₹{1}.").format(
                        abs(flt(self.variance_amount)), threshold
                    )
                )
            if not self.signoff_by_manager:
                frappe.throw(
                    _("Variance ₹{0} exceeds threshold ₹{1}. Manager sign-off required.").format(
                        abs(flt(self.variance_amount)), threshold
                    )
                )

    def calculate_from_transactions(self):
        """Pull actual tender-wise totals from POS invoices for this session."""
        session = frappe.get_doc("CH POS Session", self.session)
        self.opening_balance = flt(session.opening_cash)
        self.business_date = session.business_date
        self.company = session.company
        self.store = session.store
        self.device = session.device

        # Tender-wise totals from POS Invoices
        payment_rows = frappe.db.sql("""
            SELECT mop.type AS mop_type, sip.mode_of_payment, SUM(sip.amount) AS total
            FROM `tabPOS Invoice` pi
            JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
            JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
            WHERE pi.pos_profile = %(pp)s
              AND pi.docstatus = 1
              AND IFNULL(pi.consolidated_invoice, '') = ''
              AND pi.posting_date = %(bd)s
              AND pi.is_return = 0
            GROUP BY mop.type, sip.mode_of_payment
        """, {"pp": session.pos_profile, "bd": session.business_date}, as_dict=True)

        cash_total = 0
        card_total = 0
        upi_total = 0
        wallet_total = 0
        bank_total = 0

        for r in payment_rows:
            amt = flt(r.total)
            mop_lower = (r.mode_of_payment or "").lower()
            if r.mop_type == "Cash":
                cash_total += amt
            elif "upi" in mop_lower or "phonepe" in mop_lower or "gpay" in mop_lower:
                upi_total += amt
            elif "wallet" in mop_lower:
                wallet_total += amt
            elif r.mop_type == "Bank":
                if "card" in mop_lower or "edc" in mop_lower or "credit" in mop_lower or "debit" in mop_lower:
                    card_total += amt
                else:
                    bank_total += amt
            else:
                bank_total += amt

        self.total_sales_cash = cash_total
        self.total_sales_card = card_total
        self.total_sales_upi = upi_total
        self.total_sales_wallet = wallet_total
        self.total_sales_bank = bank_total
        self.total_gross_sales = cash_total + card_total + upi_total + wallet_total + bank_total

        # Return-related cash out
        return_cash = flt(frappe.db.sql("""
            SELECT COALESCE(SUM(sip.amount), 0)
            FROM `tabPOS Invoice` pi
            JOIN `tabSales Invoice Payment` sip ON sip.parent = pi.name
            JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
            WHERE pi.pos_profile = %(pp)s
              AND pi.docstatus = 1
              AND IFNULL(pi.consolidated_invoice, '') = ''
              AND pi.posting_date = %(bd)s
              AND pi.is_return = 1
              AND mop.type = 'Cash'
        """, {"pp": session.pos_profile, "bd": session.business_date})[0][0])
        self.refund_cash_out = abs(return_cash)

        # Cash movements from CH Cash Drop (all movement types)
        movements = frappe.db.sql("""
            SELECT IFNULL(movement_type, 'Cash Drop') AS movement_type,
                   COALESCE(SUM(amount), 0) AS total
            FROM `tabCH Cash Drop`
            WHERE session = %(session)s AND docstatus = 1
            GROUP BY IFNULL(movement_type, 'Cash Drop')
        """, {"session": self.session}, as_dict=True)

        mov_map = {m.movement_type: flt(m.total) for m in movements}
        self.cash_drop_total = mov_map.get("Cash Drop", 0)
        self.petty_cash_out = mov_map.get("Petty Expense", 0)
        self.buyback_cash_out = mov_map.get("Buyback Cash Payout", 0)

        # Expected closing cash
        self.expected_closing_cash = (
            flt(self.opening_balance)
            + flt(self.total_sales_cash)
            - flt(self.refund_cash_out)
            - flt(self.cash_drop_total)
            - flt(self.petty_cash_out)
            - flt(self.buyback_cash_out)
        )

        self.variance_amount = flt(self.actual_closing_cash) - flt(self.expected_closing_cash)
