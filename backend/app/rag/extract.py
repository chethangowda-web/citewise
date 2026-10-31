"""Turn an uploaded file into plain text.

Every format raises ValueError (and only ValueError) for a file it cannot read, so the API answers 422 rather
than crashing. HTML and Word files can carry text a reader never sees (display:none blocks, hidden runs), which
is a standard way to smuggle instructions to a model; that text is dropped here, before anything is embedded.
"""
import io
import zipfile
from html.parser import HTMLParser

from defusedxml import ElementTree as SafeET
from pypdf import PdfReader
from pypdf.errors import PyPdfError

SUPPORTED_EXTENSIONS = (".pdf", ".md", ".txt", ".docx", ".html", ".htm")
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024  # a small .docx can unpack to something enormous (a zip bomb)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return _pdf_text(data)
    if name.endswith(".docx"):
        return _docx_text(data)
    if name.endswith((".html", ".htm")):
        return _html_text(data.decode("utf-8", errors="replace"))
    return data.decode("utf-8", errors="replace")


def _pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except (PyPdfError, ValueError, KeyError, OSError, RecursionError) as e:
        raise ValueError("This PDF could not be read. It may be damaged or password protected.") from e


def _docx_text(data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        info = zf.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ValueError("This is not a valid .docx file.") from e
    if info.file_size > MAX_DOCX_XML_BYTES:
        raise ValueError("This .docx unpacks to something too large to read safely.")
    try:
        root = SafeET.fromstring(zf.read(info))  # refuses entity-expansion and external-entity tricks
    except (SafeET.ParseError, ValueError, zipfile.BadZipFile) as e:  # defusedxml's errors are ValueErrors
        raise ValueError("This .docx could not be read.") from e

    paragraphs: list[str] = []
    for p in root.iter(_W + "p"):
        parts: list[str] = []
        for run in p.iter(_W + "r"):
            props = run.find(_W + "rPr")
            if props is not None and props.find(_W + "vanish") is not None:
                continue  # formatted as hidden text
            for node in run:
                if node.tag == _W + "t" and node.text:
                    parts.append(node.text)
                elif node.tag == _W + "tab":
                    parts.append("\t")
                elif node.tag == _W + "br":
                    parts.append("\n")
        text = "".join(parts).strip()
        if not text:
            continue
        style = p.find(f"{_W}pPr/{_W}pStyle")
        if style is not None and (style.get(_W + "val") or "").lower().startswith(("heading", "title")):
            text = "# " + text
        paragraphs.append(text)
    return "\n\n".join(paragraphs)


_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
_SKIP = {"script", "style", "noscript", "template", "head", "svg", "iframe", "object", "canvas"}
_BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "table", "tr", "ul", "ol",
          "blockquote", "pre", "figure", "form", "fieldset", "dl", "dt", "dd"}
_HIDING_STYLES = ("display:none", "visibility:hidden", "font-size:0", "opacity:0", "height:0;overflow:hidden")


def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    d = {k: (v or "") for k, v in attrs}
    style = d.get("style", "").lower().replace(" ", "")
    return "hidden" in d or d.get("aria-hidden", "").lower() == "true" or any(s in style for s in _HIDING_STYLES)


class _TextExtractor(HTMLParser):
    """Visible text only, with headings and list items kept recognisable. Comments never reach the output."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[tuple[str, bool]] = []  # open elements and whether each one hides its content

    @property
    def _hidden(self) -> bool:
        return any(h for _, h in self.stack)

    def _break(self) -> None:
        if self.out and not self.out[-1].endswith("\n\n"):
            self.out.append("\n\n")

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _VOID:
            if tag in ("br", "hr") and not self._hidden:
                self.out.append("\n")
            return
        self.stack.append((tag, tag in _SKIP or _is_hidden(attrs)))
        if self._hidden:
            return
        if tag in _BLOCK or tag in ("h1", "h2", "h3", "h4", "h5", "h6", "li"):
            self._break()
        if tag[0] == "h" and len(tag) == 2 and tag[1].isdigit():
            self.out.append("# ")
        elif tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        names = [n for n, _ in self.stack]
        if tag in names:  # sloppy HTML: close everything opened since the matching start tag
            del self.stack[len(names) - 1 - names[::-1].index(tag):]
        if not self._hidden and (tag in _BLOCK or tag in ("li", "h1", "h2", "h3", "h4", "h5", "h6")):
            self._break()

    def handle_data(self, data: str) -> None:
        if not self._hidden and data.strip():
            self.out.append(data if self.out and not self.out[-1].endswith(("\n", " ")) else data.lstrip())


def _html_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, AssertionError, RecursionError) as e:  # html.parser is lenient, but never trust input
        raise ValueError("This HTML file could not be read.") from e
    text = "".join(parser.out)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(lines).strip().replace("\n\n\n", "\n\n")
