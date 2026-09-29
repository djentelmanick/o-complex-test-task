import re
from pathlib import Path

from tests.api.conftest import make_client

STATIC = Path("app/adapters/inbound/http/static")


async def test_page_references_only_same_origin_assets() -> None:
    async with make_client() as client:
        html = (await client.get("/")).text
        for asset in ("/static/app.js", "/static/styles.css"):
            assert asset in html
            assert (await client.get(asset)).status_code == 200
    assert not re.search(r"<script(?![^>]*\bsrc=)", html), "inline scripts are blocked by CSP"
    assert "style=" not in html
    assert "http://" not in html and "https://" not in html


def test_script_never_injects_html() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "textContent" in script
