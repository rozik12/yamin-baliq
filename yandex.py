"""Яндекс Доставка (b2b.taxi.yandex.net): создание заявки с автоподтверждением и опрос статуса.
Включается переменной YANDEX_DELIVERY_TOKEN. Поля запроса взяты из документации claims/create."""
import asyncio
import json
import os
import urllib.error
import urllib.request
import uuid

HOST = "https://b2b.taxi.yandex.net/b2b/cargo/integration/v2"
TOKEN = os.environ.get("YANDEX_DELIVERY_TOKEN", "")
FINAL_OK = {"delivered", "delivered_finish"}
FINAL_BAD = {"cancelled", "cancelled_by_taxi", "cancelled_with_payment", "cancelled_with_items_on_hands",
             "failed", "estimating_failed", "performer_not_found", "returned", "returned_finish"}


def enabled(cfg):
    return bool(TOKEN and cfg and cfg.get("pickup"))


def _call(path, body=None, query=""):
    req = urllib.request.Request(
        f"{HOST}/{path}{query}", method="POST", data=json.dumps(body or {}).encode("utf-8"),
        headers={"Authorization": f"Bearer {TOKEN}", "Accept-Language": "ru", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from None


def _point(pid, order, contact, addr, kind):
    p = {"point_id": pid, "visit_order": order, "type": kind, "contact": contact,
         "address": {"fullname": addr["fullname"]}}
    if addr.get("lat") is not None and addr.get("lon") is not None:
        p["address"]["coordinates"] = [addr["lon"], addr["lat"]]
    if addr.get("comment"):
        p["address"]["comment"] = addr["comment"]
    return p


def build_claim(cfg, oid, total_uzs, dest):
    pk = cfg["pickup"]
    return {
        "items": [{"title": f"Заказ №{oid}", "quantity": 1, "weight": cfg.get("weight_kg", 3),
                   "size": {"length": 0.35, "width": 0.35, "height": 0.25},
                   "cost_value": f"{total_uzs}.00", "cost_currency": "UZS", "pickup_point": 1, "dropoff_point": 2}],
        "route_points": [
            _point(1, 1, {"name": pk.get("contact_name", "Ресторан"), "phone": pk["phone"]}, pk, "source"),
            _point(2, 2, {"name": dest.get("name", "Клиент"), "phone": dest["phone"]}, dest, "destination")],
        "emergency_contact": {"name": pk.get("contact_name", "Ресторан"), "phone": pk["phone"]},
        "client_requirements": {"taxi_class": cfg.get("taxi_class", "express")},
        "comment": f"Заказ ресторана №{oid}",
        "auto_accept": True,
    }


async def create_claim(cfg, oid, total_uzs, dest):
    body = build_claim(cfg, oid, total_uzs, dest)
    res = await asyncio.to_thread(_call, "claims/create", body, f"?request_id=order-{oid}-{uuid.uuid4().hex[:8]}")
    return res["id"], res.get("status"), res.get("pricing")


async def claim_info(claim_id):
    return await asyncio.to_thread(_call, "claims/info", None, f"?claim_id={claim_id}")
