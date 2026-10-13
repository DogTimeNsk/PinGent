from pingent.locales import Translator


def test_russian_translations_include_dynamic_status_values() -> None:
    translator = Translator("ru")

    assert translator.get("session_active") == "активна"
    assert translator.get("status", workspace="/tmp/work", session="активна") == (
        "Статус Pingent\nНастроенный workspace: /tmp/work\nСессия: активна"
    )


def test_help_lists_supported_commands_without_format_values() -> None:
    help_text = Translator("en").get("help")

    for command in (
        "/status",
        "/model",
        "/workspaces",
        "/workspace <number>",
        "/tasks",
        "/cancel <number>",
        "/new",
    ):
        assert command in help_text
