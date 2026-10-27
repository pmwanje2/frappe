# Copyright (c) 2026, Frappe Technologies and Contributors
# License: MIT. See LICENSE

"""Offline behavior tests executing this checkout's native function bodies.

Run directly with Python's unittest runner; no installed Frappe, site or network.
Storage, permission and expression boundaries are doubles, not native DB proof.
The loader compiles and executes functions, not a translated workflow algorithm.
"""

import ast
import unittest
from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
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
		)
		self.frappe = ModuleType("frappe")
		self.frappe.local = self.local
		self.frappe._dict = Row
		self.frappe.whitelist = lambda **kwargs: lambda function: function
		self.frappe.flags = Row()
		self.frappe.in_test = True

		# session is a live proxy in Frappe. Do not pin the original session here.
		class Session:
			def __getattr__(_self, key):
				return getattr(self.local.session, key)

		self.frappe.session = Session()
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
		self.frappe.get_all = lambda *args, **kwargs: self.transitions
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


if __name__ == "__main__":
	unittest.main()
