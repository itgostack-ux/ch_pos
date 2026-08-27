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
	// Restore Frappe desk sidebar and navbar when leaving POS.
	// layout_manager._apply_fullscreen() calls $("header.navbar").hide() which
	// adds an inline style="display:none". Removing the CSS class alone is not
	// enough — we must explicitly call .show() to clear that inline style.
	$("body").removeClass("ch-pos-fullscreen");
	$("header.navbar").show();
	$(".body-sidebar-container, .body-sidebar").show();

	// Older POS builds wrote these values inline on shared Desk containers.
	// Clear any leaked values so a Form opened from POS gets the normal Desk
	// sizing and the main section remains the sole vertical scroll owner.
	$(".main-section, .page-container").css({
		margin: "",
		padding: "",
		"max-width": "",
	});
};

frappe.pages["ch-pos-app"].refresh = function (wrapper) {
	// Frappe calls this on every route change WITHIN this page (including
	// browser Back/Forward, since those are just hash changes to Frappe's
	// router) — the mode segment (/app/ch-pos-app/<mode>) is what lets Back
	// step through recently-visited POS menus instead of leaving the app
	// entirely. See sidebar.js, which pushes a route on every mode switch.
	const pos = wrapper.pos;
	if (!pos) return; // still loading (frappe.require callback hasn't run yet)
	const route_mode = frappe.get_route()[1];
	if (route_mode && route_mode !== pos.state.active_mode) {
		pos.event_bus.emit("mode:route_change", route_mode);
	}
};
