from __future__ import annotations

import ast
import datetime
import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ch_pos import config, hooks, rate_limits
from ch_pos.api import (
	attach_api,
	ai,
	free_sale_api,
	gift_redemption,
	manager_approval,
	payment_gateway_api,
	pos_api,
	session_validation,
	token_api,
)
from ch_pos.pos_core.doctype.ch_business_date import ch_business_date
from ch_pos.pos_core.doctype.ch_manager_pin import ch_manager_pin
from ch_pos.pos_core.doctype.ch_pos_session import ch_pos_session
from ch_pos.pos_kiosk.doctype.pos_kiosk_token import pos_kiosk_token
from ch_pos.overrides import discount_control, pos_invoice


class TestFinalResidualBlockers(TestCase):
	def test_atomic_fixed_window_counter_does_not_lose_concurrent_increments(self):
		class FakeRedis:
			def __init__(self):
				self.values = {}
				self.lock = threading.Lock()

			def make_key(self, key):
				return key.encode()

			def set(self, key, value, nx=False, ex=None):
				with self.lock:
					if nx and key in self.values:
						return False
					self.values[key] = int(value)
					return True

			def incrby(self, key, amount):
				with self.lock:
					self.values[key] = self.values.get(key, 0) + amount
					return self.values[key]

			def expire(self, key, seconds):
				return True

		fake = FakeRedis()
		with patch.object(rate_limits.frappe, "cache", return_value=fake):
			with ThreadPoolExecutor(max_workers=16) as pool:
				counts = list(
					pool.map(
						lambda _: rate_limits.increment_fixed_window("test", "same", 60),
						range(100),
					)
				)
		self.assertEqual(sorted(counts), list(range(1, 101)))

	def test_ai_rate_limit_fails_closed_when_redis_is_unavailable(self):
		with patch.object(
			ai, "increment_fixed_window", side_effect=ConnectionError("redis unavailable")
		):
			with self.assertRaisesRegex(ConnectionError, "redis unavailable"):
				ai._consume_rate_limit("compare", "user:test", 10, 60)

	def test_manager_pin_prefilters_bounded_store_candidates_before_decryption(self):
		source = inspect.getsource(ch_manager_pin.verify_manager_pin)
		self.assertIn('"manager_pin_candidate_limit"', source)
		self.assertIn("JOIN `tabUser`", source)
		self.assertIn("LIMIT %(limit)s", source)
		self.assertLess(source.index("assert_store_scope"), source.index("get_decrypted_password"))

	def test_company_modes_use_explicit_capabilities_not_legal_names(self):
		self.assertEqual(
			pos_api._get_company_type(
				"Arbitrary Tenant Holdings",
				[frappe._dict(is_retail_enabled=0, is_service_enabled=1)],
			),
			"service",
		)
		self.assertIsNone(
			pos_api._get_company_type(
				"GoFix Service Named But Unconfigured",
				[frappe._dict(is_retail_enabled=0, is_service_enabled=0)],
			)
		)
		self.assertEqual(
			pos_api._get_company_type(
				"Tenant",
				[frappe._dict(is_retail_enabled=1, is_service_enabled=1)],
			),
			"hybrid",
		)
		from ch_pos.api import search

		self.assertNotIn("company.lower()", inspect.getsource(search.pos_item_search))

	def test_pos_rejects_forged_client_plan_classification(self):
		with (
			patch.object(config, "get_control_setting", return_value=100),
			patch.object(free_sale_api.frappe, "get_all", return_value=[]),
		):
			with self.assertRaises(frappe.PermissionError):
				free_sale_api.canonicalize_cart_items([
					{"item_code": "PHONE-1", "qty": 1, "rate": 100, "is_vas": 1}
				])

	def test_pos_derives_plan_classification_from_master(self):
		plan = frappe._dict(
			name="PLAN-1",
			company="CH Demo",
			status="Active",
			plan_type="Value Added Service",
			service_item="VAS-SKU",
			is_sellable=1,
			pricing_mode="Fixed",
			price=299,
			percentage_value=0,
			valid_from=None,
			valid_to=None,
			min_device_price=0,
			max_device_price=0,
		)
		with (
			patch.object(config, "get_control_setting", return_value=100),
			patch.object(free_sale_api.frappe, "get_all", return_value=[plan]),
		):
			rows = free_sale_api.canonicalize_cart_items(
				[{"item_code": "VAS-SKU", "qty": 1, "rate": 299, "warranty_plan": "PLAN-1"}],
				company="CH Demo",
			)
		self.assertEqual(rows[0]["is_vas"], 1)
		self.assertEqual(rows[0]["is_warranty"], 0)

	def test_free_sale_hash_covers_every_billed_row_and_server_total(self):
		rows = [
			{"item_code": "PHONE-1", "qty": 1, "rate": 1000, "is_vas": 0, "is_warranty": 0},
			{
				"item_code": "VAS-SKU",
				"qty": 1,
				"rate": 200,
				"warranty_plan": "PLAN-1",
				"is_vas": 1,
				"is_warranty": 0,
			},
		]
		original_hash = free_sale_api.compute_cart_hash("CUST-1", rows, canonical=True)
		changed_rows = [dict(row) for row in rows]
		changed_rows[1]["rate"] = 1
		self.assertNotEqual(
			original_hash,
			free_sale_api.compute_cart_hash("CUST-1", changed_rows, canonical=True),
		)
		self.assertEqual(free_sale_api.compute_cart_total(rows), 1200)
		request_source = inspect.getsource(free_sale_api.request_free_sale_approval)
		self.assertIn('"grand_total": compute_cart_total(canonical_items)', request_source)
		self.assertIn("canonical_billed_items", inspect.getsource(pos_api.create_pos_invoice))

	def test_direct_submit_cannot_forge_item_manager_approval(self):
		item = frappe._dict(
			item_code="ITEM-1",
			rate=80,
			qty=1,
			serial_no="",
			discount_amount=20,
			custom_manager_approved=1,
			custom_manager_user="manager@example.test",
		)
		doc = MagicMock()
		doc.is_pos = 1
		doc.company = "CH Demo"
		doc.docstatus = 1
		doc.items = [item]
		doc.flags = frappe._dict(ch_pos_verified_manager_approvals=[])
		doc.pos_profile = "POS-1"
		doc.get.side_effect = lambda fieldname, default=None: default
		from ch_item_master.ch_item_master import commercial_api

		with (
			patch.object(discount_control, "_resolve_pos_channel", return_value="POS"),
			patch.object(commercial_api, "get_commercial_policy", return_value={}),
			patch.object(
				commercial_api,
				"validate_pos_discount",
				return_value={"allowed": False, "needs_approval": True, "reason": "approval required"},
			),
			patch.object(commercial_api, "log_pos_override"),
			patch.object(commercial_api, "check_offer_precedence"),
			patch.object(discount_control.frappe.db, "exists", return_value=True),
		):
			with self.assertRaises(frappe.PermissionError):
				discount_control.validate_pos_commercial_policy(doc)
		self.assertNotIn(
			"doc.docstatus == 0",
			inspect.getsource(discount_control.validate_pos_commercial_policy),
		)

	def test_pos_cancel_fails_closed_when_session_lookup_errors(self):
		doc = MagicMock()
		doc.name = "SINV-1"
		doc.posting_date = datetime.date.today()
		doc.pos_profile = "POS-1"
		doc.get.side_effect = lambda fieldname, default=None: 1 if fieldname == "is_pos" else default
		with (
			patch.object(pos_invoice, "is_privileged_user", return_value=False),
			patch.object(pos_invoice, "has_configured_roles", return_value=False),
			patch(
				"ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session.get_active_session",
				side_effect=RuntimeError("database unavailable"),
			),
			patch.object(pos_invoice.frappe, "log_error") as log_error,
		):
			with self.assertRaises(frappe.ValidationError):
				pos_invoice._enforce_cancel_policy(doc)
		log_error.assert_called_once()
		tree = ast.parse(inspect.getsource(pos_invoice._enforce_cancel_policy))
		self.assertFalse(any(isinstance(node, ast.Pass) for node in ast.walk(tree)))

	def test_model_comparison_has_role_permission_and_profile_scope(self):
		source = inspect.getsource(pos_api._require_model_comparison_access)
		self.assertIn('"model_comparison_roles"', source)
		self.assertIn('has_permission("Item"', source)
		self.assertIn('has_permission("POS Profile"', source)
		self.assertIn("assert_pos_profile_scope", source)

	def test_model_comparison_loop_has_no_database_calls(self):
		tree = ast.parse(inspect.getsource(pos_api.get_model_comparison))
		item_loop = next(
			node for node in ast.walk(tree)
			if isinstance(node, ast.For) and ast.unparse(node.target) == "item" and ast.unparse(node.iter) == "items"
		)
		calls = [
			ast.unparse(node.func) for node in ast.walk(item_loop)
			if isinstance(node, ast.Call)
		]
		self.assertFalse(any(call.startswith("frappe.db.") for call in calls))

	def test_ai_authorization_is_role_scoped_and_rate_limited(self):
		source = inspect.getsource(ai._authorize_ai)
		self.assertIn('"pos_ai_roles"', source)
		self.assertIn("assert_pos_profile_scope", source)
		self.assertIn("_enforce_ai_rate_limit", source)

	def test_free_sale_get_only_renders_confirmation(self):
		preview_source = inspect.getsource(free_sale_api.preview_approval)
		response_source = inspect.getsource(free_sale_api.respond_to_approval)
		self.assertIn('<form method="post"', preview_source)
		self.assertIn('methods=["GET"]', preview_source)
		self.assertNotIn("frappe.db.set_value", preview_source)
		self.assertIn('methods=["POST"]', response_source)
		self.assertIn("frappe.db.set_value", response_source)

	def test_registered_scheduler_batches_are_configurable_and_transaction_safe(self):
		for function in (
			pos_kiosk_token.expire_old_tokens,
			ch_pos_session.auto_close_stale_sessions,
			ch_pos_session.auto_close_overnight_sessions,
			gift_redemption.expire_stale_gift_redemptions,
			pos_api.release_expired_prebook_reservations,
			pos_api.calculate_attach_rate_bonus,
			token_api.recover_stale_pos_billing,
			session_validation.auto_close_pending_tokens_at_eod,
		):
			source = inspect.getsource(function)
			self.assertIn('"scheduler_batch_limit"', source)
			self.assertNotIn("frappe.db.commit", source)

	def test_app_screen_access_is_configurable_and_fails_closed_for_guest(self):
		self.assertEqual(
			hooks.add_to_apps_screen[0]["has_permission"],
			"ch_pos.config.has_app_permission",
		)
		self.assertFalse(config.has_app_permission("Guest"))
		with patch.object(config, "has_configured_roles", return_value=True) as allowed:
			self.assertTrue(config.has_app_permission("pos.user@example.com"))
			allowed.assert_called_once_with(
				"app_access_roles", config.APP_ACCESS_ROLE_DEFAULTS, "pos.user@example.com"
			)

	def test_pure_status_schedulers_use_set_based_updates(self):
		for function in (pos_kiosk_token.expire_old_tokens, gift_redemption.expire_stale_gift_redemptions):
			tree = ast.parse(inspect.getsource(function))
			self.assertFalse(any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(tree)))
			self.assertEqual(inspect.getsource(function).count("frappe.db.set_value"), 1)

	def test_eod_and_attach_schedulers_prefetch_loop_data(self):
		eod_source = inspect.getsource(session_validation.auto_close_pending_tokens_at_eod)
		self.assertIn("FOR UPDATE", eod_source)
		self.assertNotIn("frappe.db.get_value", eod_source)

		attach_tree = ast.parse(inspect.getsource(pos_api.calculate_attach_rate_bonus))
		executive_loop = next(
			node for node in ast.walk(attach_tree)
			if isinstance(node, ast.For) and ast.unparse(node.target) == "exec_doc"
		)
		loop_source = ast.unparse(executive_loop)
		self.assertNotIn("frappe.db.count", loop_source)
		self.assertNotIn("frappe.db.exists", loop_source)
		self.assertNotIn("frappe.db.sql", loop_source)

	def test_session_close_collapses_scalar_writes(self):
		source = inspect.getsource(ch_pos_session.CHPOSSession.close_session)
		self.assertIn("self.db_set(update_fields, update_modified=False)", source)
		self.assertNotIn("for field, value in update_fields.items()", source)

	def test_guest_endpoints_enforce_payload_bounds(self):
		self.assertIn("_bounded_public_text", inspect.getsource(token_api.create_token))
		self.assertIn("len(token) > 256", inspect.getsource(gift_redemption.spin_wheel))
		self.assertIn("_bounded_callback_body", inspect.getsource(payment_gateway_api.pine_labs_webhook))
		self.assertIn("len(token) > 256", inspect.getsource(free_sale_api._validate_signed_link))

	def test_gateway_endpoints_are_configured_and_fail_closed(self):
		machine = frappe._dict(
			name="MACHINE-1",
			machine_name="Counter One",
			api_base_url="https://gateway.example.test/auth/token",
			order_api_url="https://gateway.example.test/pay/orders",
		)
		settings = {
			"payment_gateway_allowed_hosts": "gateway.example.test",
			"payment_gateway_timeout_seconds": 15,
		}
		with patch.object(
			payment_gateway_api,
			"get_control_setting",
			side_effect=lambda fieldname, default=None: settings.get(fieldname, default),
		):
			self.assertEqual(
				payment_gateway_api._resolve_gateway_url(machine, "api_base_url"),
				machine.api_base_url,
			)
			self.assertEqual(payment_gateway_api._gateway_timeout_seconds(), 15)
			machine.api_base_url = "http://gateway.example.test/auth/token"
			with self.assertRaises(frappe.ValidationError):
				payment_gateway_api._resolve_gateway_url(machine, "api_base_url")
			machine.api_base_url = "https://gateway.example.test.attacker.invalid/auth/token"
			with self.assertRaises(frappe.PermissionError):
				payment_gateway_api._resolve_gateway_url(machine, "api_base_url")

		source = inspect.getsource(payment_gateway_api)
		self.assertNotIn("PINE_AUTH_URLS", source)
		self.assertNotIn("PINE_ORDER_URLS", source)
		machine_listing = inspect.getsource(payment_gateway_api.get_payment_machines)
		self.assertNotIn('"client_secret"', machine_listing)
		self.assertNotIn('"client_id"', machine_listing)

	def test_pincode_lookup_is_configurable_and_server_mediated(self):
		with (
			patch.object(pos_api, "require_authenticated_user"),
			patch.object(pos_api.frappe, "has_permission"),
			patch.object(pos_api, "is_privileged_user", return_value=True),
			patch.object(pos_api, "get_control_setting", return_value="Disabled"),
			patch.object(pos_api.frappe.db, "get_value") as get_value,
		):
			self.assertEqual(
				pos_api.lookup_pincode("600001"),
				{"found": False, "provider": "Disabled"},
			)
			get_value.assert_not_called()

		cart_path = Path(pos_api.__file__).parents[1] / "public/js/pos_app/shared/cart_panel.js"
		cart_source = cart_path.read_text()
		self.assertIn("ch_pos.api.pos_api.lookup_pincode", cart_source)
		self.assertNotIn("api.postalpincode.in", cart_source)
		self.assertNotIn("fetch(`http", cart_source)

	def test_camera_scanner_uses_same_origin_asset_and_has_safe_fallback(self):
		camera_path = Path(pos_api.__file__).parents[1] / "public/js/pos_app/shared/camera_scanner.js"
		source = camera_path.read_text()
		self.assertIn(
			"/assets/frappe/node_modules/html5-qrcode/html5-qrcode.min.js",
			source,
		)
		self.assertNotIn("https://", source)
		self.assertNotIn("jsdelivr", source.lower())
		self.assertIn("Promise.reject", source)
		self.assertIn("Scanner library unavailable", source)

	def test_privileged_user_can_verify_an_owned_manager_request(self):
		source = inspect.getsource(manager_approval.verify_approval)
		self.assertIn("and not is_privileged_user()", source)

	def test_pos_runtime_role_decisions_are_centralized(self):
		package_root = Path(config.__file__).parent
		offenders = []
		for path in package_root.rglob("*.py"):
			if path == Path(config.__file__) or "tests" in path.parts or path.name.startswith("test_"):
				continue
			if "frappe.get_roles" in path.read_text():
				offenders.append(str(path.relative_to(package_root)))
		self.assertEqual(offenders, [])

	def test_voucher_email_uses_escaped_invoice_company_branding(self):
		source = inspect.getsource(pos_api._send_voucher_email)
		self.assertNotIn("GoGizmo Retail Pvt Ltd", source)
		self.assertNotIn("Visit any GoGizmo", source)
		self.assertIn('escape_html(invoice.get("company")', source)
		self.assertIn("escaped_invoice_name", source)
		approval_source = inspect.getsource(pos_api.pos_send_approval_link)
		self.assertNotIn("Congruence Holdings", approval_source)
		self.assertNotIn("GoGizmo", approval_source)
		self.assertIn('get_cached_value("Company", doc.company, "company_name")', approval_source)
		for function in (free_sale_api._send_approval_email, pos_api.create_cross_store_transfer):
			operational_source = inspect.getsource(function)
			self.assertNotIn("Congruence Holdings", operational_source)
			self.assertNotIn("GoGizmo", operational_source)

	def test_business_date_audit_failure_releases_lock_without_committing(self):
		doc = MagicMock()
		doc.previous_date = "2026-07-20"
		doc.business_date = "2026-07-20"
		with (
			patch.object(ch_business_date.frappe.db, "sql", side_effect=[[(1,)], []]) as sql,
			patch.object(ch_business_date.frappe.db, "exists", return_value=True),
			patch.object(ch_business_date.frappe.db, "commit") as commit,
			patch.object(ch_business_date.frappe, "get_doc", return_value=doc),
			patch.object(ch_business_date.frappe, "has_permission"),
			patch("ch_pos.audit.log_business_event", side_effect=RuntimeError("audit failed")) as audit,
			patch.object(ch_business_date, "nowdate", return_value="2026-07-22"),
			patch.object(ch_business_date, "now_datetime", return_value=datetime.datetime(2026, 7, 22, 10)),
		):
			with self.assertRaisesRegex(RuntimeError, "audit failed"):
				ch_business_date.advance_business_date("STORE-1", "2026-07-21")
		commit.assert_not_called()
		audit.assert_called_once()
		self.assertTrue(audit.call_args.kwargs["raise_on_error"])
		self.assertIn("RELEASE_LOCK", sql.call_args_list[-1].args[0])

	def test_token_sequence_is_atomic_seeded_and_retry_safe(self):
		with (
			patch.object(
				token_api.frappe.db,
				"sql",
				side_effect=[[(12,)], None, [(12,)], None],
			) as sql,
			patch.object(token_api.frappe.utils, "today", return_value="2026-07-22"),
			patch.object(token_api, "getseries", side_effect=["0000000013", "0000000014"]),
		):
			self.assertEqual(token_api._next_daily_seq("POS A"), 13)
			self.assertEqual(token_api._next_daily_seq("POS A"), 14)
		self.assertIn("MAX(CAST(SUBSTRING_INDEX", sql.call_args_list[0].args[0])
		self.assertIn("ON DUPLICATE KEY UPDATE", sql.call_args_list[1].args[0])
		self.assertNotIn("COUNT(*) + 1", inspect.getsource(token_api._next_daily_seq))
		for function in (token_api.create_token, token_api.quick_walkin, token_api.log_counter_walkin):
			source = inspect.getsource(function)
			self.assertIn("_generate_token_display", source)
			self.assertNotIn("GET_LOCK", source)

	def test_token_scope_business_key_is_unique(self):
		path = Path(pos_kiosk_token.__file__).with_suffix(".json")
		doctype = json.loads(path.read_text())
		field = next(row for row in doctype["fields"] if row["fieldname"] == "token_scope_key")
		self.assertEqual(field.get("unique"), 1)
		self.assertIn("token_scope_key", inspect.getsource(pos_kiosk_token.POSKioskToken.before_validate))

	def test_interactive_list_loops_have_no_database_calls(self):
		for function in (
			attach_api._get_warranty_plans,
			pos_api.get_warranty_plans,
			pos_api.get_vas_plans,
			pos_api.get_vas_plans_with_rules,
			pos_api.get_store_repairs,
			pos_api.list_my_proformas,
		):
			tree = ast.parse(inspect.getsource(function))
			for loop in (node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While))):
				loop_source = ast.unparse(loop)
				self.assertNotIn("frappe.db.", loop_source, function.__name__)
		attach_source = inspect.getsource(attach_api._get_warranty_plans)
		self.assertIn('"warranty_plan_result_limit"', attach_source)
		self.assertIn("ORDER BY wp.price ASC, wp.name ASC", attach_source)
		self.assertNotIn("limit_page_length=0", attach_source)
		vas_source = inspect.getsource(pos_api.get_vas_plans_with_rules)
		self.assertIn('"warranty_plan_result_limit"', vas_source)
		self.assertIn('"vas_cart_item_limit"', vas_source)
		self.assertIn("plan_categories.get", vas_source)

	def test_store_dashboard_uses_aggregate_and_bounded_queries(self):
		source = inspect.getsource(pos_api.store_dashboard)
		self.assertIn("SUM(CASE WHEN pi.is_return = 0", source)
		self.assertIn('"store_dashboard_staff_limit"', source)
		self.assertIn("LIMIT %(staff_limit)s", source)
		self.assertNotIn('frappe.get_all(\n        "Sales Invoice"', source)
		self.assertNotIn("for inv in invoices", source)

	def test_buyback_sensitive_details_are_role_gated_and_masked(self):
		self.assertEqual(pos_api._mask_sensitive_value("123456789012", 4), "********9012")
		self.assertEqual(pos_api._mask_sensitive_value("123", 4), "***")
		source = inspect.getsource(pos_api.get_pos_buyback_detail)
		self.assertIn('"buyback_sensitive_data_roles"', source)
		self.assertIn('"sensitive_payout_data_visible"', source)
		self.assertIn("_mask_sensitive_value(o.customer_bank_account_number)", source)
		self.assertIn('if can_view_sensitive_data else ""', source)

	def test_new_security_settings_are_declared(self):
		path = Path(pos_api.__file__).parents[1] / "pos_core/doctype/ch_pos_control_settings/ch_pos_control_settings.json"
		settings = json.loads(path.read_text())
		fieldnames = {row["fieldname"] for row in settings["fields"]}
		self.assertTrue({
			"app_access_roles",
			"model_comparison_roles",
			"model_comparison_limit",
			"pos_ai_roles",
			"pos_ai_requests_per_user",
			"pos_ai_requests_per_ip",
			"pos_ai_rate_window_seconds",
			"pos_ai_max_payload_chars",
			"pos_ai_max_cart_items",
			"pos_ai_related_row_limit",
			"manager_pin_candidate_limit",
			"model_comparison_related_row_limit",
			"pos_search_related_row_limit",
			"guided_candidate_limit",
			"guided_related_row_limit",
			"token_report_row_limit",
			"session_report_row_limit",
			"retail_company_modes",
			"service_company_modes",
			"shared_company_modes",
			"payment_gateway_allowed_hosts",
			"payment_gateway_timeout_seconds",
			"pincode_lookup_provider",
			"scheduler_batch_limit",
			"stale_billing_timeout_minutes",
			"scheduler_notification_recipient_limit",
			"attach_bonus_slab_limit",
			"warranty_plan_result_limit",
			"warranty_plan_category_row_limit",
			"vas_cart_item_limit",
			"store_repair_result_limit",
			"proforma_result_limit",
			"store_dashboard_staff_limit",
			"buyback_sensitive_data_roles",
			"guest_payload_max_bytes",
		}.issubset(fieldnames))
