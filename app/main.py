"""Dispute Defender web app (FastAPI)."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import AgentError, DisputeAgent, plan_to_evidences
from .config import ROOT, settings
from .heuristic import heuristic_plan
from .packet import build_packet
from .paypal import MockPayPal, PayPalClient, PayPalError
from .seed import build_seed
from .store import MerchantStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dispute-defender")

seed = build_seed()
store = MerchantStore(seed, settings.data_dir / "store.json")
paypal = (PayPalClient(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_base_url,
                       settings.paypal_webhook_id)
          if settings.paypal_live else MockPayPal(seed["disputes"]))
agent = DisputeAgent(paypal, store) if settings.ai_enabled else None
pool = ThreadPoolExecutor(max_workers=4)

# dispute_id -> {"status": idle|analyzing|ready|error|responded, "plan", "engine", "trace", "actions", ...}
cases: dict[str, dict] = {}
cases_lock = threading.Lock()

CATALOG = [
    {"sku": "MUG-SPKL-1", "name": "Speckled Mug", "price": "29.00", "img": "mug"},
    {"sku": "BWL-SERVE-L", "name": "Large Serving Bowl - Ash Glaze", "price": "96.00", "img": "bowl"},
    {"sku": "VAS-TALL-SAGE", "name": "Tall Sage Vase", "price": "148.00", "img": "vase"},
    {"sku": "PLN-HANG-S", "name": "Hanging Planter - Small", "price": "48.00", "img": "planter"},
]

app = FastAPI(title="Dispute Defender")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _case(dispute_id: str) -> dict:
    with cases_lock:
        return cases.setdefault(dispute_id, {"status": "idle", "plan": None, "engine": None, "trace": [], "actions": []})


def _paypal_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except PayPalError as e:
        raise HTTPException(status_code=502 if e.status >= 500 else e.status,
                            detail={"paypal_status": e.status, "paypal_error": e.body})


# ---- analysis -------------------------------------------------------------------------------
def _run_analysis(dispute_id: str) -> None:
    c = _case(dispute_id)
    emit = lambda e: c["trace"].append({**e, "t": _now()})
    try:
        if agent:
            emit({"type": "status", "text": f"Claude ({settings.anthropic_model}) is investigating"})
            plan, engine = agent.analyze(dispute_id, emit), "claude"
        else:
            emit({"type": "status", "text": "No ANTHROPIC_API_KEY: using the rules engine"})
            plan, engine = heuristic_plan(paypal.get_dispute(dispute_id), store), "rules"
        c.update(plan=plan, engine=engine, status="ready", analyzed_at=_now())
        emit({"type": "done", "recommendation": plan["recommendation"]})
    except (AgentError, PayPalError, Exception) as e:  # surface every failure in the UI rather than hang
        log.exception("analysis failed for %s", dispute_id)
        c.update(status="error", error=str(e))
        emit({"type": "error", "text": str(e)})


def start_analysis(dispute_id: str, force: bool = False) -> bool:
    c = _case(dispute_id)
    with cases_lock:
        if c["status"] == "analyzing" or (c["status"] in ("ready", "responded") and not force):
            return False
        c.update(status="analyzing", trace=[], error=None)
    pool.submit(_run_analysis, dispute_id)
    return True


# ---- read API --------------------------------------------------------------------------------
@app.get("/api/meta")
def meta():
    return {
        "paypal_mode": paypal.mode,
        "paypal_client_id": settings.paypal_client_id if settings.paypal_live else None,
        "ai_engine": "claude" if agent else "rules",
        "model": settings.anthropic_model if agent else None,
        "seller": store.data["seller"],
    }


def _row(d: dict) -> dict:
    tx = (d.get("disputed_transactions") or [{}])[0]
    order = store.find_order(tx.get("seller_transaction_id"), tx.get("invoice_number"))
    c = _case(d["dispute_id"])
    p = c["plan"] or {}
    return {
        "dispute_id": d["dispute_id"],
        "buyer": tx.get("buyer", {}).get("name") or (order or {}).get("customer", {}).get("name"),
        "order_id": (order or {}).get("order_id"),
        "reason": d["reason"], "amount": float(d["dispute_amount"]["value"]),
        "currency": d["dispute_amount"]["currency_code"], "stage": d.get("dispute_life_cycle_stage"),
        "status": d["status"], "due": d.get("seller_response_due_date"), "created": d.get("create_time"),
        "outcome": (d.get("dispute_outcome") or {}).get("outcome_code"),
        "case_status": c["status"], "engine": c["engine"],
        "recommendation": p.get("recommendation"), "win_probability": p.get("win_probability"),
        "headline": p.get("headline"),
    }


@app.get("/api/disputes")
def list_disputes():
    ids = [s["dispute_id"] for s in _paypal_call(paypal.list_disputes)]
    ids += [i for i in store.known_disputes if i not in ids]

    def fetch(dispute_id):
        try:
            return paypal.get_dispute(dispute_id)
        except PayPalError:
            return None

    rows = [_row(d) for d in pool.map(fetch, ids) if d]
    return sorted(rows, key=lambda r: (r["status"] == "RESOLVED", r["due"] or "9999"))


@app.get("/api/disputes/{dispute_id}")
def get_dispute(dispute_id: str):
    d = _paypal_call(paypal.get_dispute, dispute_id)
    tx = d["disputed_transactions"][0]
    order = store.find_order(tx.get("seller_transaction_id"), tx.get("invoice_number"))
    oid = order["order_id"] if order else None
    return {
        "dispute": d, "order": order,
        "shipment": store.shipment(oid) if oid else None,
        "inbox": store.messages(oid) if oid else [],
        "case": _case(dispute_id), "row": _row(d),
    }


@app.get("/api/paypal-log")
def paypal_log():
    return paypal.calls[-60:][::-1]


# ---- agent ----------------------------------------------------------------------------------
@app.post("/api/disputes/{dispute_id}/analyze")
async def analyze(dispute_id: str, force: bool = False):
    """Start (or attach to) an analysis and stream its trace as server-sent events."""
    start_analysis(dispute_id, force=force)
    c = _case(dispute_id)

    async def events():
        sent = 0
        while True:
            trace = c["trace"]
            while sent < len(trace):
                yield f"data: {json.dumps(trace[sent], default=str)}\n\n"
                sent += 1
            if c["status"] != "analyzing" and sent >= len(c["trace"]):
                yield f"data: {json.dumps({'type': 'end', 'case': c}, default=str)}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/analyze-all")
def analyze_all():
    started = [r["dispute_id"] for r in list_disputes()
               if r["status"] != "RESOLVED" and start_analysis(r["dispute_id"])]
    return {"started": started}


class PlanEdit(BaseModel):
    recommendation: str | None = None
    seller_statement: str | None = None
    buyer_message: str | None = None
    evidence: list[dict] | None = None
    offer: dict | None = None
    accept_claim_type: str | None = None


@app.put("/api/disputes/{dispute_id}/plan")
def edit_plan(dispute_id: str, edit: PlanEdit):
    c = _case(dispute_id)
    if not c["plan"]:
        raise HTTPException(409, "Analyze the dispute first.")
    c["plan"].update({k: v for k, v in edit.model_dump().items() if v is not None})
    c["plan"]["edited_by_merchant"] = True
    return c


@app.get("/api/disputes/{dispute_id}/packet.pdf")
def packet(dispute_id: str):
    c = _case(dispute_id)
    if not c["plan"]:
        raise HTTPException(409, "Analyze the dispute first.")
    pdf = build_packet(_paypal_call(paypal.get_dispute, dispute_id), c["plan"], store)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{dispute_id}-evidence.pdf"'})


# ---- respond via PayPal -----------------------------------------------------------------------
@app.post("/api/disputes/{dispute_id}/respond")
def respond(dispute_id: str):
    """Execute the approved plan against the PayPal Disputes API."""
    c = _case(dispute_id)
    plan = c["plan"]
    if not plan:
        raise HTTPException(409, "Analyze the dispute first.")
    d = _paypal_call(paypal.get_dispute, dispute_id)
    rec, results = plan["recommendation"], []

    if rec == "FIGHT":
        evidences = plan_to_evidences(plan)
        pdf = build_packet(d, plan, store)
        results.append(("provide-evidence", _paypal_call(
            paypal.provide_evidence, dispute_id, evidences, [(f"{dispute_id}-evidence.pdf", pdf, "application/pdf")])))
    elif rec == "OFFER":
        offer = plan.get("offer") or {"offer_type": "REFUND", "amount": d["dispute_amount"]["value"]}
        results.append(("make-offer", _paypal_call(
            paypal.make_offer, dispute_id, plan.get("buyer_message") or plan["headline"], offer["offer_type"],
            {"currency_code": d["dispute_amount"]["currency_code"], "value": offer["amount"]})))
    elif rec == "ACCEPT":
        results.append(("accept-claim", _paypal_call(
            paypal.accept_claim, dispute_id, plan.get("buyer_message") or "Claim accepted.",
            plan.get("accept_claim_type") or "REFUND")))
    else:
        raise HTTPException(400, f"Unknown recommendation {rec}")

    if rec == "FIGHT" and plan.get("buyer_message"):
        try:
            results.append(("send-message", paypal.send_message(dispute_id, plan["buyer_message"])))
        except PayPalError as e:  # messaging is optional (not allowed at every stage); don't fail the response
            results.append(("send-message", {"skipped": str(e)}))

    c["status"] = "responded"
    c["actions"].append({"time": _now(), "action": rec, "calls": [r[0] for r in results]})
    return {"results": [{"call": n, "response": r} for n, r in results], "dispute": paypal.get_dispute(dispute_id)}


class Ruling(BaseModel):
    outcome: str  # SELLER_FAVOR | BUYER_FAVOR


@app.post("/api/disputes/{dispute_id}/simulate-ruling")
def simulate_ruling(dispute_id: str, ruling: Ruling):
    """Sandbox-only: ask PayPal to adjudicate a dispute that is under review."""
    _paypal_call(paypal.adjudicate, dispute_id, ruling.outcome)
    return paypal.get_dispute(dispute_id)


# ---- webhooks -----------------------------------------------------------------------------------
def handle_event(event: dict) -> dict:
    etype = event.get("event_type", "")
    dispute_id = (event.get("resource") or {}).get("dispute_id")
    if etype in ("CUSTOMER.DISPUTE.CREATED", "CUSTOMER.DISPUTE.UPDATED") and dispute_id:
        store.add_known_dispute(dispute_id)
        started = start_analysis(dispute_id, force=etype.endswith("UPDATED"))
        return {"dispute_id": dispute_id, "analysis_started": started}
    return {"ignored": etype}


@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request):
    event = await request.json()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not await asyncio.to_thread(paypal.verify_webhook, headers, event):
        raise HTTPException(400, "Webhook signature verification failed")
    return handle_event(event)


class TrackDispute(BaseModel):
    dispute_id: str


@app.post("/api/disputes/track")
def track_dispute(t: TrackDispute):
    """Add a dispute to the queue by ID, for when it hasn't reached the list endpoint (or a webhook) yet."""
    d = _paypal_call(paypal.get_dispute, t.dispute_id.strip())
    store.add_known_dispute(d["dispute_id"])
    return {"dispute_id": d["dispute_id"], "analysis_started": start_analysis(d["dispute_id"])}


@app.post("/api/simulate/dispute")
def simulate_dispute():
    if not isinstance(paypal, MockPayPal):
        raise HTTPException(400, "In sandbox mode, open a real dispute from your sandbox buyer account.")
    event = paypal.simulate_incoming()
    if not event:
        raise HTTPException(404, "No more queued demo disputes.")
    return {"webhook_event": event, **handle_event(event)}


# ---- sandbox shop (Orders API v2) ------------------------------------------------------------
class ShopOrder(BaseModel):
    sku: str
    qty: int = 1


@app.get("/api/shop/catalog")
def catalog():
    return CATALOG


@app.post("/api/shop/orders")
def shop_create(o: ShopOrder):
    item = next((c for c in CATALOG if c["sku"] == o.sku), None)
    if not item:
        raise HTTPException(404, "Unknown SKU")
    invoice = f"FW-S{uuid.uuid4().hex[:6].upper()}"
    created = _paypal_call(paypal.create_order, invoice, [{**item, "qty": o.qty}], "8.00")
    return {"id": created["id"], "invoice_id": invoice}


@app.post("/api/shop/orders/{order_id}/capture")
def shop_capture(order_id: str):
    cap = _paypal_call(paypal.capture_order, order_id)
    pu = cap["purchase_units"][0]
    capture = pu["payments"]["captures"][0]
    payer = cap.get("payer", {})
    addr = (pu.get("shipping") or {}).get("address", {})
    items = pu.get("items") or []
    store.add_shop_order({
        "order_id": capture.get("invoice_id") or pu.get("reference_id"),
        "paypal_order_id": cap["id"], "capture_id": capture["id"], "created": _now(), "source": "sandbox_shop",
        "customer": {"name": " ".join((payer.get("name") or {}).values()), "email": payer.get("email_address", "")},
        "ship_to": ", ".join(v for k, v in addr.items() if k != "country_code"),
        "items": [{"sku": i.get("sku", ""), "name": i["name"], "qty": int(i.get("quantity", 1)),
                   "price": i.get("unit_amount", {}).get("value", "")} for i in items],
        "shipping": "8.00", "total": capture["amount"]["value"], "status": "PAID",
    })
    return {"status": cap["status"], "capture_id": capture["id"]}


# ---- static -----------------------------------------------------------------------------------
@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    # Browsers otherwise cache app.js heuristically and keep serving an old UI after a deploy.
    response = await call_next(request)
    if request.url.path in ("/", "/shop") or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/shop")
def shop():
    return FileResponse(ROOT / "static" / "shop.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.exception_handler(PayPalError)
async def paypal_error(_: Request, e: PayPalError):
    return JSONResponse({"paypal_status": e.status, "paypal_error": e.body}, status_code=502)
