"""Local state and bounded request context for API-key providers."""

from dataclasses import dataclass
from functools import cached_property
import io
import json
import math
import re
from uuid import uuid4


DEFAULT_CONTEXT_BUDGET = 16_384
RESPONSE_RESERVE = 2_048
MIN_CONTEXT_BUDGET = 3_072
MAX_CONTEXT_BUDGET = 1_000_000

REFERENCE = re.compile(
    r"\b(?:the|that|this|previous|earlier|first|second|third|last)\s+"
    r"(?:screenshot|image|picture|photo|attachment|document|chart)\b"
    r"|\b(?:screenshot|image|picture|photo)\s+(?:above|attached|again|#?\d+)\b"
    r"|\b(?:describe|read|analyze|transcribe)\s+(?:screenshot|image|document)\b",
    re.IGNORECASE,
)
VISUAL_FOLLOWUP = re.compile(
    r"\b(?:what (?:color|colour|shape) (?:is|was) (?:it|that|this)|"
    r"what does (?:it|that|this)|does (?:it|that) show|"
    r"(?:read|zoom|describe|transcribe|look at) (?:it|that))\b",
    re.IGNORECASE,
)
IMPORTANT = re.compile(
    r"\b(?:decid\w*|decis\w*|must|required|never|prefer\w*|constraint\w*|"
    r"todo|pending|remaining|unfinished|next step|still need|error|block\w*)\b",
    re.IGNORECASE,
)
NUMBERED_IMAGE = re.compile(r"\b(?:screenshot|image|picture|photo)\s+#?(\d+)\b", re.IGNORECASE)


@dataclass
class Attachment:
    name: str
    image: object = None
    text: str = ""

    @cached_property
    def png(self):
        buffer = io.BytesIO()
        self.image.save(buffer, format="PNG")
        return buffer.getvalue()


@dataclass
class RequestContext:
    turns: list
    summary: str
    through: int
    attachment_id: str | None
    attachment: Attachment | None


class ApiContext:
    def __init__(self):
        self.attachments = {}
        self.missing_attachments = {}
        self.active = None
        self.pending = None
        self.last_used = None
        self.last_image_turn = -1
        self.summary = ""
        self.through = 0

    def attach(self, name, image, text):
        if self.pending in self.attachments:
            self.attachments.pop(self.pending)
        self.active = None
        self.pending = None
        if image is not None or text:
            self.active = uuid4().hex
            self.attachments[self.active] = Attachment(name, image, text)
            self.pending = self.active

    def select_attachment(self, question, turn_count):
        if self.pending in self.attachments:
            return self.pending
        query = question.casefold()
        for reference, item in reversed(self.attachments.items()):
            if item.name and "." in item.name and item.name.casefold() in query:
                return reference
        images = [reference for reference, item in self.attachments.items() if item.image is not None]
        numbered = NUMBERED_IMAGE.search(query)
        if numbered:
            index = int(numbered[1]) - 1
            return images[index] if 0 <= index < len(images) else None
        if REFERENCE.search(query):
            match = re.search(r"\b(first|second|third)\s+(?:screenshot|image|picture|photo)\b", query)
            if match:
                ordinal = ("first", "second", "third").index(match[1])
                return images[ordinal] if ordinal < len(images) else None
            if re.search(r"\b(?:document|attachment)\b", query) and self.active in self.attachments:
                return self.active
            if re.search(r"\b(?:earlier|previous)\b", query) and len(images) > 1:
                return images[-2]
            if self.last_used in images:
                return self.last_used
            return images[-1] if images else self.active
        if self.last_used in self.attachments and turn_count == self.last_image_turn and VISUAL_FOLLOWUP.search(query):
            return self.last_used
        return None

    def commit(self, context, turn_count):
        self.summary, self.through = context.summary, context.through
        self.note_attachment(context.attachment_id, turn_count)

    def note_attachment(self, reference, turn_count):
        if reference in self.attachments:
            self.last_used = reference
            if self.pending == reference:
                self.pending = None
            if self.attachments[reference].image is not None:
                self.last_image_turn = turn_count + 1


def summarize(previous, turns, max_chars):
    lines = previous.splitlines()
    for question, answer in turns:
        for role, message in (("User", question), ("Assistant", answer)):
            sentences = [part.strip() for part in re.split(r"\n+|(?<=[.!?])\s+", message) if part.strip()]
            if sentences:
                salient = [part for part in sentences if IMPORTANT.search(part)] or sentences[:1]
                lines.extend(f"{role}: {part[:360]}" for part in salient[:4])
    lines = list(dict.fromkeys(lines))
    chosen = []
    size = 0
    ranked = sorted(range(len(lines)), key=lambda index: (bool(IMPORTANT.search(lines[index])), index), reverse=True)
    for index in ranked:
        line = lines[index]
        if size + len(line) + 1 <= max_chars:
            chosen.append(index)
            size += len(line) + 1
    return "\n".join(lines[index] for index in sorted(chosen))


def estimate_input(instructions, entries, image=None, tools=()):
    # Conservative approximation for text, with framing and image tiles accounted for.
    text = sum(math.ceil(len(value.encode("utf-8")) / 2) + 12 for _, value in entries)
    tokens = 64 + text + math.ceil(len(instructions.encode("utf-8")) / 2)
    if tools:
        tokens += math.ceil(len(json.dumps(tools, ensure_ascii=False).encode("utf-8")) / 2) + 32
    if image is not None:
        tokens += 768 + math.ceil(image.width / 512) * math.ceil(image.height / 512) * 950
    return tokens


def bounded_budget(value):
    if type(value) is int and MIN_CONTEXT_BUDGET <= value <= MAX_CONTEXT_BUDGET:
        return value
    return DEFAULT_CONTEXT_BUDGET
