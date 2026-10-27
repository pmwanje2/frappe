# Copyright (c) 2026, Frappe Technologies and Contributors
# License: MIT. See LICENSE

"""Offline behavior tests executing this checkout's native function bodies.

Run directly with Python's unittest runner; no installed Frappe, site or network.
Storage, permission and expression boundaries are doubles, not native DB proof.
The loader compiles and executes functions, not a translated workflow algorithm.
"""

import ast
import sys
import unittest
from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from functools import wraps
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
MAKER = "maker@example.test"
CHECKER = "checker@example.test"
OTHER = "other@example.test"
ROLE = "Test Approver"


class Row(dict):
	__getattr__ = dict.get
	__setattr__ = dict.__setitem__

	def as_dict(self):
		return self


def functions(path, namespace, names=None):
	"""Execute exact native definitions, without importing the site's dependencies."""
	source = (ROOT / path).read_text()
	tree = ast.parse(source)
	body = [
		node
		for node in tree.body
		if isinstance(node, ast.FunctionDef)
		and (names is None or node.name in names or node.name.startswith("_workflow_candidate"))
	]
	exec(compile(ast.Module(body=body, type_ignores=[]), str(ROOT / path), "exec"), namespace)
	return namespace


class TestWorkflowActionCandidates(unittest.TestCase):
	def setUp(self):
		self.local = SimpleNamespace(
			session=Row(user=MAKER, sid="original-sid", data=Row(audit="saving-session")),
			cache={"saving": object()},
			role_permissions={},
			user_perms=object(),
			request_cache=defaultdict(dict),
			flags=Row(),
		)
		self.frappe = ModuleType("frappe")
		self.frappe.local = self.local
		self.frappe._dict = Row
		self.frappe.whitelist = lambda **kwargs: lambda function: function
		self.frappe.in_test = True

		# session is a live proxy in Frappe. Do not pin the original session here.
		class LocalProxy:
			def __init__(_self, field):
				_self.field = field

			def __getattr__(_self, key):
				return getattr(getattr(self.local, _self.field), key)

		self.frappe.session = LocalProxy("session")
		self.frappe.flags = LocalProxy("flags")
		self.frappe.utils = SimpleNamespace(
			now_datetime=lambda: None,
			add_to_date=lambda value: value,
			get_datetime=lambda value: value,
			now=lambda: None,
		)
		self.transitions = []
		self.holders = {ROLE: [CHECKER, OTHER]}
		self.denied = set()
		self.actions = []
		self.frappe.get_all = lambda *args, **kwargs: [
			Row((field, row.get(field)) for field in kwargs["fields"]) for row in self.transitions
		]
		self.frappe.db = SimpleNamespace(
			get_value=lambda *args, **kwargs: "email@example.test", get_list=lambda *args, **kwargs: []
		)
		self.frappe.safe_eval = lambda expression, globals_, locals_: eval(
			expression, {"__builtins__": {}, **globals_}, locals_
		)
		cache = {"frappe": self.frappe, "wraps": wraps, "_KWD_MARK": object(), "Callable": Callable}
		functions("frappe/utils/caching.py", cache, {"request_cache", "__generate_request_cache_key"})
		self.frappe.request_cache = cache["request_cache"]
		# Execute set_user verbatim too; __init__.py itself contains Python 3.14 syntax.
		source = (ROOT / "frappe/__init__.py").read_text()
		set_user = "def set_user(" + source.split("\ndef set_user(", 1)[1].split("\ndef ", 1)[0]
		namespace = {"local": self.local, "_dict": Row}
		exec(compile(set_user, str(ROOT / "frappe/__init__.py"), "exec"), namespace)
		self.frappe.set_user = namespace["set_user"]
		permissions = ModuleType("frappe.permissions")
		permissions.has_permission = self.permission
		self.enterContext(
			patch.dict("sys.modules", {"frappe": self.frappe, "frappe.permissions": permissions})
		)
		self.native = {
			"frappe": self.frappe,
			"contextmanager": contextmanager,
			"defaultdict": defaultdict,
			"datetime": datetime,
			"deepcopy": deepcopy,
		}
		functions(
			"frappe/model/workflow.py",
			self.native,
			{
				"get_workflow_safe_globals",
				"is_transition_condition_satisfied",
				"has_approval_access",
			},
		)
		functions("frappe/workflow/doctype/workflow_action/workflow_action.py", self.native)
		self.native.update(
			datetime=datetime,
			get_workflow_name=lambda doctype: "Test Workflow",
			get_doc_workflow_state=lambda doc: doc.workflow_state,
			is_workflow_action_already_created=lambda doc: bool(self.actions),
			update_completed_workflow_actions=lambda *args, **kwargs: None,
			clear_doctype_notifications=lambda doctype: None,
			get_state_optional_field_value=lambda *args: False,
			get_users_with_role=lambda role: list(self.holders.get(role, [])),
			send_email_alert=lambda workflow: False,
			get_workflow_action_url=lambda action, doc, user: f"{user}/{action}",
		)
		self.doc = Row(
			doctype="ToDo",
			name="TEST-TODO",
			owner=MAKER,
			modified_by=MAKER,
			workflow_state="Pending",
			assigned=CHECKER,
		)

		class Action(Row):
			def append(action, field, row):
				action.setdefault(field, []).append(Row(row))

			def insert(action, *, ignore_permissions):
				action.modified_by = self.local.session.user
				self.actions.append(action)

		self.frappe.get_doc = lambda data: Action(data)

	def permission(self, doctype, user=None, **kwargs):
		return user not in self.denied

	def transition(self, condition, **values):
		row = Row(
			allowed=ROLE,
			action="Approve",
			state="Pending",
			next_state="Approved",
			condition=condition,
			allow_self_approval=0,
			send_email_to_creator=0,
		)
		row.update(values)
		self.transitions.append(row)
		return row

	def test_creation_adds_candidate_edge_when_saver_condition_is_false(self):
		self.transition("doc.assigned == frappe.session.user")
		self.assertEqual(
			self.native["get_next_possible_transitions"]("Test Workflow", "Pending", self.doc), []
		)
		self.native["process_workflow_actions"](self.doc, "on_update")
		self.assertEqual(len(self.actions), 1, "assigned candidate must receive native role indexing")
		self.assertEqual([row.role for row in self.actions[0].permitted_roles], [ROLE])
		self.assertEqual(self.actions[0].modified_by, MAKER)

	def test_recipients_recheck_condition_for_each_candidate(self):
		transition = self.transition("doc.assigned == frappe.session.user")
		result = self.native["get_users_next_action_data"]([transition], self.doc)
		self.assertEqual(set(result), {CHECKER}, "unassigned role holder must not receive an action")

	def test_candidate_condition_uses_isolated_request_cache(self):
		@self.frappe.request_cache
		def list_rows():
			return [self.local.session.user]

		self.frappe.db.get_list = lambda *args, **kwargs: list_rows()
		self.assertEqual(list_rows(), [MAKER])
		cache = self.local.request_cache
		transition = self.transition('frappe.db.get_list("ToDo") == [doc.assigned]')
		for holders in ([CHECKER, OTHER], [OTHER, CHECKER]):
			with self.subTest(order=holders):
				self.holders[ROLE] = holders
				self.assertEqual(
					set(self.native["get_users_next_action_data"]([transition], self.doc)), {CHECKER}
				)
		self.assertIs(self.local.request_cache, cache)
		self.assertEqual(list_rows(), [MAKER])

	def test_candidate_permission_denial_cannot_add_an_edge(self):
		self.transition("doc.assigned == frappe.session.user")
		self.denied.add(CHECKER)
		self.native["process_workflow_actions"](self.doc, "on_update")
		self.assertEqual(self.actions, [])

	def test_condition_and_permission_errors_fail_closed_and_restore_context(self):
		for failure in ("condition", "permission"):
			for full_context in (False, True):
				with self.subTest(failure=failure, full_context=full_context):
					if full_context:
						for key in ("form_dict", "jenv_restricted", "jenv_unrestricted", "new_doc_templates"):
							setattr(self.local, key, {"saving": key})
					before = vars(self.local).copy()
					session = self.local.session.copy()
					transition = self.transition('frappe.db.get_list("ToDo")')

					def unavailable(*args, **kwargs):
						raise PermissionError("isolated storage unavailable")

					self.frappe.db.get_list = unavailable
					permissions = sys.modules["frappe.permissions"]
					with patch.object(
						permissions,
						"has_permission",
						unavailable if failure == "permission" else self.permission,
					):
						try:
							result = self.native["get_users_next_action_data"]([transition], self.doc)
						except PermissionError:
							self.fail(
								"uncertain recipient permission must fail closed, not escape the lookup"
							)
						self.assertEqual(result, {})
					self.assertEqual(vars(self.local), before)
					for key, value in before.items():
						self.assertIs(getattr(self.local, key), value)
					self.assertEqual(self.local.session, session)

	def test_success_restores_session_and_all_original_local_references(self):
		before = vars(self.local).copy()
		transition = self.transition("doc.assigned == frappe.session.user")
		self.assertEqual(set(self.native["get_users_next_action_data"]([transition], self.doc)), {CHECKER})
		self.assertEqual(vars(self.local), before)
		for key, value in before.items():
			self.assertIs(getattr(self.local, key), value)
		self.assertEqual(
			self.local.session, Row(user=MAKER, sid="original-sid", data=Row(audit="saving-session"))
		)

	def test_preserves_qualifying_role_index_without_current_holders(self):
		self.holders.clear()
		self.transition("")
		self.native["process_workflow_actions"](self.doc, "on_update")
		self.assertEqual([row.role for row in self.actions[0].permitted_roles], [ROLE])

	def test_owner_withdraw_index_is_not_an_email_creator_rule(self):
		self.holders[ROLE] = [MAKER]
		transition = self.transition(
			"doc.owner == frappe.session.user", action="Withdraw", allow_self_approval=1
		)
		self.native["process_workflow_actions"](self.doc, "on_update")
		self.assertEqual(len(self.actions), 1)
		self.assertEqual(self.native["get_users_next_action_data"]([transition], self.doc), {})
		transition.send_email_to_creator = 1
		self.assertEqual(set(self.native["get_users_next_action_data"]([transition], self.doc)), {MAKER})

	def test_optional_state_stays_excluded_for_all_candidates(self):
		self.transition("doc.assigned == frappe.session.user")
		self.native["get_state_optional_field_value"] = lambda *args: True
		self.native["process_workflow_actions"](self.doc, "on_update")
		self.assertEqual(self.actions, [])

	def test_self_approval_gate_is_preserved(self):
		self.holders[ROLE] = [MAKER]
		transition = self.transition("")
		transition.send_email_to_creator = 1
		self.assertEqual(self.native["get_users_next_action_data"]([transition], self.doc), {})
		transition.allow_self_approval = 1
		self.assertEqual(set(self.native["get_users_next_action_data"]([transition], self.doc)), {MAKER})

	def test_retry_does_not_create_a_duplicate_action(self):
		self.transition("doc.assigned == frappe.session.user")
		self.native["process_workflow_actions"](self.doc, "on_update")
		self.native["process_workflow_actions"](self.doc, "on_update")
		self.assertEqual(len(self.actions), 1)

	def test_recipient_is_rechecked_after_reassignment_or_role_revocation(self):
		transition = self.transition("doc.assigned == frappe.session.user")
		self.doc.assigned = OTHER
		self.assertEqual(set(self.native["get_users_next_action_data"]([transition], self.doc)), {OTHER})
		self.holders[ROLE].remove(OTHER)
		self.assertEqual(self.native["get_users_next_action_data"]([transition], self.doc), {})

	def test_creator_email_flag_survives_transition_discovery(self):
		self.holders[ROLE] = [MAKER]
		self.transition("", allow_self_approval=1, send_email_to_creator=1)
		transitions = self.native["get_next_possible_transitions"]("Test Workflow", "Pending", self.doc)
		self.assertEqual(set(self.native["get_users_next_action_data"](transitions, self.doc)), {MAKER})

	def test_candidate_edges_preserve_owner_editor_and_assignee_exclusions(self):
		for name, condition, expected in (
			(
				"independent-review",
				"doc.owner != frappe.session.user and doc.modified_by != frappe.session.user",
				{CHECKER, OTHER},
			),
			(
				"assigned-review",
				"doc.assigned == frappe.session.user and doc.owner != frappe.session.user",
				{CHECKER},
			),
			(
				"independent-handoff",
				"doc.modified_by != frappe.session.user and doc.owner != frappe.session.user",
				{OTHER},
			),
		):
			with self.subTest(family=name):
				self.transitions.clear()
				self.actions.clear()
				self.holders[ROLE] = [MAKER, CHECKER, OTHER]
				self.doc.modified_by = CHECKER if name == "independent-handoff" else MAKER
				self.transition(condition)
				self.native["process_workflow_actions"](self.doc, "on_update")
				self.assertEqual(len(self.actions), 1)
				self.assertEqual(
					set(self.native["get_users_next_action_data"](self.transitions, self.doc)), expected
				)

	def test_candidate_does_not_inherit_saver_permission_bypasses(self):
		self.local.flags = Row(
			ignore_permissions=True, ignore_user_permissions=True, ignore_user_permissions_for_doctype="ToDo"
		)

		def list_rows(*args, **kwargs):
			if any(
				self.frappe.flags.get(key)
				for key in (
					"ignore_permissions",
					"ignore_user_permissions",
					"ignore_user_permissions_for_doctype",
				)
			):
				return ["forbidden broader scope"]
			return [self.local.session.user]

		self.frappe.db.get_list = list_rows
		transition = self.transition('frappe.db.get_list("ToDo") == [doc.assigned]')
		self.assertEqual(set(self.native["get_users_next_action_data"]([transition], self.doc)), {CHECKER})
		self.assertTrue(self.frappe.flags.ignore_permissions)

	def test_candidate_diagnostic_mutations_are_isolated_on_success_and_error(self):
		for fail in (False, True):
			for logs_present in (False, True):
				with self.subTest(error=fail, logs_present=logs_present):
					self.local.flags = Row(candidate_diagnostics=["saving"])
					for key in ("message_log", "permission_debug_log"):
						if logs_present:
							setattr(self.local, key, ["saving"])
						elif hasattr(self.local, key):
							delattr(self.local, key)
					before = vars(self.local).copy()

					def list_rows(*args, **kwargs):
						self.local.flags.candidate_diagnostics.append(self.local.session.user)
						self.local.message_log.append("private candidate message")
						self.local.permission_debug_log.append("private candidate debug")
						if fail:
							raise PermissionError("isolated query denied")
						return [self.local.session.user]

					self.frappe.db.get_list = list_rows
					transition = self.transition('frappe.db.get_list("ToDo") == [doc.assigned]')
					self.assertEqual(
						set(self.native["get_users_next_action_data"]([transition], self.doc)),
						set() if fail else {CHECKER},
					)
					self.assertEqual(set(vars(self.local)), set(before))
					for key, value in before.items():
						self.assertIs(getattr(self.local, key), value)
					self.assertEqual(self.local.flags.candidate_diagnostics, ["saving"])
					if logs_present:
						self.assertEqual(self.local.message_log, ["saving"])
						self.assertEqual(self.local.permission_debug_log, ["saving"])


if __name__ == "__main__":
	unittest.main()
