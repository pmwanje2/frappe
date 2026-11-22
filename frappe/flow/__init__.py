from __future__ import annotations

import frappe
from frappe.flow.lib.agent import Agent
from frappe.flow.lib.tool import Tool, ToolContext, build_schema, tool
from frappe.flow.lib.trigger import Trigger

__all__ = [
	"Agent",
	"Tool",
	"ToolContext",
	"Trigger",
	"build_schema",
	"tool",
]


def is_disabled() -> bool:
	"""Maximus sites use Eve; only an explicit site/bench opt-in enables Flow."""
	return frappe.conf.get("disable_frappe_flow", 1) not in (0, "0")
