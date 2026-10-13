"""Executable composition root for a local Pingent installation."""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from pingent.codex import CodexAppServer
from pingent.config import Settings
from pingent.external import ExternalAdapterHost
from pingent.locales import Translator
from pingent.service import PingentService
from pingent.storage import SessionRepository
from pingent.telegram import build_router, notify_approval, notify_task
from pingent.web import create_web_server


async def run() -> None:
    settings = Settings()
    repository = SessionRepository(settings.database_path)
    await repository.initialize()

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    translator = Translator(settings.locale)
    external_host: ExternalAdapterHost | None = None

    async def notify_task_for_channels(event) -> None:
        if event.task.telegram_user_id in settings.allowed_telegram_user_ids:
            await notify_task(bot, event, translator)
        if external_host is not None:
            await external_host.notify_task(event)

    service = PingentService(
        repository=repository,
        agent_factory=lambda workspace: CodexAppServer(
            workspace,
            settings.codex_binary,
            settings.codex_model,
            settings.codex_request_timeout_seconds,
        ),
        allowed_user_ids=settings.allowed_telegram_user_ids,
        workspaces=settings.workspaces,
        approval_notifier=lambda approval: notify_approval(bot, approval, translator),
        task_notifier=notify_task_for_channels,
        external_actor_ids={adapter.actor_id for adapter in settings.external_adapters},
    )
    external_host = ExternalAdapterHost(settings.external_adapters, service)
    for actor_id in await repository.active_web_actor_ids():
        service.register_web_actor(actor_id)
    await external_host.start()
    await service.recover_tasks()
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(service, translator, settings.codex_model))
    web_server = create_web_server(settings, repository, service) if settings.web_enabled else None
    web_task = asyncio.create_task(web_server.serve()) if web_server is not None else None
    try:
        await dispatcher.start_polling(bot)
    finally:
        if web_server is not None:
            web_server.should_exit = True
        if web_task is not None:
            await web_task
        await external_host.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
