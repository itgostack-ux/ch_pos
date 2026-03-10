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
