# Copyright (c) 2021, Frappe Technologies and Contributors
# License: MIT. See LICENSE

import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.query_builder.utils import get_query_builder
from frappe.tests import IntegrationTestCase
from frappe.utils.caching import request_cache
from frappe.utils.user import get_users_with_role
from frappe.workflow.doctype.workflow_action import workflow_action as actions


class TestWorkflowAction(IntegrationTestCase):
	def test_candidate_action_creation_uses_real_permission_aware_list(self):
		"""Fresh-lab integration only; never selected by the source-unit runner."""
		from frappe.tests.utils import make_test_records
		from frappe.workflow.doctype.workflow.test_workflow import create_todo_workflow

		make_test_records("User")
		frappe.set_user("Administrator")
		workflow = create_todo_workflow()
		self.addCleanup(frappe.delete_doc, "Workflow", workflow.name)
		for name in ("test2@example.com", "test3@example.com"):
			user = frappe.get_doc("User", name)
			self.assertEqual(user.user_type, "System User")
			prior_roles = {row.role for row in user.roles}
			self.assertNotIn("System Manager", prior_roles)
			user.add_roles("Test Approver")
			if "Test Approver" not in prior_roles:
				self.addCleanup(user.remove_roles, "Test Approver")
		workflow.send_email_alert = 0
		for transition in workflow.transitions:
			transition.condition = (
				"frappe.session.user == doc.allocated_to and "
				'frappe.db.get_list("ToDo", filters={"name": doc.name}, pluck="name") == [doc.name]'
			)
		workflow.save()
		todo = frappe.get_doc(
			doctype="ToDo",
			description="ordinary candidate list access",
			allocated_to="test2@example.com",
			assigned_by="Administrator",
		).insert()
		for name, expected in (("test2@example.com", [todo.name]), ("test3@example.com", [])):
			with self.subTest(candidate=name), self.set_user(name):
				self.assertNotIn("System Manager", frappe.get_roles())
				self.assertEqual(
					frappe.db.get_list("ToDo", filters={"name": todo.name}, pluck="name"), expected
				)
				self.assertEqual(frappe.has_permission("ToDo", doc=todo, print_logs=False), bool(expected))
		rows = frappe.get_all(
			"Workflow Action",
			fields=["name", "modified_by"],
			filters={"reference_doctype": "ToDo", "reference_name": todo.name, "status": "Open"},
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].modified_by, "Administrator")
		self.assertEqual(frappe.session.user, "Administrator")


class TestWorkflowActionCandidateUnits(unittest.TestCase):
	"""Imported native modules; storage/metadata doubles, no site or DB connection.

	Run only this class in the separately approved source-unit container. Native
	permission and role functions execute; SQL and metadata stay in memory. This
	does not replace integration with a real permission-aware database query.
	"""

	def setUp(self):
		self.original_local = dict(frappe.local)
		self.addCleanup(self.restore_local)
		frappe.local.session = frappe._dict(
			user="maker@example.test", sid="saving-sid", data={"audit": "saving"}
		)
		frappe.local.flags = frappe._dict()
		frappe.local.lang = "en"
		frappe.local.conf = frappe._dict(db_type="mariadb")
		frappe.local.system_settings = frappe._dict(apply_strict_user_permissions=0)
		frappe.local.cache = {"saving": object()}
		frappe.local.role_permissions = {}
		frappe.local.user_perms = object()
		frappe.local.request_cache = defaultdict(dict)
		frappe.local.qb = get_query_builder("mariadb")
		self.members = {"checker@example.test", "other@example.test"}
		self.disabled = set()
		self.scopes = {}
		self.role_cache = {}
		self.sql_error = False
		frappe.local.db = SimpleNamespace(
			db_type="mariadb",
			sql=self.sql,
			get_value=lambda *args, **kwargs: "email@example.test",
			get_list=lambda *args, **kwargs: [],
		)
		meta = frappe._dict(
			name="ToDo",
			issingle=0,
			is_submittable=0,
			allow_import=0,
			is_tree=0,
			permissions=[frappe._dict(role="Test Approver", permlevel=0, read=1, if_owner=0)],
			get_link_fields=lambda: [],
		)
		self.enterContext(patch.object(frappe, "in_test", True))
		self.enterContext(patch.object(frappe, "cache", SimpleNamespace(hget=self.cached_value)))
		self.enterContext(patch.object(frappe, "client_cache", SimpleNamespace(get_value=lambda key: [])))
		self.enterContext(patch.object(frappe, "get_meta", return_value=meta))
		self.enterContext(patch.object(frappe, "get_hooks", return_value={}))
		self.enterContext(patch.object(frappe, "get_all", return_value=[]))
		self.enterContext(patch.object(frappe, "get_cached_value", return_value="System User"))
		self.enterContext(patch.object(actions, "get_workflow_action_url", return_value="signed-test-link"))
		self.doc = frappe._dict(
			doctype="ToDo",
			name="TEST-TODO",
			owner="maker@example.test",
			modified_by="maker@example.test",
			assigned="checker@example.test",
			meta=meta,
			get_all_children=lambda **kwargs: [],
		)
		self.doc.as_dict = lambda: self.doc
		self.transition = frappe._dict(
			allowed="Test Approver",
			action="Approve",
			allow_self_approval=0,
			send_email_to_creator=0,
			condition="doc.assigned == frappe.session.user",
		)

	def restore_local(self):
		for key, _value in list(frappe.local):
			delattr(frappe.local, key)
		for key, value in self.original_local.items():
			setattr(frappe.local, key, value)

	def cached_value(self, name, key, generator=None, **kwargs):
		if name == "roles":
			if key not in self.role_cache:
				self.role_cache[key] = generator()
			return self.role_cache[key]
		if name == "user_permissions":
			return self.scopes.get(key, {})
		if name == "merged_translations":
			return {}
		if name == "is_table":
			return False
		return generator() if generator else None

	def sql(self, query, values=None, **kwargs):
		if self.sql_error:
			raise RuntimeError("isolated SQL read failure")
		values = list((values or {}).values())
		if "tabHas Role" in query and "tabUser" in query:
			return sorted(self.members - self.disabled)
		if "tabHas Role" in query:
			return ["Test Approver"] if any(user in self.members for user in values) else []
		return []

	def test_native_recipient_condition_excludes_unassigned_role_holder(self):
		self.assertEqual(
			set(actions.get_users_next_action_data([self.transition], self.doc)), {"checker@example.test"}
		)

	def test_native_request_cache_is_isolated_and_original_context_restored(self):
		@request_cache
		def list_rows():
			return [frappe.get_user().name]

		# Warm a userless request-cache key as the saver before checking candidates.
		frappe.local.user_perms = None
		self.assertEqual(list_rows(), ["maker@example.test"])
		before = dict(frappe.local)
		self.transition.condition = 'frappe.db.get_list("ToDo") == [doc.assigned]'
		with patch.object(
			frappe.local.db, "get_list", side_effect=lambda *args, **kwargs: list_rows(), create=True
		):
			self.assertEqual(
				set(actions.get_users_next_action_data([self.transition], self.doc)), {"checker@example.test"}
			)
		for key, value in before.items():
			self.assertIs(getattr(frappe.local, key), value)
		self.assertEqual(frappe.session.sid, "saving-sid")
		self.assertEqual(frappe.session.data, {"audit": "saving"})
		self.assertEqual(list_rows(), ["maker@example.test"])

	def test_native_role_api_excludes_disabled_and_revoked_users(self):
		self.assertEqual(set(get_users_with_role("Test Approver")), self.members)
		self.disabled.add("checker@example.test")
		self.members.remove("other@example.test")
		self.assertEqual(get_users_with_role("Test Approver"), [])
		self.assertEqual(actions.get_users_next_action_data([self.transition], self.doc), {})

	def test_native_document_read_denial_prevents_recipient(self):
		self.scopes["checker@example.test"] = {
			"ToDo": [frappe._dict(doc="OTHER-TODO", applicable_for="ToDo")]
		}
		self.assertEqual(actions.get_users_next_action_data([self.transition], self.doc), {})

	def test_native_role_lookup_failure_fails_closed(self):
		self.sql_error = True
		before = dict(frappe.local)
		try:
			result = actions.get_users_next_action_data([self.transition], self.doc)
		except RuntimeError:
			self.fail("role lookup uncertainty must not escape or grant a recipient")
		self.assertEqual(result, {})
		for key, value in before.items():
			self.assertIs(getattr(frappe.local, key), value)

	def test_native_candidate_does_not_inherit_bypasses_or_leak_diagnostics(self):
		frappe.local.flags = frappe._dict(
			ignore_permissions=True,
			ignore_user_permissions=True,
			ignore_user_permissions_for_doctype="ToDo",
			candidate_diagnostics=["saving"],
		)
		frappe.local.message_log = ["saving message"]
		frappe.local.permission_debug_log = ["saving permission debug"]
		before = dict(frappe.local)

		def list_rows(*args, **kwargs):
			if any(
				frappe.flags.get(key)
				for key in (
					"ignore_permissions",
					"ignore_user_permissions",
					"ignore_user_permissions_for_doctype",
				)
			):
				return ["permission bypass inherited"]
			frappe.flags.candidate_diagnostics.append(frappe.session.user)
			frappe.local.message_log.append("candidate message")
			frappe.local.permission_debug_log.append("candidate permission debug")
			return [frappe.session.user]

		self.transition.condition = 'frappe.db.get_list("ToDo") == [doc.assigned]'
		with patch.object(frappe.local.db, "get_list", side_effect=list_rows):
			self.assertEqual(
				set(actions.get_users_next_action_data([self.transition], self.doc)), {"checker@example.test"}
			)
		for key, value in before.items():
			self.assertIs(getattr(frappe.local, key), value)
		self.assertEqual(frappe.flags.candidate_diagnostics, ["saving"])
		self.assertTrue(frappe.flags.ignore_permissions)
		self.assertEqual(frappe.local.message_log, ["saving message"])
		self.assertEqual(frappe.local.permission_debug_log, ["saving permission debug"])
