"""Portable, local chat files."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile

from PIL import Image
from api_context import ApiContext, Attachment


def save_chat(path, history, image, draft, *, attachment_name="", attachment_text="", context=None,
              instructions=""):
    if context is None:
        context = ApiContext()
        context.attach(attachment_name, image, attachment_text)
    blobs = Path(path).parent / ".attachments"
    attachments = dict(context.missing_attachments)
    for reference, item in context.attachments.items():
        record = {"name": item.name}
        if item.image is not None:
            record["image"] = store_blob(blobs, item.png, ".png")
        if item.text:
            record["text"] = store_blob(blobs, item.text.encode("utf-8"), ".txt")
        attachments[reference] = record
    document = {
        "version": 2, "turns": history, "draft": draft, "attachments": attachments,
        "active_attachment": context.active, "pending_attachment": context.pending,
        "last_attachment": context.last_used, "last_image_turn": context.last_image_turn,
        "summary": {"through": context.through, "text": context.summary},
        "instructions": instructions,
    }
    write_json(path, document)


def store_blob(folder, content, suffix):
    filename = hashlib.sha256(content).hexdigest() + suffix
    path = folder / filename
    if not path.is_file():
        folder.mkdir(exist_ok=True)
        atomic_write(path, content)
    return filename


def atomic_write(path, content):
    path = Path(path)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            pending = Path(temp.name)
            temp.write(content)
        os.replace(pending, path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def write_json(path, document):
    atomic_write(path, json.dumps(document, ensure_ascii=False).encode("utf-8"))


def read_blob(folder, filename, suffix):
    if not isinstance(filename, str) or not re.fullmatch(r"[a-f0-9]{64}" + re.escape(suffix), filename):
        raise ValueError("Invalid attachment reference.")
    return (folder / filename).read_bytes()


def open_png(data):
    with Image.open(io.BytesIO(data), formats=["PNG"]) as png:
        return png.convert("RGB")


def load_chat(path):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") not in (1, 2):
        raise ValueError("This file is not a supported chat.")
    turns = document.get("turns")
    draft = document.get("draft", "")
    instructions = document.get("instructions")
    if (not isinstance(turns, list) or not isinstance(draft, str) or
            any(not isinstance(turn, list) or len(turn) != 2 or
                not all(isinstance(text, str) for text in turn) for turn in turns)):
        raise ValueError("The chat file contains invalid messages.")
    context = ApiContext()
    missing = 0
    if document["version"] == 1:
        name, text = document.get("attachment_name", ""), document.get("attachment_text", "")
        encoded = document.get("image_png")
        if (not isinstance(name, str) or not isinstance(text, str) or
                ("image_png" in document and not isinstance(encoded, str))):
            raise ValueError("The chat file contains an invalid attachment.")
        image = open_png(base64.b64decode(encoded, validate=True)) if encoded is not None else None
        context.attach(name or "Screenshot", image, text)
        if turns and context.active:
            context.pending = None
            context.last_used = context.active
            if image is not None:
                context.last_image_turn = len(turns)
    else:
        blobs = Path(path).parent / ".attachments"
        records = document.get("attachments", {})
        if not isinstance(records, dict):
            raise ValueError("The chat file contains invalid attachment references.")
        for reference, record in records.items():
            if not isinstance(record, dict) or not isinstance(record.get("name"), str):
                raise ValueError("The chat file contains an invalid attachment.")
            try:
                image = open_png(read_blob(blobs, record["image"], ".png")) if "image" in record else None
                text = read_blob(blobs, record["text"], ".txt").decode("utf-8") if "text" in record else ""
            except (OSError, ValueError, Image.DecompressionBombError):
                missing += 1
                context.missing_attachments[reference] = record
                continue
            context.attachments[reference] = Attachment(record["name"], image, text)
        for field, attribute in (("active_attachment", "active"), ("pending_attachment", "pending"),
                                 ("last_attachment", "last_used")):
            value = document.get(field)
            if isinstance(value, str) and (value in context.attachments or value in context.missing_attachments):
                setattr(context, attribute, value)
        last_image_turn = document.get("last_image_turn")
        if type(last_image_turn) is int and 0 <= last_image_turn <= len(turns):
            context.last_image_turn = last_image_turn
        summary = document.get("summary", {})
        through = summary.get("through") if isinstance(summary, dict) else None
        if type(through) is int and 0 <= through <= len(turns) and isinstance(summary.get("text"), str):
            context.through, context.summary = through, summary["text"]
    return [tuple(turn) for turn in turns], draft, context, missing, instructions if isinstance(instructions, str) else None
