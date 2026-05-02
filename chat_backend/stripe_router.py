import os

from fastapi import APIRouter, Header, HTTPException, Request

from chat_backend.auth import check_rate_limit, validate_session_token
from chat_backend.database import get_db


router = APIRouter(prefix="/stripe", tags=["stripe"])


def _read_env(name: str) -> str:
    value = os.getenv(name, "")
    return value.strip()


def _ensure_stripe_donations_table() -> None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS stripe_donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_intent_id TEXT NOT NULL UNIQUE,
            user_id TEXT,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


@router.get("/config")
async def stripe_config(request: Request):
    check_rate_limit(request, 60)

    stripe_publishable_key = _read_env("STRIPE_PUBLISHABLE_KEY")
    if not stripe_publishable_key:
        return {
            "status": "error",
            "msg": "Payment backend is not configured (missing STRIPE_PUBLISHABLE_KEY)",
        }

    return {
        "status": "ok",
        "publishable_key": stripe_publishable_key,
    }


@router.post("/create-payment-intent")
async def create_payment_intent(
    request: Request,
    payload: dict,
    session_secret: str = Header(default=None, alias="session-secret"),
):
    check_rate_limit(request, 20)

    stripe_secret_key = _read_env("STRIPE_SECRET_KEY")
    stripe_publishable_key = _read_env("STRIPE_PUBLISHABLE_KEY")

    if not stripe_secret_key:
        return {
            "status": "error",
            "msg": "Payment backend is not configured (missing STRIPE_SECRET_KEY)",
        }

    if not stripe_publishable_key:
        return {
            "status": "error",
            "msg": "Payment backend is not configured (missing STRIPE_PUBLISHABLE_KEY)",
        }

    amount_minor = int(payload.get("amount_minor") or 0)
    if amount_minor < 50:
        return {"status": "error", "msg": "Amount too small"}
    if amount_minor > 100000000:
        return {"status": "error", "msg": "Amount too large"}

    user_id = ""
    if session_secret:
        valid, session_user = validate_session_token(session_secret)
        if not valid:
            raise HTTPException(status_code=401, detail=session_user)
        user_id = session_user

    donation_type = str(payload.get("donation_type") or "once").strip().lower()
    if donation_type not in ("once", "monthly"):
        donation_type = "once"

    try:
        import stripe

        stripe.api_key = stripe_secret_key
        intent = stripe.PaymentIntent.create(
            amount=amount_minor,
            currency="jpy",
            automatic_payment_methods={"enabled": True},
            metadata={
                "type": "donation",
                "user_id": user_id,
                "donation_type": donation_type,
            },
        )
    except Exception as exc:
        return {"status": "error", "msg": f"Stripe error: {exc}"}

    return {
        "status": "ok",
        "client_secret": intent.client_secret,
        "publishable_key": stripe_publishable_key,
        "currency": "jpy",
    }


@router.post("/webhook")
async def stripe_webhook(request: Request):
    check_rate_limit(request, 120)

    stripe_secret_key = _read_env("STRIPE_SECRET_KEY")
    stripe_webhook_secret = _read_env("STRIPE_WEBHOOK_SECRET")
    stripe_signature = request.headers.get("stripe-signature", "")

    if not stripe_secret_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY is not configured")
    if not stripe_webhook_secret:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET is not configured")
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    raw_body = await request.body()

    try:
        import stripe

        stripe.api_key = stripe_secret_key
        event = stripe.Webhook.construct_event(
            payload=raw_body,
            sig_header=stripe_signature,
            secret=stripe_webhook_secret,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid webhook signature: {exc}")

    def _safe_get(obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            value = obj.get(key, default)
            return default if value is None else value
        try:
            value = obj[key]
            return default if value is None else value
        except Exception:
            value = getattr(obj, key, default)
            return default if value is None else value

    event_type = _safe_get(event, "type", "")

    if event_type == "payment_intent.succeeded":
        payment_intent = _safe_get(_safe_get(event, "data", {}), "object", {})
        payment_intent_id = str(_safe_get(payment_intent, "id", "") or "").strip()
        amount_raw = _safe_get(payment_intent, "amount", 0)
        try:
            amount_minor = int(amount_raw or 0)
        except Exception:
            amount_minor = 0
        currency = str(_safe_get(payment_intent, "currency", "jpy") or "jpy").strip().lower()
        metadata = _safe_get(payment_intent, "metadata", {}) or {}
        user_id = str(_safe_get(metadata, "user_id", "") or "").strip()

        if payment_intent_id:
            _ensure_stripe_donations_table()
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO stripe_donations (
                    payment_intent_id,
                    user_id,
                    amount_minor,
                    currency,
                    status
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    payment_intent_id,
                    user_id,
                    amount_minor,
                    currency,
                    "succeeded",
                ),
            )
            conn.commit()
            conn.close()

            print(
                "[stripe] donation recorded"
                f" payment_intent_id={payment_intent_id}"
                f" user_id={user_id or 'anonymous'}"
                f" amount_minor={amount_minor}"
                f" currency={currency}"
            )

    return {"status": "ok"}
