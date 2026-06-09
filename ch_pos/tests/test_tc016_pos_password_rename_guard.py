"""
TC_016 regression guard.

Run:
  bench --site erpnext.local execute ch_pos.tests.test_tc016_pos_password_rename_guard.run
"""

from __future__ import annotations

from pathlib import Path

import frappe


PASS = 0
FAIL = 0
RESULTS: list[tuple[str, str, str]] = []


def _ok(tid: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    RESULTS.append(("PASS", tid, detail))
    print(f"  PASS {tid}: {detail}" if detail else f"  PASS {tid}")


def _fail(tid: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    RESULTS.append(("FAIL", tid, detail))
    print(f"  FAIL {tid}: {detail}" if detail else f"  FAIL {tid}")


def _read(rel_path: str) -> str:
    base = Path(frappe.get_app_path("ch_pos"))
    return (base / rel_path).read_text(encoding="utf-8")


def tc016_pos_password_rename() -> None:
    tid = "TC_016"
    sources = {
        "doctype_meta": _read("pos_core/doctype/ch_manager_pin/ch_manager_pin.json"),
        "hooks": _read("hooks.py"),
        "workspace": _read("pos_core/workspace/pos/pos.json"),
        "controller": _read("pos_core/doctype/ch_manager_pin/ch_manager_pin.py"),
    }

    checks = {
        "doctype_name": '"name": "CH POS Password"',
        "doctype_label": '"label": "POS Password"',
        "hooks_mapping": '"CH POS Password": "pos_core/doctype/ch_manager_pin/ch_manager_pin.js"',
        "workspace_label": '"label": "CH POS Password"',
        "workspace_link": '"link_to": "CH POS Password"',
        "controller_class": "class CHPOSPassword(Document):",
        "controller_message": 'title=_("CH POS Password Error")',
    }

    haystack = "\n".join(sources.values())
    missing = [name for name, needle in checks.items() if needle not in haystack]
    if missing:
        _fail(tid, f"Missing rename markers: {', '.join(missing)}")
        return

    _ok(tid, "Manager PIN is exposed as CH POS Password across metadata, hooks, workspace, and controller")


def run() -> dict:
    global PASS, FAIL, RESULTS
    PASS = 0
    FAIL = 0
    RESULTS = []

    print("\n=== TC_016 Manager PIN Rename Guard ===\n")
    tc016_pos_password_rename()

    print(f"\n  Summary: {PASS} pass / {FAIL} fail")
    if FAIL:
        raise AssertionError(f"{FAIL} guard(s) failed")
    return {"pass": PASS, "fail": FAIL}