import json
from pathlib import Path

import pytest

from app.adapters.outbound.crm.mock import MockCRMGateway
from app.domain.errors import LeadNotFound
from app.domain.models import Role

FIXTURE = Path("data/crm_dialogs.json")


async def test_loads_demo_leads_from_fixture() -> None:
    crm = MockCRMGateway.from_json_file(FIXTURE)
    leads = await crm.list_leads()
    assert [lead.id for lead in leads] == ["lead-new", "lead-repeat", "lead-price"]
    repeat = await crm.get_lead("lead-repeat")
    assert repeat.dialog[0].role is Role.CLIENT


async def test_unknown_lead_raises() -> None:
    crm = MockCRMGateway.from_json_file(FIXTURE)
    with pytest.raises(LeadNotFound):
        await crm.get_lead("nope")


def test_invalid_fixture_fails_fast(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"id": "x", "name": "X", "dialog": [{"role": "bot"}]}]))
    with pytest.raises(ValueError):
        MockCRMGateway.from_json_file(path)
