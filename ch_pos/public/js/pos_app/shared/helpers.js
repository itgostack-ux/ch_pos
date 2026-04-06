/**
 * CH POS — Shared Helpers
 *
 * Utility functions used across all POS modules.
 */

/**
 * Format a number in Indian locale (₹ format).
 * @param {number|string} val
 * @returns {string}
 */
export function format_number(val) {
	const num = parseFloat(val) || 0;
	return num.toLocaleString("en-IN", {
		minimumFractionDigits: 0,
		maximumFractionDigits: 2,
	});
}

/**
 * Generate a deterministic pastel color for item placeholders.
 * @param {string} code - Item code to hash
 * @returns {{ bg: string, text: string }}
 */
export function item_placeholder_color(code) {
	const bgs = ["#e8eaf6", "#e0f2f1", "#fce4ec", "#fff3e0", "#f3e5f5", "#e8f5e9", "#fff8e1", "#e3f2fd"];
	const txts = ["#3949ab", "#00695c", "#c62828", "#e65100", "#6a1b9a", "#2e7d32", "#f57f17", "#1565c0"];
	const idx = (code || "").split("").reduce((a, c) => a + c.charCodeAt(0), 0) % bgs.length;
	return { bg: bgs[idx], text: txts[idx] };
}

/**
 * Debounce a function call.
 * @param {Function} fn
 * @param {number} delay - Milliseconds
 * @returns {Function}
 */
export function debounce(fn, delay = 300) {
	let timer;
	return function (...args) {
		clearTimeout(timer);
		timer = setTimeout(() => fn.apply(this, args), delay);
	};
}

/**
 * Render skeleton loading cards into a container.
 * @param {jQuery} container
 * @param {number} count
 * @param {"card"|"row"} type
 */
export function show_skeleton(container, count = 8, type = "card") {
	const items = Array(count)
		.fill(0)
		.map(() => `<div class="ch-pos-skeleton skeleton-${type}"></div>`)
		.join("");
	container.html(items);
}

/**
 * Render an empty state into a container.
 * @param {jQuery} container
 * @param {object} opts
 * @param {string} opts.icon - FontAwesome icon class
 * @param {string} opts.title
 * @param {string} [opts.subtitle]
 * @param {string} [opts.action_label]
 * @param {Function} [opts.action_fn]
 */
export function show_empty_state(container, opts = {}) {
	const action = opts.action_label
		? `<button class="btn btn-sm btn-outline-primary mt-2 ch-pos-empty-action">${opts.action_label}</button>`
		: "";

	container.html(`
		<div class="ch-pos-empty-state">
			<div class="empty-icon"><i class="fa ${opts.icon || "fa-inbox"}"></i></div>
			<div class="empty-title">${opts.title || __("Nothing here")}</div>
			${opts.subtitle ? `<div class="empty-subtitle">${opts.subtitle}</div>` : ""}
			${action}
		</div>
	`);

	if (opts.action_fn) {
		container.find(".ch-pos-empty-action").on("click", opts.action_fn);
	}
}

// Make format_number available globally for templates that use it
window.format_number = format_number;

/**
 * Validate an Indian phone number (mobile or landline).
 *
 * Accepts:
 *   Mobile  : 10-digit numbers starting with 6-9
 *             with optional prefix: +91, 0091, or 0
 *   Landline: STD code (2-4 digits) + subscriber (6-8 digits)
 *             with optional 0/+91 prefix, total 10–11 digits
 *
 * @param {string} val
 * @returns {boolean}
 */
export function validate_india_phone(val) {
	const clean = (val || "").replace(/[\s\-().]/g, "");
	// Strip +91 or 0091 or leading 0
	const stripped = clean
		.replace(/^\+91/, "")
		.replace(/^0091/, "")
		.replace(/^0(?=[6-9])/, "")   // leading 0 only before mobile digits
		.replace(/^0(?=\d{9,10}$)/, ""); // leading 0 for landlines

	// Mobile: exactly 10 digits starting with 6-9
	if (/^[6-9]\d{9}$/.test(stripped)) return true;

	// Landline: STD(2-5 digits) + subscriber(5-8 digits) = 7-11 total, allow full with 0 prefix
	// Common: 011-XXXXXXXX (10 total sans prefix), 0XXXXXXXXXX (11 with leading 0)
	const withZero = clean.replace(/^\+91|^0091/, "");
	if (/^0[1-9]\d{8,9}$/.test(withZero)) return true;   // 11-digit with leading 0
	if (/^[1-9][1-9]\d{6,8}$/.test(stripped)) return true; // 8-10 digits bare landline

	return false;
}

/**
 * Show an inline error and return false, or clear error and return true.
 * @param {jQuery|HTMLElement} input
 * @param {string} val
 * @returns {boolean}
 */
export function assert_india_phone(input, val) {
	const $el = $(input);
	if (!val) { // allow empty (use reqd for mandatory)
		$el.removeClass("ch-phone-invalid");
		return true;
	}
	if (validate_india_phone(val)) {
		$el.removeClass("ch-phone-invalid").attr("title", "");
		return true;
	}
	$el.addClass("ch-phone-invalid").attr("title", __("Enter a valid Indian phone number"));
	frappe.show_alert({ message: __("Enter a valid Indian phone number (mobile or landline)"), indicator: "orange" });
	return false;
}

/**
 * Validate a PAN card number.
 * Format: 5 uppercase letters + 4 digits + 1 uppercase letter (e.g. ABCDE1234F)
 */
export function validate_pan(val) {
	if (!val) return false;
	return /^[A-Z]{5}[0-9]{4}[A-Z]$/.test(val.trim().toUpperCase());
}

/**
 * Validate an Aadhaar number.
 * Must be exactly 12 digits, cannot start with 0 or 1.
 */
export function validate_aadhaar(val) {
	if (!val) return false;
	const clean = val.replace(/[\s\-]/g, "");
	return /^[2-9]\d{11}$/.test(clean);
}

/**
 * Validate ID number based on ID type. Returns true if valid or empty.
 * Shows alert on invalid input.
 */
export function validate_id_number(id_type, id_number) {
	if (!id_number || !id_type) return true;
	const val = id_number.trim();
	if (!val) return true;

	if (id_type === "PAN Card" || id_type === "PAN") {
		if (!validate_pan(val)) {
			frappe.show_alert({
				message: __("Invalid PAN. Format: ABCDE1234F (5 letters + 4 digits + 1 letter)"),
				indicator: "orange",
			});
			return false;
		}
	} else if (id_type === "Aadhar Card" || id_type === "Aadhaar") {
		if (!validate_aadhaar(val)) {
			frappe.show_alert({
				message: __("Invalid Aadhaar. Must be exactly 12 digits, not starting with 0 or 1"),
				indicator: "orange",
			});
			return false;
		}
	}
	return true;
}

/**
 * Extract a readable error message from frappe.xcall rejection.
 * frappe.xcall rejects with r?.message which is often undefined for
 * ValidationError (frappe.throw). We also check frappe.last_response
 * which contains the full server response including _server_messages.
 * @param {*} err - The caught error object
 * @param {string} [fallback] - Fallback message if nothing parsable
 * @returns {string} HTML-safe error message
 */
export function parse_xcall_error(err, fallback) {
	// Try frappe.last_response._server_messages first (most reliable for frappe.throw)
	const lr = frappe.last_response;
	if (lr && lr._server_messages) {
		try {
			const msgs = JSON.parse(lr._server_messages);
			const parts = msgs.map(m => {
				try { return JSON.parse(m).message || m; } catch (_) { return m; }
			}).filter(Boolean);
			if (parts.length) return parts.join("<br>");
		} catch (_) { /* fall through */ }
	}

	if (!err) return fallback || __("An unexpected error occurred");

	// frappe.xcall sometimes passes the message string directly
	if (typeof err === "string") return err;
	if (err.message) return err.message;

	// Check _server_messages on the error object
	if (err._server_messages) {
		try {
			const msgs = JSON.parse(err._server_messages);
			const parts = msgs.map(m => {
				try { return JSON.parse(m).message || m; } catch (_) { return m; }
			}).filter(Boolean);
			if (parts.length) return parts.join("<br>");
		} catch (_) { /* fall through */ }
	}

	return fallback || __("An unexpected error occurred");
}

/**
 * Show a visible error dialog from a .catch() handler.
 * Use this instead of silently swallowing errors.
 * Also exposed as window.ch_pos_show_error for non-module scripts.
 */
export function show_api_error(err, title) {
	const msg = parse_xcall_error(err);
	frappe.msgprint({ title: title || __("Error"), message: msg, indicator: "red" });
}

// Expose globally so all catch blocks can use it without imports
window.ch_pos_show_error = function (err, title) {
	show_api_error(err, title);
};
