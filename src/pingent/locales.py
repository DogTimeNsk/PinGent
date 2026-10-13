"""Editable built-in Telegram message dictionaries."""

MESSAGES = {
    "en": {
        "start": "Send a text task for Codex. Use /help to see available commands.",
        "help": (
            "Pingent commands:\n"
            "/status — show the selected workspace and session status\n"
            "/model — show the configured Codex model\n"
            "/workspaces — list configured workspaces\n"
            "/workspace <number> — select a workspace\n"
            "/tasks — show the active task and queue\n"
            "/cancel <number> — cancel a queued task or interrupt the active task\n"
            "/new — reset the selected workspace session\n\n"
            "Send any other text to queue a Codex task."
        ),
        "new": "The local Pingent session was reset. Send the next task to start a new one.",
        "started": "Task started.",
        "queued": "Task queued (position: {position}).",
        "tasks_empty": "No running or queued tasks for this workspace.",
        "tasks": "Tasks for {workspace}:\n{items}",
        "task_item": "{number}. [{status}] {prompt}",
        "task_status_running": "running",
        "task_status_queued": "queued",
        "cancel_invalid": "Choose a task number from /tasks.",
        "cancelling": "Cancellation requested for the active task.",
        "cancelled": "Task cancelled.",
        "interrupted": "Pingent restarted while this task was running. It was not restarted.",
        "busy": "A task is already running for this session.",
        "codex_error": "Codex could not complete the task. Check the local Pingent logs.",
        "approval_required": (
            "Codex requested an unsupported approval. The task was stopped safely."
        ),
        "session_unavailable": (
            "The saved Codex session is unavailable. Send the task again to start a new session."
        ),
        "approve": "Approve once",
        "decline": "Decline",
        "invalid_decision": "Invalid approval decision",
        "approval_unavailable": "This approval is no longer available",
        "decision_recorded": "Decision recorded",
        "session_active": "active",
        "session_not_started": "not started",
        "status": "Pingent status\nConfigured workspace: {workspace}\nSession: {session}",
        "workspaces": "Configured workspaces:\n{items}\n\nUse /workspace <number> to select one.",
        "workspace_item": "{number}. {workspace}",
        "workspace_item_selected": "{number}. {workspace} (selected)",
        "workspace_invalid": "Choose a workspace number from /workspaces.",
        "workspace_selected": "Active workspace: {workspace}",
        "model_default": "Codex default",
        "model": "Configured Codex model: {model}",
    },
    "ru": {
        "start": "Отправьте текстовую задачу для Codex. Список команд: /help.",
        "help": (
            "Команды Pingent:\n"
            "/status — показать выбранный workspace и статус сессии\n"
            "/model — показать настроенную модель Codex\n"
            "/workspaces — показать настроенные workspace\n"
            "/workspace <номер> — выбрать workspace\n"
            "/tasks — показать активную задачу и очередь\n"
            "/cancel <номер> — отменить задачу в очереди или прервать активную\n"
            "/new — сбросить сессию выбранного workspace\n\n"
            "Любое другое текстовое сообщение будет поставлено в очередь для Codex."
        ),
        "new": "Локальная сессия Pingent сброшена. Следующая задача создаст новую сессию.",
        "started": "Задача запущена.",
        "queued": "Задача поставлена в очередь (позиция: {position}).",
        "tasks_empty": "Для этого workspace нет выполняющихся или ожидающих задач.",
        "tasks": "Задачи для {workspace}:\n{items}",
        "task_item": "{number}. [{status}] {prompt}",
        "task_status_running": "выполняется",
        "task_status_queued": "в очереди",
        "cancel_invalid": "Выберите номер задачи из /tasks.",
        "cancelling": "Для активной задачи запрошена отмена.",
        "cancelled": "Задача отменена.",
        "interrupted": (
            "Pingent был перезапущен во время выполнения задачи. Она не была запущена повторно."
        ),
        "busy": "Для этой сессии уже выполняется задача.",
        "codex_error": "Codex не смог выполнить задачу. Проверьте локальные логи Pingent.",
        "approval_required": "Codex запросил неподдерживаемое подтверждение. Задача остановлена.",
        "session_unavailable": "Сохранённая сессия Codex недоступна. Отправьте задачу снова.",
        "approve": "Одобрить один раз",
        "decline": "Отклонить",
        "invalid_decision": "Некорректное решение для подтверждения",
        "approval_unavailable": "Это подтверждение больше недоступно",
        "decision_recorded": "Решение сохранено",
        "session_active": "активна",
        "session_not_started": "не начата",
        "status": "Статус Pingent\nНастроенный workspace: {workspace}\nСессия: {session}",
        "workspaces": "Настроенные workspace:\n{items}\n\nВыберите: /workspace <номер>.",
        "workspace_item": "{number}. {workspace}",
        "workspace_item_selected": "{number}. {workspace} (выбран)",
        "workspace_invalid": "Выберите номер workspace из /workspaces.",
        "workspace_selected": "Активный workspace: {workspace}",
        "model_default": "по умолчанию Codex",
        "model": "Настроенная модель Codex: {model}",
    },
}


class Translator:
    def __init__(self, locale: str) -> None:
        self._messages = MESSAGES[locale]

    def get(self, key: str, **values: object) -> str:
        return self._messages[key].format(**values)
