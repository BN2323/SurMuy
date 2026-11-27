"""Load one image or document for the active chat."""

from pathlib import Path
from xml.etree import ElementTree
import zipfile

from PIL import Image, UnidentifiedImageError


def read_attachment(path):
    path = Path(path)
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Choose a file smaller than 25 MB.")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pdfminer.high_level import extract_text

        text = extract_text(str(path), maxpages=25)
    elif suffix == ".docx":
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        with zipfile.ZipFile(path) as doc:
            entry = doc.getinfo("word/document.xml")
            if entry.file_size > 8 * 1024 * 1024:
                raise ValueError("The Word document is too large.")
            document = ElementTree.fromstring(doc.read(entry))
        text = "\n".join("".join(span.text or "" for span in paragraph.iter(namespace + "t"))
                         for paragraph in document.iter(namespace + "p"))
    else:
        try:
            with Image.open(path) as image:
                return image.convert("RGB"), ""
        except UnidentifiedImageError:
            raw = path.read_bytes()
            try:
                encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
                text = raw.decode(encoding)
                if "\0" in text:
                    raise ValueError()
            except ValueError:
                raise ValueError("Choose an image, text file, PDF, or Word document.") from None
    text = text.strip()
    if not text:
        raise ValueError("The file has no readable text.")
    return None, text if len(text) <= 24_000 else text[:24_000] + "\n[Remaining text omitted]"
