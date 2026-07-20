import time
import logging
import httpx
from config import CLICKPESA_CLIENT_ID, CLICKPESA_API_KEY

log = logging.getLogger("clickpesa")

BASE_URL = "https://api.clickpesa.com/third-parties"

_token_cache = {"token": None, "expires_at": 0}


async def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/generate-token",
            headers={
                "client-id": CLICKPESA_CLIENT_ID,
                "api-key": CLICKPESA_API_KEY,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            body = resp.text
            log.error("ClickPesa token error %s: %s", resp.status_code, body)
            raise ValueError(f"Payment service auth failed ({resp.status_code})")
        data = resp.json()

    token = data["token"]
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + 3300
    return token


async def initiate_ussd_push(phone: str, amount: str, order_ref: str) -> dict:
    token = await _get_token()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/payments/initiate-ussd-push-request",
            headers={"Authorization": token},
            json={
                "amount": str(amount),
                "currency": "TZS",
                "orderReference": order_ref,
                "phoneNumber": phone,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            body = resp.text
            log.error("ClickPesa initiate error %s: %s", resp.status_code, body)
            try:
                detail = resp.json().get("message", body)
            except Exception:
                detail = body
            raise ValueError(f"Payment initiation failed: {detail}")
        return resp.json()


async def check_payment(order_ref: str) -> dict:
    token = await _get_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/payments/all",
            headers={"Authorization": token},
            params={"orderReference": order_ref, "limit": 1},
            timeout=15,
        )
        if resp.status_code != 200:
            log.error("ClickPesa query error %s: %s", resp.status_code, resp.text)
            raise ValueError(f"Payment status check failed ({resp.status_code})")
        data = resp.json()
        payments = data.get("data", [])
        if payments:
            return payments[0]
        return {"status": "PROCESSING"}
