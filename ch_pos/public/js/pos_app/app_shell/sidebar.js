/**
 * CH POS — Premium Sidebar
 *
 * Retail-grade mode switcher with icon pills, grouped sections,
 * operator/store identity, and network status.
 *
 * Access control: modules are shown/hidden based on which companies
 * the logged-in executive has access to at this store.
 * - GoGizmo (retail): Sell, Returns, Buyback, Inventory, Sales Tools
 * - GoFix (services): Sell (accessories), Returns, Buyback, Repair, Service
 * - Shared (always): Lookup, Insights
 */
import { PosState, EventBus } from "../state.js";
import { validate_india_phone } from "../shared/helpers.js";

/**
 * Company-to-mode mapping.
 * Modes not listed here are always visible (shared).
 */
const COMPANY_MODE_MAP = {
	// Retail company modes (GoGizmo or any retail company)
	retail: ["sell", "returns", "buyback", "material_request", "stock_transfer", "model_compare", "claims", "exceptions"],
	// Service company modes (GoFix or any service company)
	service: ["sell", "returns", "buyback", "repair", "queue", "service", "claims", "exceptions"],
};

/** Heuristic: does this company name indicate a service company? */
function _is_service_company(company) {
	if (!company) return false;
	const lc = company.toLowerCase();
	return lc.includes("gofix") || lc.includes("service");
}

const MODE_SECTIONS = [
	{
		label: __("Sales"),
		modes: [
			{ key: "sell",    icon: "fa-shopping-bag", label: __("Sell") },
			{ key: "returns", icon: "fa-undo",         label: __("Returns") },
		],
	},
	{
		label: __("Services"),
		modes: [
			{ key: "buyback", icon: "fa-exchange",  label: __("Buyback") },
			{ key: "repair",  icon: "fa-wrench",    label: __("Repair") },
			{ key: "queue",   icon: "fa-ticket",    label: __("Queue") },
			{ key: "service", icon: "fa-cogs",      label: __("Service") },
			{ key: "claims",  icon: "fa-shield",    label: __("Claims") },
			{ key: "exceptions", icon: "fa-exclamation-triangle", label: __("Exceptions") },
		],
	},
	{
		label: __("Inventory"),
		modes: [
			{ key: "material_request", icon: "fa-clipboard",    label: __("Request Stock") },
			{ key: "stock_transfer",   icon: "fa-truck",        label: __("Transfers") },
		],
	},
	{
		label: __("Sales Tools"),
		modes: [
			{ key: "model_compare", icon: "fa-columns", label: __("Compare") },
		],
	},
	{
		label: __("Lookup"),
		modes: [
			{ key: "imei",        icon: "fa-barcode",     label: __("IMEI") },
			{ key: "customer360", icon: "fa-user-circle", label: __("Customers") },
		],
	},
	{
		label: __("Insights"),
		modes: [
			{ key: "reports", icon: "fa-bar-chart", label: __("Dashboard") },
		],
	},
];

export class Sidebar {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.collapsed = localStorage.getItem("ch_pos_sidebar_collapsed") === "1";
		this._allowed_modes = null; // computed after profile loads
		this.render();
		this.bind();
		if (this.collapsed) this._apply_collapsed(true);
	}

	/** Compute which modes the current user can access based on active company */
	_compute_allowed_modes() {
		const access = PosState.executive_access;
		if (!access || !access.companies || !access.companies.length) {
			// No executive records — show everything (admin / fallback)
			this._allowed_modes = null;
			return;
		}

		const active = PosState.active_company;
		const allowed = new Set();

		if (active) {
			// Show modules based on the active company type
			if (_is_service_company(active)) {
				COMPANY_MODE_MAP.service.forEach((m) => allowed.add(m));
			} else {
				COMPANY_MODE_MAP.retail.forEach((m) => allowed.add(m));
			}
		} else {
			// No active company yet — show all accessible
			for (const cr of access.companies) {
				if (_is_service_company(cr.company)) {
					COMPANY_MODE_MAP.service.forEach((m) => allowed.add(m));
				} else {
					COMPANY_MODE_MAP.retail.forEach((m) => allowed.add(m));
				}
			}
		}

		this._allowed_modes = allowed;
	}

	_is_mode_allowed(modeKey) {
		if (!this._allowed_modes) return true; // no restriction
		// If the mode isn't in any company map, it's shared (always visible)
		const allMapped = [...COMPANY_MODE_MAP.retail, ...COMPANY_MODE_MAP.service];
		if (!allMapped.includes(modeKey)) return true;
		return this._allowed_modes.has(modeKey);
	}

	render() {
		this._compute_allowed_modes();

		let html = `
			<button class="ch-pos-sidebar-toggle" title="${__("Toggle sidebar")}">
				<i class="fa fa-chevron-left"></i>
			</button>`;

		for (const section of MODE_SECTIONS) {
			// Filter modes in this section by access
			const visible_modes = section.modes.filter((m) => this._is_mode_allowed(m.key));
			if (!visible_modes.length) continue; // hide entire section

			html += `<div class="ch-pos-sidebar-section">${section.label}</div>`;
			for (const mode of visible_modes) {
				const active = mode.key === PosState.active_mode ? " active" : "";
				html += `
					<button class="ch-pos-sidebar-item${active}"
						data-mode="${mode.key}"
						title="${mode.label}">
						<span class="sidebar-icon"><i class="fa ${mode.icon}"></i></span>
						<span class="sidebar-label">${mode.label}</span>
					</button>`;
			}
		}

		// Bottom: Store identity + operator + executive badge + network
		const exec = PosState.executive_access?.own_executive;
		const exec_name = exec ? exec.executive_name : (frappe.session.user_fullname || frappe.session.user);
		const initials = (exec_name || "U").split(" ").map(w => w[0]).join("").substring(0, 2).toUpperCase();
		const store = PosState.warehouse || PosState.pos_profile || "";
		const role_badge = exec ? `<span class="sidebar-role-badge sidebar-role-${(exec.role || "").toLowerCase().replace(/\s+/g, "-")}">${frappe.utils.escape_html(exec.role)}</span>` : "";
		const company_badge = PosState.active_company
			? `<span class="sidebar-company-badge">${frappe.utils.escape_html(PosState.active_company)}</span>`
			: "";

		html += `
			<div class="ch-pos-sidebar-bottom">
				<div class="ch-pos-walkin-btn-wrap">
					<button class="ch-pos-walkin-btn" title="${__("Log Walk-in")}">
						<i class="fa fa-sign-in"></i>
						<span class="sidebar-label">${__("Log Walk-in")}</span>
					</button>
				</div>
				<div class="store-info">
					<div class="store-avatar">${frappe.utils.escape_html(initials)}</div>
					<div class="store-detail">
						<span class="store-name">${frappe.utils.escape_html(store)}</span>
						<span class="store-operator">${frappe.utils.escape_html(exec_name)} ${role_badge}</span>
						${company_badge}
					</div>
				</div>
				<div class="ch-pos-sidebar-status">
					<span class="ch-pos-online-dot${navigator.onLine ? "" : " offline"}"></span>
					<span class="ch-pos-online-label">${navigator.onLine ? __("Online") : __("Offline")}</span>
				</div>
			</div>`;

		this.wrapper.html(html);

		// Hook point for session controls (rendered by SessionControls externally)
		EventBus.emit("sidebar:rendered", this.wrapper);
	}

	/** Re-render store info after profile loads */
	update_store_info() {
		// Full re-render to pick up executive access + module filtering
		this.render();
		if (this.collapsed) this._apply_collapsed(true);
	}

	bind() {
		const sidebar = this.wrapper;

		// Walk-in Log button — creates a token record for direct counter walk-ins
		sidebar.on("click", ".ch-pos-walkin-btn", () => {
			const d = new frappe.ui.Dialog({
				title: __("Log Walk-in"),
				fields: [
					{
						label: __("Purpose"),
						fieldname: "visit_purpose",
						fieldtype: "Select",
						options: "Enquiry\nRepair\nSales\nBuyback\nOther",
						default: "Enquiry",
						reqd: 1,
					},
					{
						label: __("Customer Name"),
						fieldname: "customer_name",
						fieldtype: "Data",
						placeholder: __("Optional"),
					},
					{
						label: __("Phone"),
						fieldname: "customer_phone",
						fieldtype: "Data",
						placeholder: __("Optional"),
					},
					{
						label: __("Remarks"),
						fieldname: "remarks",
						fieldtype: "Small Text",
						placeholder: __("Optional — what did the customer need?"),
					},
				],
				primary_action_label: __("Log Walk-in"),
				primary_action: (values) => {
					if (values.customer_phone && !validate_india_phone(values.customer_phone)) {
						frappe.show_alert({ message: __("Enter a valid Indian phone number (10 digits starting with 6-9)"), indicator: "orange" });
						return;
					}
					d.hide();
					frappe.call({
						method: "ch_pos.api.token_api.log_counter_walkin",
						args: {
							pos_profile: PosState.pos_profile,
							visit_purpose: values.visit_purpose,
							customer_name: values.customer_name || "",
							customer_phone: values.customer_phone || "",
							remarks: values.remarks || "",
						},
						callback: (r) => {
							const res = r.message || {};
							if (res.status === "ok") {
								frappe.show_alert({
									message: __("Walk-in logged: {0} ({1})", [res.token, res.visit_purpose]),
									indicator: "green",
								});
								EventBus.emit("walkin:logged", res);
							} else {
								frappe.show_alert({ message: __("Could not log walk-in"), indicator: "orange" });
							}
						},
					});
				},
			});
			d.show();
		});

		// Collapse toggle
		sidebar.on("click", ".ch-pos-sidebar-toggle", () => {
			this.collapsed = !this.collapsed;
			localStorage.setItem("ch_pos_sidebar_collapsed", this.collapsed ? "1" : "0");
			this._apply_collapsed(this.collapsed);
		});

		// Mode navigation
		sidebar.on("click", ".ch-pos-sidebar-item", function () {
			const mode = $(this).data("mode");
			if (mode === PosState.active_mode) return;
			sidebar.find(".ch-pos-sidebar-item").removeClass("active");
			$(this).addClass("active");
			PosState.active_mode = mode;
			EventBus.emit("mode:switch", mode);
		});

		// Programmatic mode set
		EventBus.on("mode:set", (mode) => {
			sidebar.find(".ch-pos-sidebar-item").removeClass("active");
			sidebar.find(`.ch-pos-sidebar-item[data-mode="${mode}"]`).addClass("active");
			PosState.active_mode = mode;
		});

		// Network updates
		EventBus.on("network:status", (online) => {
			const dot = sidebar.find(".ch-pos-online-dot");
			const label = sidebar.find(".ch-pos-online-label");
			dot.toggleClass("offline", !online);
			label.text(online ? __("Online") : __("Offline"));
		});

		// Profile loaded → update store info
		EventBus.on("profile:loaded", () => this.update_store_info());

		// Company switched → re-render sidebar to show/hide modules
		EventBus.on("company:switched", () => {
			this.update_store_info();
			// If current mode is no longer allowed, switch to first valid mode
			if (!this._is_mode_allowed(PosState.active_mode)) {
				const first_valid = this.wrapper.find(".ch-pos-sidebar-item").first().data("mode");
				if (first_valid) {
					PosState.active_mode = first_valid;
					EventBus.emit("mode:switch", first_valid);
				}
			}
		});
	}

	_apply_collapsed(collapsed) {
		const container = this.wrapper.closest(".ch-pos-container");
		if (collapsed) {
			this.wrapper.addClass("collapsed");
			container.addClass("sidebar-collapsed");
			this.wrapper.find(".ch-pos-sidebar-toggle i").removeClass("fa-chevron-left").addClass("fa-chevron-right");
		} else {
			this.wrapper.removeClass("collapsed");
			container.removeClass("sidebar-collapsed");
			this.wrapper.find(".ch-pos-sidebar-toggle i").removeClass("fa-chevron-right").addClass("fa-chevron-left");
		}
	}

	static get ALL_MODES() {
		return MODE_SECTIONS.flatMap((s) => s.modes.map((m) => m.key));
	}
	static get TRANSACTIONAL_MODES() {
		return ["sell", "returns", "buyback", "repair"];
	}
	static get NON_TRANSACTIONAL_MODES() {
		return ["service", "imei", "customer360", "reports", "material_request", "stock_transfer", "model_compare", "claims", "exceptions", "queue"];
	}
}
