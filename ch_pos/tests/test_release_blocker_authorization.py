from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ch_pos.api.manager_approval import consume_action_grant, consume_approval_grant


def _function_node(function):
    module = ast.parse(inspect.getsource(function))
    return next(node for node in ast.walk(module) if isinstance(node, ast.FunctionDef))


class TestApprovalGrantSecurity(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")

    @patch("ch_pos.api.manager_approval._consume_cache_value")
    def test_item_approval_is_single_use(self, consume):
        payload = {
            "requested_by": "Administrator",
            "pos_profile": "POS-A",
            "item_code": "ITEM-A",
            "rate": 99,
            "qty": 1,
            "manager_user": "manager@example.com",
        }
        consume.side_effect = [payload, None]
        token = "a" * 48

        result = consume_approval_grant(token, "POS-A", "ITEM-A", 99, 1)
        self.assertEqual(result["manager_user"], "manager@example.com")
        with self.assertRaises(frappe.PermissionError):
            consume_approval_grant(token, "POS-A", "ITEM-A", 99, 1)

    @patch("ch_pos.api.manager_approval._consume_cache_value")
    def test_item_approval_rejects_transaction_mismatch(self, consume):
        consume.return_value = {
            "requested_by": "Administrator",
            "pos_profile": "POS-A",
            "item_code": "ITEM-A",
            "rate": 99,
            "qty": 1,
        }
        with self.assertRaises(frappe.PermissionError):
            consume_approval_grant("b" * 48, "POS-B", "ITEM-A", 99, 1)

    @patch("ch_pos.api.manager_approval._consume_cache_value")
    def test_credit_approval_rejects_other_customer(self, consume):
        consume.return_value = {
            "requested_by": "Administrator",
            "customer": "CUSTOMER-A",
            "company": "COMPANY-A",
            "store": "STORE-A",
        }
        with self.assertRaises(frappe.PermissionError):
            consume_action_grant(
                "credit",
                "c" * 48,
                {"customer": "CUSTOMER-B", "company": "COMPANY-A", "store": "STORE-A"},
            )


class TestReleaseBlockerContracts(IntegrationTestCase):
    def test_below_floor_approval_ignores_caller_flags(self):
        from ch_pos.api.pos_api import create_pos_invoice

        source = inspect.getsource(create_pos_invoice)
        self.assertIn("consume_approval_grant", source)
        self.assertNotIn('item.get("manager_approved")', source)
        self.assertNotIn("inv.custom_discount_authorized_by = discount_authorized_by", source)
        self.assertIn("ch_pos_verified_manager_approvals", source)

    def test_header_discount_requires_bound_approval(self):
        from ch_pos.api.pos_api import create_pos_invoice, verify_discount_auth

        invoice_source = inspect.getsource(create_pos_invoice)
        approval_source = inspect.getsource(verify_discount_auth)
        self.assertIn('consume_action_grant(\n                "discount"', invoice_source)
        self.assertIn('issue_action_grant(\n        "discount"', approval_source)
        self.assertNotIn("inv.custom_discount_authorized_by = discount_authorized_by", invoice_source)

    def test_promotional_discounts_are_server_authoritative(self):
        from ch_pos.api.pos_api import create_pos_invoice

        source = inspect.getsource(create_pos_invoice)
        for resolver in (
            "_resolve_coupon_discount",
            "_resolve_voucher_discount",
            "_resolve_bank_offer_discount",
        ):
            self.assertIn(resolver, source)
        self.assertNotIn("+ flt(coupon_discount_amount)", source)
        self.assertNotIn("+ flt(voucher_amount)", source)
        self.assertNotIn("+ flt(bank_offer_discount)", source)
        self.assertNotIn('"discount_percentage": flt(item.get(', source)

    def test_exchange_credit_is_scoped_and_server_authoritative(self):
        from ch_pos.api.pos_api import create_pos_invoice

        source = inspect.getsource(create_pos_invoice)
        self.assertIn("_resolve_exchange_credit", source)
        self.assertIn('get_control_setting("buyback_exchange_mode_of_payment"', source)
        self.assertIn('"amount": exchange_credit', source)
        self.assertIn("Exchange payment amount is controlled by the server", source)

    def test_pos_invoice_pipeline_does_not_commit_partial_state(self):
        from ch_pos.api.pos_api import (
            _force_insert_tax_rows,
            _sync_header_totals_pre_submit,
            _update_gst_breakup,
            _write_item_calculations,
            _write_tax_rows_and_header,
        )

        for function in (
            _force_insert_tax_rows,
            _sync_header_totals_pre_submit,
            _update_gst_breakup,
            _write_item_calculations,
            _write_tax_rows_and_header,
        ):
            self.assertNotIn("frappe.db.commit", inspect.getsource(function))

    def test_pos_invoice_propagates_profile_cost_center_to_posting_rows(self):
        from ch_pos.api.pos_api import create_pos_invoice, _force_insert_tax_rows

        invoice_source = inspect.getsource(create_pos_invoice)
        tax_source = inspect.getsource(_force_insert_tax_rows)

        self.assertIn("inv.cost_center = pos_cost_center", invoice_source)
        self.assertIn('"cost_center": pos_cost_center', invoice_source)
        self.assertIn("transaction_cc or src.cost_center or default_cc", tax_source)

    def test_offer_lookup_requires_company_scope(self):
        from ch_pos.api.offers import get_applicable_offers

        source = inspect.getsource(get_applicable_offers)
        self.assertIn("_resolve_company_scope", source)
        self.assertIn('"approval_status": "Approved"', source)
        self.assertIn("_filter_company_offers", source)

    @patch("ch_pos.api.pos_api.frappe.db.get_value")
    def test_voucher_rejects_other_company(self, get_value):
        from ch_pos.api.pos_api import _resolve_voucher_discount

        get_value.side_effect = [
            "VOUCHER-1",
            frappe._dict(
                {
                    "name": "VOUCHER-1",
                    "voucher_code": "SECRET",
                    "docstatus": 1,
                    "status": "Active",
                    "company": "COMPANY-B",
                    "currency": "INR",
                    "balance": 500,
                }
            ),
        ]
        with self.assertRaises(frappe.PermissionError):
            _resolve_voucher_discount(
                "SECRET",
                "CUSTOMER-A",
                "COMPANY-A",
                1000,
                currency="INR",
            )

    @patch("ch_pos.api.pos_api.frappe.db.get_value")
    def test_coupon_rejects_other_customer(self, get_value):
        from ch_pos.api.pos_api import _resolve_coupon_discount

        get_value.side_effect = [
            "COUPON-1",
            frappe._dict(
                {
                    "name": "COUPON-1",
                    "coupon_code": "SECRET",
                    "pricing_rule": "RULE-1",
                    "customer": "CUSTOMER-B",
                    "maximum_use": 1,
                    "used": 0,
                }
            ),
        ]
        with self.assertRaises(frappe.PermissionError):
            _resolve_coupon_discount(
                "SECRET",
                "CUSTOMER-A",
                1000,
                company="COMPANY-A",
            )

    @patch("ch_pos.api.pos_api.frappe.db.get_value")
    @patch("ch_pos.api.pos_api.frappe.db.exists")
    def test_bank_offer_rejects_other_company(self, exists, get_value):
        from ch_pos.api.pos_api import _resolve_bank_offer_discount

        exists.side_effect = [True, False]
        get_value.return_value = frappe._dict(
            {
                "name": "OFFER-1",
                "company": "COMPANY-B",
                "offer_name": "Offer",
                "offer_type": "Bank Offer",
                "offer_level": "Bill",
                "value_type": "Amount",
                "value": 500,
                "channel": "POS",
                "status": "Active",
                "approval_status": "Approved",
                "min_bill_amount": 100,
                "payment_mode": "Credit Card",
                "stackable": 0,
            }
        )
        with self.assertRaises(frappe.PermissionError):
            _resolve_bank_offer_discount(
                "OFFER-1",
                "COMPANY-A",
                1000,
                ["Credit Card"],
            )

    def test_prebooking_cancel_has_role_permission_and_scope_gates(self):
        from ch_pos.api.pos_api import cancel_pre_booking

        source = inspect.getsource(cancel_pre_booking)
        self.assertIn('"prebook_cancel_roles"', source)
        self.assertIn('so.check_permission("cancel")', source)
        self.assertIn("assert_any_warehouse_scope", source)
        self.assertIn('pe.check_permission("cancel")', source)
        self.assertIn('pe.check_permission("delete")', source)

    def test_mass_backfill_is_not_whitelisted(self):
        from ch_pos.api.pos_api import backfill_draft_documents

        node = _function_node(backfill_draft_documents)
        decorator_names = {ast.unparse(item) for item in node.decorator_list}
        self.assertFalse(any("whitelist" in name for name in decorator_names))
        self.assertIn("require_privileged_user", inspect.getsource(backfill_draft_documents))

    def test_guest_free_sale_response_requires_signature(self):
        from ch_pos.api.free_sale_api import _validate_signed_link, respond_to_approval

        source = inspect.getsource(respond_to_approval)
        validator_source = inspect.getsource(_validate_signed_link)
        self.assertIn("_validate_signed_link", source)
        self.assertIn("if not sig or not hmac.compare_digest", validator_source)
        self.assertNotIn("if sig:", validator_source)

    def test_free_sale_consumption_uses_authoritative_approvers(self):
        from ch_pos.api.pos_api import create_pos_invoice

        source = inspect.getsource(create_pos_invoice)
        self.assertIn("authoritative_approvers", source)
        self.assertNotIn(
            "inv.custom_free_sale_approved_by = (free_sale_approved_by", source
        )

    def test_session_unlock_authenticates_password(self):
        from ch_pos.api.isolation_api import unlock_session

        source = inspect.getsource(unlock_session)
        self.assertIn("check_password", source)
        self.assertIn("assert_session_operator", source)
        self.assertIn("assert_session_scope", source)

    def test_edc_auto_match_requires_named_session(self):
        from ch_pos.pos_core.doctype.pos_edc_settlement.pos_edc_settlement import (
            POSEDCSettlement,
        )

        source = inspect.getsource(POSEDCSettlement.auto_match)
        self.assertIn("if not self.session", source)
        self.assertIn("_assert_edc_scope", source)
        self.assertIn("edc_reconciliation_roles", source)

    def test_payment_machine_scope_requires_store_and_company(self):
        from ch_pos.api.payment_gateway_api import _machine_matches_scope

        machine = frappe._dict({"store": "STORE-A", "company": "COMPANY-A"})
        self.assertTrue(
            _machine_matches_scope(machine, ({"STORE-A"}, {"COMPANY-A"}))
        )
        self.assertFalse(
            _machine_matches_scope(machine, ({"STORE-B"}, {"COMPANY-A"}))
        )
        self.assertFalse(
            _machine_matches_scope(machine, ({"STORE-A"}, {"COMPANY-B"}))
        )

    def test_buyback_mutations_apply_document_scope(self):
        from ch_pos.api.pos_api import (
            pos_approve_customer_buyback,
            pos_send_customer_otp,
            pos_settle_buyback_cashback,
        )

        for function in (
            pos_approve_customer_buyback,
            pos_send_customer_otp,
            pos_settle_buyback_cashback,
        ):
            self.assertIn("_assert_buyback_doc_scope", inspect.getsource(function))

    def test_manager_lookup_is_database_bounded(self):
        from ch_pos.api.manager_approval import _resolve_manager_user

        source = inspect.getsource(_resolve_manager_user)
        self.assertIn("LIMIT 50", source)
        self.assertNotIn("limit_page_length=0", source)

    def test_sensitive_role_lists_exist_in_control_settings(self):
        settings_path = (
            Path(__file__).parents[1]
            / "pos_core"
            / "doctype"
            / "ch_pos_control_settings"
            / "ch_pos_control_settings.json"
        )
        settings = json.loads(settings_path.read_text())
        fields = {row["fieldname"] for row in settings["fields"]}
        self.assertTrue(
            {
                "manager_pin_roles",
                "prebook_cancel_roles",
                "prebook_refund_roles",
                "session_override_roles",
                "session_report_roles",
                "stock_transfer_roles",
                "token_operation_roles",
                "token_view_roles",
                "edc_reconciliation_roles",
                "payment_gateway_roles",
                "invoice_share_roles",
                "reprint_roles",
                "buyback_otp_bypass_roles",
                "buyback_settlement_roles",
                "buyback_exchange_mode_of_payment",
                "fifo_alert_roles",
            }.issubset(fields)
        )
