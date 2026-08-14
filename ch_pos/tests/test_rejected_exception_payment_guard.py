"""Regression guards for rejected/expired POS exception recovery."""

from pathlib import Path

import frappe


def _source(relative_path: str) -> str:
	return (Path(frappe.get_app_path("ch_pos")) / relative_path).read_text(encoding="utf-8")


def test_payment_revalidates_exception_immediately_before_submit():
	source = _source("public/js/pos_app/shared/payment_dialog.js")

	assert "async _validate_exception_requests_before_submit()" in source
	assert "check_exception_valid" in source
	assert "async _submit_invoice()" in source
	assert "await this._validate_exception_requests_before_submit()" in source
	assert 'EventBus.emit("cart:exception_invalid"' in source
	assert "this._close(false)" in source


def test_invalid_exception_restores_cart_pricing():
	source = _source("public/js/pos_app/services/cart_service.js")

	assert 'EventBus.on("cart:exception_invalid"' in source
	assert 'EventBus.emit("exception:invalidated"' in source
	assert "_invalidate_exception_link(exception_name" in source
	assert "this._remove_exception_from_item(item)" in source
	assert "cart_item.rate = flt(cart_item.pre_exception_rate)" in source
	assert "cart_item.price_list_rate = flt(cart_item.pre_exception_price_list_rate)" in source
	assert '["Rejected", "Expired", "Consumed", "Cancelled"]' in source

	payment_source = _source("public/js/pos_app/shared/payment_dialog.js")
	assert 'EventBus.on("exception:invalidated"' in payment_source
	assert "this._close(false)" in payment_source


def test_approved_exception_remains_in_status_polling():
	source = _source("public/js/pos_app/services/cart_service.js")
	collector = source.split("_collect_pending_exception_requests()", 1)[1].split(
		"_sync_pending_exception_statuses()", 1
	)[0]

	assert 'status === "Approved"' not in collector
	assert 'status === "Auto-Approved"' not in collector
