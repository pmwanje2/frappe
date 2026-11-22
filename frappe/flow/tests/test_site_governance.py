# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from contextlib import ExitStack
from datetime import datetime
from importlib import import_module
from unittest import TestCase
from unittest.mock import patch

from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request, Response

import frappe
from frappe import hooks
from frappe.flow import api, knowledge, launcher, triggers
from frappe.flow.boot import boot_session
from frappe.flow.daemon import callback, lifecycle
from frappe.flow.daemon.client import DaemonClient, DaemonError
from frappe.flow.doctype.flow_model.flow_model import FlowModel
from frappe.flow.knowledge import ingest
from frappe.studio import api as studio_api
from frappe.www.flow import index

HTML = "<html><body><p>Eve</p></body></html>"
PATHS = ("/desk", "/insights", "/kamra", "/urypos")
RPC_METHODS = (api.boot, api.create_session, FlowModel.test_connection, callback.callback)
SYNC_METHODS = (
	"permissions.ensure_flow_role",
	"assistant.sync_builtin_assistant",
	"assistant.sync_assistant_skills",
	"lib.agent.sync_agents",
	"lib.agent.sync_tools",
	"lib.trigger.sync_triggers",
	"daemon.lifecycle.after_install",
	"daemon.lifecycle.after_migrate",
)


class TestFlowSiteGovernance(TestCase):
	"""Run request and lifecycle entry points without a database or a Flow process."""

	def setUp(self):
		for name, value in {
			"conf": frappe._dict(),
			"session": frappe._dict(user="manager@example.com"),
			"flags": frappe._dict(),
			"site": "test.localhost",
			"request": Request(EnvironBuilder(path="/flow/session/example").get_environ()),
		}.items():
			self.enterContext(patch.object(frappe.local, name, value, create=True))

	def test_default_and_disabled_launcher_preserve_app_html_for_a_system_manager(self):
		with (
			patch.object(frappe, "has_permission", return_value=True) as permission,
			patch.object(frappe, "get_hooks", return_value=[{"route": p} for p in PATHS]) as app_hooks,
			patch.object(launcher, "bundled_asset", return_value="/assets/flow_launcher.bundle.js"),
			patch.object(launcher, "get_assets_json", return_value={}),
		):
			for config in ({}, {"disable_frappe_flow": 1}, {"disable_frappe_flow": "invalid"}):
				with patch.dict(frappe.local.conf, config):
					for path in PATHS:
						with self.subTest(config=config, path=path):
							self.assertEqual(self.page(path), HTML)
			permission.assert_not_called()
			app_hooks.assert_not_called()

	def test_opt_in_injects_launcher_only_for_users_with_native_permission(self):
		frappe.conf.disable_frappe_flow = 0
		with (
			patch.object(frappe, "has_permission", return_value=True) as permission,
			patch.object(frappe, "get_hooks", return_value=[{"route": p} for p in PATHS]),
			patch.object(launcher, "bundled_asset", return_value="/assets/flow_launcher.bundle.js"),
			patch.object(launcher, "get_assets_json", return_value={}),
		):
			for path in PATHS:
				with self.subTest(path=path):
					self.assertIn('src="/assets/flow_launcher.bundle.js"', self.page(path))
			permission.assert_called_with("Flow Session", "read")
			permission.return_value = False
			self.assertEqual(self.page("/desk"), HTML)

	def page(self, path):
		request = Request(EnvironBuilder(path=path).get_environ())
		response = Response(HTML, mimetype="text/html")
		launcher.add_to_page(response, request)
		return response.get_data(as_text=True)

	def test_disabled_page_is_not_found_before_guest_redirect_or_boot(self):
		for config in ({}, {"disable_frappe_flow": 1}):
			for user in ("Guest", "manager@example.com", "Administrator"):
				with (
					self.subTest(config=config, user=user),
					patch.dict(frappe.local.conf, config),
					patch.dict(frappe.local.session, user=user),
					patch.object(index, "get_boot", return_value={}) as boot,
					patch("frappe.utils.get_assets_json", return_value={}),
					patch("frappe.translate.get_messages_for_boot", return_value={}),
				):
					with self.assertRaises((frappe.PageDoesNotExistError, frappe.Redirect)) as raised:
						index.get_context(frappe._dict())
					self.assertIsInstance(raised.exception, frappe.PageDoesNotExistError)
					boot.assert_not_called()
					self.assertNotIn("redirect_location", frappe.flags)

	def test_opt_in_retains_native_page_boot_and_rpc_registration(self):
		frappe.conf.disable_frappe_flow = 0
		with (
			patch.object(index, "get_boot", return_value={"flow_boot": {}}),
			patch("frappe.utils.get_assets_json", return_value={}),
			patch("frappe.translate.get_messages_for_boot", return_value={}),
		):
			self.assertEqual(index.get_context(frappe._dict()).boot, {"flow_boot": {}})
		for method in RPC_METHODS:
			with self.subTest(method=method.__qualname__):
				frappe.is_whitelisted(method)

	def test_disabled_site_refuses_flow_rpc_but_not_other_rpc(self):
		for config in ({}, {"disable_frappe_flow": 1}):
			with patch.dict(frappe.local.conf, config):
				for method in RPC_METHODS:
					with self.subTest(config=config, method=method.__qualname__):
						with self.assertRaises(frappe.DoesNotExistError):
							frappe.is_whitelisted(method)
				frappe.is_whitelisted(frappe.ping)

	def test_desk_boot_advertises_flow_only_on_opt_in_sites(self):
		for config, enabled in (({}, False), ({"disable_frappe_flow": 1}, False), ({"disable_frappe_flow": 0}, True)):
			with self.subTest(config=config), patch.dict(frappe.local.conf, config):
				boot = frappe._dict()
				boot_session(boot)
				self.assertEqual(bool(boot.get("flow_enabled")), enabled)
				self.assertEqual("flow_supported_file_types" in boot, enabled)

	def test_studio_advertises_flow_only_on_opt_in_sites(self):
		with patch.object(frappe, "has_permission", return_value=True) as permission:
			self.assertFalse(studio_api.can_use_flow())
			frappe.conf.disable_frappe_flow = 1
			self.assertFalse(studio_api.can_use_flow())
			permission.assert_not_called()
			frappe.conf.disable_frappe_flow = 0
			self.assertTrue(studio_api.can_use_flow())
			permission.assert_called_once_with("Flow Session", "read")

	def test_flow_hooks_sync_and_provision_only_on_opt_in_sites(self):
		for config in ({}, {"disable_frappe_flow": 1}, {"disable_frappe_flow": 0}):
			with self.subTest(config=config), patch.dict(frappe.local.conf, config), ExitStack() as stack:
				steps = {}
				for path in SYNC_METHODS:
					module, name = path.rsplit(".", 1)
					steps[path] = stack.enter_context(patch.object(import_module(f"frappe.flow.{module}"), name))
				# Non-Flow hooks (including Studio) must remain registered, but require a bench to run.
				for event, args in (
					("after_install", ()),
					("after_migrate", ()),
					("after_app_install", ("example",)),
					("after_app_uninstall", ("example",)),
				):
					paths = [name for name in getattr(hooks, event) if name.startswith("frappe.flow.")]
					self.assertTrue(paths)
					for path in paths:
						module, name = path.rsplit(".", 1)
						getattr(import_module(module), name)(*args)
				enabled = config.get("disable_frappe_flow") == 0
				counts = (2, 2, 2, 2, 2, 3, 1, 1)  # one count per native lifecycle step above
				for (path, step), count in zip(steps.items(), counts, strict=True):
					with self.subTest(step=path):
						self.assertEqual(step.call_count, count if enabled else 0)

	def test_disabled_site_skips_trigger_dispatch_and_preexisting_queue(self):
		frappe.conf.disable_frappe_flow = 1
		with (
			patch("frappe.flow.triggers.triggers._doctype_triggers", return_value=[]) as lookup,
			patch("frappe.utils.now_datetime", return_value=datetime(2026, 1, 1)),
			patch.object(frappe, "get_all", return_value=[]) as scheduled,
			patch.object(frappe, "get_doc", return_value=frappe._dict(enabled=False)) as queued,
		):
			triggers.dispatch(frappe._dict(doctype="ToDo"), "on_update")
			triggers.dispatch_scheduled()
			self.assertIsNone(triggers.fire("old-trigger"))
			for step in (lookup, scheduled, queued):
				with self.subTest(step=step):
					step.assert_not_called()

	def test_disabled_site_skips_knowledge_scheduler(self):
		frappe.conf.disable_frappe_flow = 1
		with (
			patch.object(knowledge, "store", create=True) as store,
			patch.object(frappe, "get_all", return_value=[]),
		):
			store.table_exists.return_value = True
			ingest.sync_due_sources()
			store.table_exists.assert_not_called()

	def test_disabled_site_does_not_provision(self):
		frappe.conf.disable_frappe_flow = 1
		with (
			patch.object(lifecycle, "update_site_config") as update,
			patch.object(frappe, "generate_hash", return_value="test-placeholder"),
		):
			lifecycle.provision_site()
			update.assert_not_called()

	def test_disabled_site_does_not_connect_even_with_existing_credentials(self):
		frappe.conf.disable_frappe_flow = 1
		frappe.conf.flow_server_secret = "unused-test-placeholder"
		with patch.object(lifecycle, "socket_path", return_value="/not-used") as socket:
			with self.assertRaises(DaemonError):
				DaemonClient.for_site()
			socket.assert_not_called()
