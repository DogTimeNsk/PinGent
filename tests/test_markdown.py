from pingent.markdown import render_telegram_html, render_web_markdown


def test_web_markdown_renders_fenced_code_and_escapes_embedded_html() -> None:
    rendered = render_web_markdown("<script>alert(1)</script>\n\n```python\nprint('ok')\n```")

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<pre><code class=\"language-python\">print('ok')" in rendered


def test_telegram_markdown_renders_fenced_code_and_common_inline_formatting() -> None:
    rendered = render_telegram_html("**bold** and `code`\n\n```text\nhello <world>\n```")

    assert rendered == (
        "<b>bold</b> and <code>code</code>\n\n"
        '<pre><code class="language-text">hello &lt;world&gt;\n</code></pre>'
    )
