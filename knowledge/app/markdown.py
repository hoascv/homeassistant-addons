"""A small, deliberately limited Markdown renderer for briefings.

Briefings arrive from whatever assistant the user pasted from. That makes them
**untrusted input**, and this module turns them into HTML that the page inserts
with innerHTML — so it is the one place in this add-on where getting it wrong
means script execution in somebody's Home Assistant session.

The defence is the order of operations, and it is the whole design:

    escape everything first, then add back only the markup this file emits.

Because `<`, `>`, `&`, `"` and `'` are replaced before any rule runs, no tag in
the source text can survive to become a tag in the output. A briefing containing
`<script>alert(1)</script>` is `&lt;script&gt;...` by the time the first rule
sees it, and every rule below matches on Markdown punctuation, never on angle
brackets. There is no allow-list to keep in sync and no sanitiser to outwit,
which is why it is done this way round rather than by stripping tags afterwards.

It is not a complete Markdown implementation and is not trying to be. It covers
what study material actually uses: headings, fenced code, lists, tables, bold,
italic, inline code and paragraphs. Anything else passes through as text, which
is the right failure mode — an unrendered construct is legible, a half-rendered
one is not.
"""
import re

# Headings start at h4: the page already uses h1-h3 for its own structure, and a
# briefing dropping an h2 into the middle of a card would outrank the card title.
_HEADING_LEVELS = {1: "h4", 2: "h4", 3: "h5", 4: "h5", 5: "h6", 6: "h6"}

_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


def escape(text):
    return "".join(_ESCAPES.get(char, char) for char in text)


def render(text):
    """Markdown to safe HTML. Never raises; unparsable input degrades to text."""
    if not text:
        return ""
    # Everything downstream operates on already-escaped text. Nothing below
    # un-escapes, so no user-supplied angle bracket can become a tag.
    lines = escape(str(text).replace("\r\n", "\n").replace("\r", "\n")).split("\n")

    html = []
    index = 0
    while index < len(lines):
        line = lines[index]

        if _is_fence(line):
            block, index = _take_fence(lines, index)
            html.append(block)
            continue

        if _is_table_row(line) and index + 1 < len(lines) and _is_table_divider(lines[index + 1]):
            block, index = _take_table(lines, index)
            html.append(block)
            continue

        if _is_list_item(line):
            block, index = _take_list(lines, index)
            html.append(block)
            continue

        heading = _heading(line)
        if heading is not None:
            html.append(heading)
            index += 1
            continue

        if _is_quote(line):
            block, index = _take_quote(lines, index)
            html.append(block)
            continue

        if not line.strip():
            index += 1
            continue

        block, index = _take_paragraph(lines, index)
        html.append(block)

    return "\n".join(html)


# --- fenced code --------------------------------------------------------------


def _is_fence(line):
    return line.strip().startswith("```")


def _take_fence(lines, index):
    """Everything up to the closing fence, verbatim.

    Verbatim is the point: this is where the diagrams live, and a box drawn with
    ─ and │ only lines up if nothing touches the spacing. An unterminated fence
    runs to the end of the briefing rather than swallowing the rest as prose.
    """
    language = lines[index].strip()[3:].strip()
    index += 1
    body = []
    while index < len(lines) and not _is_fence(lines[index]):
        body.append(lines[index])
        index += 1
    if index < len(lines):
        index += 1  # the closing fence itself

    # The language label is already escaped; restrict it further so it can only
    # ever be a class name.
    css = ""
    if re.fullmatch(r"[A-Za-z0-9_+-]{1,20}", language or ""):
        css = f' class="lang-{language}"'
    return f"<pre class=\"md-code\"><code{css}>{chr(10).join(body)}</code></pre>", index


# --- headings -----------------------------------------------------------------


def _heading(line):
    match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
    if not match:
        return None
    tag = _HEADING_LEVELS[len(match.group(1))]
    return f"<{tag} class=\"md-h\">{_inline(match.group(2))}</{tag}>"


# --- lists --------------------------------------------------------------------


_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def _is_list_item(line):
    return bool(_BULLET.match(line) or _NUMBER.match(line))


def _take_list(lines, index):
    ordered = bool(_NUMBER.match(lines[index]))
    pattern = _NUMBER if ordered else _BULLET
    items = []
    while index < len(lines):
        match = pattern.match(lines[index])
        if match:
            items.append(match.group(1).strip())
            index += 1
            continue
        # A wrapped continuation line belongs to the item above it.
        if items and lines[index].strip() and not _is_list_item(lines[index]) \
                and lines[index].startswith((" ", "\t")):
            items[-1] += " " + lines[index].strip()
            index += 1
            continue
        break
    tag = "ol" if ordered else "ul"
    body = "".join(f"<li>{_inline(item)}</li>" for item in items)
    return f'<{tag} class="md-list">{body}</{tag}>', index


# --- tables -------------------------------------------------------------------


def _is_table_row(line):
    return line.strip().startswith("|") and line.strip().endswith("|") and line.count("|") >= 3


def _is_table_divider(line):
    stripped = line.strip()
    return bool(stripped) and bool(re.fullmatch(r"\|[\s:|-]+\|", stripped)) and "-" in stripped


def _cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _take_table(lines, index):
    head = _cells(lines[index])
    index += 2  # header and divider
    rows = []
    while index < len(lines) and _is_table_row(lines[index]):
        rows.append(_cells(lines[index]))
        index += 1

    header = "".join(f"<th>{_inline(cell)}</th>" for cell in head)
    body = "".join(
        "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f'<table class="md-table"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>', index


# --- block quotes -------------------------------------------------------------


def _is_quote(line):
    return line.lstrip().startswith("&gt;")  # already escaped, so > is &gt;


def _take_quote(lines, index):
    body = []
    while index < len(lines) and _is_quote(lines[index]):
        body.append(lines[index].lstrip()[4:].strip())
        index += 1
    return f'<blockquote class="md-quote">{_inline(" ".join(body))}</blockquote>', index


# --- paragraphs ---------------------------------------------------------------


def _take_paragraph(lines, index):
    # The first line is consumed unconditionally. render() only reaches here for
    # a line nothing else claimed, and a paragraph that could decline to consume
    # anything would leave the index where it was and spin forever — which is
    # exactly what a |pipe row| with no divider underneath used to do.
    body = [lines[index].strip()]
    index += 1
    while index < len(lines) and lines[index].strip() and not (
        _is_fence(lines[index]) or _is_list_item(lines[index])
        or _heading(lines[index]) is not None or _is_quote(lines[index])
        or _is_table_row(lines[index])
    ):
        body.append(lines[index].strip())
        index += 1
    return f'<p class="md-p">{_inline(" ".join(body))}</p>', index


# --- inline -------------------------------------------------------------------

# Inline code is taken first and its content is not re-scanned, so `**not bold**`
# inside backticks stays literal — which matters when the subject is code.
_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])")


def _inline(text):
    parts = []
    last = 0
    for match in _CODE.finditer(text):
        parts.append(_emphasis(text[last:match.start()]))
        parts.append(f'<code class="md-inline">{match.group(1)}</code>')
        last = match.end()
    parts.append(_emphasis(text[last:]))
    return "".join(parts)


def _emphasis(text):
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return _ITALIC.sub(r"<em>\1</em>", text)
