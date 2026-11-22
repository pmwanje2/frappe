# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Only opt-in sites sync Flow definitions or provision the Flow daemon."""

from frappe.flow import is_disabled


def after_install() -> None:
	if is_disabled():
		return
	from frappe.flow.daemon import lifecycle

	_sync_builtin_assistant()
	lifecycle.after_install()


def after_migrate() -> None:
	if is_disabled():
		return
	from frappe.flow.daemon import lifecycle
	from frappe.flow.lib.agent import sync_agents, sync_tools
	from frappe.flow.lib.trigger import sync_triggers

	_sync_builtin_assistant()
	sync_agents()
	sync_tools()
	sync_triggers()
	lifecycle.after_migrate()


def after_app_install(app: str) -> None:
	if is_disabled():
		return
	from frappe.flow.assistant import sync_assistant_skills
	from frappe.flow.lib.agent import sync_agents, sync_tools
	from frappe.flow.lib.trigger import sync_triggers

	sync_assistant_skills(app)
	sync_agents(app)
	sync_tools(app)
	sync_triggers(app)


def after_app_uninstall(app: str) -> None:
	if is_disabled():
		return
	from frappe.flow.assistant import sync_assistant_skills
	from frappe.flow.lib.trigger import sync_triggers

	sync_assistant_skills(app)
	sync_triggers(app)


def _sync_builtin_assistant() -> None:
	from frappe.flow.assistant import sync_builtin_assistant
	from frappe.flow.permissions import ensure_flow_role

	ensure_flow_role()
	sync_builtin_assistant()
