frappe.provide("ch_pos");

// Register Service Worker for offline resilience.
// The SW is served from /pos-sw.js (root scope) so it can cache /app/ch-pos-app.
// Registration is idempotent — repeated page loads are safe.
(function _register_pos_sw() {
	if (!("serviceWorker" in navigator)) return;
	navigator.serviceWorker
		.register("/pos-sw.js", { scope: "/app/ch-pos-app" })
		.then((reg) => {
			// Listen for background-sync trigger from SW
			navigator.serviceWorker.addEventListener("message", (event) => {
				if (event.data && event.data.type === "sync:bg_sync_triggered") {
					// Notify the running POS app if it is loaded
					if (window.cur_pos && window.cur_pos.sync_service) {
						window.cur_pos.sync_service.sync_pending();
					}
				}
			});
			// Register background sync tag so the SW flushes queue even after page close
			if (reg.sync) {
				reg.sync.register("pos-invoice-sync").catch(() => {});
			}
		})
		.catch((err) => {
			// Non-fatal — POS still works online without SW
			console.warn("[CH POS] Service Worker registration failed:", err);
		});
})();

frappe.pages["ch-pos-app"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: __("CH POS"),
		single_column: true,
	});

	frappe.require("ch_pos.bundle.js", function () {
		wrapper.pos = new ch_pos.PosApp(wrapper);
		window.cur_pos = wrapper.pos;
	});
};

frappe.pages["ch-pos-app"].on_page_show = function (wrapper) {
	// Hide Frappe desk sidebar — POS has its own navigation
	$("body").addClass("ch-pos-fullscreen");
};

frappe.pages["ch-pos-app"].on_page_hide = function (wrapper) {
	// Restore Frappe desk sidebar when leaving POS
	$("body").removeClass("ch-pos-fullscreen");
};

frappe.pages["ch-pos-app"].refresh = function (wrapper) {
	// noop — handled inside PosApp
};
