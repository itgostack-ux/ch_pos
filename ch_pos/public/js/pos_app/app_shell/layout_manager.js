/**
 * CH POS — Layout Manager
 *
 * Orchestrates the app shell: sidebar, content panel, cart panel.
 * Creates the DOM structure, initializes child components,
 * and manages mode switching (show/hide panels).
 */
import { PosState, EventBus } from "../state.js";
import { Sidebar } from "./sidebar.js";
import { SessionBar } from "./session_bar.js";
import { NetworkStatus } from "./network_status.js";

export class LayoutManager {
	/**
	 * @param {HTMLElement} wrapper - The Frappe page wrapper
	 */
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.page = wrapper.page;
		this.sidebar = null;
		this.session_bar = null;
		this.network_status = null;

		// DOM references (set after render)
		this.$container = null;
		this.$content_panel = null;
		this.$cart_panel = null;
		this.$offline_bar = null;
	}

	/** Initialize the full-screen app shell */
	init() {
		this._apply_fullscreen();
		this._render_shell();
		this._init_components();
		this._setup_company_guard();
		this._bind_mode_switch();
		this._bind_keyboard_shortcuts();
	}

	/** Apply full-screen CSS classes and hide Frappe chrome */
	_apply_fullscreen() {
		this.page.clear_actions();
		this.wrapper.find(".page-content").addClass("ch-pos-page");
		this.wrapper.find(".page-head").hide();
		$("header.navbar").hide();
		$("body").addClass("ch-pos-fullscreen");
	}

	/** Build the 3-column DOM structure */
	_render_shell() {
		const content = this.wrapper.find(".layout-main-section");
		content.empty().append(`
			<div class="ch-pos-session-bar"></div>
			<div class="ch-pos-offline-bar"></div>
			<div class="ch-pos-main-area">
				<div class="ch-pos-no-company-overlay">
					<div class="ch-pos-no-company-box" style="cursor:pointer">
						<i class="fa fa-building-o"></i>
						<h3>${__("No Company Selected")}</h3>
						<p>${__("Click here to select a store and open a session.")}</p>
						<button class="btn btn-primary btn-sm ch-btn-retry-session" style="margin-top:12px">
							<i class="fa fa-refresh"></i> ${__("Select Store")}
						</button>
					</div>
				</div>
				<div class="ch-pos-container">
					<div class="ch-pos-sidebar"></div>
					<div class="ch-pos-content-panel"></div>
					<div class="ch-pos-cart-panel"></div>
				</div>
			</div>
		`);

		// Cache DOM references
		this.$session_bar = content.find(".ch-pos-session-bar");
		this.$offline_bar = content.find(".ch-pos-offline-bar");
		this.$main_area = content.find(".ch-pos-main-area");
		this.$no_company_overlay = content.find(".ch-pos-no-company-overlay");
		this.$container = content.find(".ch-pos-container");
		this.$sidebar = content.find(".ch-pos-sidebar");
		this.$content_panel = content.find(".ch-pos-content-panel");
		this.$cart_panel = content.find(".ch-pos-cart-panel");
	}

	/** Show/hide blocking overlay when no company is selected */
	_setup_company_guard() {
		const update = () => {
			const has_company = PosState.active_company || PosState.company;
			this.$main_area.toggleClass("ch-pos-no-company", !has_company);
		};
		// Initial check
		update();
		// React to company changes
		EventBus.on("profile:loaded", update);
		EventBus.on("company:switched", update);
		EventBus.on("session:loaded", update);

		// Click overlay to restart the session opening flow
		this.$no_company_overlay.on("click", ".ch-btn-retry-session, .ch-pos-no-company-box", () => {
			EventBus.emit("session:restart_flow");
		});
	}

	/** Initialize child components */
	_init_components() {
		// Session bar
		this.session_bar = new SessionBar(this.$session_bar);

		// Sidebar
		this.sidebar = new Sidebar(this.$sidebar);

		// Network status bar
		this.network_status = new NetworkStatus(this.$offline_bar);
		this.network_status.render();
	}

	/** Handle mode switching — toggle content & cart visibility */
	_bind_mode_switch() {
		EventBus.on("mode:switch", (mode) => {
			this._switch_to(mode);
		});
	}

	/**
	 * Switch to a mode: update content panel and cart visibility.
	 * @param {string} mode - Mode key (sell, buyback, repair, etc.)
	 */
	_switch_to(mode) {
		// Rescue the Billed By control before the panel is cleared. On Service
		// Intake it lives INSIDE the form; emptying the panel would destroy it,
		// and the next sale would have no way to name who is billing.
		const $inline = this.$content_panel.find(".ch-pos-executive-bar.ch-pos-exec-inline");
		if ($inline.length) {
			this.$cart_panel.prepend($inline.removeClass("ch-pos-exec-inline"));
		}

		// Remove all delegated jQuery handlers from previous module, then clear DOM
		this.$content_panel.off();
		this.$content_panel.empty();

		// Show/hide cart based on mode type
		if (Sidebar.NON_TRANSACTIONAL_MODES.includes(mode)) {
			this.$cart_panel.hide();
		} else {
			this.$cart_panel.show();
		}

		// Service Intake takes in a device; it does not sell anything. A cart
		// beside it is an invitation to bill against a repair that has no price
		// yet, and it was showing the previous mode's basket next to a ticket
		// for a different customer entirely.
		//
		// Hiding only the cart's contents left a third of the screen holding one
		// dropdown while the intake form -- eighteen fields over three sections
		// -- was squeezed into what remained. The panel now closes entirely, the
		// form runs the full width like the Front Desk, and "Billed By" moves
		// into the form, where the person taking the device in can see it.
		const intake = mode === "repair";
		this.$cart_panel.toggleClass("ch-pos-cart-intake-only", intake);
		if (intake) this.$cart_panel.hide();
		this.$container.toggleClass("ch-pos-full-width", intake);

		// Emit for module workspaces to render their content
		EventBus.emit("workspace:render", {
			mode: mode,
			panel: this.$content_panel,
			cart_panel: this.$cart_panel,
		});

		// Move the Billed By control into the intake form -- moved, not cloned,
		// because it carries its own bindings and two copies would drift apart.
		// After the emit: the workspace replaces the panel's HTML when it draws.
		if (intake) {
			const $exec = this.$cart_panel.find(".ch-pos-executive-bar");
			const $host = this.$content_panel.find(".ch-pos-mode-panel").first();
			if ($exec.length && $host.length) {
				$host.prepend($exec.addClass("ch-pos-exec-inline"));
			}
		}
	}

	/** Get the content panel (for modules to render into) */
	get content_panel() {
		return this.$content_panel;
	}

	/** Get the cart panel */
	get cart_panel() {
		return this.$cart_panel;
	}

	/** Bind global keyboard shortcuts for POS workflow */
	_bind_keyboard_shortcuts() {
		$(document).on("keydown.ch_pos_shortcuts", (e) => {
			// Block shortcuts when no company is selected
			if (!(PosState.active_company || PosState.company)) return;

			// Only handle shortcuts outside of dialog overlays
			if ($(".modal.show").length || $(".ch-pay-overlay.ch-pay-visible").length) return;

			const tag = (e.target.tagName || "").toLowerCase();
			const in_input = tag === "input" || tag === "textarea" || tag === "select";

			switch (e.key) {
				case "F2":
					e.preventDefault();
					EventBus.emit("search:focus");
					break;
				case "F5":
					e.preventDefault();
					EventBus.emit("cart:hold");
					break;
				case "F8":
					e.preventDefault();
					EventBus.emit("cart:pay");
					break;
				case "F9":
					e.preventDefault();
					EventBus.emit("held_bills:open");
					break;
				case "Escape":
					if (!in_input) {
						e.preventDefault();
						EventBus.emit("cart:cancel");
					}
					break;
			}
		});
	}

	/** Teardown — restore Frappe UI */
	destroy() {
		$("body").removeClass("ch-pos-fullscreen");
		$("header.navbar").show();
		$(".body-sidebar-container, .body-sidebar").show();
		$(document).off("keydown.ch_pos_shortcuts");
		EventBus.clear();
	}
}
