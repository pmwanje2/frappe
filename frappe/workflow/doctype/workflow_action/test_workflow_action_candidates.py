# Copyright (c) 2026, Frappe Technologies and Contributors
# License: MIT. See LICENSE

"""Offline behavior tests executing this checkout's native function bodies.

Run directly with Python's unittest runner; no installed Frappe, site or network.
Storage, permission and expression boundaries are doubles, not native DB proof.
The loader compiles and executes functions, not a translated workflow algorithm.
"""

import ast
import json
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
FIXTURES = json.loads(Path(__file__).with_name("candidate_workflow_fixtures.json").read_text())


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

	def test_recipient_roster_is_read_once_per_role_per_invocation(self):
		reads = []

		def users_with_role(role):
			reads.append(role)
			return list(self.holders.get(role, []))

		self.native["get_users_with_role"] = users_with_role
		self.transition("", action="Approve")
		self.transition("", action="Reject")
		self.assertEqual(
			set(self.native["get_users_next_action_data"](self.transitions, self.doc)), {CHECKER, OTHER}
		)
		self.assertEqual(reads, [ROLE], "two edges must reuse one native enabled-member roster")
		self.holders[ROLE] = [OTHER]
		self.assertEqual(set(self.native["get_users_next_action_data"](self.transitions, self.doc)), {OTHER})
		self.assertEqual(reads, [ROLE, ROLE], "a new invocation must re-read revocations")

	def test_discovery_roster_is_read_once_per_role_per_invocation(self):
		reads = []

		def users_with_role(role):
			reads.append(role)
			return list(self.holders.get(role, []))

		self.native["get_users_with_role"] = users_with_role
		self.transition("doc.assigned == frappe.session.user", action="Approve")
		self.transition("doc.assigned == frappe.session.user", action="Reject")
		transitions = self.native["get_next_possible_transitions"](
			"Test Workflow", "Pending", self.doc, include_candidate_transitions=True
		)
		self.assertEqual({row.action for row in transitions}, {"Approve", "Reject"})
		self.assertEqual(reads, [ROLE], "candidate discovery must not re-read a roster per candidate or edge")
		self.holders[ROLE] = []
		self.assertEqual(
			self.native["get_next_possible_transitions"](
				"Test Workflow", "Pending", self.doc, include_candidate_transitions=True
			),
			[],
		)
		self.assertEqual(reads, [ROLE, ROLE])

	def test_direct_candidate_predicate_rechecks_membership_each_time(self):
		transition = self.transition("doc.assigned == frappe.session.user")
		self.assertTrue(self.native["is_workflow_action_candidate"](CHECKER, self.doc, transition))
		self.holders[ROLE].remove(CHECKER)
		self.assertFalse(self.native["is_workflow_action_candidate"](CHECKER, self.doc, transition))

	def use_workflow_fixture(self, workflow, state, *, roles=None):
		"""Configuration/storage doubles only; discovery and receivers remain native."""
		self.actions.clear()
		self.doc.doctype = workflow["document_type"]
		self.doc.workflow_state = state
		self.transitions = [
			Row(send_email_to_creator=0, **row) for row in workflow["transitions"] if row["state"] == state
		]
		if roles is not None:
			self.transitions = [row for row in self.transitions if row.allowed in roles]
		optional = {row["state"]: row["is_optional_state"] for row in workflow["states"]}
		self.native["get_workflow_name"] = lambda doctype: workflow["name"]
		self.native["get_state_optional_field_value"] = lambda workflow_name, state: optional[state]
		self.doc.update(
			group_a="Test Group",
			group_b="Test Group",
			amount_a=2,
			amount_b=2,
			amount_c=2,
			amount_d=2,
			blocked_amount=0,
			adjustment_amount=0,
			adjustments=[],
			kind="Review Type B",
			entries=[Row(key="KEY_A"), Row(key="KEY_B")],
		)

		def get_value(doctype, filters=None, fieldname=None, **kwargs):
			if doctype == "User" and fieldname == "email":
				return filters
			if doctype == "Has Role":
				self.assertEqual(fieldname, "name")
				self.assertEqual(filters["parenttype"], "User")
				self.assertEqual(filters["role"], "Test Self Approver")
				return "native-role-row" if filters["parent"] in {CHECKER, OTHER} else None
			if doctype == "Test Policy":
				self.assertEqual(filters, "Test Group")
				self.assertIn(fieldname, {"limit_a", "limit_b"})
				return 5
			if doctype == "Test Lookup" and fieldname == "category":
				return {"KEY_A": "Allowed", "KEY_B": "Allowed"}[filters]
			raise AssertionError((doctype, filters, fieldname))

		self.frappe.db.get_value = get_value

	def assert_candidate_pipeline(self, edges, recipients):
		transitions = self.native["get_next_possible_transitions"](
			"Test Workflow", self.doc.workflow_state, self.doc, include_candidate_transitions=True
		)
		self.assertEqual({(row.allowed, row.action) for row in transitions}, edges)
		self.native["process_workflow_actions"](self.doc, "on_update")
		self.assertEqual(len(self.actions), int(bool(edges)))
		if edges:
			self.assertEqual({row.role for row in self.actions[0].permitted_roles}, {r for r, _ in edges})
			self.assertEqual(self.actions[0].modified_by, MAKER)
		data = self.native["get_users_next_action_data"](transitions, self.doc)
		self.assertEqual(
			{user: {row.action_name for row in item.possible_actions} for user, item in data.items()},
			recipients,
		)

	def test_owner_human_self_approver_owner_keeps_unlimited_native_candidates(self):
		role = "Test Self Approver"
		self.holders = {role: [CHECKER, OTHER]}
		for workflow in FIXTURES["owner_cases"]:
			with self.subTest(doctype=workflow["document_type"]):
				self.use_workflow_fixture(
					workflow,
					"Pending Approval" if workflow["document_type"] == "Review Type A" else "Pending",
					roles={role},
				)
				self.doc.owner, self.doc.modified_by = CHECKER, MAKER
				for field in (
					"amount_a",
					"amount_b",
					"amount_c",
					"amount_d",
				):
					self.doc[field] = 9
				self.assertTrue(
					self.native["is_workflow_action_candidate"](CHECKER, self.doc, self.transitions[0])
				)
				# Creator-email suppression does not remove the legitimate owner candidate/index.
				self.assert_candidate_pipeline({(role, "Approve")}, {OTHER: {"Approve"}})

	def test_owner_administrator_owned_bill_only_its_self_approver_forwarder_receives(self):
		role = "Test Self Approver"
		self.holders = {role: [CHECKER, OTHER]}
		for workflow in FIXTURES["owner_cases"]:
			for forwarder in (CHECKER, OTHER, MAKER):
				with self.subTest(doctype=workflow["document_type"], forwarder=forwarder):
					self.use_workflow_fixture(
						workflow,
						"Pending Approval" if workflow["document_type"] == "Review Type A" else "Pending",
						roles={role},
					)
					self.doc.owner, self.doc.modified_by = "Administrator", forwarder
					eligible = forwarder in {CHECKER, OTHER}
					self.assert_candidate_pipeline(
						{(role, "Approve")} if eligible else set(),
						{forwarder: {"Approve"}} if eligible else {},
					)

	def test_owner_forwarding_clerk_owned_bill_does_not_grant_self_approval(self):
		role = "Test Self Approver"
		self.holders = {role: [CHECKER, OTHER]}
		for workflow in FIXTURES["owner_cases"]:
			with self.subTest(doctype=workflow["document_type"]):
				self.use_workflow_fixture(
					workflow,
					"Pending Approval" if workflow["document_type"] == "Review Type A" else "Pending",
					roles={role},
				)
				self.doc.owner, self.doc.modified_by = MAKER, CHECKER
				self.assert_candidate_pipeline(set(), {})

	def test_owner_system_owned_clerk_bill_keeps_first_approver_limit_and_editor_refusal(self):
		role = "Test Reviewer"
		self.holders = {role: [MAKER, CHECKER, OTHER]}
		for workflow in FIXTURES["owner_cases"]:
			for amount, allowed in ((2, True), (9, False)):
				with self.subTest(doctype=workflow["document_type"], amount=amount):
					self.use_workflow_fixture(
						workflow,
						"Pending Approval" if workflow["document_type"] == "Review Type A" else "Pending",
						roles={role},
					)
					self.doc.owner, self.doc.modified_by = "Administrator", MAKER
					for field in (
						"amount_a",
						"amount_b",
						"amount_c",
						"amount_d",
					):
						self.doc[field] = amount
					self.assert_candidate_pipeline(
						{(role, "Approve")} if allowed else set(),
						{CHECKER: {"Approve"}, OTHER: {"Approve"}} if allowed else {},
					)

	def test_owner_system_owned_writeoff_and_deduction_refusals_reach_native_pipeline(self):
		role = "Test Self Approver"
		self.holders = {role: [CHECKER, OTHER]}
		exclusions = {
			"Review Type F": [("blocked_amount", 1)],
			"Review Type D": [("blocked_amount", 1)],
			"Review Type B": [("blocked_amount", 1), ("kind", "Blocked")],
			"Review Type C": [("adjustment_amount", 1), ("adjustments", ["deduction"])],
		}
		for workflow in FIXTURES["owner_cases"]:
			for field, value in exclusions.get(workflow["document_type"], []):
				with self.subTest(doctype=workflow["document_type"], refusal=field):
					self.use_workflow_fixture(workflow, "Pending", roles={role})
					self.doc.owner, self.doc.modified_by = "Administrator", CHECKER
					self.doc[field] = value
					self.assert_candidate_pipeline(set(), {})

	def test_scoped_release_native_release_reject_roles_and_scope_refusals(self):
		workflow = FIXTURES["scoped_release"]
		roles = {"Test Release Reviewer", "Test Alternate Reviewer"}
		self.holders = {role: [MAKER, CHECKER, OTHER] for role in roles}
		for request_type in (
			"Kind A",
			"Kind B",
			"Kind C",
			"Kind D",
			"Kind E",
			"Outside Scope",
			"invalid",
			None,
		):
			with self.subTest(request_type=request_type):
				self.use_workflow_fixture(workflow, "Pending Release")
				self.doc.owner, self.doc.modified_by = MAKER, CHECKER
				self.doc.request_kind = request_type
				allowed = request_type in {
					"Kind A",
					"Kind B",
					"Kind C",
					"Kind D",
					"Kind E",
				}
				self.assert_candidate_pipeline(
					{(role, "Release") for role in roles} if allowed else set(),
					{OTHER: {"Release"}} if allowed else {},
				)
				# Reject's Cancelled destination is optional: it is never indexed/mailed,
				# but its actual native candidate predicate must still preserve MR scope.
				for row in self.transitions:
					self.assertEqual(
						self.native["is_workflow_action_candidate"](OTHER, self.doc, row), allowed
					)
					self.assertFalse(self.native["is_workflow_action_candidate"](MAKER, self.doc, row))
					self.assertFalse(self.native["is_workflow_action_candidate"](CHECKER, self.doc, row))

	def test_owner_mixed_optional_withdraw_and_approve_with_and_without_holders(self):
		workflow = next(row for row in FIXTURES["owner_cases"] if row["document_type"] == "Review Type F")
		role = "Test Reviewer"
		for has_holders in (True, False):
			for owner in (MAKER, OTHER):
				with self.subTest(holders=has_holders, owner=owner):
					self.use_workflow_fixture(workflow, "Pending", roles={role, "Test Editor"})
					self.holders = {role: [CHECKER], "Test Editor": [owner]} if has_holders else {}
					self.doc.owner, self.doc.modified_by = owner, MAKER
					indexed = has_holders or owner == MAKER
					self.assert_candidate_pipeline(
						{(role, "Approve")} if indexed else set(),
						{CHECKER: {"Approve"}} if has_holders else {},
					)

	def test_unconditional_release_mixed_optional_cancelled_preserves_release_without_holders(self):
		role = "Test Releaser"
		for has_holders in (True, False):
			with self.subTest(holders=has_holders):
				self.use_workflow_fixture(FIXTURES["optional_release"], "Pending Release")
				self.holders = {role: [MAKER, CHECKER]} if has_holders else {}
				self.assert_candidate_pipeline(
					{(role, "Release")}, {CHECKER: {"Release"}} if has_holders else {}
				)

	def test_workflow_state_and_creator_email_gates_use_native_enqueue_and_receivers(self):
		for workflow_email in (0, 1):
			for state_email in (0, 1):
				for creator_email in (0, 1):
					with self.subTest(workflow=workflow_email, state=state_email, creator=creator_email):
						self.actions.clear()
						self.transitions.clear()
						self.holders = {ROLE: [MAKER, CHECKER, OTHER]}
						self.doc.owner, self.doc.modified_by = MAKER, CHECKER
						self.transition(
							"doc.owner == frappe.session.user or doc.modified_by != frappe.session.user",
							allow_self_approval=1,
							send_email_to_creator=creator_email,
						)
						# Execute the actual native workflow-level getter as well as process's state gate.
						functions(
							"frappe/model/workflow.py",
							self.native,
							{"send_email_alert", "get_workflow_field_value"},
						)
						self.frappe.get_cached_value = lambda *args: workflow_email
						self.frappe.db.get_value = lambda doctype, *args, **kwargs: (
							state_email if doctype == "Workflow Document State" else args[0]
						)
						queued, sent = [], []
						self.native["enqueue"] = lambda method, **kwargs: queued.append((method, kwargs))
						self.native["get_common_email_args"] = lambda doc: {
							"subject": "native test",
							"message": "approval",
						}
						self.frappe.sendmail = lambda **kwargs: sent.append(kwargs)
						self.native["process_workflow_actions"](self.doc, "on_update")
						self.assertEqual(len(self.actions), 1, "email flags cannot suppress indexing")
						self.assertEqual(len(queued), int(bool(workflow_email and state_email)))
						for method, kwargs in queued:
							self.assertIs(method, self.native["send_workflow_action_email"])
							self.assertEqual(kwargs["queue"], "short")
							self.assertTrue(kwargs["enqueue_after_commit"])
							method(kwargs["doc"], kwargs["transitions"])
						self.assertEqual(
							{mail["recipients"][0] for mail in sent},
							({OTHER, MAKER} if creator_email else {OTHER}) if queued else set(),
						)
						for mail in sent:
							self.assertEqual(
								[row.action_name for row in mail["args"]["actions"]], ["Approve"]
							)
						self.native["process_workflow_actions"](self.doc, "on_update")
						self.assertEqual(len(queued), int(bool(workflow_email and state_email)))

	def test_synthetic_pattern_cells_retain_platform_baseline_and_no_workflow_path(self):
		self.assertEqual(
			FIXTURES["pattern_cells"],
			[
				"cell_a",
				"cell_b",
				"cell_c",
				"cell_d",
				"cell_e",
				"cell_f",
				"cell_g",
				"cell_h",
			],
		)
		for tenant, state, roles, edges, receivers in (
			(
				"baseline_a",
				"Draft",
				{"Test Reference Reviewer": [MAKER, CHECKER, OTHER], "Test Owner Reviewer": [MAKER]},
				{
					("Test Reference Reviewer", "Approve"),
					("Test Reference Reviewer", "Reject"),
					("Test Owner Reviewer", "Approve"),
					("Test Owner Reviewer", "Reject"),
				},
				{CHECKER: {"Approve", "Reject"}, OTHER: {"Approve", "Reject"}},
			),
			(
				"baseline_b",
				"Draft",
				{"Test Stock Reviewer": [MAKER, CHECKER, OTHER]},
				{("Test Stock Reviewer", "Approve")},
				{CHECKER: {"Approve"}, OTHER: {"Approve"}},
			),
			(
				"baseline_c",
				"Pending Review",
				{"Test Quality Reviewer": [MAKER, CHECKER, OTHER], "Test Owner Reviewer": [MAKER]},
				{
					("Test Quality Reviewer", "Approve"),
					("Test Quality Reviewer", "Reject"),
					("Test Owner Reviewer", "Approve"),
					("Test Owner Reviewer", "Reject"),
				},
				{CHECKER: {"Reject"}, OTHER: {"Approve", "Reject"}},
			),
		):
			with self.subTest(tenant=tenant, family="synthetic baseline"):
				self.use_workflow_fixture(FIXTURES["platform_baselines"][tenant], state)
				self.holders = roles
				self.doc.update(
					has_reference="Reference",
					reference_code="TEST-REFERENCE",
					side_a="Reference",
					side_b="Reference",
					submitted_by=CHECKER,
				)
				self.assert_candidate_pipeline(edges, receivers)
		self.assertEqual(FIXTURES["no_workflow_patterns"], {"cell_g": [], "cell_h": []})
		for tenant in ("cell_g", "cell_h"):
			with self.subTest(tenant=tenant, family="synthetic native platform and no-workflow paths"):
				self.actions.clear()
				self.transitions.clear()
				self.holders = {ROLE: [CHECKER, OTHER]}
				self.native["get_workflow_name"] = lambda doctype: "Platform baseline"
				self.native["get_state_optional_field_value"] = lambda *args: False
				self.transition("")
				self.assert_candidate_pipeline(
					{(ROLE, "Approve")}, {CHECKER: {"Approve"}, OTHER: {"Approve"}}
				)
				self.actions.clear()
				self.native["get_workflow_name"] = lambda doctype: None
				self.native["process_workflow_actions"](self.doc, "on_update")
				self.assertEqual(self.actions, [])


class TestWorkflowActionFixtureSetup(unittest.TestCase):
	def test_integration_setup_promotes_fresh_users_and_preserves_prior_roles(self):
		"""Execute only fixture setup; role persistence is doubled, not DB proof."""
		path = ROOT / "frappe/workflow/doctype/workflow_action/test_workflow_action.py"
		tree = ast.parse(path.read_text())
		integration = next(node for node in tree.body if isinstance(node, ast.ClassDef))
		methods = [node for node in integration.body if isinstance(node, ast.FunctionDef)]
		self.assertEqual(len(methods), 2)
		for method in methods:
			for prior_approver in (False, True):
				with self.subTest(method=method.name, prior_approver=prior_approver):

					class FixtureUser:
						def __init__(self):
							self.roles = [Row(role=ROLE)] if prior_approver else []
							self.user_type = "System User" if prior_approver else "Website User"
							self.enabled = 1
							self.reloads = 0

						def add_roles(self, role):
							if not any(row.role == role for row in self.roles):
								self.roles.append(Row(role=role))
							self.user_type = "System User"

						def reload(self):
							self.reloads += 1
							return self

						def remove_roles(self, role):
							self.roles = [row for row in self.roles if row.role != role]

					users = {name: FixtureUser() for name in ("test2@example.com", "test3@example.com")}
					case = unittest.TestCase()
					workflow = Row(name="Test Workflow")
					namespace = {
						"self": case,
						"frappe": SimpleNamespace(
							set_user=lambda name: None,
							get_doc=lambda doctype, name: users[name],
							delete_doc=lambda *args: None,
						),
						"make_test_records": lambda doctype: None,
						"create_todo_workflow": lambda: workflow,
					}
					setup = []
					for statement in method.body:
						if isinstance(statement, ast.Assign) and any(
							isinstance(target, ast.Attribute) and target.attr == "send_email_alert"
							for target in statement.targets
						):
							break
						if not isinstance(statement, (ast.Import, ast.ImportFrom)):
							setup.append(statement)
					try:
						exec(compile(ast.Module(body=setup, type_ignores=[]), str(path), "exec"), namespace)
						for user in users.values():
							self.assertEqual(user.reloads, 1)
							self.assertEqual(user.user_type, "System User")
							self.assertTrue(user.enabled)
							self.assertEqual([row.role for row in user.roles], [ROLE])
					finally:
						case.doCleanups()
					for user in users.values():
						self.assertEqual([row.role for row in user.roles], [ROLE] if prior_approver else [])


if __name__ == "__main__":
	unittest.main()
