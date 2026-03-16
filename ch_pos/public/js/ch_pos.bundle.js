/**
 * CH POS — Entry Point
 *
 * Modular POS application for GoGizmo & GoFix stores.
 * This bundle imports all modules and wires them together.
 *
 * Architecture:
 *   state.js          — Central state + EventBus
 *   app_shell/        — Layout, sidebar, network status
 *   shared/           — Cart panel, helpers, reusable components
 *   modules/          — sell, buyback, repair, service, returns, etc.
 *   services/         — API layer (item, cart, customer, sync)
 */
import { PosState, EventBus } from "./pos_app/state.js";
import { LayoutManager } from "./pos_app/app_shell/layout_manager.js";
import { CartPanel } from "./pos_app/shared/cart_panel.js";
import { format_number } from "./pos_app/shared/helpers.js";

// Services
import { ItemService } from "./pos_app/services/item_service.js";
import { CartService } from "./pos_app/services/cart_service.js";
import { SyncService } from "./pos_app/services/sync_service.js";

// Module workspaces
import { SellWorkspace } from "./pos_app/modules/sell/sell_workspace.js";
import { BuybackWorkspace } from "./pos_app/modules/buyback/buyback_workspace.js";
import { RepairWorkspace } from "./pos_app/modules/repair/repair_workspace.js";
import { ServiceWorkspace } from "./pos_app/modules/service/service_workspace.js";
import { ReturnsWorkspace } from "./pos_app/modules/returns/returns_workspace.js";
import { ImeiWorkspace } from "./pos_app/modules/imei/imei_workspace.js";
import { Customer360Workspace } from "./pos_app/modules/customer360/customer360_workspace.js";
import { ReportsWorkspace } from "./pos_app/modules/reports/reports_workspace.js";
import { MaterialRequestWorkspace } from "./pos_app/modules/material_request/material_request_workspace.js";
import { StockTransferWorkspace } from "./pos_app/modules/stock_transfer/stock_transfer_workspace.js";
import { ModelCompareWorkspace } from "./pos_app/modules/model_compare/model_compare_workspace.js";
import { ClaimsWorkspace } from "./pos_app/modules/claims/claims_workspace.js";
import { ExceptionWorkspace } from "./pos_app/modules/exceptions/exception_workspace.js";

// Shared
import { PaymentDialog } from "./pos_app/shared/payment_dialog.js";
import { NetworkStatus } from "./pos_app/app_shell/network_status.js";

// ── Make globally available for Frappe page lifecycle ────
frappe.provide("ch_pos");

ch_pos.PosApp = class PosApp {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.page = wrapper.page;
		this.layout = new LayoutManager(wrapper);
		this.cart_panel = null;
		this.init();
	}

	init() {
		this.layout.init();
		this._init_cart_panel();
		this._init_services();
		this._init_modules();
		this._check_pos_profile();
	}

	_init_cart_panel() {
		this.cart_panel = new CartPanel(this.layout.cart_panel);
	}

	_init_services() {
		this.sync_service = new SyncService();
		this.item_service = new ItemService();
		this.cart_service = new CartService();
		this.payment_dialog = new PaymentDialog();
		this.network_status = new NetworkStatus();
	}

	_init_modules() {
		this.sell_workspace = new SellWorkspace();
		this.buyback_workspace = new BuybackWorkspace();
		this.repair_workspace = new RepairWorkspace();
		this.service_workspace = new ServiceWorkspace();
		this.returns_workspace = new ReturnsWorkspace();
		this.imei_workspace = new ImeiWorkspace();
		this.customer360_workspace = new Customer360Workspace();
		this.reports_workspace = new ReportsWorkspace();
		this.material_request_workspace = new MaterialRequestWorkspace();
		this.stock_transfer_workspace = new StockTransferWorkspace();
		this.model_compare_workspace = new ModelCompareWorkspace();
		this.claims_workspace = new ClaimsWorkspace();
		this.exception_workspace = new ExceptionWorkspace();
	}

	// ── Profile Selection ───────────────────────────────
	_check_pos_profile() {
		frappe.call({
			method: "erpnext.selling.page.point_of_sale.point_of_sale.check_opening_entry",
			args: { user: frappe.session.user },
			callback: (r) => {
				const entries = r.message || [];
				// Always show the profile selector so the user can choose
				// between multiple POS profiles or resume an existing session
				this._show_profile_selector(entries);
			},
		});
	}

	_show_profile_selector(open_entries) {
		open_entries = open_entries || [];

		// Build a hint showing any already-open sessions
		const open_map = {}; // pos_profile → opening entry name
		open_entries.forEach((e) => { open_map[e.pos_profile] = e; });

		const fields = [
			{
				fieldname: "pos_profile",
				fieldtype: "Link",
				label: __("POS Profile"),
				options: "POS Profile",
				reqd: 1,
			},
		];

		if (open_entries.length) {
			const names = open_entries.map((e) => `<b>${e.pos_profile}</b>`).join(", ");
			fields.unshift({
				fieldname: "open_info",
				fieldtype: "HTML",
				options: `<div class="alert alert-info" style="margin-bottom:10px">
					${__('Open sessions')}: ${names}.
					${__('Selecting one of them will resume that session.')}
				</div>`,
			});
			// Pre-fill with the first open profile
			fields[1].default = open_entries[0].pos_profile;
		}

		const dlg = new frappe.ui.Dialog({
			title: __("Select POS Profile"),
			fields,
			primary_action_label: __("Open POS"),
			primary_action: (values) => {
				dlg.hide();
				const selected = values.pos_profile;
				if (open_map[selected]) {
					// Resume existing open session
					this._load_profile(open_map[selected]);
				} else {
					// No open session for this profile — create one
					this._create_opening_entry(selected);
				}
			},
		});
		dlg.show();
	}

	_create_opening_entry(pos_profile) {
		// Fetch payment methods from POS Profile, then create opening with zero balances
		frappe.call({
			method: "frappe.client.get",
			args: { doctype: "POS Profile", name: pos_profile },
			callback: (r) => {
				const profile = r.message;
				const company = profile.company || frappe.defaults.get_default("company");
				const balance_details = (profile.payments || []).map((p) => ({
					mode_of_payment: p.mode_of_payment,
					opening_amount: 0,
				}));

				frappe.call({
					method: "erpnext.selling.page.point_of_sale.point_of_sale.create_opening_voucher",
					args: {
						pos_profile: pos_profile,
						company: company,
						balance_details: JSON.stringify(balance_details),
					},
					callback: (res) => {
						if (res.message) {
							this._load_profile({
								pos_profile: pos_profile,
								company: company,
								name: res.message.name,
							});
						}
					},
				});
			},
		});
	}

	_load_profile(data) {
		PosState.pos_profile = data.pos_profile;
		PosState.company = data.company;

		frappe.call({
			method: "ch_pos.api.pos_api.get_pos_profile_data",
			args: { pos_profile: data.pos_profile },
			callback: (r) => {
				if (r.message) {
					const d = r.message;
					PosState.warehouse = d.warehouse;
					PosState.price_list = d.price_list;
					PosState.payment_modes = d.payment_modes || [];
					PosState.store_caps = d.store_caps || {};
					PosState.pos_ext = d.pos_ext || {};

					// Executive access control
					const access = d.executive_access || null;
					PosState.executive_access = access;
					if (access && access.companies && access.companies.length) {
						// Default to the profile's company, or first accessible
						PosState.active_company = access.companies.find(
							(c) => c.company === d.company
						) ? d.company : access.companies[0].company;

						// Default sales executive to own record
						if (access.own_executive) {
							PosState.sales_executive = access.own_executive.name;
							PosState.sales_executive_name = access.own_executive.executive_name;
						}
					} else {
						PosState.active_company = d.company;
					}

					// Trigger initial module load (default: sell mode)
					EventBus.emit("profile:loaded", PosState);
					EventBus.emit("executive_access:loaded", access);
					EventBus.emit("mode:switch", "sell");

					// Keyboard shortcuts
					this._bind_keyboard_shortcuts();
				}
			},
		});
	}

	_bind_keyboard_shortcuts() {
		$(document).on("keydown.ch_pos", (e) => {
			// Skip when inside an input/textarea/select
			const tag = (e.target.tagName || "").toLowerCase();
			if (tag === "input" || tag === "textarea" || tag === "select") {
				// Allow F-key shortcuts even in inputs
				if (!e.key.startsWith("F")) return;
			}
			// Skip when a Frappe dialog is open (except our own payment dialog)
			if ($(".modal.show").length && !$(e.target).closest(".ch-pos-payment-summary").length) return;

			if (e.key === "F2") {
				e.preventDefault();
				// Focus search bar
				EventBus.emit("search:focus");
			} else if (e.key === "F4") {
				e.preventDefault();
				EventBus.emit("cart:pay");
			} else if (e.key === "F8") {
				e.preventDefault();
				EventBus.emit("cart:hold");
			} else if (e.key === "Escape") {
				// Only if no dialog is open
				if (!$(".modal.show").length) {
					EventBus.emit("search:focus");
				}
			} else if (e.altKey && e.key === "n") {
				e.preventDefault();
				EventBus.emit("customer:new");
			} else if (e.altKey && e.key === "e") {
				e.preventDefault();
				EventBus.emit("exchange:open");
			} else if (e.altKey && e.key === "v") {
				e.preventDefault();
				EventBus.emit("vas:open");
			}
		});
	}

	destroy() {
		$(document).off("keydown.ch_pos");
		EventBus.clear();
		this.layout.destroy();
	}
};
