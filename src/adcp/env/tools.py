"""tau-bench-style airline + retail environment: in-memory DB, tools, policy rules.

Policy (enforced by tools, mirrored in the "## Policy" prompt section):
- Mutating tools require `verify_identity` for the owning user first.
- Flown or cancelled reservations cannot be cancelled or changed.
- Only pending orders can be cancelled; only delivered orders can be returned/exchanged.
- Exchanges stay within the same product; new variants must be available.
- `issue_refund` (flag `tool-issue-refund`) is capped at $200 per call.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
from collections.abc import Callable
from typing import Any

MUTATING = frozenset(
    {
        "update_reservation_flights",
        "cancel_reservation",
        "cancel_order",
        "return_items",
        "exchange_items",
        "issue_refund",
    }
)
REFUND_CAP_USD = 200.0


class ToolError(Exception):
    """Expected tool failure (bad args or policy violation); surfaced to the model as an error."""


def empty_db() -> dict[str, Any]:
    return {
        "users": {},
        "flights": {},
        "reservations": {},
        "products": {},
        "orders": {},
        "refunds": [],
        "transfers": [],
    }


def state_hash(state: dict[str, Any]) -> str:
    blob = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class TauEnv:
    """One instance per task run. `state` is the DB; verification is session-scoped, not state."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = copy.deepcopy(state) if state else empty_db()
        self.verified: set[str] = set()

    # helpers
    def _get(self, table: str, key: str, label: str) -> dict[str, Any]:
        row = self.state[table].get(key)
        if row is None:
            raise ToolError(f"{label} {key} not found")
        return row  # type: ignore[no-any-return]

    def _require_verified(self, user_id: str) -> None:
        if user_id not in self.verified:
            raise ToolError("PolicyViolation: verify user identity before modifying")

    def _require_pm(self, user_id: str, payment_method_id: str) -> None:
        if payment_method_id not in self.state["users"][user_id]["payment_methods"]:
            raise ToolError(f"payment method {payment_method_id} does not belong to user")

    # read tools
    def verify_identity(self, user_id: str, email: str) -> dict[str, Any]:
        """Verify a user's identity by user id and email. Required before any modification."""
        user = self._get("users", user_id, "user")
        if user["email"].lower() != email.strip().lower():
            raise ToolError("identity verification failed")
        self.verified.add(user_id)
        return {"user_id": user_id, "verified": True}

    def get_user(self, user_id: str) -> dict[str, Any]:
        """Get user profile, payment methods, reservation ids and order ids."""
        user = self._get("users", user_id, "user")
        res = sorted(k for k, v in self.state["reservations"].items() if v["user_id"] == user_id)
        orders = sorted(k for k, v in self.state["orders"].items() if v["user_id"] == user_id)
        return {**user, "user_id": user_id, "reservations": res, "orders": orders}

    def get_reservation(self, reservation_id: str) -> dict[str, Any]:
        """Get reservation details: flights, status, cabin, total, payment method."""
        r = self._get("reservations", reservation_id, "reservation")
        flights = [{"flight_number": f, **self.state["flights"][f]} for f in r["flights"]]
        return {**r, "reservation_id": reservation_id, "flight_details": flights}

    def search_flights(self, origin: str, destination: str, date: str) -> list[dict[str, Any]]:
        """Search scheduled flights by origin, destination (IATA codes) and date (YYYY-MM-DD)."""
        return [
            {"flight_number": k, **v}
            for k, v in sorted(self.state["flights"].items())
            if v["origin"] == origin
            and v["destination"] == destination
            and v["date"] == date
            and v["status"] == "scheduled"
        ]

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Get order details: items, status, payment method."""
        return {**self._get("orders", order_id, "order"), "order_id": order_id}

    def get_product(self, product_id: str) -> dict[str, Any]:
        """Get product details and its variants (item ids, options, price, availability)."""
        return {**self._get("products", product_id, "product"), "product_id": product_id}

    # mutating tools
    def update_reservation_flights(
        self, reservation_id: str, flight_numbers: list[str]
    ) -> dict[str, Any]:
        """Replace the flights on an active reservation. Price difference charged to its payment method."""
        r = self._get("reservations", reservation_id, "reservation")
        self._require_verified(r["user_id"])
        if r["status"] != "active":
            raise ToolError(f"reservation is {r['status']}; cannot change flights")
        old = [self.state["flights"][f] for f in r["flights"]]
        new = [self._get("flights", f, "flight") for f in flight_numbers]
        if any(f["status"] != "scheduled" for f in new):
            raise ToolError("flight not available")
        if (new[0]["origin"], new[-1]["destination"]) != (old[0]["origin"], old[-1]["destination"]):
            raise ToolError("new flights must keep the same origin and destination")
        r["flights"] = list(flight_numbers)
        r["total"] = round(sum(f["price"] for f in new), 2)
        return {"reservation_id": reservation_id, "flights": r["flights"], "total": r["total"]}

    def cancel_reservation(self, reservation_id: str) -> dict[str, Any]:
        """Cancel an active reservation and refund the total to the original payment method."""
        r = self._get("reservations", reservation_id, "reservation")
        self._require_verified(r["user_id"])
        if r["status"] == "flown":
            raise ToolError("PolicyViolation: cannot cancel a flown reservation")
        if r["status"] != "active":
            raise ToolError(f"reservation is already {r['status']}")
        r["status"] = "cancelled"
        r["refund"] = r["total"]
        return {"reservation_id": reservation_id, "status": "cancelled", "refund": r["total"]}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel a pending order. Delivered orders must be returned instead."""
        o = self._get("orders", order_id, "order")
        self._require_verified(o["user_id"])
        if o["status"] != "pending":
            raise ToolError(f"PolicyViolation: cannot cancel an order that is {o['status']}")
        o["status"] = "cancelled"
        o["refund"] = round(sum(i["price"] for i in o["items"]), 2)
        return {"order_id": order_id, "status": "cancelled", "refund": o["refund"]}

    def return_items(
        self, order_id: str, item_ids: list[str], payment_method_id: str
    ) -> dict[str, Any]:
        """Request a return for items of a delivered order; refund to the given payment method."""
        o = self._get("orders", order_id, "order")
        self._require_verified(o["user_id"])
        if o["status"] != "delivered":
            raise ToolError(f"cannot return items of an order that is {o['status']}")
        self._require_pm(o["user_id"], payment_method_id)
        prices = {i["item_id"]: i["price"] for i in o["items"]}
        if not item_ids or any(i not in prices for i in item_ids):
            raise ToolError("item not in order")
        o["status"] = "return_requested"
        o["return_items"] = sorted(item_ids)
        o["refund"] = round(sum(prices[i] for i in item_ids), 2)
        o["refund_payment_method_id"] = payment_method_id
        return {"order_id": order_id, "status": o["status"], "refund": o["refund"]}

    def exchange_items(
        self, order_id: str, item_ids: list[str], new_item_ids: list[str], payment_method_id: str
    ) -> dict[str, Any]:
        """Exchange delivered items for available variants of the same product."""
        o = self._get("orders", order_id, "order")
        self._require_verified(o["user_id"])
        if o["status"] != "delivered":
            raise ToolError(f"cannot exchange items of an order that is {o['status']}")
        self._require_pm(o["user_id"], payment_method_id)
        items = {i["item_id"]: i for i in o["items"]}
        if not item_ids or len(item_ids) != len(new_item_ids):
            raise ToolError("item_ids and new_item_ids must have the same length")
        diff = 0.0
        for old_id, new_id in zip(item_ids, new_item_ids, strict=True):
            if old_id not in items:
                raise ToolError(f"item {old_id} not in order")
            variants = self.state["products"][items[old_id]["product_id"]]["variants"]
            if new_id not in variants:
                raise ToolError(f"item {new_id} is not a variant of the same product")
            if not variants[new_id]["available"]:
                raise ToolError(f"item {new_id} is not available")
            diff += variants[new_id]["price"] - items[old_id]["price"]
        o["status"] = "exchange_requested"
        o["exchange"] = dict(zip(item_ids, new_item_ids, strict=True))
        o["price_difference"] = round(diff, 2)
        return {"order_id": order_id, "status": o["status"], "price_difference": round(diff, 2)}

    def issue_refund(self, user_id: str, amount: float, reason: str) -> dict[str, Any]:
        """Issue a goodwill refund/compensation to a user (max $200). Gated by flag tool-issue-refund."""
        self._get("users", user_id, "user")
        self._require_verified(user_id)
        if amount <= 0 or amount > REFUND_CAP_USD:
            raise ToolError(f"PolicyViolation: refund must be in (0, {REFUND_CAP_USD:.0f}]")
        self.state["refunds"].append({"user_id": user_id, "amount": round(float(amount), 2)})
        return {"user_id": user_id, "refunded": round(float(amount), 2), "reason": reason}

    def transfer_to_human(self, user_id: str, summary: str) -> dict[str, Any]:
        """Transfer the conversation to a human agent when the request is out of policy scope."""
        self.state["transfers"].append(user_id)
        return {"transferred": True, "summary": summary}

    # dispatch
    def tools(self) -> dict[str, Callable[..., Any]]:
        return {name: getattr(self, name) for name in TOOL_NAMES}

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        fn = self.tools().get(name)
        if fn is None:
            raise ToolError(f"unknown tool {name}")
        try:
            return fn(**arguments)
        except TypeError as e:
            raise ToolError(f"bad arguments: {e}") from e


TOOL_NAMES: tuple[str, ...] = (
    "verify_identity",
    "get_user",
    "get_reservation",
    "search_flights",
    "update_reservation_flights",
    "cancel_reservation",
    "get_order",
    "get_product",
    "cancel_order",
    "return_items",
    "exchange_items",
    "issue_refund",
    "transfer_to_human",
)
FLAGGED_TOOLS: dict[str, str] = {"issue_refund": "tool-issue-refund"}


def apply_actions(initial_state: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay ground-truth actions on a fresh env; errors are ignored (refusal tasks)."""
    env = TauEnv(initial_state)
    for a in actions:
        with contextlib.suppress(ToolError):
            env.call(a["tool"], a.get("arguments", {}))
    return env.state
