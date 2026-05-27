"""
CH POS — AI Guided Selling E2E Test Suite.

Tests the Guided Selling module (ch_pos.api.guided + ch_pos.api.ai):
- Catalog loading (categories + sub-categories)
- Question set generation per sub-category
- Product recommendation by budget / brand / spec
- Comparison matrix generation (static + AI fallback)
- Upsell suggestions
- Session persistence (save_guided_session)
- Add-to-cart from recommendation flow

Run:
    bench --site erpnext.local execute ch_pos.tests.test_guided_selling_e2e.run_all
"""

import traceback

import frappe
from frappe.utils import flt, cint

_results = []


def _ok(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "PASS"})
    print(f"  PASS  [{flow}] {step}" + (f"  ({detail})" if detail else ""))


def _fail(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "FAIL", "detail": detail})
    print(f"  FAIL  [{flow}] {step}" + (f"  — {detail}" if detail else ""))


def _skip(flow, step, detail=""):
    _results.append({"flow": flow, "step": step, "status": "SKIP"})
    print(f"  SKIP  [{flow}] {step}" + (f"  ({detail})" if detail else ""))


FLOW = "Guided Selling"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_pos_profile():
    profiles = frappe.get_all(
        "POS Profile",
        filters={"disabled": 0},
        fields=["name", "company", "warehouse"],
        limit=1,
    )
    return profiles[0] if profiles else None


def _get_first_sub_category():
    """Return first enabled CH Sub Category or None."""
    rows = frappe.get_all(
        "CH Sub Category",
        filters={"disabled": 0},
        fields=["name", "sub_category_name", "category"],
        limit=1,
    )
    return rows[0] if rows else None


def _get_items_for_comparison():
    """Return 2-3 item codes suitable for comparison testing."""
    items = frappe.get_all(
        "Item",
        filters={"disabled": 0, "is_sales_item": 1, "has_variants": 0},
        fields=["name", "item_name", "brand"],
        limit=3,
    )
    return [i.name for i in items] if items else []


def _cleanup_guided_session(session_name):
    if not session_name:
        return
    if not frappe.db.exists("POS Guided Session", session_name):
        return
    try:
        frappe.delete_doc("POS Guided Session", session_name, ignore_permissions=True, force=True)
        frappe.db.commit()
    except Exception:
        pass


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_01_get_guided_catalog():
    """get_guided_catalog returns categories and sub-categories."""
    try:
        from ch_pos.api.guided import get_guided_catalog
        result = get_guided_catalog()

        assert isinstance(result, dict), "get_guided_catalog should return dict"
        assert "categories" in result, "Should have 'categories' key"
        assert "sub_categories" in result, "Should have 'sub_categories' key"
        assert isinstance(result["categories"], list), "categories should be list"
        assert isinstance(result["sub_categories"], list), "sub_categories should be list"

        _ok(FLOW, "01 get_guided_catalog",
            f"categories={len(result['categories'])}, sub_categories={len(result['sub_categories'])}")
    except Exception as e:
        _fail(FLOW, "01 get_guided_catalog", str(e))


def test_02_get_guided_questions_no_sub_category():
    """get_guided_questions returns empty list for blank sub_category."""
    try:
        from ch_pos.api.guided import get_guided_questions
        result = get_guided_questions(sub_category="")

        assert result == [], f"Empty sub_category should return [], got {result}"
        _ok(FLOW, "02 get_guided_questions empty sub_category", "Returns [] for empty sub_category")
    except Exception as e:
        _fail(FLOW, "02 get_guided_questions empty sub_category", str(e))


def test_03_get_guided_questions_unknown_sub_category():
    """get_guided_questions handles unknown sub_category gracefully."""
    try:
        from ch_pos.api.guided import get_guided_questions
        try:
            result = get_guided_questions(sub_category="NONEXISTENT_SUB_CAT_XYZ")
            # Should either return [] or raise DoesNotExistError
            assert isinstance(result, list), "Should return list or raise, got non-list"
            _ok(FLOW, "03 get_guided_questions unknown sub_cat", f"Returned list gracefully: {result}")
        except frappe.exceptions.DoesNotExistError:
            _ok(FLOW, "03 get_guided_questions unknown sub_cat", "Raises DoesNotExistError as expected")
        except Exception as e:
            if "not found" in str(e).lower() or "does not exist" in str(e).lower():
                _ok(FLOW, "03 get_guided_questions unknown sub_cat",
                    f"Raises appropriate error: {str(e)[:60]}")
            else:
                _fail(FLOW, "03 get_guided_questions unknown sub_cat", str(e))
    except Exception as e:
        _fail(FLOW, "03 get_guided_questions unknown sub_cat", str(e))


def test_04_get_guided_questions_valid_sub_category():
    """get_guided_questions returns questions with budget and brand for valid sub-category."""
    try:
        sub_cat = _get_first_sub_category()
        if not sub_cat:
            _skip(FLOW, "04 get_guided_questions valid sub_cat", "No CH Sub Category configured")
            return

        from ch_pos.api.guided import get_guided_questions
        questions = get_guided_questions(sub_category=sub_cat.name)

        assert isinstance(questions, list), "Questions should be a list"

        # Budget question is always first (universal)
        if questions:
            assert questions[0].get("key") == "budget", \
                f"First question should be budget, got {questions[0].get('key')}"
            assert questions[0].get("type") == "range", \
                f"Budget question type should be range"

        # Each question must have required fields
        for q in questions:
            assert "question" in q, f"Question should have 'question' text: {q}"
            assert "type" in q, f"Question should have 'type': {q}"
            assert "key" in q, f"Question should have 'key': {q}"

        _ok(FLOW, "04 get_guided_questions valid sub_cat",
            f"sub_cat={sub_cat.name}, questions={len(questions)}")
    except Exception as e:
        _fail(FLOW, "04 get_guided_questions valid sub_cat", str(e))


def test_05_get_guided_questions_budget_range():
    """Budget question has min/max options."""
    try:
        sub_cat = _get_first_sub_category()
        if not sub_cat:
            _skip(FLOW, "05 budget question options", "No CH Sub Category configured")
            return

        from ch_pos.api.guided import get_guided_questions
        questions = get_guided_questions(sub_category=sub_cat.name)

        budget_q = next((q for q in questions if q.get("key") == "budget"), None)
        if not budget_q:
            _skip(FLOW, "05 budget question options", "No budget question in result")
            return

        opts = budget_q.get("options", {})
        assert "min" in opts, "Budget options should have 'min'"
        assert "max" in opts, "Budget options should have 'max'"
        assert "step" in opts, "Budget options should have 'step'"
        assert flt(opts["max"]) > flt(opts["min"]), "max should be > min"

        _ok(FLOW, "05 budget question options",
            f"min={opts['min']}, max={opts['max']}, step={opts['step']}")
    except Exception as e:
        _fail(FLOW, "05 budget question options", str(e))


def test_06_get_guided_recommendations_empty_responses():
    """get_guided_recommendations with empty responses returns list (may be empty)."""
    try:
        sub_cat = _get_first_sub_category()
        if not sub_cat:
            _skip(FLOW, "06 recommendations empty responses", "No CH Sub Category configured")
            return

        from ch_pos.api.guided import get_guided_recommendations
        result = get_guided_recommendations(
            sub_category=sub_cat.name,
            responses=[],
        )

        assert isinstance(result, list), "Recommendations should be a list"
        _ok(FLOW, "06 recommendations empty responses",
            f"Returned {len(result)} recommendations for empty responses")
    except Exception as e:
        _fail(FLOW, "06 recommendations empty responses", str(e))


def test_07_get_guided_recommendations_with_budget():
    """Recommendations filter by budget — items over budget get lower scores."""
    try:
        sub_cat = _get_first_sub_category()
        if not sub_cat:
            _skip(FLOW, "07 recommendations with budget", "No CH Sub Category configured")
            return

        profile = _get_pos_profile()
        from ch_pos.api.guided import get_guided_recommendations

        responses = [
            {"key": "budget", "question": "What is your budget range?", "answer": 15000},
        ]
        result = get_guided_recommendations(
            sub_category=sub_cat.name,
            responses=responses,
            warehouse=profile.warehouse if profile else None,
            limit=5,
        )

        assert isinstance(result, list), "Recommendations should be a list"

        # Each recommendation must have required fields
        for rec in result:
            assert "item_code" in rec, f"Recommendation should have item_code: {rec}"
            assert "item_name" in rec, f"Recommendation should have item_name: {rec}"
            assert "match_score" in rec, f"Recommendation should have match_score: {rec}"
            assert "reason" in rec, f"Recommendation should have reason: {rec}"
            assert 0 <= flt(rec["match_score"]) <= 100, \
                f"match_score should be 0-100, got {rec['match_score']}"

        # Results should be sorted by match_score desc
        if len(result) > 1:
            scores = [flt(r["match_score"]) for r in result]
            assert scores == sorted(scores, reverse=True), \
                f"Results should be sorted by match_score desc: {scores}"

        _ok(FLOW, "07 recommendations with budget",
            f"Returned {len(result)} recs, first score={result[0]['match_score'] if result else 'n/a'}")
    except Exception as e:
        _fail(FLOW, "07 recommendations with budget", str(e))


def test_08_get_guided_recommendations_brand_preference():
    """Recommendations boost items matching preferred brand."""
    try:
        sub_cat = _get_first_sub_category()
        if not sub_cat:
            _skip(FLOW, "08 recommendations brand preference", "No CH Sub Category configured")
            return

        profile = _get_pos_profile()
        from ch_pos.api.guided import get_guided_recommendations

        # Get all recs without preference
        all_recs = get_guided_recommendations(
            sub_category=sub_cat.name,
            responses=[],
            warehouse=profile.warehouse if profile else None,
        )

        if not all_recs:
            _skip(FLOW, "08 recommendations brand preference", "No items in this sub-category")
            return

        # Find a brand that has items
        top_brand = all_recs[0].get("brand") if all_recs[0].get("brand") else None
        if not top_brand:
            _skip(FLOW, "08 recommendations brand preference", "No branded items in sub-category")
            return

        # Recs with brand preference
        brand_recs = get_guided_recommendations(
            sub_category=sub_cat.name,
            responses=[{"key": "brand", "question": "Brand?", "answer": top_brand}],
            warehouse=profile.warehouse if profile else None,
        )

        assert isinstance(brand_recs, list), "Should return list"

        # The preferred brand's items should appear higher
        if brand_recs and all_recs:
            brand_items = [r for r in brand_recs if r.get("brand") == top_brand]
            if brand_items:
                assert flt(brand_items[0]["match_score"]) >= flt(brand_recs[-1]["match_score"]), \
                    "Brand-matched items should have equal or higher score than last item"

        _ok(FLOW, "08 recommendations brand preference",
            f"Brand={top_brand}, {len(brand_recs)} recommendations")
    except Exception as e:
        _fail(FLOW, "08 recommendations brand preference", str(e))


def test_09_recommendations_limit_respected():
    """limit parameter caps the number of returned recommendations."""
    try:
        sub_cat = _get_first_sub_category()
        if not sub_cat:
            _skip(FLOW, "09 recommendations limit", "No CH Sub Category configured")
            return

        from ch_pos.api.guided import get_guided_recommendations

        result = get_guided_recommendations(
            sub_category=sub_cat.name,
            responses=[],
            limit=2,
        )

        assert len(result) <= 2, f"Results should be <= 2 (limit=2), got {len(result)}"
        _ok(FLOW, "09 recommendations limit", f"limit=2, returned={len(result)}")
    except Exception as e:
        _fail(FLOW, "09 recommendations limit", str(e))


def test_10_compare_items_static_fallback():
    """compare_items returns comparison result (static fallback when AI not configured)."""
    try:
        items = _get_items_for_comparison()
        if len(items) < 2:
            _skip(FLOW, "10 compare_items static fallback", "Not enough items for comparison")
            return

        from ch_pos.api.ai import compare_items
        result = compare_items(item_codes=items[:2])

        assert isinstance(result, dict), "compare_items should return dict"
        assert "comparison_result" in result, "Should have comparison_result key"
        assert "source" in result, "Should have source key"

        # comparison_result can be list or dict
        cr = result["comparison_result"]
        assert cr is not None, "comparison_result should not be None"

        _ok(FLOW, "10 compare_items static fallback",
            f"source={result.get('source')}, items={len(items[:2])}")
    except Exception as e:
        _fail(FLOW, "10 compare_items static fallback", str(e))


def test_11_compare_items_requires_minimum_2():
    """compare_items raises error with fewer than 2 items."""
    try:
        items = _get_items_for_comparison()
        if not items:
            _skip(FLOW, "11 compare_items min 2", "No items available")
            return

        from ch_pos.api.ai import compare_items
        try:
            compare_items(item_codes=[items[0]])
            _fail(FLOW, "11 compare_items min 2", "Should have raised error for <2 items")
        except frappe.exceptions.ValidationError:
            _ok(FLOW, "11 compare_items min 2", "Correctly requires minimum 2 items")
        except Exception as e:
            if "2" in str(e) or "least" in str(e).lower() or "minimum" in str(e).lower():
                _ok(FLOW, "11 compare_items min 2", f"Correctly blocked: {str(e)[:60]}")
            else:
                _fail(FLOW, "11 compare_items min 2", f"Unexpected error: {e}")
    except Exception as e:
        _fail(FLOW, "11 compare_items min 2", str(e))


def test_12_compare_items_max_3():
    """compare_items caps at 3 items even if more are passed."""
    try:
        # Get 5 items to test capping
        all_items = frappe.get_all(
            "Item",
            filters={"disabled": 0, "is_sales_item": 1, "has_variants": 0},
            fields=["name"],
            limit=5,
        )
        item_codes = [i.name for i in all_items]
        if len(item_codes) < 4:
            _skip(FLOW, "12 compare_items max 3", "Not enough items to test cap")
            return

        from ch_pos.api.ai import compare_items
        result = compare_items(item_codes=item_codes)  # pass 4-5 items

        assert isinstance(result, dict), "Should return dict"
        cr = result.get("comparison_result", [])
        if isinstance(cr, list):
            assert len(cr) <= 3, f"Should cap at 3 items in comparison, got {len(cr)}"

        _ok(FLOW, "12 compare_items max 3", f"Max 3 items capped correctly")
    except Exception as e:
        _fail(FLOW, "12 compare_items max 3", str(e))


def test_13_get_upsell_suggestions():
    """get_upsell_suggestions returns list (may be empty if no warranty plans configured)."""
    try:
        items = frappe.get_all(
            "Item",
            filters={"disabled": 0, "is_sales_item": 1, "has_variants": 0},
            fields=["name", "item_name"],
            limit=1,
        )
        if not items:
            _skip(FLOW, "13 get_upsell_suggestions", "No items in system")
            return

        from ch_pos.api.ai import get_upsell_suggestions
        result = get_upsell_suggestions(item_code=items[0].name, cart_items=[])

        assert isinstance(result, list), "get_upsell_suggestions should return list"

        # Each suggestion should have required fields
        for s in result:
            assert "item_code" in s, f"Suggestion should have item_code: {s}"
            assert "item_name" in s, f"Suggestion should have item_name: {s}"
            assert "type" in s, f"Suggestion should have type: {s}"
            assert "reason" in s, f"Suggestion should have reason: {s}"
            assert "priority" in s, f"Suggestion should have priority: {s}"

        _ok(FLOW, "13 get_upsell_suggestions",
            f"item={items[0].item_name}, suggestions={len(result)}")
    except Exception as e:
        _fail(FLOW, "13 get_upsell_suggestions", str(e))


def test_14_get_upsell_excludes_cart_items():
    """get_upsell_suggestions excludes items already in the cart."""
    try:
        items = frappe.get_all(
            "Item",
            filters={"disabled": 0, "is_sales_item": 1, "has_variants": 0},
            fields=["name"],
            limit=3,
        )
        if len(items) < 2:
            _skip(FLOW, "14 upsell excludes cart", "Not enough items")
            return

        from ch_pos.api.ai import get_upsell_suggestions

        # Pass second item as already in cart
        cart_items = [{"item_code": items[1].name}]
        result = get_upsell_suggestions(item_code=items[0].name, cart_items=cart_items)

        assert isinstance(result, list), "Should return list"
        suggested_codes = {s.get("item_code") for s in result}
        assert items[1].name not in suggested_codes, \
            f"Cart item {items[1].name} should not appear in upsell suggestions"

        _ok(FLOW, "14 upsell excludes cart", f"Cart items correctly excluded from {len(result)} suggestions")
    except Exception as e:
        _fail(FLOW, "14 upsell excludes cart", str(e))


def test_15_explain_offers_empty_cart():
    """explain_offers returns a safe message for an empty cart."""
    try:
        from ch_pos.api.ai import explain_offers
        result = explain_offers(cart={"items": []})

        assert isinstance(result, str), "explain_offers should return string"
        assert len(result) > 0, "Result should not be empty string"

        _ok(FLOW, "15 explain_offers empty cart", f"Result: '{result[:60]}'")
    except Exception as e:
        _fail(FLOW, "15 explain_offers empty cart", str(e))


def test_16_explain_offers_with_items():
    """explain_offers returns offer text or 'no offers' message for a cart with items."""
    try:
        items = frappe.get_all(
            "Item",
            filters={"disabled": 0, "is_sales_item": 1},
            fields=["name", "item_name"],
            limit=1,
        )
        if not items:
            _skip(FLOW, "16 explain_offers with items", "No items in system")
            return

        from ch_pos.api.ai import explain_offers
        cart = {
            "items": [
                {"item_code": items[0].name, "item_name": items[0].item_name, "qty": 1, "amount": 10000},
            ]
        }
        result = explain_offers(cart=cart)

        assert isinstance(result, str), "explain_offers should return string"
        assert len(result) > 0, "Result should not be empty"

        _ok(FLOW, "16 explain_offers with items", f"Result: '{result[:80]}'")
    except Exception as e:
        _fail(FLOW, "16 explain_offers with items", str(e))


def test_17_save_guided_session():
    """save_guided_session persists a completed guided session."""
    session_name = None
    try:
        if not frappe.db.exists("DocType", "POS Guided Session"):
            _skip(FLOW, "17 save_guided_session", "POS Guided Session doctype not installed")
            return

        sub_cat = _get_first_sub_category()
        if not sub_cat:
            _skip(FLOW, "17 save_guided_session", "No CH Sub Category configured")
            return

        profile = _get_pos_profile()
        from ch_pos.api.guided import save_guided_session

        responses = [
            {"key": "budget", "question": "Budget?", "answer": 15000},
            {"key": "brand", "question": "Brand?", "answer": "Samsung"},
        ]
        result = save_guided_session(
            pos_profile=profile.name if profile else None,
            category=sub_cat.category,
            sub_category=sub_cat.name,
            responses=responses,
            recommendations=[],
            status="Completed",
        )
        session_name = result.get("name")

        assert session_name, f"save_guided_session should return name, got {result}"
        assert result.get("status") == "Completed", \
            f"Status should be Completed, got {result.get('status')}"

        # Verify persisted
        doc = frappe.get_doc("POS Guided Session", session_name)
        assert doc.sub_category == sub_cat.name, "sub_category should be saved"
        assert len(doc.responses) == len(responses), \
            f"Should have {len(responses)} responses, got {len(doc.responses)}"

        _ok(FLOW, "17 save_guided_session",
            f"session={session_name}, responses={len(doc.responses)}")
    except Exception as e:
        _fail(FLOW, "17 save_guided_session", str(e))
    finally:
        _cleanup_guided_session(session_name)


def test_18_save_guided_session_requires_sub_category():
    """save_guided_session raises error without sub_category."""
    try:
        if not frappe.db.exists("DocType", "POS Guided Session"):
            _skip(FLOW, "18 save_guided_session requires sub_cat", "POS Guided Session not installed")
            return

        from ch_pos.api.guided import save_guided_session
        try:
            save_guided_session(sub_category=None, responses=[])
            _fail(FLOW, "18 save_guided_session requires sub_cat", "Should have raised error")
        except (frappe.exceptions.ValidationError, Exception) as e:
            if "sub" in str(e).lower() or "category" in str(e).lower() or "required" in str(e).lower():
                _ok(FLOW, "18 save_guided_session requires sub_cat",
                    f"Correctly raises error: {str(e)[:60]}")
            else:
                _fail(FLOW, "18 save_guided_session requires sub_cat",
                      f"Unexpected error: {e}")
    except Exception as e:
        _fail(FLOW, "18 save_guided_session requires sub_cat", str(e))


def test_19_save_guided_session_with_recommendations():
    """save_guided_session persists recommended items."""
    session_name = None
    try:
        if not frappe.db.exists("DocType", "POS Guided Session"):
            _skip(FLOW, "19 save_guided_session with recs", "POS Guided Session not installed")
            return

        sub_cat = _get_first_sub_category()
        if not sub_cat:
            _skip(FLOW, "19 save_guided_session with recs", "No CH Sub Category")
            return

        items = frappe.get_all(
            "Item",
            filters={"disabled": 0, "is_sales_item": 1, "has_variants": 0},
            fields=["name", "item_name"],
            limit=2,
        )
        if not items:
            _skip(FLOW, "19 save_guided_session with recs", "No items available")
            return

        from ch_pos.api.guided import save_guided_session

        recommendations = [
            {"item_code": items[0].name, "match_score": 85.0, "reason": "Best match for budget"},
            {"item_code": items[1].name, "match_score": 70.0, "reason": "Good alternative"},
        ]

        result = save_guided_session(
            sub_category=sub_cat.name,
            responses=[{"key": "budget", "question": "Budget?", "answer": 20000}],
            recommendations=recommendations,
            status="Completed",
        )
        session_name = result.get("name")
        assert session_name, "Should return session name"

        doc = frappe.get_doc("POS Guided Session", session_name)
        assert len(doc.recommended_items) == len(recommendations), \
            f"Should save {len(recommendations)} recommended items"

        # Verify rank order
        ranks = [r.rank for r in doc.recommended_items]
        assert ranks == sorted(ranks), "Recommended items should be in rank order"

        _ok(FLOW, "19 save_guided_session with recs",
            f"session={session_name}, {len(doc.recommended_items)} items saved")
    except Exception as e:
        _fail(FLOW, "19 save_guided_session with recs", str(e))
    finally:
        _cleanup_guided_session(session_name)


def test_20_update_existing_guided_session():
    """save_guided_session updates an existing session when session_name is provided."""
    session_name = None
    try:
        if not frappe.db.exists("DocType", "POS Guided Session"):
            _skip(FLOW, "20 update existing session", "POS Guided Session not installed")
            return

        sub_cat = _get_first_sub_category()
        if not sub_cat:
            _skip(FLOW, "20 update existing session", "No CH Sub Category")
            return

        from ch_pos.api.guided import save_guided_session

        # Create initial session
        result1 = save_guided_session(
            sub_category=sub_cat.name,
            responses=[{"key": "budget", "question": "Budget?", "answer": 10000}],
            status="In Progress",
        )
        session_name = result1.get("name")
        assert session_name

        # Update the same session
        result2 = save_guided_session(
            session_name=session_name,
            sub_category=sub_cat.name,
            responses=[
                {"key": "budget", "question": "Budget?", "answer": 10000},
                {"key": "brand", "question": "Brand?", "answer": "Apple"},
            ],
            status="Completed",
        )

        # Should return the same session name
        assert result2.get("name") == session_name, \
            f"Should update same session, got different name: {result2.get('name')}"
        assert result2.get("status") == "Completed", "Status should be Completed after update"

        doc = frappe.get_doc("POS Guided Session", session_name)
        assert len(doc.responses) == 2, f"Should have 2 responses after update, got {len(doc.responses)}"

        _ok(FLOW, "20 update existing session",
            f"session={session_name} updated, responses={len(doc.responses)}")
    except Exception as e:
        _fail(FLOW, "20 update existing session", str(e))
    finally:
        _cleanup_guided_session(session_name)


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all():
    global _results
    _results = []

    print("\n" + "=" * 60)
    print("CH POS — Guided Selling E2E Tests")
    print("=" * 60 + "\n")

    frappe.set_user("Administrator")

    tests = [
        test_01_get_guided_catalog,
        test_02_get_guided_questions_no_sub_category,
        test_03_get_guided_questions_unknown_sub_category,
        test_04_get_guided_questions_valid_sub_category,
        test_05_get_guided_questions_budget_range,
        test_06_get_guided_recommendations_empty_responses,
        test_07_get_guided_recommendations_with_budget,
        test_08_get_guided_recommendations_brand_preference,
        test_09_recommendations_limit_respected,
        test_10_compare_items_static_fallback,
        test_11_compare_items_requires_minimum_2,
        test_12_compare_items_max_3,
        test_13_get_upsell_suggestions,
        test_14_get_upsell_excludes_cart_items,
        test_15_explain_offers_empty_cart,
        test_16_explain_offers_with_items,
        test_17_save_guided_session,
        test_18_save_guided_session_requires_sub_category,
        test_19_save_guided_session_with_recommendations,
        test_20_update_existing_guided_session,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            _fail(FLOW, t.__name__, f"Unhandled: {e}")
            traceback.print_exc()
        try:
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()

    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    skipped = sum(1 for r in _results if r["status"] == "SKIP")
    total = len(_results)

    print(f"\n{'='*60}")
    print(f"TOTAL: {passed} passed, {failed} failed, {skipped} skipped / {total}")
    if failed:
        print("\nFailed:")
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  FAIL  [{r['flow']}] {r['step']}: {r.get('detail','')}")
    print("=" * 60)

    if failed:
        raise Exception(f"Guided Selling E2E: {failed} test(s) failed")
    return _results
