import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from app.adapters.outbound.crm.mock import MockCRMGateway
from app.adapters.outbound.kb_files import load_markdown_documents
from app.application.handle_incoming import message_event_key
from app.bootstrap import build_container
from app.config import Settings
from app.domain.errors import DomainError
from app.domain.models import Role

logger = logging.getLogger("app.cli")


class _AmoCRMNotConfigured(DomainError):
    pass


async def _ingest(settings: Settings) -> None:
    documents = load_markdown_documents(settings.kb_dir)
    async with build_container(settings) as container:
        report = await container.ingest_knowledge.execute(documents)
    logger.info(
        "knowledge base ingested: indexed=%d skipped=%d deleted=%d",
        report.indexed,
        report.skipped,
        report.deleted,
    )


async def _with_amocrm(settings: Settings, action: Callable[[Any], Awaitable[Any]]) -> None:
    async with build_container(settings) as container:
        if container.amocrm is None:
            raise _AmoCRMNotConfigured("set CRM_PROVIDER=amocrm and AMOCRM_* in .env")
        await action(container.amocrm)


async def _amocrm_seed(settings: Settings, tools: Any) -> None:
    # AmoCRM шлёт вебхук почти мгновенно, раньше, чем мы успеем пометить примечание обработанным,
    # поэтому на время заливки демо-истории снимаем наш вебхук
    paused: list[str] = []
    if settings.amocrm_webhook_secret is not None:
        secret = settings.amocrm_webhook_secret.get_secret_value()
        paused = await tools.registrar.destinations_with_secret(secret)
        for destination in paused:
            await tools.registrar.unregister(destination)
    try:
        await _create_demo_leads(settings, tools)
    finally:
        for destination in paused:
            await tools.registrar.register(destination)


async def _create_demo_leads(settings: Settings, tools: Any) -> None:
    demo = await MockCRMGateway.from_json_file(settings.crm_fixture_path).list_leads()
    for lead in demo:
        lead_id = await tools.gateway.create_lead(lead.name)
        for message in lead.dialog:
            note_id = await tools.gateway.add_message(lead_id, message.role, message.text)
            # Демо-история — не новые обращения: помечаем их обработанными до прихода вебхуков
            await tools.processed_events.first_seen(message_event_key(note_id))
        print(f"{lead_id}\t{lead.name}")


async def _print_new_lead(tools: Any, name: str) -> None:
    # Печатаем только id, чтобы его можно было забрать в переменную: ID=$(make -s new-lead ...)
    print(await tools.gateway.create_lead(name))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inquiry-assistant")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest", help="load data/kb/*.md into the vector store")
    auth = commands.add_parser("amocrm-auth", help="exchange AmoCRM authorization code")
    auth.add_argument("code")
    commands.add_parser("amocrm-seed", help="create demo leads with dialog history")
    say = commands.add_parser("amocrm-say", help="add an incoming client message to a lead")
    say.add_argument("lead_id")
    say.add_argument("text")
    reply = commands.add_parser("amocrm-reply", help="add an outgoing manager message to a lead")
    reply.add_argument("lead_id")
    reply.add_argument("text")
    new_lead = commands.add_parser("amocrm-new-lead", help="create an empty lead, print its id")
    new_lead.add_argument("name")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    try:
        if args.command == "ingest":
            asyncio.run(_ingest(settings))
        elif args.command == "amocrm-auth":
            asyncio.run(_with_amocrm(settings, lambda t: t.oauth.exchange_code(args.code)))
            logger.info("AmoCRM authorized")
        elif args.command == "amocrm-seed":
            asyncio.run(_with_amocrm(settings, lambda t: _amocrm_seed(settings, t)))
        elif args.command == "amocrm-say":
            asyncio.run(
                _with_amocrm(
                    settings,
                    lambda t: t.gateway.add_message(args.lead_id, Role.CLIENT, args.text),
                )
            )
        elif args.command == "amocrm-reply":
            asyncio.run(
                _with_amocrm(
                    settings,
                    lambda t: t.gateway.add_message(args.lead_id, Role.MANAGER, args.text),
                )
            )
        elif args.command == "amocrm-new-lead":
            asyncio.run(_with_amocrm(settings, lambda t: _print_new_lead(t, args.name)))
    except DomainError as exc:
        logger.error("%s failed: %s", args.command, exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
