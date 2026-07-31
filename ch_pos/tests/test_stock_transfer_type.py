"""CHPOS-created Stock Entries are visibly classified as Store Transfers."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import frappe

from ch_pos.api import pos_api


class _FakeStockEntry:
    def __init__(self):
        self.name = "MAT-STE-POS-TEST"
        self.items = []
        self.inserted = False

    def append(self, _fieldname, values):
        row = frappe._dict(values)
        self.items.append(row)
        return row

    def insert(self):
        self.inserted = True


class TestStockTransferType(unittest.TestCase):
    def _create(self, items):
        stock_entry = _FakeStockEntry()
        with (
            patch.object(pos_api.frappe, "has_permission"),
            patch.object(pos_api, "require_configured_roles"),
            patch.object(pos_api, "assert_store_scope"),
            patch.object(pos_api, "is_privileged_user", return_value=True),
            patch.object(
                pos_api.frappe.db,
                "get_value",
                return_value="Test Company",
            ),
            patch.object(pos_api, "_get_open_reserved_sales_order_for_serial", return_value=None),
            patch.object(pos_api.frappe, "new_doc", return_value=stock_entry),
        ):
            result = pos_api.create_stock_transfer("SOURCE-WH", "TARGET-WH", items)

        return result, stock_entry

    def test_create_stock_transfer_stamps_store_transfer(self):
        result, stock_entry = self._create([])

        self.assertEqual(result, stock_entry.name)
        self.assertTrue(stock_entry.inserted)
        self.assertEqual(stock_entry.stock_entry_type, "Material Transfer")
        self.assertEqual(stock_entry.custom_transfer_type, "Store Transfer")

    def test_server_rejects_duplicate_serial_in_payload(self):
        with self.assertRaises(frappe.ValidationError):
            self._create([
                {
                    "item_code": "ITEM-1",
                    "serial_no": "350846580771725\n350846580771725",
                    "qty": 2,
                }
            ])

    def test_workspace_guards_duplicate_scan_events_and_callbacks(self):
        source = (
            Path(frappe.get_app_path("ch_pos"))
            / "public/js/pos_app/modules/stock_transfer/stock_transfer_workspace.js"
        ).read_text(encoding="utf-8")
        for marker in (
            'panel.off(".chStockTransfer")',
            'body.off(".chStockTransferNew")',
            "this._transfer_scans_in_flight = new Set()",
            "this._transfer_scans_in_flight.has(scan_key)",
            "returned_already",
        ):
            self.assertIn(marker, source)
