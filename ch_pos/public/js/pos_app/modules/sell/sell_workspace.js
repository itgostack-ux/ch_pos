/**
 * CH POS — Sell Workspace
 *
 * Default sell mode: toolbar + scrollable product grid + pager.
 * Card-first display, retail-first layout.
 */
import { PosState, EventBus } from "../../state.js";
import { ContextualToolbar } from "../../app_shell/contextual_toolbar.js";
import { ProductGrid } from "./product_grid.js";

export class SellWorkspace {
	constructor() {
		this.toolbar = null;
		this.grid = null;
		this._bind_events();
	}

	_bind_events() {
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "sell") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		// Clean up previous sub-component EventBus listeners
		if (this.grid) this.grid.destroy();

		// Toolbar
		this.toolbar = new ContextualToolbar(panel);
		this.toolbar.render_sell_toolbar();

		// Scrollable items area + pager
		panel.append(`
			<div class="ch-pos-items-area">
				<div class="ch-pos-items-grid list-view"></div>
			</div>
			<div class="ch-pos-items-pager"></div>
		`);

		this.grid = new ProductGrid(panel);
		EventBus.emit("items:reload");
	}
}
