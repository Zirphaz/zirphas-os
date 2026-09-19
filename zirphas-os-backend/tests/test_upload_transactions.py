from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import event, func, select, text

from app.core.config import get_settings
from app.models import Attachment, Client, Device, ServiceOrder
from app.services.uploads import register_pending_upload


def create_order(factory):
    with factory.begin() as db:
        client = Client(name="Cliente de teste", phone="11999999999")
        db.add(client)
        db.flush()
        device = Device(
            client_id=client.id, kind="celular", brand="Marca", model="Modelo",
            imei_serial="upload-test-serial",
        )
        db.add(device)
        db.flush()
        order = ServiceOrder(
            device_id=device.id, problem_description="Tela quebrada",
            physical_checklist={"screen_condition": "quebrada"},
            tracking_token_hash="a" * 64,
        )
        db.add(order)
        db.flush()
        return order.id


def png_upload():
    content = BytesIO()
    with Image.new("RGB", (2, 2), "red") as picture:
        picture.save(content, "PNG")
    return {"file": ("entrada.png", content.getvalue(), "image/png")}


def test_upload_is_removed_when_database_commit_fails(api):
    client, headers, factory = api
    order_id = create_order(factory)
    upload_dir = get_settings().upload_dir
    engine = factory.kw["bind"]

    def fail_commit(connection):
        # The INSERT and file write succeeded; fail at the actual commit boundary.
        assert connection.scalar(select(func.count(Attachment.id))) == 1
        assert len(list(upload_dir.glob("*.jpg"))) == 1
        raise RuntimeError("simulated database commit failure")

    event.listen(engine, "commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="simulated database commit failure"):
            client.post(
                f"/api/v1/orders/{order_id}/attachments",
                headers=headers["front_desk"], files=png_upload(),
            )
    finally:
        event.remove(engine, "commit", fail_commit)

    assert list(upload_dir.glob("*.jpg")) == []
    with factory() as db:
        assert db.scalar(select(func.count(Attachment.id))) == 0


def test_successful_upload_keeps_file_and_attachment(api):
    client, headers, factory = api
    order_id = create_order(factory)
    response = client.post(
        f"/api/v1/orders/{order_id}/attachments",
        headers=headers["front_desk"], files=png_upload(),
    )
    assert response.status_code == 201, response.text
    with factory() as db:
        attachment = db.get(Attachment, response.json()["id"])
        assert (get_settings().upload_dir / attachment.storage_name).is_file()


def test_later_rollback_does_not_remove_committed_uploads(api, tmp_path):
    _, _, factory = api
    committed = tmp_path / "committed.jpg"
    rolled_back = tmp_path / "rolled-back.jpg"
    another_rolled_back = tmp_path / "another-rolled-back.jpg"

    with factory() as db:
        db.execute(text("SELECT 1"))
        register_pending_upload(db, committed)
        committed.write_bytes(b"committed image")
        db.commit()
        assert committed.is_file()

        db.execute(text("SELECT 1"))
        for path in (rolled_back, another_rolled_back):
            register_pending_upload(db, path)
            path.write_bytes(b"uncommitted image")
        db.rollback()

        assert committed.read_bytes() == b"committed image"
        assert not rolled_back.exists()
        assert not another_rolled_back.exists()
