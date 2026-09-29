from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from app.domain.errors import LeadNotFound
from app.domain.models import DialogMessage, Lead, Role


class _MessageFixture(BaseModel):
    role: Role
    text: str


class _LeadFixture(BaseModel):
    id: str
    name: str
    dialog: list[_MessageFixture]


_LEADS = TypeAdapter(list[_LeadFixture])


class MockCRMGateway:
    """Имитация AmoCRM: история диалогов демо-лидов из JSON-фикстуры."""

    def __init__(self, leads: Sequence[Lead]) -> None:
        self._leads = {lead.id: lead for lead in leads}

    @classmethod
    def from_json_file(cls, path: Path) -> "MockCRMGateway":
        fixtures = _LEADS.validate_json(path.read_bytes())
        return cls(
            [
                Lead(
                    id=f.id,
                    name=f.name,
                    dialog=tuple(DialogMessage(m.role, m.text) for m in f.dialog),
                )
                for f in fixtures
            ]
        )

    async def get_lead(self, lead_id: str) -> Lead:
        try:
            return self._leads[lead_id]
        except KeyError:
            raise LeadNotFound(lead_id) from None

    async def list_leads(self) -> list[Lead]:
        return list(self._leads.values())
