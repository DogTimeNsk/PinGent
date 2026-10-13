"""Safe Markdown rendering for Pingent's user-facing adapters."""

from html import escape

from markdown_it import MarkdownIt
from markdown_it.token import Token

_MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False})


def render_web_markdown(text: str) -> str:
    """Render CommonMark for the SPA while treating embedded HTML as text."""
    return _MARKDOWN.render(text)


def render_telegram_html(text: str) -> str:
    """Render the supported CommonMark subset using Telegram's HTML tags."""
    output: list[str] = []
    list_depth = 0
    ordered_counters: list[int] = []

    for token in _MARKDOWN.parse(text):
        if token.type == "inline":
            output.append(_render_inline(token.children or []))
        elif token.type == "fence":
            language = token.info.strip().split(maxsplit=1)[0]
            language_attribute = (
                f' class="language-{escape(language, quote=True)}"' if language else ""
            )
            output.append(f"<pre><code{language_attribute}>{escape(token.content)}</code></pre>\n")
        elif token.type == "code_block":
            output.append(f"<pre>{escape(token.content)}</pre>\n")
        elif token.type == "heading_open":
            output.append("<b>")
        elif token.type == "heading_close":
            output.append("</b>\n\n")
        elif token.type == "paragraph_close":
            output.append("\n\n")
        elif token.type == "blockquote_open":
            output.append("<blockquote>")
        elif token.type == "blockquote_close":
            output.append("</blockquote>\n\n")
        elif token.type == "bullet_list_open":
            list_depth += 1
        elif token.type == "bullet_list_close":
            list_depth -= 1
            output.append("\n")
        elif token.type == "ordered_list_open":
            list_depth += 1
            ordered_counters.append(1)
        elif token.type == "ordered_list_close":
            list_depth -= 1
            ordered_counters.pop()
            output.append("\n")
        elif token.type == "list_item_open":
            prefix = "  " * max(list_depth - 1, 0)
            if ordered_counters:
                prefix += f"{ordered_counters[-1]}. "
                ordered_counters[-1] += 1
            else:
                prefix += "• "
            output.append(prefix)
        elif token.type == "list_item_close":
            output.append("\n")
        elif token.type == "hr":
            output.append("────────\n")

    return "".join(output).strip()


def _render_inline(tokens: list[Token]) -> str:
    output: list[str] = []
    tags = {
        "strong_open": "<b>",
        "strong_close": "</b>",
        "em_open": "<i>",
        "em_close": "</i>",
        "s_open": "<s>",
        "s_close": "</s>",
        "link_close": "</a>",
    }
    for token in tokens:
        if token.type == "text":
            output.append(escape(token.content))
        elif token.type == "code_inline":
            output.append(f"<code>{escape(token.content)}</code>")
        elif token.type in {"softbreak", "hardbreak"}:
            output.append("\n")
        elif token.type == "link_open":
            href = token.attrs.get("href", "")
            output.append(f'<a href="{escape(href, quote=True)}">')
        elif token.type in tags:
            output.append(tags[token.type])
        else:
            output.append(escape(token.content))
    return "".join(output)
