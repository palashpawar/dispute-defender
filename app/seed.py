"""Demo data: a small ceramics shop ("Fernwood Ceramics") and the disputes filed against it.

Everything is generated relative to "now" so due dates and tracking timelines stay realistic
whenever the demo is started. Dispute objects follow the PayPal Disputes API v1 schema; the
merchant-side records (orders, shipments, inbox, refunds) are what a real shop would have in
its own systems and are what the agent investigates.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


POLICIES = {
    "returns": (
        "Returns accepted within 30 days of delivery for unused items in original packaging. "
        "Buyer pays return shipping unless the item arrived damaged or was not as described, in which "
        "case we email a prepaid label. Refunds are issued within 3 business days of the return being "
        "received at our studio."
    ),
    "shipping": (
        "Orders ship within 2 business days via USPS Priority or UPS Ground with tracking. Orders over "
        "$150 require an adult signature on delivery. We are not responsible for packages marked "
        "delivered to the address the buyer provided at checkout, but we will help file a carrier claim."
    ),
    "handmade": (
        "Every piece is wheel-thrown and glazed by hand. Glaze color, speckling and dimensions vary up to "
        "10% from listing photos; this is disclosed on every product page and is not considered a defect. "
        "Cracks, chips and leaks are defects and are always covered."
    ),
    "workshops": (
        "Pottery workshop seats are non-refundable within 72 hours of the session. Seats can be moved to "
        "another date once at no charge. Attendance is recorded at check-in."
    ),
    "subscriptions": (
        "The Monthly Mug Club renews on the 1st. Members can cancel any time from their account page; "
        "cancellation stops the next renewal. A confirmation email is sent for every cancellation."
    ),
}


def build_seed(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)

    def d(days: float, hours: float = 0) -> str:
        return _iso(now - timedelta(days=days, hours=hours))

    def due(days: float) -> str:
        return _iso(now + timedelta(days=days))

    seller = {"email": "studio@fernwood-ceramics.example", "merchant_id": "FERNWOODDEMO1", "name": "Fernwood Ceramics"}

    orders = [
        # 1. INR - but delivered with adult signature. Strong fight.
        dict(order_id="FW-10482", paypal_order_id="5O190127TN364715T", capture_id="9TS74213BR558331X",
             created=d(19), customer={"name": "Marcus Hale", "email": "m.hale@example.com"},
             ship_to="418 Larkin St Apt 3, San Francisco, CA 94109",
             items=[{"sku": "VAS-TALL-SAGE", "name": "Tall Sage Vase", "qty": 1, "price": "148.00"},
                    {"sku": "BWL-NEST-3", "name": "Nesting Bowl Set (3)", "qty": 1, "price": "64.00"}],
             shipping="12.00", total="224.00", status="DELIVERED"),
        # 2. INR - lost in transit, tracking stalled. Should not fight; reship/refund.
        dict(order_id="FW-10511", paypal_order_id="7KD32945LS103284A", capture_id="2HL90832KD771045M",
             created=d(16), customer={"name": "Priya Raman", "email": "priya.r@example.com"},
             ship_to="77 Elm Ave, Montclair, NJ 07042",
             items=[{"sku": "MUG-SPKL-2", "name": "Speckled Mug Pair", "qty": 1, "price": "58.00"}],
             shipping="8.00", total="66.00", status="SHIPPED"),
        # 3. SNAD - cracked bowl, photo sent to support. Offer refund with return.
        dict(order_id="FW-10533", paypal_order_id="3BV82011MM449920K", capture_id="6FD12093NB334872P",
             created=d(13), customer={"name": "Elena Sokolova", "email": "elena.s@example.com"},
             ship_to="2210 N Halsted St, Chicago, IL 60614",
             items=[{"sku": "BWL-SERVE-L", "name": "Large Serving Bowl - Ash Glaze", "qty": 1, "price": "96.00"}],
             shipping="10.00", total="106.00", status="DELIVERED"),
        # 4. UNAUTHORISED - but repeat customer, same address, thanked us. Fight.
        dict(order_id="FW-10547", paypal_order_id="8NM29384PQ120938B", capture_id="4JK88210LM092384C",
             created=d(11), customer={"name": "Daniel Okafor", "email": "dokafor@example.com"},
             ship_to="15 Brattle St, Cambridge, MA 02138",
             items=[{"sku": "SET-DINNER-4", "name": "Dinnerware Set for 4 - Oat", "qty": 1, "price": "289.00"}],
             shipping="0.00", total="289.00", status="DELIVERED"),
        # 5. DUPLICATE_TRANSACTION - actually two different orders. Fight.
        dict(order_id="FW-10560", paypal_order_id="1QW73648RT902817D", capture_id="7PL33920QA118273E",
             created=d(9, 5), customer={"name": "Hannah Kim", "email": "hannahkim@example.com"},
             ship_to="903 Pike St, Seattle, WA 98101",
             items=[{"sku": "MUG-SPKL-1", "name": "Speckled Mug", "qty": 2, "price": "29.00"}],
             shipping="8.00", total="66.00", status="DELIVERED"),
        dict(order_id="FW-10561", paypal_order_id="1QW73648RT902818F", capture_id="7PL33920QA118274F",
             created=d(9, 4), customer={"name": "Hannah Kim", "email": "hannahkim@example.com"},
             ship_to="Rachel Kim, 44 Grove St, Portland, OR 97205",
             items=[{"sku": "PLN-HANG-S", "name": "Hanging Planter - Small", "qty": 1, "price": "48.00"},
                    {"sku": "MUG-SPKL-1", "name": "Speckled Mug", "qty": 1, "price": "29.00"}],
             shipping="9.00", total="86.00", status="DELIVERED"),
        # 6. CREDIT_NOT_PROCESSED - refund was already issued. Fight with proof of refund.
        dict(order_id="FW-10498", paypal_order_id="9ZX10293UV837465G", capture_id="3MN55102WE998123H",
             created=d(24), customer={"name": "Sofia Alvarez", "email": "sofia.alvarez@example.com"},
             ship_to="5120 Gaston Ave, Dallas, TX 75214",
             items=[{"sku": "TPT-STONE", "name": "Stoneware Teapot", "qty": 1, "price": "84.00"}],
             shipping="9.00", total="93.00", status="REFUNDED"),
        # 7. CREDIT_NOT_PROCESSED - return received, refund never sent (our fault). Accept.
        dict(order_id="FW-10502", paypal_order_id="2CV83746XY019283J", capture_id="5RT77310PO443812K",
             created=d(22), customer={"name": "James Whitfield", "email": "jwhitfield@example.com"},
             ship_to="88 Peachtree Pl NE, Atlanta, GA 30309",
             items=[{"sku": "PLT-SIDE-4", "name": "Side Plates (4) - Cobalt Rim", "qty": 1, "price": "72.00"}],
             shipping="9.00", total="81.00", status="RETURN_RECEIVED"),
        # 8. SNAD on a workshop seat - attended per check-in log. Fight.
        dict(order_id="FW-10570", paypal_order_id="6BN10928AS776102L", capture_id="8WE21093ZX554019M",
             created=d(8), customer={"name": "Olivia Brennan", "email": "obrennan@example.com"},
             ship_to="(in-person workshop - no shipping)",
             items=[{"sku": "WKS-WHEEL-INTRO", "name": "Intro to the Wheel - Saturday Workshop (2 seats)", "qty": 2, "price": "75.00"}],
             shipping="0.00", total="150.00", status="FULFILLED"),
        # 9. CANCELED_RECURRING_BILLING - cancellation came after renewal. Partial.
        dict(order_id="FW-10589", paypal_order_id="4HJ29384KL665012N", capture_id="1AS90213DF887034P",
             created=d(1, 10), customer={"name": "Noah Fischer", "email": "noah.fischer@example.com"},
             ship_to="301 W 5th St, Austin, TX 78701",
             items=[{"sku": "SUB-MUGCLUB-M", "name": "Monthly Mug Club - Renewal", "qty": 1, "price": "38.00"}],
             shipping="0.00", total="38.00", status="PROCESSING"),
    ]

    history = [  # prior orders from the same customers, used for account-history evidence
        dict(order_id="FW-09871", created=d(210), customer_email="dokafor@example.com", total="54.00",
             ship_to="15 Brattle St, Cambridge, MA 02138", status="DELIVERED"),
        dict(order_id="FW-10044", created=d(150), customer_email="dokafor@example.com", total="112.00",
             ship_to="15 Brattle St, Cambridge, MA 02138", status="DELIVERED"),
        dict(order_id="FW-10301", created=d(60), customer_email="dokafor@example.com", total="38.00",
             ship_to="15 Brattle St, Cambridge, MA 02138", status="DELIVERED"),
        dict(order_id="FW-10210", created=d(90), customer_email="m.hale@example.com", total="46.00",
             ship_to="418 Larkin St Apt 3, San Francisco, CA 94109", status="DELIVERED"),
        dict(order_id="FW-10320", created=d(55), customer_email="noah.fischer@example.com", total="38.00",
             ship_to="301 W 5th St, Austin, TX 78701", status="DELIVERED"),
    ]

    shipments = {
        "FW-10482": dict(carrier="UPS", tracking_number="1Z999AA10123456784", signature="M HALE",
                         events=[(d(18), "Label created", "Portland, OR"), (d(17), "Picked up", "Portland, OR"),
                                 (d(15), "Arrived at facility", "Oakland, CA"), (d(14, 3), "Out for delivery", "San Francisco, CA"),
                                 (d(14), "Delivered - signed by M HALE (front desk)", "San Francisco, CA")]),
        "FW-10511": dict(carrier="USPS", tracking_number="9405511899223197428490", signature=None,
                         events=[(d(15), "Shipping label created", "Portland, OR"), (d(14), "Accepted at USPS origin facility", "Portland, OR"),
                                 (d(12), "In transit to next facility", "Kearny, NJ"),
                                 (d(12), "Tracking stalled - no scans since this event", "Kearny, NJ")]),
        "FW-10533": dict(carrier="USPS", tracking_number="9405511899223197433112", signature=None,
                         events=[(d(12), "Accepted", "Portland, OR"), (d(9), "Delivered, in/at mailbox", "Chicago, IL")]),
        "FW-10547": dict(carrier="UPS", tracking_number="1Z999AA10123459921", signature="D OKAFOR",
                         events=[(d(10), "Picked up", "Portland, OR"), (d(6), "Delivered - signed by D OKAFOR", "Cambridge, MA")]),
        "FW-10560": dict(carrier="USPS", tracking_number="9405511899223197440021", signature=None,
                         events=[(d(8), "Accepted", "Portland, OR"), (d(6), "Delivered, front door", "Seattle, WA")]),
        "FW-10561": dict(carrier="USPS", tracking_number="9405511899223197440038", signature=None,
                         events=[(d(8), "Accepted", "Portland, OR"), (d(7), "Delivered, in/at mailbox", "Portland, OR")]),
        "FW-10498": dict(carrier="USPS", tracking_number="9405511899223197399870", signature=None,
                         events=[(d(23), "Accepted", "Portland, OR"), (d(20), "Delivered", "Dallas, TX")]),
        "FW-10502": dict(carrier="USPS", tracking_number="9405511899223197401234", signature=None,
                         events=[(d(21), "Accepted", "Portland, OR"), (d(18), "Delivered", "Atlanta, GA"),
                                 (d(9), "Return shipment delivered to Fernwood Ceramics", "Portland, OR")]),
    }
    shipments = {
        k: {**v, "order_id": k,
            "events": [{"time": t, "status": s, "location": loc} for t, s, loc in v["events"]]}
        for k, v in shipments.items()
    }

    inbox = [
        dict(order_id="FW-10482", sender="m.hale@example.com", time=d(13), channel="email",
             body="Hi - I haven't received my vase yet, tracking says delivered but I don't see it?"),
        dict(order_id="FW-10482", sender="studio", time=d(12, 20), channel="email",
             body="Hi Marcus, UPS shows it was signed for by 'M HALE' at the front desk of your building on the delivery day. "
                  "Could you check with your building manager? Happy to help open a UPS claim if it's missing."),
        dict(order_id="FW-10511", sender="priya.r@example.com", time=d(6), channel="email",
             body="It's been over a week with no movement on tracking. Is my order lost?"),
        dict(order_id="FW-10533", sender="elena.s@example.com", time=d(8), channel="email",
             body="The serving bowl arrived with a hairline crack running from the rim - photo attached. It leaks when I wash it.",
             attachments=["IMG_2291.jpg (crack along rim, ~4cm)"]),
        dict(order_id="FW-10547", sender="dokafor@example.com", time=d(5), channel="instagram_dm",
             body="The oat dinnerware set just arrived and it's gorgeous - thank you! Already used it for dinner."),
        dict(order_id="FW-10561", sender="hannahkim@example.com", time=d(9, 3), channel="email",
             body="Just placed a second order - the planter + mug are a gift for my sister so please ship to her Portland address!"),
        dict(order_id="FW-10498", sender="sofia.alvarez@example.com", time=d(17), channel="email",
             body="Teapot lid doesn't fit snugly. I'd like to return it please."),
        dict(order_id="FW-10498", sender="studio", time=d(10), channel="email",
             body="Received your return today - refund of $93.00 has been issued to your PayPal account. Sorry it didn't work out!"),
        dict(order_id="FW-10502", sender="jwhitfield@example.com", time=d(3), channel="email",
             body="You received my return over a week ago (tracking confirms). Where is my refund?"),
        dict(order_id="FW-10570", sender="obrennan@example.com", time=d(2), channel="email",
             body="The workshop wasn't what I expected - I thought we'd get to keep glazed pieces the same day."),
        dict(order_id="FW-10589", sender="noah.fischer@example.com", time=d(0, 20), channel="email",
             body="I cancelled my Mug Club yesterday but got charged anyway. Please refund."),
    ]

    refunds = [
        dict(order_id="FW-10498", refund_id="1JU08902781691411", amount="93.00", time=d(10), status="COMPLETED",
             note="Full refund after return received"),
    ]

    workshop_logs = {
        "FW-10570": dict(session=d(5), checked_in=["Olivia Brennan", "Guest of Olivia Brennan"],
                         instructor="Mara Lind",
                         listing_text=("3-hour beginner session on the wheel. You'll throw 2-3 pieces; we trim, glaze and fire them "
                                       "and they're ready for pickup in 3 weeks."),
                         notes="Both seats checked in at 10:02. Pieces fired, ready for pickup on shelf B4."),
    }

    subscription_events = {
        "FW-10589": [
            dict(time=d(1, 10), event="Renewal charged $38.00 (scheduled, 1st of month)"),
            dict(time=d(1, 2), event="Member cancelled from account page; confirmation email sent"),
            dict(time=d(1, 1), event="Renewal box not yet shipped (status: PROCESSING)"),
        ],
    }

    def dispute(i: int, order: dict, reason: str, amount: str, stage: str, created_days: float,
                due_days: float | None, buyer_msg: str, status: str = "WAITING_FOR_SELLER_RESPONSE") -> dict:
        return {
            "dispute_id": f"PP-D-{27000 + i * 137}",
            "create_time": d(created_days),
            "update_time": d(created_days),
            "disputed_transactions": [{
                "buyer_transaction_id": f"B{order['capture_id'][1:]}",
                "seller_transaction_id": order["capture_id"],
                "create_time": order["created"],
                "transaction_status": "COMPLETED",
                "gross_amount": {"currency_code": "USD", "value": order["total"]},
                "invoice_number": order["order_id"],
                "buyer": {"name": order["customer"]["name"]},
                "seller": seller,
                "items": [{"item_id": it["sku"], "item_name": it["name"], "item_quantity": str(it["qty"])} for it in order["items"]],
            }],
            "reason": reason,
            "status": status,
            "dispute_amount": {"currency_code": "USD", "value": amount},
            "dispute_life_cycle_stage": stage,
            "dispute_channel": "INTERNAL" if stage == "INQUIRY" else "EXTERNAL",
            "seller_response_due_date": due(due_days) if due_days is not None else None,
            "messages": [{"posted_by": "BUYER", "time_posted": d(created_days), "content": buyer_msg}],
            "evidences": [],
            "allowed_response_options": {
                "accept_claim": {"accept_claim_types": ["REFUND", "REFUND_WITH_RETURN", "PARTIAL_REFUND"]},
                "make_offer": {"offer_types": ["REFUND", "REFUND_WITH_RETURN", "REFUND_WITH_REPLACEMENT"]},
            },
        }

    by_id = {o["order_id"]: o for o in orders}
    disputes = [
        dispute(1, by_id["FW-10482"], "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "224.00", "CHARGEBACK", 6, 4.5,
                "I never received this order."),
        dispute(2, by_id["FW-10511"], "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "66.00", "INQUIRY", 4, 9,
                "Order never arrived and tracking hasn't updated in almost two weeks."),
        dispute(3, by_id["FW-10533"], "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED", "106.00", "INQUIRY", 7, 1.2,
                "Bowl arrived cracked and leaks. Not usable."),
        dispute(4, by_id["FW-10547"], "UNAUTHORISED", "289.00", "CHARGEBACK", 3, 8,
                "I did not authorize this transaction."),
        dispute(5, by_id["FW-10561"], "DUPLICATE_TRANSACTION", "86.00", "CHARGEBACK", 5, 6,
                "I was charged twice for the same order within an hour."),
        dispute(6, by_id["FW-10498"], "CREDIT_NOT_PROCESSED", "93.00", "INQUIRY", 8, 2.5,
                "I returned the item and never got my money back."),
        dispute(7, by_id["FW-10502"], "CREDIT_NOT_PROCESSED", "81.00", "INQUIRY", 2, 11,
                "Seller received my return 9 days ago and has not refunded me."),
        dispute(9, by_id["FW-10589"], "CANCELED_RECURRING_BILLING", "38.00", "INQUIRY", 0.5, 14,
                "I cancelled this subscription and was still charged."),
        # Held back by the mock sandbox and delivered via a simulated CUSTOMER.DISPUTE.CREATED webhook.
        dispute(8, by_id["FW-10570"], "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED", "150.00", "CHARGEBACK", 0, 13,
                "Service not as described. Did not receive finished pottery."),
    ]

    return {
        "seller": seller,
        "policies": POLICIES,
        "orders": orders,
        "order_history": history,
        "shipments": shipments,
        "inbox": inbox,
        "refunds": refunds,
        "workshop_logs": workshop_logs,
        "subscription_events": subscription_events,
        "disputes": disputes,
    }
