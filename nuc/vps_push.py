"""
vps_push.py — Push weekly plan to VPS webapp
"""
import os
import httpx
import json


async def push_plan_to_vps(plan: dict, recipes: list, shopping_list: str):
    """Send the finalized weekly plan to the VPS webhook."""
    url = os.environ["VPS_WEBHOOK_URL"]
    secret = os.environ["VPS_SHARED_SECRET"]

    payload = {
        "plan": plan,
        "recipes": recipes,
        "shopping_list": shopping_list
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            url,
            json=payload,
            headers={
                "X-Secret": secret,
                "Content-Type": "application/json"
            }
        )
        response.raise_for_status()
