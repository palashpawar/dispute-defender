"""The Dispute Defender agent: Claude investigates a PayPal dispute and drafts the response.

Claude gets read-only tools over the PayPal dispute and the merchant's own records, decides
whether to FIGHT, OFFER or ACCEPT, and returns a structured case plan through the
`submit_case_plan` tool. Nothing is sent to PayPal here: the merchant reviews the plan in the
UI and approves the submission, which `main.py` turns into Disputes API calls.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

import anthropic

from .config import settings
from .paypal import PayPalError
from .store import MerchantStore

log = logging.getLogger(__name__)

EVIDENCE_TYPES = [
    "PROOF_OF_FULFILLMENT", "PROOF_OF_DELIVERY_SIGNATURE", "ONLINE_TRACKING_INFORMATION", "PROOF_OF_REFUND",
    "PROOF_OF_RECEIPT_COPY", "RETURN_POLICY", "BILLING_AGREEMENT", "ITEM_DESCRIPTION", "DETAILS_OF_PURCHASE",
    "ORDER_DETAILS", "DELIVERY_ADDRESS", "CANCELLATION_DETAILS", "PROOF_FOR_SOFTWARE_OR_SERVICE_DELIVERED",
    "OTHER",  # MERCHANT_RESPONSE is added from seller_statement in plan_to_evidences
]
POLICY_TOPICS = ["returns", "shipping", "handmade", "workshops", "subscriptions"]


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


_S, _NS = {"type": "string"}, {"type": ["string", "null"]}

CASE_PLAN_SCHEMA = _obj({
    "recommendation": {"type": "string", "enum": ["FIGHT", "OFFER", "ACCEPT"],
                       "description": "FIGHT = submit evidence; OFFER = propose a resolution; ACCEPT = refund the claim."},
    "win_probability": {"type": "number", "description": "Estimated probability (0-1) PayPal rules for the seller if contested."},
    "headline": {"type": "string", "description": "One sentence a busy merchant can read in the queue."},
    "reasoning": {"type": "string", "description": "3-5 sentences: why this recommendation, citing the facts."},
    "key_facts": {"type": "array", "items": _obj({
        "fact": _S, "source": {"type": "string", "description": "Where it came from, e.g. 'UPS tracking', 'support inbox 10/14'."},
        "supports": {"type": "string", "enum": ["SELLER", "BUYER", "NEUTRAL"]}})},
    "evidence": {"type": "array", "description": "Evidence items to submit to PayPal (empty unless FIGHT).", "items": _obj({
        "evidence_type": {"type": "string", "enum": EVIDENCE_TYPES},
        "notes": {"type": "string", "description": "What this item proves, max ~500 chars."},
        "carrier_name": {**_NS, "description": "UPS, USPS, FEDEX... only for tracking evidence."},
        "tracking_number": _NS,
        "refund_id": {**_NS, "description": "Only for PROOF_OF_REFUND."}})},
    "seller_statement": {"type": "string", "description": "The merchant's written response to PayPal (under 2000 chars), factual and calm."},
    "buyer_message": {"type": "string", "description": "A short, polite message to the buyer consistent with the recommendation."},
    "offer": {"anyOf": [{"type": "null"}, _obj({
        "offer_type": {"type": "string", "enum": ["REFUND", "REFUND_WITH_RETURN", "REFUND_WITH_REPLACEMENT"]},
        "amount": {"type": "string", "description": "USD, e.g. '33.00'"}})]},
    "accept_claim_type": {"anyOf": [{"type": "null"}, {"type": "string", "enum": ["REFUND", "REFUND_WITH_RETURN", "PARTIAL_REFUND"]}]},
    "risk_flags": {"type": "array", "items": _S, "description": "Weaknesses, missing evidence, or deadline risks."},
})

TOOLS = [
    {"name": "get_dispute", "description": "Fetch the full PayPal dispute (Disputes API v1): reason, amount, stage, deadline, buyer messages, transactions.",
     "input_schema": _obj({"dispute_id": _S})},
    {"name": "lookup_order", "description": "Find the merchant's order for a PayPal transaction. Pass the seller_transaction_id and/or the invoice_number (our order id) from the dispute.",
     "input_schema": _obj({"seller_transaction_id": _NS, "invoice_number": _NS})},
    {"name": "get_shipment_tracking", "description": "Carrier, tracking number, signature and scan history for an order.",
     "input_schema": _obj({"order_id": _S})},
    {"name": "get_support_messages", "description": "Customer support emails/DMs about an order (the buyer's own words, and our replies).",
     "input_schema": _obj({"order_id": _S})},
    {"name": "get_refunds", "description": "Refunds already issued for an order (with PayPal refund IDs).",
     "input_schema": _obj({"order_id": _S})},
    {"name": "get_customer_history", "description": "All orders from a customer email: addresses used, totals, outcomes.",
     "input_schema": _obj({"email": _S})},
    {"name": "get_fulfillment_records", "description": "Non-shipping fulfillment proof: workshop check-in logs, subscription renewal/cancellation events.",
     "input_schema": _obj({"order_id": _S})},
    {"name": "get_store_policy", "description": "The shop's published policy text for a topic.",
     "input_schema": _obj({"topic": {"type": "string", "enum": POLICY_TOPICS}})},
    {"name": "submit_case_plan", "description": "Submit your final case plan. Call exactly once, when the investigation is complete.",
     "input_schema": CASE_PLAN_SCHEMA},
]
for _t in TOOLS:
    _t["strict"] = True

SYSTEM = """You are Dispute Defender, a chargeback analyst working for a small online merchant that takes payments with PayPal.

For each PayPal dispute you investigate the merchant's records with your tools and decide the response that leaves the merchant best off:
- FIGHT when the records genuinely refute the claim. Draft evidence using PayPal evidence types that match the dispute reason (e.g. not-received -> tracking/delivery signature; unauthorised -> delivery to the account holder's usual address, purchase history, post-delivery contact; credit-not-processed -> PROOF_OF_REFUND with the refund id; duplicate -> ORDER_DETAILS showing two distinct orders).
- OFFER when the buyer has a partly valid point or a goodwill resolution is cheaper than losing (fees, time, seller metrics).
- ACCEPT when the merchant is at fault. Never contest a claim the evidence does not refute; that costs the merchant credibility with PayPal.

Rules:
- Start with get_dispute, then link it to the order and check every relevant record before deciding. Request independent lookups in parallel.
- Only cite facts that came from a tool result. If a record you would need is missing, say so in risk_flags and lower win_probability.
- win_probability is calibrated: delivery signature for an item-not-received case is strong (~0.85+); ambiguous or missing proof is below 0.5.
- seller_statement is what PayPal's reviewer reads: chronological, factual, references evidence items, no emotion, under 2000 characters.
- Mind the deadline (seller_response_due_date): flag anything due within 48 hours.
- Finish by calling submit_case_plan exactly once."""


class AgentError(Exception):
    pass


class DisputeAgent:
    def __init__(self, paypal, store: MerchantStore):
        self.paypal, self.store = paypal, store
        self.client = anthropic.Anthropic(max_retries=3, timeout=120.0)  # a stalled request retries instead of hanging the case

    # ---- tool execution -----------------------------------------------------------------
    def run_tool(self, name: str, args: dict) -> Any:
        s = self.store
        if name == "get_dispute":
            return self.paypal.get_dispute(args["dispute_id"])
        if name == "lookup_order":
            o = s.find_order(args.get("seller_transaction_id"), args.get("invoice_number"))
            return o or {"error": "No matching order in the merchant's records."}
        if name == "get_shipment_tracking":
            return s.shipment(args["order_id"]) or {"error": "No shipment on file (not shipped, or not a physical item)."}
        if name == "get_support_messages":
            return s.messages(args["order_id"])
        if name == "get_refunds":
            return s.refunds(args["order_id"])
        if name == "get_customer_history":
            return s.customer_history(args["email"])
        if name == "get_fulfillment_records":
            return s.fulfillment_records(args["order_id"])
        if name == "get_store_policy":
            return {"topic": args["topic"], "policy": s.policy(args["topic"])}
        raise AgentError(f"unknown tool {name}")

    # ---- agent loop ----------------------------------------------------------------------
    def _create(self, messages: list) -> Any:
        kw: dict = dict(
            model=settings.anthropic_model, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": settings.anthropic_effort},
        )
        if settings.use_fallbacks:
            try:
                return self.client.beta.messages.create(**kw, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            except anthropic.BadRequestError as e:
                if "fallback" not in str(e).lower():
                    raise
                log.warning("server-side fallbacks unavailable on this endpoint; continuing without them")
        return self.client.messages.create(**kw)

    def analyze(self, dispute_id: str, emit: Callable[[dict], None] = lambda e: None) -> dict:
        messages: list = [{"role": "user", "content": f"Investigate PayPal dispute {dispute_id} and submit a case plan."}]
        nudged = False
        for _ in range(14):
            resp = self._create(messages)
            if resp.stop_reason == "refusal":
                raise AgentError("The model declined this request.")
            for block in resp.content:
                if block.type == "thinking" and getattr(block, "thinking", ""):
                    emit({"type": "thought", "text": block.thinking})
                elif block.type == "text" and block.text.strip():
                    emit({"type": "thought", "text": block.text})
            calls = [b for b in resp.content if b.type == "tool_use"]
            messages.append({"role": "assistant", "content": resp.content})
            if not calls:
                if resp.stop_reason == "max_tokens" or nudged:
                    raise AgentError(f"Agent stopped without a case plan (stop_reason={resp.stop_reason}).")
                nudged = True
                messages.append({"role": "user", "content": "Please call submit_case_plan with your final plan now."})
                continue

            results, plan = [], None
            for call in calls:
                if call.name == "submit_case_plan":
                    plan = call.input
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": "Plan received."})
                    continue
                emit({"type": "tool", "name": call.name, "input": call.input})
                try:
                    out = self.run_tool(call.name, call.input)
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(out, default=str)})
                    emit({"type": "tool_result", "name": call.name, "summary": summarize(call.name, out)})
                except (PayPalError, AgentError) as e:
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": str(e), "is_error": True})
                    emit({"type": "tool_result", "name": call.name, "summary": f"error: {e}"})
            if plan is not None:
                return normalize_plan(plan)
            messages.append({"role": "user", "content": results})
        raise AgentError("Agent exceeded the step limit without a case plan.")


def summarize(name: str, out: Any) -> str:
    """One-line description of a tool result for the live trace in the UI."""
    if isinstance(out, dict) and "error" in out:
        return out["error"]
    if name == "get_dispute":
        return f"{out['reason']} · ${out['dispute_amount']['value']} · {out['dispute_life_cycle_stage']} · due {(out.get('seller_response_due_date') or 'n/a')[:10]}"
    if name == "lookup_order":
        return f"{out['order_id']} · {out['customer']['name']} · ${out['total']} · {len(out['items'])} item(s) · {out['status']}"
    if name == "get_shipment_tracking":
        last = out["events"][-1]["status"] if out.get("events") else "no scans"
        return f"{out['carrier']} {out['tracking_number']} · last scan: {last}"
    if name in ("get_support_messages", "get_refunds", "get_customer_history"):
        return f"{len(out)} record(s)"
    if name == "get_fulfillment_records":
        return ", ".join(k for k, v in out.items() if v) or "no records"
    if name == "get_store_policy":
        return f"{out['topic']} policy"
    return "ok"


def normalize_plan(plan: dict) -> dict:
    plan = dict(plan)
    plan["win_probability"] = max(0.0, min(1.0, float(plan.get("win_probability", 0))))
    plan["seller_statement"] = (plan.get("seller_statement") or "")[:2000]
    if plan["recommendation"] != "FIGHT":
        plan["evidence"] = plan.get("evidence") or []
    return plan


def plan_to_evidences(plan: dict) -> list[dict]:
    """Turn the case plan's evidence items into the PayPal provide-evidence `evidences` array."""
    out = []
    for e in plan.get("evidence", []):
        if e["evidence_type"] == "MERCHANT_RESPONSE":
            continue
        item: dict = {"evidence_type": e["evidence_type"], "notes": (e.get("notes") or "")[:2000]}
        info: dict = {}
        if e.get("tracking_number"):
            info["tracking_info"] = [{"carrier_name": (e.get("carrier_name") or "OTHER").upper(),
                                      "tracking_number": e["tracking_number"]}]
        if e.get("refund_id"):
            info["refund_ids"] = [e["refund_id"]]
        if info:
            item["evidence_info"] = info
        out.append(item)
    if plan.get("seller_statement"):
        out.append({"evidence_type": "MERCHANT_RESPONSE", "notes": plan["seller_statement"][:2000]})
    return out
