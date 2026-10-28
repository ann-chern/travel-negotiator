from pathlib import Path
from app.web.presentation import option_view
from app.web.api import _serialize_reply
from types import SimpleNamespace

def test_option_id_preserved_for_click():
    offer=option_view({"id":"offer-123", "hotel":"Demo", "price_rub":"1000"})
    assert offer["id"] == "offer-123"
    reply=SimpleNamespace(status="ready", message="ok",version=1,options=({"id":"offer-123", "price_rub":"1000"},), compromises=())
    payload=_serialize_reply(reply)
    assert payload["options"][0]["id"]=="offer-123"

def test_card_button_and_cache_busting():
    root=Path(__file__).parents[1]/"app/web/static"
    js=(root/"app.js").read_text(encoding="utf-8")
    html=(root/"index.html").read_text(encoding="utf-8")
    assert 'choose.addEventListener("click"' in js
    assert 'submit(o.id||' in js
    assert 'src="/static/app.js' in html
    assert 'defer' in html
    assert 'choose.dataset.offerId' in js
