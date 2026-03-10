/**
 * CH POS — Layout Manager
 *
 * Orchestrates the app shell: sidebar, content panel, cart panel.
 * Creates the DOM structure, initializes child components,
 * and manages mode switching (show/hide panels).
 */
import { PosState, EventBus } from "../state.js";
import { Sidebar } from "./sidebar.js";
import { NetworkStatus } from "./network_status.js";

export class LayoutManager {
	/**
	 * @param {HTMLElement} wrapper - The Frappe page wrapper
	 */
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.page = wrapper.page;
		this.sidebar = null;
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
		this._bind_mode_switch();
	}

	/** Apply full-screen CSS classes and hide Frappe chrome */
	_apply_fullscreen() {
		this.page.clear_actions();
		this.wrapper.find(".page-content").addClass("ch-pos-page");
		this.wrapper.find(".page-head").hide();
		$("header.navbar").hide();
		$("body").addClass("ch-pos-fullscreen");
		// Ensure absolute full-screen by removing any Frappe page padding
		$(".main-section").css({ margin: 0, padding: 0, "max-width": "100%" });
		$(".page-container").css({ margin: 0, padding: 0, "max-width": "100%" });
	}

	/** Build the 3-column DOM structure */
	_render_shell() {
		const content = this.wrapper.find(".layout-main-section");
		content.empty().append(`
			<div class="ch-pos-offline-bar"></div>
			<div class="ch-pos-container">
				<div class="ch-pos-sidebar"></div>
				<div class="ch-pos-content-panel"></div>
				<div class="ch-pos-cart-panel"></div>
			</div>
		`);

		// Cache DOM references
		this.$offline_bar = content.find(".ch-pos-offline-bar");
		this.$container = content.find(".ch-pos-container");
		this.$sidebar = content.find(".ch-pos-sidebar");
		this.$content_panel = content.find(".ch-pos-content-panel");
		this.$cart_panel = content.find(".ch-pos-cart-panel");
	}

	/** Initialize child components */
	_init_components() {
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
		// Remove all delegated jQuery handlers from previous module, then clear DOM
		this.$content_panel.off();
		this.$content_panel.empty();

		// Show/hide cart based on mode type
		if (Sidebar.NON_TRANSACTIONAL_MODES.includes(mode)) {
			this.$cart_panel.hide();
		} else {
			this.$cart_panel.show();
		}

		// Emit for module workspaces to render their content
		EventBus.emit("workspace:render", {
			mode: mode,
			panel: this.$content_panel,
			cart_panel: this.$cart_panel,
		});
	}

	/** Get the content panel (for modules to render into) */
	get content_panel() {
		return this.$content_panel;
	}

	/** Get the cart panel */
	get cart_panel() {
		return this.$cart_panel;
	}

	/** Teardown — restore Frappe UI */
	destroy() {
		$("body").removeClass("ch-pos-fullscreen");
		$("header.navbar").show();
		EventBus.clear();
	}
}
