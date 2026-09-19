from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import CashEntry, Part, Payment, ServiceOrder

BASE = "/api/v1"


def request(client, headers, method, path, body=None, expected=200):
    response = client.request(method, BASE + path, headers=headers, json=body)
    assert response.status_code == expected, response.text
    return response.json() if response.content else None


def prepare(api, *, stock=2, submit=True):
    client, headers, _ = api
    front, tech, admin = headers["front_desk"], headers["tech"], headers["admin"]
    customer = request(client, front, "POST", "/clients", {"name": "Cliente <script>alert(1)</script>",
        "phone": "11999998888", "document": "12345678900"}, expected=201)
    device = request(client, front, "POST", f"/clients/{customer['id']}/devices", {
        "kind": "celular", "brand": "Marca", "model": "Modelo", "imei_serial": "IMEI-UNICO-123"}, expected=201)
    part = request(client, admin, "POST", "/inventory", {"name": "Tela", "sku": "TELA-X",
        "quantity": stock, "cost_price": "100.00", "sale_price": "200.00", "min_quantity": 1}, expected=201)
    created = request(client, front, "POST", "/orders", {"device_id": device["id"],
        "problem_description": "Tela não funciona", "physical_checklist": {"powers_on": True,
        "screen_condition": "Trincada", "case_condition": "Riscos na traseira", "accessories": []}}, expected=201)
    order_id = created["order"]["id"]
    token = created["tracking_url"].rsplit("/", 1)[-1]
    request(client, tech, "POST", f"/orders/{order_id}/transition", {"status": "analyzing"})
    request(client, tech, "PUT", f"/orders/{order_id}/parts/{part['id']}", {"quantity": 1})
    order = request(client, admin, "PUT", f"/orders/{order_id}/quote", {
        "labor_amount": "150.00", "commission_amount": "20.00", "technical_report": "Troca do conjunto da tela."})
    if submit:
        order = request(client, tech, "POST", f"/orders/{order_id}/transition", {"status": "awaiting_approval"})
    return order_id, token, part["id"], order["quote_version"]


def approve(api, token, version):
    client, _, _ = api
    return request(client, {}, "POST", f"/public/status/{token}/decision", {"approved": True, "quote_version": version})


def test_complete_workflow_with_profit_stock_payments_and_cash(api):
    client, h, factory = api
    oid, token, pid, version = prepare(api)
    assert approve(api, token, version)["status"] == "repairing"
    payment = {"amount": "120.00", "method": "pix", "idempotency_key": "deposit-0001"}
    first = request(client, h["front_desk"], "POST", f"/orders/{oid}/payments", payment, expected=201)
    repeated = request(client, h["front_desk"], "POST", f"/orders/{oid}/payments", payment, expected=201)
    assert first["id"] == repeated["id"]
    request(client, h["front_desk"], "POST", f"/orders/{oid}/payments", {**payment, "amount": "121.00"}, expected=409)
    dashboard = request(client, h["admin"], "GET", "/dashboard")
    assert Decimal(dashboard["cash_total"]) == 0
    assert Decimal(dashboard["advance_payments"]) == 120
    request(client, h["tech"], "POST", f"/orders/{oid}/transition", {"status": "completed"})
    order = request(client, h["admin"], "GET", f"/orders/{oid}")
    assert order["parts_cost"] == "100.00"
    assert order["net_profit"] == "230.00"
    assert order["balance_due"] == "230.00"
    request(client, h["front_desk"], "POST", f"/orders/{oid}/transition", {"status": "delivered"}, expected=409)
    request(client, h["front_desk"], "POST", f"/orders/{oid}/payments", {
        "amount": "230.01", "method": "cash", "idempotency_key": "overpay-001"}, expected=409)
    request(client, h["front_desk"], "POST", f"/orders/{oid}/payments", {
        "amount": "230.00", "method": "cash", "idempotency_key": "balance-001"}, expected=201)
    request(client, h["front_desk"], "POST", f"/orders/{oid}/transition", {"status": "delivered"})
    request(client, h["front_desk"], "POST", f"/orders/{oid}/transition", {"status": "delivered"}, expected=409)
    with factory() as db:
        assert db.get(Part, pid).quantity == 1
        assert db.scalar(select(func.count()).select_from(Payment)) == 2
        assert db.scalar(select(func.count()).select_from(CashEntry)) == 1
        assert db.get(ServiceOrder, oid).delivered_at is not None
    dashboard = request(client, h["admin"], "GET", "/dashboard")
    assert Decimal(dashboard["cash_total"]) == 350
    assert Decimal(dashboard["advance_payments"]) == 0
    assert Decimal(dashboard["net_profit"]) == 230
    assert dashboard["technical_productivity"][0]["completed_orders"] == 1
    receipt = client.get(BASE + f"/orders/{oid}/receipt", headers=h["front_desk"])
    assert receipt.status_code == 200
    assert "&lt;script&gt;" in receipt.text and "<script>alert" not in receipt.text


def test_shortage_waits_for_stock_and_completion_deducts_once(api):
    client, h, factory = api
    oid, token, pid, version = prepare(api, stock=0)
    assert approve(api, token, version)["status"] == "awaiting_parts"
    request(client, h["tech"], "POST", f"/orders/{oid}/transition", {"status": "repairing"}, expected=409)
    request(client, h["admin"], "POST", f"/inventory/{pid}/adjustments", {"delta": 1})
    request(client, h["tech"], "POST", f"/orders/{oid}/transition", {"status": "repairing"})
    request(client, h["tech"], "POST", f"/orders/{oid}/transition", {"status": "completed"})
    request(client, h["tech"], "POST", f"/orders/{oid}/transition", {"status": "completed"}, expected=409)
    with factory() as db:
        assert db.get(Part, pid).quantity == 0


def test_customer_decline_stale_quote_and_link_rotation(api):
    client, h, factory = api
    oid, token, _, version = prepare(api)
    request(client, {}, "POST", f"/public/status/{token}/decision", {"approved": True, "quote_version": version - 1}, expected=409)
    declined = request(client, {}, "POST", f"/public/status/{token}/decision", {"approved": False, "quote_version": version})
    assert declined["status"] == "analyzing" and declined["technical_report"] is None
    quote = request(client, h["tech"], "PUT", f"/orders/{oid}/quote", {
        "labor_amount": "100.00", "technical_report": "Novo diagnóstico da tela."})
    assert quote["quote_version"] > version
    request(client, h["tech"], "POST", f"/orders/{oid}/transition", {"status": "awaiting_approval"})
    request(client, {}, "POST", f"/public/status/{token}/decision", {"approved": True, "quote_version": version}, expected=409)
    new_url = request(client, h["front_desk"], "POST", f"/orders/{oid}/tracking-link")["tracking_url"]
    request(client, {}, "GET", f"/public/status/{token}", expected=404)
    new_token = new_url.rsplit("/", 1)[-1]
    assert approve(api, new_token, quote["quote_version"])["status"] == "repairing"
    with factory() as db:
        assert db.get(ServiceOrder, oid).tracking_token_hash not in (token, new_token)


def test_privacy_ownership_and_rbac(api):
    client, h, _ = api
    oid, token, pid, version = prepare(api)
    for path in ("/orders", "/inventory", "/clients", "/dashboard", "/users"):
        request(client, {}, "GET", path, expected=401)
    for path in ("/clients", "/dashboard", "/users"):
        request(client, h["tech"], "GET", path, expected=403)
    public = request(client, {}, "GET", f"/public/status/{token}")
    for forbidden in ("phone", "document", "imei_serial", "commission_amount", "unit_cost", "parts_cost", "net_profit", "tracking_token_hash"):
        assert forbidden not in str(public)
    technical = request(client, h["tech"], "GET", f"/orders/{oid}")
    assert technical["device"]["imei_serial"] == "IMEI-UNICO-123"
    assert "commission_amount" not in technical and "unit_cost" not in technical["parts"][0]
    assert "cost_price" not in request(client, h["tech"], "GET", "/inventory")[0]
    request(client, h["tech"], "POST", f"/orders/{oid}/decision", {"approved": True, "quote_version": version}, expected=403)
    request(client, h["other_tech"], "PUT", f"/orders/{oid}/parts/{pid}", {"quantity": 0}, expected=403)
    request(client, h["front_desk"], "POST", f"/orders/{oid}/transition", {"status": "repairing"}, expected=403)
    request(client, h["tech"], "POST", f"/orders/{oid}/transition", {"status": "repairing"}, expected=409)
    assert client.get(f"/status/{token}").status_code == 200
    response = client.get(BASE + f"/public/status/{token}")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_approved_quote_cannot_be_edited(api):
    client, h, _ = api
    oid, token, pid, version = prepare(api)
    approve(api, token, version)
    request(client, h["admin"], "PUT", f"/orders/{oid}/quote", {
        "labor_amount": "1.00", "technical_report": "Changed report"}, expected=409)
    request(client, h["tech"], "PUT", f"/orders/{oid}/parts/{pid}", {"quantity": 0}, expected=409)


def test_photos_are_validated_private_and_frozen_after_submission(api):
    client, h, _ = api
    oid, token, _, _ = prepare(api, submit=False)
    endpoint = BASE + f"/orders/{oid}/attachments"
    fake = client.post(endpoint, headers=h["front_desk"], files={"file": ("fake.jpg", b"<script>alert(1)</script>", "image/jpeg")})
    assert fake.status_code == 422
    buffer = BytesIO()
    Image.new("RGB", (20, 20), "green").save(buffer, format="PNG")
    content = buffer.getvalue()
    denied = client.post(endpoint, headers=h["tech"], files={"file": ("photo.png", content, "image/png")})
    assert denied.status_code == 403
    uploaded = client.post(endpoint, headers=h["front_desk"], files={"file": ("../../photo.png", content, "image/png")})
    assert uploaded.status_code == 201, uploaded.text
    photo = uploaded.json()
    assert photo["original_name"] == "photo.png" and "storage_name" not in photo
    download = f"{endpoint}/{photo['id']}"
    assert client.get(download).status_code == 401
    received = client.get(download, headers=h["tech"])
    assert received.status_code == 200 and received.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(received.content)) as image:
        assert image.size == (20, 20)
    request(client, h["tech"], "POST", f"/orders/{oid}/transition", {"status": "awaiting_approval"})
    assert client.post(endpoint, headers=h["front_desk"], files={"file": ("photo.png", content, "image/png")}).status_code == 409
    assert client.get(f"/uploads/{photo['original_name']}").status_code == 404


def test_entry_checklist_and_serial_validation(api):
    client, h, _ = api
    customer = request(client, h["front_desk"], "POST", "/clients", {
        "name": "Cliente", "phone": "11999998888"}, expected=201)
    request(client, h["front_desk"], "POST", f"/clients/{customer['id']}/devices", {
        "kind": "celular", "brand": "Marca", "model": "Modelo", "imei_serial": "   "}, expected=422)
    request(client, h["front_desk"], "POST", "/orders", {"device_id": 1,
        "problem_description": "Tela", "physical_checklist": {}}, expected=422)
    request(client, h["front_desk"], "POST", "/orders", {"device_id": 999,
        "problem_description": "Tela", "physical_checklist": {"powers_on": True,
        "screen_condition": "Inteira", "case_condition": "Boa"}}, expected=404)


@pytest.mark.parametrize("amount", ["-0.01", "1.001", "NaN", "Infinity"])
def test_money_validation_rejects_invalid_values(api, amount):
    client, h, _ = api
    request(client, h["admin"], "POST", "/inventory", {"name": "Peça", "sku": "p",
        "quantity": 1, "cost_price": amount, "sale_price": "20.00"}, expected=422)


def test_duplicate_record_returns_conflict_and_session_recovers(api):
    client, h, _ = api
    data = {"name": "Cliente", "phone": "11999998888", "document": "12345678901"}
    request(client, h["front_desk"], "POST", "/clients", data, expected=201)
    request(client, h["front_desk"], "POST", "/clients", data, expected=409)
    assert len(request(client, h["front_desk"], "GET", "/clients")) == 1
    assert client.get("/health").status_code == 200
