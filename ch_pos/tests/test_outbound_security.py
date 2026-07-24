from unittest.mock import Mock, patch

import frappe
from frappe.tests import IntegrationTestCase

from ch_pos.api.outbound_security import (
	parse_exact_host_allowlist,
	post_json_to_allowed_https,
	validate_allowed_https_url,
)


PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]


class TestOutboundSecurity(IntegrationTestCase):
	@patch("ch_pos.api.outbound_security.socket.getaddrinfo", return_value=PUBLIC_DNS)
	def test_exact_https_host_is_required(self, _dns):
		hosts = parse_exact_host_allowlist("api.example.test", label="AI")
		self.assertEqual(
			validate_allowed_https_url(
				"https://api.example.test/v1/chat", hosts, label="AI"
			),
			"https://api.example.test/v1/chat",
		)
		for endpoint in (
			"http://api.example.test/v1/chat",
			"https://user:secret@api.example.test/v1/chat",
			"https://other.example.test/v1/chat",
		):
			with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
				validate_allowed_https_url(endpoint, hosts, label="AI")

	@patch(
		"ch_pos.api.outbound_security.socket.getaddrinfo",
		return_value=[(2, 1, 6, "", ("127.0.0.1", 443))],
	)
	def test_private_resolution_is_rejected(self, _dns):
		with self.assertRaises(frappe.PermissionError):
			validate_allowed_https_url(
				"https://api.example.test/v1",
				{"api.example.test"},
				label="AI",
			)

	@patch("ch_pos.api.outbound_security.socket.getaddrinfo", return_value=PUBLIC_DNS)
	@patch("ch_pos.api.outbound_security.requests.post")
	def test_redirect_and_oversized_responses_are_rejected(self, post, _dns):
		redirect = Mock(status_code=302)
		post.return_value = redirect
		with self.assertRaises(frappe.ValidationError):
			post_json_to_allowed_https(
				"https://api.example.test/v1",
				allowed_hosts={"api.example.test"},
				label="AI",
				headers={},
				payload={},
				timeout=5,
			)
		post.assert_called_with(
			"https://api.example.test/v1",
			headers={},
			json={},
			timeout=5,
			allow_redirects=False,
			stream=True,
		)

		oversized = Mock(status_code=200)
		oversized.iter_content.return_value = [b"x" * 1025]
		post.return_value = oversized
		with self.assertRaises(frappe.ValidationError):
			post_json_to_allowed_https(
				"https://api.example.test/v1",
				allowed_hosts={"api.example.test"},
				label="AI",
				headers={},
				payload={},
				timeout=5,
				max_response_bytes=1024,
			)
