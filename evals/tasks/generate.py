"""Deterministic generator for the 50-task tau-style eval set. `python evals/tasks/generate.py`."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from adcp.env.tools import MUTATING, TauEnv, ToolError, empty_db

SEED = 20261102
OUT = Path(__file__).with_name("tasks.jsonl")

FIRST = [
    "mia",
    "noah",
    "ava",
    "liam",
    "zoe",
    "ethan",
    "isla",
    "omar",
    "lena",
    "raj",
    "sofia",
    "kai",
]
LAST = ["chen", "garcia", "patel", "kim", "nguyen", "silva", "khan", "rossi", "berg", "okafor"]
AIRPORTS = ["JFK", "SFO", "ORD", "SEA", "BOS", "ATL", "DEN", "LAX", "MIA", "AUS"]
PRODUCTS = {
    "headphones": [("black", 129.99), ("white", 129.99), ("blue", 139.99)],
    "water bottle": [("500ml", 19.99), ("750ml", 24.99), ("1l", 29.99)],
    "desk lamp": [("led", 49.99), ("halogen", 39.99), ("smart", 69.99)],
    "running shoes": [("size 9", 89.99), ("size 10", 89.99), ("size 11", 94.99)],
    "backpack": [("grey", 59.99), ("green", 59.99), ("navy", 64.99)],
}

# (kind, domain, count)
PLAN: list[tuple[str, str, int]] = [
    ("cancel_reservation", "airline", 6),
    ("change_flight", "airline", 6),
    ("cancel_flown", "airline", 4),
    ("reservation_status", "airline", 4),
    ("delay_compensation", "airline", 4),
    ("cancel_order", "retail", 6),
    ("return_items", "retail", 6),
    ("exchange_items", "retail", 5),
    ("order_status", "retail", 3),
    ("cancel_delivered", "retail", 3),
    ("transfer", "retail", 3),
]


def _user(rng: random.Random, db: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    uid = f"{first}_{last}_{rng.randint(1000, 9999)}"
    cc, gc = f"credit_card_{rng.randint(1000, 9999)}", f"gift_card_{rng.randint(1000, 9999)}"
    user = {
        "name": f"{first.title()} {last.title()}",
        "email": f"{first}.{last}{rng.randint(1, 99)}@example.com",
        "zip": f"{rng.randint(10000, 99999)}",
        "payment_methods": {cc: {"type": "credit_card"}, gc: {"type": "gift_card"}},
    }
    db["users"][uid] = user
    return uid, user


def _airline_db(rng: random.Random) -> tuple[dict[str, Any], str, dict[str, Any]]:
    db = empty_db()
    uid, user = _user(rng, db)
    pm = next(iter(user["payment_methods"]))
    o, d = rng.sample(AIRPORTS, 2)
    o2, d2 = rng.sample([a for a in AIRPORTS if a not in (o, d)], 2)
    day = rng.randint(3, 20)
    for i, (orig, dest, dd) in enumerate(
        [(o, d, day), (o, d, day + 2), (o, d, day + 2), (o, d, day + 2), (o2, d2, day - 1)]
    ):
        fno = f"HAT{rng.randint(100, 999)}{i}"
        db["flights"][fno] = {
            "origin": orig,
            "destination": dest,
            "date": f"2026-11-{dd:02d}",
            "price": float(rng.randint(120, 480)),
            "status": "landed" if orig == o2 else "scheduled",
        }
    fl = list(db["flights"])
    r1, r2 = f"R{rng.randint(10000, 99999)}", f"R{rng.randint(10000, 99999)}"
    db["reservations"][r1] = {
        "user_id": uid,
        "flights": [fl[0]],
        "cabin": rng.choice(["economy", "business"]),
        "status": "active",
        "total": db["flights"][fl[0]]["price"],
        "payment_method_id": pm,
    }
    db["reservations"][r2] = {
        "user_id": uid,
        "flights": [fl[4]],
        "cabin": "economy",
        "status": "flown",
        "total": db["flights"][fl[4]]["price"],
        "payment_method_id": pm,
    }
    return db, uid, {"r1": r1, "r2": r2, "flights": fl}


def _retail_db(rng: random.Random) -> tuple[dict[str, Any], str, dict[str, Any]]:
    db = empty_db()
    uid, _ = _user(rng, db)
    names = rng.sample(sorted(PRODUCTS), 3)
    item_of: dict[str, list[str]] = {}
    for name in names:
        pid = f"P{rng.randint(1000, 9999)}"
        variants = {}
        for opt, price in PRODUCTS[name]:
            iid = f"I{rng.randint(100000, 999999)}"
            variants[iid] = {"option": opt, "price": price, "available": True}
        db["products"][pid] = {"name": name, "variants": variants}
        item_of[name] = [pid, *variants]
    pms = list(db["users"][uid]["payment_methods"])

    def line(name: str, k: int) -> dict[str, Any]:
        pid, iid = item_of[name][0], item_of[name][1 + k]
        return {
            "item_id": iid,
            "product_id": pid,
            "name": name,
            "price": db["products"][pid]["variants"][iid]["price"],
        }

    o1, o2 = f"#W{rng.randint(1000000, 9999999)}", f"#W{rng.randint(1000000, 9999999)}"
    db["orders"][o1] = {
        "user_id": uid,
        "status": "pending",
        "items": [line(names[0], 0)],
        "payment_method_id": pms[0],
    }
    db["orders"][o2] = {
        "user_id": uid,
        "status": "delivered",
        "items": [line(names[1], 0), line(names[2], 1)],
        "payment_method_id": pms[0],
    }
    return db, uid, {"o1": o1, "o2": o2, "names": names, "items": item_of, "pms": pms}


def _ident(uid: str, db: dict[str, Any]) -> str:
    u = db["users"][uid]
    return f"Hi, I'm {u['name']} (user id {uid}, email {u['email']})."


def make_task(kind: str, domain: str, idx: int, rng: random.Random) -> dict[str, Any]:
    if domain == "airline":
        db, uid, k = _airline_db(rng)
    else:
        db, uid, k = _retail_db(rng)
    email = db["users"][uid]["email"]
    verify = {"tool": "verify_identity", "arguments": {"user_id": uid, "email": email}}
    hi = _ident(uid, db)
    acts: list[dict[str, Any]]
    tags = [domain, kind]
    if kind == "cancel_reservation":
        r1 = k["r1"]
        text = f"{hi} Please cancel reservation {r1}, my plans changed."
        acts = [
            verify,
            {"tool": "get_reservation", "arguments": {"reservation_id": r1}},
            {"tool": "cancel_reservation", "arguments": {"reservation_id": r1}},
        ]
        outs = [r1, "cancelled", f"{db['reservations'][r1]['total']:.2f}"]
    elif kind == "change_flight":
        r1, fl = k["r1"], k["flights"]
        f0 = db["flights"][fl[0]]
        alts = sorted(fl[1:4], key=lambda f: (db["flights"][f]["price"], f))
        target = alts[0]
        date = db["flights"][target]["date"]
        text = f"{hi} Move reservation {r1} to the cheapest flight on {date}, same route."
        acts = [
            verify,
            {"tool": "get_reservation", "arguments": {"reservation_id": r1}},
            {
                "tool": "search_flights",
                "arguments": {
                    "origin": f0["origin"],
                    "destination": f0["destination"],
                    "date": date,
                },
            },
            {
                "tool": "update_reservation_flights",
                "arguments": {"reservation_id": r1, "flight_numbers": [target]},
            },
        ]
        outs = [target]
    elif kind == "cancel_flown":
        r2 = k["r2"]
        text = f"{hi} Cancel reservation {r2} and refund me."
        acts = [verify, {"tool": "get_reservation", "arguments": {"reservation_id": r2}}]
        outs = ["cannot", r2]
        tags.append("refusal")
    elif kind == "reservation_status":
        r1 = k["r1"]
        text = f"{hi} Which flight is reservation {r1} on, and is it still active?"
        acts = [{"tool": "get_reservation", "arguments": {"reservation_id": r1}}]
        outs = [db["reservations"][r1]["flights"][0], "active"]
        tags.append("read_only")
    elif kind == "delay_compensation":
        r1 = k["r1"]
        f = db["reservations"][r1]["flights"][0]
        db["flights"][f]["status"] = "delayed"
        text = f"{hi} Flight {f} on reservation {r1} was delayed 5 hours. I want compensation."
        acts = [
            verify,
            {"tool": "get_reservation", "arguments": {"reservation_id": r1}},
            {
                "tool": "issue_refund",
                "arguments": {"user_id": uid, "amount": 100.0, "reason": "delay compensation"},
            },
        ]
        outs = ["$100"]
        tags.append("new_tool")
    elif kind == "cancel_order":
        o1 = k["o1"]
        text = f"{hi} Please cancel order {o1}, I no longer need it."
        acts = [
            verify,
            {"tool": "get_order", "arguments": {"order_id": o1}},
            {"tool": "cancel_order", "arguments": {"order_id": o1}},
        ]
        outs = [o1, "cancelled"]
    elif kind == "return_items":
        o2 = k["o2"]
        item = db["orders"][o2]["items"][rng.randint(0, 1)]
        pm = k["pms"][1]
        text = f"{hi} I want to return the {item['name']} from order {o2}; refund to my gift card."
        acts = [
            verify,
            {"tool": "get_order", "arguments": {"order_id": o2}},
            {
                "tool": "return_items",
                "arguments": {
                    "order_id": o2,
                    "item_ids": [item["item_id"]],
                    "payment_method_id": pm,
                },
            },
        ]
        outs = [f"{item['price']:.2f}", "return"]
    elif kind == "exchange_items":
        o2 = k["o2"]
        item = db["orders"][o2]["items"][0]
        variants = db["products"][item["product_id"]]["variants"]
        new_id = next(i for i in variants if i != item["item_id"])
        opt = variants[new_id]["option"]
        text = f"{hi} Exchange the {item['name']} in order {o2} for the {opt} version."
        acts = [
            verify,
            {"tool": "get_order", "arguments": {"order_id": o2}},
            {"tool": "get_product", "arguments": {"product_id": item["product_id"]}},
            {
                "tool": "exchange_items",
                "arguments": {
                    "order_id": o2,
                    "item_ids": [item["item_id"]],
                    "new_item_ids": [new_id],
                    "payment_method_id": k["pms"][0],
                },
            },
        ]
        outs = ["exchange", opt]
    elif kind == "order_status":
        o1 = k["o1"]
        text = f"{hi} What's the status of order {o1}?"
        acts = [{"tool": "get_order", "arguments": {"order_id": o1}}]
        outs = [o1, "pending"]
        tags.append("read_only")
    elif kind == "cancel_delivered":
        o2 = k["o2"]
        text = f"{hi} Cancel order {o2} please."
        acts = [verify, {"tool": "get_order", "arguments": {"order_id": o2}}]
        outs = ["cannot", "return"]
        tags.append("refusal")
    elif kind == "transfer":
        text = f"{hi} I'm filing a legal complaint about my order and want a supervisor now."
        acts = [
            {
                "tool": "transfer_to_human",
                "arguments": {"user_id": uid, "summary": "legal complaint; requests supervisor"},
            }
        ]
        outs = ["human agent"]
        tags.append("escalation")
    else:
        raise ValueError(kind)
    _check(db, acts, kind)
    return {
        "id": f"{domain}-{idx:03d}",
        "domain": domain,
        "user_input": text,
        "initial_state": db,
        "expected_actions": acts,
        "expected_outputs": outs,
        "tags": tags,
    }


def _check(db: dict[str, Any], acts: list[dict[str, Any]], kind: str) -> None:
    env = TauEnv(db)
    for a in acts:
        try:
            env.call(a["tool"], a["arguments"])
        except ToolError as e:
            raise AssertionError(f"{kind}: ground truth action failed: {a} -> {e}") from e
    assert any(a["tool"] in MUTATING for a in acts) or kind in {
        "cancel_flown",
        "reservation_status",
        "order_status",
        "cancel_delivered",
        "transfer",
    }


def generate(seed: int = SEED) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    tasks, counters = [], {"airline": 0, "retail": 0}
    for kind, domain, n in PLAN:
        for _ in range(n):
            counters[domain] += 1
            tasks.append(make_task(kind, domain, counters[domain], rng))
    return tasks


def main() -> None:
    tasks = generate()
    OUT.write_text("".join(json.dumps(t, sort_keys=True) + "\n" for t in tasks))
    print(f"wrote {len(tasks)} tasks -> {OUT}")


if __name__ == "__main__":
    main()
