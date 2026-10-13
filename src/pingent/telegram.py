"""Telegram long-polling adapter for the initial vertical slice."""

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from pingent.errors import (
    ApprovalNotFoundError,
    TaskNotFoundError,
    WorkspaceNotFoundError,
)
from pingent.locales import Translator
from pingent.markdown import render_telegram_html
from pingent.models import ApprovalRequest, TaskEvent
from pingent.service import PingentService

MAX_TELEGRAM_MESSAGE_LENGTH = 4096


def build_router(service: PingentService, translator: Translator, model: str | None) -> Router:
    router = Router(name="pingent")

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if not await _is_authorized(message, service, "start"):
            return
        await message.answer(translator.get("start"))

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        if not await _is_authorized(message, service, "help"):
            return
        await message.answer(translator.get("help"))

    @router.message(Command("new"))
    async def new_session(message: Message) -> None:
        if not await _is_authorized(message, service, "reset_session"):
            return
        assert message.from_user is not None
        await service.reset_session(message.from_user.id)
        await message.answer(translator.get("new"))

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        if await _is_authorized(message, service, "status"):
            assert message.from_user is not None
            current_status = await service.status(message.from_user.id)
            await message.answer(
                translator.get(
                    "status",
                    workspace=current_status.workspace,
                    session=translator.get(f"session_{current_status.state}"),
                )
            )

    @router.message(Command("workspaces"))
    async def workspaces(message: Message) -> None:
        if not await _is_authorized(message, service, "list_workspaces"):
            return
        assert message.from_user is not None
        current_status = await service.status(message.from_user.id)
        configured_workspaces = await service.list_workspaces(message.from_user.id)
        items = "\n".join(
            translator.get(
                "workspace_item_selected"
                if workspace == current_status.workspace
                else "workspace_item",
                number=index,
                workspace=workspace,
            )
            for index, workspace in enumerate(configured_workspaces, start=1)
        )
        await message.answer(translator.get("workspaces", items=items))

    @router.message(Command("workspace"))
    async def select_workspace(message: Message) -> None:
        if not await _is_authorized(message, service, "select_workspace"):
            return
        assert message.from_user is not None
        argument = _command_argument(message.text)
        try:
            number = int(argument)
            workspace = await service.select_workspace(message.from_user.id, number)
        except (TypeError, ValueError, WorkspaceNotFoundError):
            await message.answer(translator.get("workspace_invalid"))
            return
        await message.answer(translator.get("workspace_selected", workspace=workspace))

    @router.message(Command("model"))
    async def model_status(message: Message) -> None:
        if await _is_authorized(message, service, "model"):
            await message.answer(
                translator.get("model", model=model or translator.get("model_default"))
            )

    @router.message(Command("tasks"))
    async def tasks(message: Message) -> None:
        if not await _is_authorized(message, service, "pending_tasks"):
            return
        assert message.from_user is not None
        pending_tasks = await service.pending_tasks(message.from_user.id)
        if not pending_tasks:
            await message.answer(translator.get("tasks_empty"))
            return
        items = "\n".join(
            translator.get(
                "task_item",
                number=index,
                status=translator.get(f"task_status_{task.status}"),
                prompt=_task_preview(task.prompt),
            )
            for index, task in enumerate(pending_tasks, start=1)
        )
        await message.answer(
            translator.get("tasks", workspace=pending_tasks[0].workspace, items=items)
        )

    @router.message(Command("cancel"))
    async def cancel(message: Message) -> None:
        if not await _is_authorized(message, service, "cancel_task"):
            return
        assert message.from_user is not None
        try:
            number = int(_command_argument(message.text))
            result = await service.cancel_task(message.from_user.id, number)
        except (TypeError, ValueError, TaskNotFoundError):
            await message.answer(translator.get("cancel_invalid"))
            return
        await message.answer(translator.get(result))

    @router.message()
    async def task(message: Message) -> None:
        if not await _is_authorized(message, service, "enqueue_task") or message.text is None:
            return
        prompt = message.text.strip()
        if not prompt:
            return
        assert message.from_user is not None
        _, position = await service.enqueue_task(message.from_user.id, prompt)
        await message.answer(translator.get("queued", position=position))

    @router.callback_query(F.data.startswith("approval:"))
    async def approval_callback(callback: CallbackQuery) -> None:
        if callback.from_user is None or callback.data is None:
            return
        if not await service.authorize_telegram_user(callback.from_user.id, "resolve_approval"):
            return
        _, approval_id, decision = callback.data.split(":", maxsplit=2)
        if decision not in {"accept", "decline"}:
            await callback.answer(translator.get("invalid_decision"), show_alert=True)
            return
        try:
            await service.resolve_approval(callback.from_user.id, approval_id, decision)
        except ApprovalNotFoundError:
            await callback.answer(translator.get("approval_unavailable"), show_alert=True)
            return
        await callback.answer(translator.get("decision_recorded"))
        if callback.message is not None:
            await callback.message.edit_reply_markup(reply_markup=None)

    return router


async def _is_authorized(message: Message, service: PingentService, action: str) -> bool:
    return message.from_user is not None and await service.authorize_telegram_user(
        message.from_user.id, action
    )


def _command_argument(text: str | None) -> str | None:
    if text is None:
        return None
    _, _, argument = text.partition(" ")
    return argument.strip() or None


def _task_preview(prompt: str) -> str:
    return prompt if len(prompt) <= 80 else f"{prompt[:77]}..."


async def _send_text(message: Message, text: str) -> None:
    for start in range(0, len(text), MAX_TELEGRAM_MESSAGE_LENGTH):
        await message.answer(text[start : start + MAX_TELEGRAM_MESSAGE_LENGTH])


async def _send_bot_text(bot, telegram_user_id: int, text: str) -> None:
    rendered = render_telegram_html(text)
    if len(rendered) <= MAX_TELEGRAM_MESSAGE_LENGTH:
        await bot.send_message(telegram_user_id, rendered, parse_mode=ParseMode.HTML)
        return
    # Keep oversized answers deliverable.  Formatting is retained for normal-sized
    # responses; a future streaming renderer can split formatted token boundaries.
    for start in range(0, len(text), MAX_TELEGRAM_MESSAGE_LENGTH):
        await bot.send_message(telegram_user_id, text[start : start + MAX_TELEGRAM_MESSAGE_LENGTH])


async def notify_task(bot, event: TaskEvent, translator: Translator) -> None:
    if event.status == "running":
        await bot.send_message(event.task.telegram_user_id, translator.get("started"))
    elif event.status == "completed":
        await _send_bot_text(bot, event.task.telegram_user_id, event.result or "")
    elif event.status == "approval_required":
        await bot.send_message(event.task.telegram_user_id, translator.get("approval_required"))
    elif event.status == "session_unavailable":
        await bot.send_message(event.task.telegram_user_id, translator.get("session_unavailable"))
    elif event.status == "interrupted":
        await bot.send_message(event.task.telegram_user_id, translator.get("interrupted"))
    elif event.status == "failed":
        await bot.send_message(event.task.telegram_user_id, translator.get("codex_error"))
    elif event.status == "cancelled":
        await bot.send_message(event.task.telegram_user_id, translator.get("cancelled"))


async def notify_approval(bot, approval: ApprovalRequest, translator: Translator) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=translator.get("approve"), callback_data=f"approval:{approval.id}:accept"
                ),
                InlineKeyboardButton(
                    text=translator.get("decline"), callback_data=f"approval:{approval.id}:decline"
                ),
            ]
        ]
    )
    await bot.send_message(approval.telegram_user_id, approval.summary, reply_markup=keyboard)
