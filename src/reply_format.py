"""Render fenced code and paired Markdown emphasis in the transcript."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FencedCode:
    language: str


EMPHASIS = re.compile(
    r"(?P<code>`[^`\r\n]*`)"
    r"|(?m:^ {0,3}\#{1,6}[ \t]+(?P<heading>[^\r\n]+))"
    r"|(?<![\\*])(?P<marker>\*{2,3})(?![\s*])"
    r"(?P<text>[^*\r\n]+?)(?<![\s\\])(?P=marker)(?!\*)"
)
FENCE = re.compile(
    r"(?m)^ {0,3}(?P<fence>(?P<delimiter>`|~)(?P=delimiter){2,})(?P<info>[^\r\n]*)\r?\n"
    r"(?P<body>[\s\S]*?)(?:^ {0,3}(?P=fence)(?P=delimiter)*[ \t]*(?=\r?$)|\Z)"
)


def readable_math(text):
    def format_math(text):
        for command, replacement in (("sqrt", "√"), ("times", "×"), ("cdot", "·"),
                                     ("div", "÷"), ("pm", "±"), ("leq", "≤"), ("geq", "≥"),
                                     ("int", "∫"), ("iint", "∬"), ("oint", "∮")):
            text = re.sub(r"\\" + command + r"(?![A-Za-z])", replacement, text)
        text = re.sub(r"\\[,;: ]|\\(?:quad|qquad)(?![A-Za-z])", " ", text)
        text = text.replace(r"\left", "").replace(r"\right", "")
        text = re.sub(r"\\(?:[dt]?frac)\s*(\d)\s*(\d)", r"\1/\2", text)
        while True:
            formatted = re.sub(r"\\(?:boxed|text|mathrm|mathbf)\{([^{}]*)\}", r"\1", text)
            formatted = re.sub(r"\^\{([0-9+-]+)\}|\^([0-9])", superscript, formatted)
            formatted = re.sub(r"_\{([0-9+-]+)\}|_([0-9])", subscript, formatted)
            formatted = re.sub(r"√\{([^{}]*)\}", r"√(\1)", formatted)
            formatted = re.sub(r"\\(?:[dt]?frac)\s*\{([^{}]*)\}\s*\{([^{}]*)\}",
                               r"(\1)/(\2)", formatted)
            formatted = re.sub(r"([∫∬∮][₀-₉₊₋⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]*)(?=\()", r"\1 ", formatted)
            if formatted == text:
                return formatted
            text = formatted

    def superscript(match):
        return (match[1] or match[2]).translate(str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻"))

    def subscript(match):
        return (match[1] or match[2]).translate(str.maketrans("0123456789+-", "₀₁₂₃₄₅₆₇₈₉₊₋"))

    text = re.sub(r"\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$",
                  lambda match: format_math(match[1] or match[2] or match[3] or ""), text)
    if re.search(r"\\(?:[dt]?frac|sqrt|boxed|int|iint|oint)(?![A-Za-z])", text):
        text = format_math(text)
    return text


def inline_spans(message):
    start = 0
    for match in EMPHASIS.finditer(message):
        yield readable_math(message[start:match.start()]), None
        if match["code"] is not None:
            yield match["code"][1:-1], "code"
        else:
            yield readable_math(match["heading"] or match["text"]), "emphasis"
        start = match.end()
    yield readable_math(message[start:]), None


def reply_spans(message):
    start = 0
    for match in FENCE.finditer(message):
        yield from inline_spans(message[start:match.start()])
        body = match["body"]
        info = match["info"].strip().split()
        if body.endswith("\r\n"):
            body = body[:-2]
        elif body.endswith("\n"):
            body = body[:-1]
        yield body, FencedCode(info[0][:32] if info else "")
        start = match.end()
    yield from inline_spans(message[start:])


def plain_reply(message):
    return "".join(text for text, _ in reply_spans(message))
