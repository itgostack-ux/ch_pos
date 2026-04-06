frappe.provide("ch_pos");

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
