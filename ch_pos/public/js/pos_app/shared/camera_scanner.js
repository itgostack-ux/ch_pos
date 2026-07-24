/**
 * CH POS — Camera Barcode/IMEI Scanner (Phase 2)
 *
 * Uses Frappe's locally installed html5-qrcode asset and falls back to the
 * keyboard-wedge input when camera scanning is unavailable.
 * `open_camera_scan(onResult)` opens a fullscreen modal with a live
 * camera preview and invokes `onResult(code)` on the first stable decode.
 *
 * Browser permission policy: requires HTTPS or http://127.0.0.1 to access
 * `getUserMedia`. On unsupported environments we surface a clean toast
 * and let the user fall back to the IMEI text input.
 *
 * The scanner library is loaded from the same ERP origin.
 */
import { EventBus } from "../state.js";

const LOCAL_SCANNER_ASSET = "/assets/frappe/node_modules/html5-qrcode/html5-qrcode.min.js";

let _scanner_loading = null;

function _load_scanner() {
	if (window.Html5Qrcode) return Promise.resolve(window.Html5Qrcode);
	if (_scanner_loading) return _scanner_loading;
	if (typeof frappe.require !== "function") {
		return Promise.reject(new Error("Frappe asset loader is unavailable"));
	}
	_scanner_loading = Promise.resolve(frappe.require(LOCAL_SCANNER_ASSET)).then(() => {
		if (!window.Html5Qrcode) throw new Error("Local scanner library was not exposed");
		return window.Html5Qrcode;
	});
	return _scanner_loading;
}

function _supports_camera() {
	return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

/**
 * Open a fullscreen camera scanner.
 * @param {(code: string) => void} on_result  Called once with the decoded text.
 * @returns {Promise<string|null>}  Resolves with the code, or null if cancelled.
 */
export function open_camera_scan(on_result) {
	return new Promise((resolve) => {
		if (!_supports_camera()) {
			frappe.show_alert({
				message: __("Camera not available on this device. Use the IMEI text box."),
				indicator: "orange",
			});
			resolve(null);
			return;
		}

		const overlay = $(`
			<div class="ch-cam-scan-overlay" style="
				position:fixed;inset:0;background:rgba(0,0,0,0.92);z-index:9999;
				display:flex;flex-direction:column;align-items:center;justify-content:center;">
				<div style="position:absolute;top:14px;right:18px;">
					<button class="btn btn-default ch-cam-close" style="background:#fff">
						<i class="fa fa-times"></i> ${__("Close")}
					</button>
				</div>
				<div style="color:#fff;font-size:15px;margin-bottom:14px;">
					<i class="fa fa-barcode"></i> ${__("Point the camera at a barcode / IMEI")}
				</div>
				<div class="ch-cam-video"
					style="width:min(90vw,720px);max-height:60vh;border-radius:12px;background:#000;overflow:hidden;"></div>
				<div class="ch-cam-status text-muted" style="margin-top:14px;color:#cbd5e1;font-size:13px;">
					${__("Loading scanner…")}
				</div>
			</div>
		`);
		$("body").append(overlay);

		let scanner = null;
		let scanner_start = null;
		let resolved = false;

		const cleanup = () => {
			overlay.hide();
			const remove_overlay = () => {
				try { scanner && scanner.clear(); } catch (error) {}
				overlay.remove();
			};
			return Promise.resolve(scanner_start)
				.catch(() => null)
				.then(() => scanner && scanner.stop())
				.catch(() => null)
				.then(remove_overlay);
		};

		const done = (code) => {
			if (resolved) return;
			resolved = true;
			cleanup().finally(() => {
				if (code && typeof on_result === "function") on_result(code);
				resolve(code || null);
			});
		};

		overlay.on("click", ".ch-cam-close", () => done(null));

		_load_scanner()
			.then((Scanner) => {
				if (resolved) return null;
				const scan_area = overlay.find(".ch-cam-video")[0];
				scan_area.id = `ch-cam-video-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
				scanner = new Scanner(scan_area.id);
				overlay.find(".ch-cam-status").text(__("Starting camera…"));

				scanner_start = scanner
					.start(
						{ facingMode: "environment" },
						{ fps: 10, qrbox: { width: 280, height: 180 } },
						(decoded_text) => {
							const text = (decoded_text || "").trim();
							if (text) {
								EventBus.emit("camera:scan", text);
								done(text);
							}
						},
						() => null,
					)
					.then(() => {
						overlay.find(".ch-cam-status").text(
							__("Hold steady — scanning…"),
						);
					})
					.catch((err) => {
						console.error("[ch_pos] camera_scanner start failed", err);
						if (!resolved) {
							overlay.find(".ch-cam-status").text(
								__("Camera permission denied or unavailable."),
							);
						}
					});
				return scanner_start;
			})
			.catch((err) => {
				console.error("[ch_pos] local scanner load failed", err);
				if (!resolved) {
					overlay.find(".ch-cam-status").text(
						__("Scanner library unavailable — use the IMEI text box."),
					);
				}
			});
	});
}
