/**
 * CH POS — Toast Notifications
 *
 * Lightweight, POS-specific toast messages that stack in the bottom-right
 * of the POS viewport without interfering with Frappe's alert system.
 */

let _container = null;

function _ensure_container() {
	if (_container && _container.length && $.contains(document, _container[0])) return;
	_container = $(`<div class="ch-pos-toast-container"></div>`);
	$("body").append(_container);
}

function _show(message, type, duration) {
	_ensure_container();
	const icon = type === "success" ? "fa-check-circle"
		: type === "error" ? "fa-exclamation-circle"
		: type === "warning" ? "fa-exclamation-triangle"
		: "fa-info-circle";
	const $toast = $(`
		<div class="ch-pos-toast ch-pos-toast-${type}">
			<i class="fa ${icon}"></i>
			<span class="ch-pos-toast-msg">${frappe.utils.escape_html(message)}</span>
		</div>
	`);
	_container.append($toast);
	// Trigger entrance animation
	requestAnimationFrame(() => $toast.addClass("ch-pos-toast-visible"));
	setTimeout(() => {
		$toast.removeClass("ch-pos-toast-visible");
		setTimeout(() => $toast.remove(), 300);
	}, duration);
}

export function pos_success(message, duration = 2500) {
	_show(message, "success", duration);
}

export function pos_error(message, duration = 4000) {
	_show(message, "error", duration);
}

export function pos_warning(message, duration = 3000) {
	_show(message, "warning", duration);
}

export function pos_info(message, duration = 2500) {
	_show(message, "info", duration);
}
