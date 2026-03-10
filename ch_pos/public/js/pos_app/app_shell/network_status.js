/**
 * CH POS — Network Status Component
 *
 * Displays offline/syncing/error states above the main container.
 * Shows queued transaction count and retry button.
 * Integrates with the SyncService for queue management.
 */
import { PosState, EventBus } from "../state.js";

export class NetworkStatus {
	/**
	 * @param {jQuery} wrapper - The .ch-pos-offline-bar element
	 */
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.bind_network_events();
	}

	render() {
		this.wrapper.html(`
			<span class="ch-pos-offline-dot"></span>
			<i class="fa fa-wifi ch-pos-offline-icon"></i>
			<span class="ch-pos-offline-text"></span>
			<span class="ch-pos-offline-queue" style="display:none"></span>
			<button class="btn btn-xs ch-pos-offline-retry" style="display:none">
				<i class="fa fa-refresh"></i> ${__("Retry Sync")}
			</button>
		`);

		// Bind retry click
		this.wrapper.on("click", ".ch-pos-offline-retry", () => {
			EventBus.emit("sync:retry");
		});

		// Set initial state
		if (!navigator.onLine) {
			this.set_state("offline");
		}
	}

	bind_network_events() {
		window.addEventListener("online", () => {
			PosState.is_online = true;
			EventBus.emit("network:status", true);
			EventBus.emit("sync:start");
		});

		window.addEventListener("offline", () => {
			PosState.is_online = false;
			EventBus.emit("network:status", false);
			this.set_state("offline");
		});

		// Listen for sync service state changes
		EventBus.on("sync:state", (state) => {
			// state = { status: "syncing"|"error"|"done", queue_count: N }
			if (state.status === "syncing") {
				this.set_state("syncing", state.queue_count);
			} else if (state.status === "error") {
				this.set_state("error");
			} else if (state.status === "done") {
				this.hide();
			}
		});
	}

	/**
	 * Set the bar visual state.
	 * @param {"offline"|"syncing"|"error"} state
	 * @param {number} [queue_count]
	 */
	set_state(state, queue_count) {
		const bar = this.wrapper;
		const icon = bar.find(".ch-pos-offline-icon");
		const text = bar.find(".ch-pos-offline-text");
		const queue = bar.find(".ch-pos-offline-queue");
		const retry = bar.find(".ch-pos-offline-retry");

		bar.removeClass("state-offline state-syncing state-error").addClass("visible");
		icon.removeClass("fa-wifi fa-spinner fa-spin fa-exclamation-triangle");
		retry.hide();
		queue.hide();

		switch (state) {
			case "offline":
				bar.addClass("state-offline");
				icon.addClass("fa-wifi");
				text.text(__("You are offline — transactions will be queued"));
				if (queue_count) {
					queue.show().text(__("{0} pending", [queue_count]));
				}
				break;

			case "syncing":
				bar.addClass("state-syncing");
				icon.addClass("fa-spinner fa-spin");
				text.text(
					queue_count
						? __("Syncing {0} pending transaction(s)...", [queue_count])
						: __("Syncing...")
				);
				break;

			case "error":
				bar.addClass("state-error");
				icon.addClass("fa-exclamation-triangle");
				text.text(__("Sync failed — will retry automatically"));
				retry.show();
				break;
		}
	}

	hide() {
		this.wrapper.removeClass("visible state-offline state-syncing state-error");
	}
}
