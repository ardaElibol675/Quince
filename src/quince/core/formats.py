"""Format registry: every file format Quince knows, with extensions, MIME types and category.

The order of ``FORMATS`` is the default order of format slices on the Convert wheel.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum


class Category(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    PDF = "pdf"
    DOCUMENT = "document"
    SLIDES = "slides"
    SPREADSHEET = "spreadsheet"
    SUBTITLE = "subtitle"
    ARCHIVE = "archive"
    CODE = "code"

    @property
    def label(self) -> str:
        return _CATEGORY_LABELS[self]

    @property
    def icon(self) -> str:
        """Freedesktop icon name used for this category."""
        return _CATEGORY_ICONS[self]


_CATEGORY_LABELS = {
    Category.IMAGE: "Image",
    Category.AUDIO: "Audio",
    Category.VIDEO: "Video",
    Category.PDF: "PDF",
    Category.DOCUMENT: "Document",
    Category.SLIDES: "Slides",
    Category.SPREADSHEET: "Spreadsheet",
    Category.SUBTITLE: "Subtitle",
    Category.ARCHIVE: "Archive",
    Category.CODE: "Code",
}

_CATEGORY_ICONS = {
    Category.IMAGE: "image-x-generic",
    Category.AUDIO: "audio-x-generic",
    Category.VIDEO: "video-x-generic",
    Category.PDF: "application-pdf",
    Category.DOCUMENT: "x-office-document",
    Category.SLIDES: "x-office-presentation",
    Category.SPREADSHEET: "x-office-spreadsheet",
    Category.SUBTITLE: "text-x-generic",
    Category.ARCHIVE: "package-x-generic",
    Category.CODE: "text-x-script",
}


@dataclass(frozen=True)
class Format:
    """A file format.

    ``extensions`` are lowercase without the leading dot; the first one is used for outputs.
    ``mime_types`` use shared-mime-info names; the first one is canonical.
    ``text`` marks plain-text formats, which content sniffing cannot tell apart.
    ``syntax`` is the language name of source code (a Markdown fence tag that pandoc's
    highlighter knows, e.g. ``python``).
    """

    id: str
    label: str
    name: str
    category: Category
    extensions: tuple[str, ...]
    mime_types: tuple[str, ...]
    text: bool = False
    syntax: str = ""

    @property
    def extension(self) -> str:
        return self.extensions[0]

    @property
    def mime_type(self) -> str:
        return self.mime_types[0]


_I, _A, _V = Category.IMAGE, Category.AUDIO, Category.VIDEO
_D, _S, _X = Category.DOCUMENT, Category.SLIDES, Category.SPREADSHEET
_C = Category.CODE
_OOXML = "application/vnd.openxmlformats-officedocument"
_ODF = "application/vnd.oasis.opendocument"

FORMATS: tuple[Format, ...] = (
    # Images
    Format("jpg", "JPG", "JPEG image", _I, ("jpg", "jpeg", "jpe"), ("image/jpeg",)),
    Format("png", "PNG", "PNG image", _I, ("png",), ("image/png",)),
    Format("webp", "WebP", "WebP image", _I, ("webp",), ("image/webp",)),
    Format("heic", "HEIC", "HEIC image", _I, ("heic", "heif"), ("image/heic", "image/heif")),
    Format("tiff", "TIFF", "TIFF image", _I, ("tiff", "tif"), ("image/tiff",)),
    Format("avif", "AVIF", "AVIF image", _I, ("avif",), ("image/avif",)),
    Format("bmp", "BMP", "BMP image", _I, ("bmp",), ("image/bmp", "image/x-bmp")),
    Format("gif", "GIF", "GIF image", _I, ("gif",), ("image/gif",)),
    Format("svg", "SVG", "SVG image", _I, ("svg",), ("image/svg+xml",), text=True),
    # Audio
    Format("mp3", "MP3", "MP3 audio", _A, ("mp3",), ("audio/mpeg",)),
    Format("m4a", "M4A", "MPEG-4 audio", _A, ("m4a",), ("audio/mp4", "audio/x-m4a")),
    Format("wav", "WAV", "WAV audio", _A, ("wav",), ("audio/x-wav", "audio/wav")),
    Format("flac", "FLAC", "FLAC audio", _A, ("flac",), ("audio/flac", "audio/x-flac")),
    Format("ogg", "OGG", "Ogg Vorbis audio", _A, ("ogg", "oga"), ("audio/ogg", "audio/x-vorbis+ogg")),
    Format("opus", "Opus", "Opus audio", _A, ("opus",), ("audio/x-opus+ogg", "audio/opus")),
    Format("aiff", "AIFF", "AIFF audio", _A, ("aiff", "aif", "aifc"), ("audio/x-aiff", "audio/aiff")),
    Format("wma", "WMA", "Windows Media audio", _A, ("wma",), ("audio/x-ms-wma",)),
    # Video
    Format("mp4", "MP4", "MPEG-4 video", _V, ("mp4", "m4v"), ("video/mp4",)),
    Format("mov", "MOV", "QuickTime video", _V, ("mov",), ("video/quicktime",)),
    Format("mkv", "MKV", "Matroska video", _V, ("mkv",), ("video/x-matroska",)),
    Format("webm", "WebM", "WebM video", _V, ("webm",), ("video/webm",)),
    Format("avi", "AVI", "AVI video", _V, ("avi",), ("video/x-msvideo", "video/vnd.avi")),
    Format("wmv", "WMV", "Windows Media video", _V, ("wmv",), ("video/x-ms-wmv",)),
    # PDF
    Format("pdf", "PDF", "PDF document", Category.PDF, ("pdf",), ("application/pdf",)),
    # Documents
    Format("docx", "DOCX", "Word document", _D, ("docx",), (f"{_OOXML}.wordprocessingml.document",)),
    Format("doc", "DOC", "Word 97-2003 document", _D, ("doc",), ("application/msword",)),
    Format("odt", "ODT", "OpenDocument text", _D, ("odt",), (f"{_ODF}.text",)),
    Format("rtf", "RTF", "Rich Text document", _D, ("rtf",), ("application/rtf", "text/rtf"), text=True),
    Format("md", "MD", "Markdown", _D, ("md", "markdown"), ("text/markdown", "text/x-markdown"), text=True),
    Format("txt", "TXT", "Plain text", _D, ("txt", "text"), ("text/plain",), text=True),
    Format("epub", "EPUB", "EPUB e-book", _D, ("epub",), ("application/epub+zip",)),
    Format("html", "HTML", "HTML page", _D, ("html", "htm", "xhtml"), ("text/html",), text=True),
    # Slides
    Format("pptx", "PPTX", "PowerPoint presentation", _S, ("pptx",), (f"{_OOXML}.presentationml.presentation",)),
    Format("ppt", "PPT", "PowerPoint 97-2003 presentation", _S, ("ppt",), ("application/vnd.ms-powerpoint",)),
    Format("odp", "ODP", "OpenDocument presentation", _S, ("odp",), (f"{_ODF}.presentation",)),
    # Spreadsheets
    Format("xlsx", "XLSX", "Excel workbook", _X, ("xlsx",), (f"{_OOXML}.spreadsheetml.sheet",)),
    Format("xls", "XLS", "Excel 97-2003 workbook", _X, ("xls",), ("application/vnd.ms-excel",)),
    Format("ods", "ODS", "OpenDocument spreadsheet", _X, ("ods",), (f"{_ODF}.spreadsheet",)),
    Format("csv", "CSV", "Comma-separated values", _X, ("csv",), ("text/csv",), text=True),
    Format("tsv", "TSV", "Tab-separated values", _X, ("tsv", "tab"), ("text/tab-separated-values",), text=True),
    # Subtitles
    Format("srt", "SRT", "SubRip subtitles", Category.SUBTITLE, ("srt",), ("application/x-subrip",), text=True),
    Format("vtt", "VTT", "WebVTT subtitles", Category.SUBTITLE, ("vtt",), ("text/vtt",), text=True),
    # Archives
    Format("zip", "ZIP", "ZIP archive", Category.ARCHIVE, ("zip",), ("application/zip",)),
    Format("tar", "TAR", "Tar archive", Category.ARCHIVE, ("tar",), ("application/x-tar",)),
    Format("targz", "TAR.GZ", "Gzip-compressed tar archive", Category.ARCHIVE, ("tar.gz", "tgz"), ("application/x-compressed-tar", "application/gzip")),
    Format("7z", "7Z", "7-Zip archive", Category.ARCHIVE, ("7z",), ("application/x-7z-compressed",)),
    Format("rar", "RAR", "RAR archive", Category.ARCHIVE, ("rar",), ("application/vnd.rar", "application/x-rar")),
    # Source code and configuration (plain text with a language; syntax = fence tag)
    Format("py", "Python", "Python source", _C, ("py", "pyw"), ("text/x-python3", "text/x-python"), text=True, syntax="python"),
    Format("js", "JavaScript", "JavaScript source", _C, ("js", "mjs", "cjs", "jsx"), ("application/javascript", "text/javascript"), text=True, syntax="javascript"),
    Format("ts", "TypeScript", "TypeScript source", _C, ("ts", "mts", "cts", "tsx"), ("text/x-typescript",), text=True, syntax="typescript"),
    Format("json", "JSON", "JSON data", _C, ("json",), ("application/json",), text=True, syntax="json"),
    Format("yaml", "YAML", "YAML data", _C, ("yaml", "yml"), ("application/x-yaml", "application/yaml"), text=True, syntax="yaml"),
    Format("toml", "TOML", "TOML configuration", _C, ("toml",), ("text/x-toml", "application/toml"), text=True, syntax="toml"),
    Format("ini", "INI", "INI configuration", _C, ("ini", "cfg", "conf"), ("text/x-ini",), text=True, syntax="ini"),
    Format("sh", "Shell", "Shell script", _C, ("sh", "bash", "zsh"), ("application/x-shellscript",), text=True, syntax="bash"),
    Format("c", "C", "C source", _C, ("c", "h"), ("text/x-csrc", "text/x-chdr"), text=True, syntax="c"),
    Format("cpp", "C++", "C++ source", _C, ("cpp", "cc", "cxx", "hpp", "hh", "hxx"), ("text/x-c++src", "text/x-c++hdr"), text=True, syntax="cpp"),
    Format("cs", "C#", "C# source", _C, ("cs",), ("text/x-csharp",), text=True, syntax="cs"),
    Format("java", "Java", "Java source", _C, ("java",), ("text/x-java",), text=True, syntax="java"),
    Format("kt", "Kotlin", "Kotlin source", _C, ("kt", "kts"), ("text/x-kotlin",), text=True, syntax="kotlin"),
    Format("go", "Go", "Go source", _C, ("go",), ("text/x-go",), text=True, syntax="go"),
    Format("rs", "Rust", "Rust source", _C, ("rs",), ("text/rust", "text/x-rust"), text=True, syntax="rust"),
    Format("php", "PHP", "PHP source", _C, ("php",), ("application/x-php",), text=True, syntax="php"),
    Format("rb", "Ruby", "Ruby source", _C, ("rb",), ("application/x-ruby",), text=True, syntax="ruby"),
    Format("lua", "Lua", "Lua source", _C, ("lua",), ("text/x-lua",), text=True, syntax="lua"),
    Format("sql", "SQL", "SQL script", _C, ("sql",), ("application/sql", "text/x-sql"), text=True, syntax="sql"),
    Format("xml", "XML", "XML document", _C, ("xml",), ("application/xml", "text/xml"), text=True, syntax="xml"),
    Format("css", "CSS", "CSS stylesheet", _C, ("css",), ("text/css",), text=True, syntax="css"),
)  # fmt: skip


def _index() -> tuple[dict[str, Format], dict[str, Format], dict[str, int]]:
    by_id: dict[str, Format] = {}
    by_ext: dict[str, Format] = {}
    position: dict[str, int] = {}
    for i, fmt in enumerate(FORMATS):
        if fmt.id in by_id:
            raise ValueError(f"duplicate format id {fmt.id!r}")
        by_id[fmt.id] = fmt
        position[fmt.id] = i
        for ext in fmt.extensions:
            if ext != ext.lower() or ext.startswith("."):
                raise ValueError(f"extension {ext!r} must be lowercase without a dot")
            if ext in by_ext:
                raise ValueError(f"extension {ext!r} is used by {by_ext[ext].id} and {fmt.id}")
            by_ext[ext] = fmt
    return by_id, by_ext, position


_BY_ID, _BY_EXT, _POSITION = _index()
# Extensions with a dot ("tar.gz") are matched before the plain suffix, longest first.
COMPOUND_EXTENSIONS: tuple[str, ...] = tuple(
    sorted((e for e in _BY_EXT if "." in e), key=len, reverse=True)
)


def get(format_id: str) -> Format:
    """The format with this id; raises ``KeyError`` for unknown ids."""
    return _BY_ID[format_id]


def find(format_id: str) -> Format | None:
    return _BY_ID.get(format_id)


def position(format_id: str) -> int:
    """Index of the format in ``FORMATS`` (its default order)."""
    return _POSITION[format_id]


def all_formats() -> Iterator[Format]:
    return iter(FORMATS)


def in_category(category: Category) -> list[Format]:
    return [f for f in FORMATS if f.category is category]


def text_format_ids() -> tuple[str, ...]:
    return tuple(f.id for f in FORMATS if f.text)


def split_extension(name: str) -> tuple[str, str]:
    """Split a file name into stem and extension, knowing compound extensions.

    ``"a.tar.gz"`` → ``("a", "tar.gz")``, ``"photo.JPG"`` → ``("photo", "JPG")``,
    ``".bashrc"`` → ``(".bashrc", "")``. The extension keeps its original case.
    """
    lower = name.lower()
    for ext in COMPOUND_EXTENSIONS:
        if lower.endswith("." + ext) and len(name) > len(ext) + 1:
            return name[: -len(ext) - 1], name[-len(ext) :]
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return name, ""
    return name[:dot], name[dot + 1 :]


def from_filename(name: str) -> Format | None:
    """The format matching the file name's extension (case-insensitive), if any."""
    _stem, ext = split_extension(name)
    return _BY_EXT.get(ext.lower()) if ext else None
