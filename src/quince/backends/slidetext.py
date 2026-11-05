"""The text of a presentation, slide by slide, read straight from PPTX and ODP files.

Each slide gives its title, its text (bulleted points with their level, plain paragraphs and
tables) and its speaker notes. Pictures, charts, dates, footers and slide numbers are left
out. PPT is converted to PPTX by LibreOffice first (``quince.backends.text``).

The files are ZIP packages of XML; they are read with ``zipfile`` and ElementTree, without
any presentation library.
"""

from __future__ import annotations

import posixpath
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from quince.core.jobs import JobError

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
NOTES_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"

TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
PRES = "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"

MAX_XML_BYTES = 64 * 1024 * 1024  # a single slide part larger than this is refused

TITLE_TYPES = {"title", "ctrTitle"}
SKIPPED_TYPES = {"dt", "ftr", "sldNum", "hdr", "sldImg"}
ODP_TITLES = {"title"}
ODP_SKIPPED = {"date-time", "footer", "header", "page-number", "page", "graphic", "chart"}
ODP_LIST_CLASSES = {"outline"}


@dataclass
class Paragraph:
    text: str
    level: int = 0
    bullet: bool = False


@dataclass
class Table:
    rows: list[list[str]]


Block = Paragraph | Table


@dataclass
class Slide:
    number: int
    title: str = ""
    blocks: list[Block] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def heading(self) -> str:
        return self.title or f"Slide {self.number}"


def _xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    info = zf.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise JobError(f"The presentation part {name} is too large")
    return ET.fromstring(zf.read(info))


def _clean(text: str) -> str:
    return "\n".join(" ".join(line.split()) for line in text.split("\n")).strip()


# --- PPTX -------------------------------------------------------------------------------


def _rels(zf: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Relationship id → (type, target part) of ``part``."""
    folder, name = posixpath.split(part)
    rels_name = posixpath.join(folder, "_rels", f"{name}.rels")
    if rels_name not in zf.namelist():
        return {}
    out = {}
    for rel in _xml(zf, rels_name).iter(f"{REL}Relationship"):
        target = rel.get("Target", "")
        if rel.get("TargetMode") != "External":
            target = posixpath.normpath(posixpath.join(folder, target)).lstrip("/")
        out[rel.get("Id", "")] = (rel.get("Type", ""), target)
    return out


def _pptx_text(paragraph: ET.Element) -> str:
    parts = []
    for node in paragraph:
        if node.tag in (f"{A}r", f"{A}fld"):
            parts.append("".join(t.text or "" for t in node.iter(f"{A}t")))
        elif node.tag == f"{A}br":
            parts.append("\n")
    return _clean("".join(parts))


def _pptx_paragraphs(body: ET.Element, bulleted: bool) -> list[Paragraph]:
    out = []
    for p in body.iter(f"{A}p"):
        text = _pptx_text(p)
        if not text:
            continue
        ppr = p.find(f"{A}pPr")
        level = int(ppr.get("lvl", "0")) if ppr is not None else 0
        bullet = bulleted
        if ppr is not None:
            if ppr.find(f"{A}buNone") is not None:
                bullet = False
            elif ppr.find(f"{A}buChar") is not None or ppr.find(f"{A}buAutoNum") is not None:
                bullet = True
        out.append(Paragraph(text, level, bullet))
    return out


def _pptx_table(tbl: ET.Element) -> Table:
    rows = []
    for tr in tbl.iter(f"{A}tr"):
        row = []
        for tc in tr.findall(f"{A}tc"):
            if tc.get("hMerge") == "1" or tc.get("vMerge") == "1":
                row.append("")
                continue
            row.append("\n".join(p.text for p in _pptx_paragraphs(tc, False)))
        rows.append(row)
    return Table(rows)


def _pptx_shapes(tree: ET.Element) -> Iterator[ET.Element]:
    """Shapes in reading (document) order, groups flattened."""
    for node in tree:
        if node.tag == f"{P}grpSp":
            yield from _pptx_shapes(node)
        elif node.tag in (f"{P}sp", f"{P}graphicFrame"):
            yield node


def _placeholder(shape: ET.Element) -> ET.Element | None:
    return shape.find(f"./{P}nvSpPr/{P}nvPr/{P}ph")


def _pptx_slide(root: ET.Element, slide: Slide) -> None:
    tree = root.find(f"./{P}cSld/{P}spTree")
    if tree is None:
        return
    for shape in _pptx_shapes(tree):
        if shape.tag == f"{P}graphicFrame":
            for tbl in shape.iter(f"{A}tbl"):
                slide.blocks.append(_pptx_table(tbl))
            continue
        body = shape.find(f"{P}txBody")
        if body is None:
            continue
        ph = _placeholder(shape)
        kind = None if ph is None else ph.get("type", "body")
        if kind in SKIPPED_TYPES:
            continue
        paragraphs = _pptx_paragraphs(body, bulleted=kind in ("body", "obj"))
        if kind in TITLE_TYPES and not slide.title:
            slide.title = " ".join(p.text.replace("\n", " ") for p in paragraphs)
        else:
            slide.blocks += paragraphs


def _pptx_notes(root: ET.Element) -> list[str]:
    tree = root.find(f"./{P}cSld/{P}spTree")
    notes: list[str] = []
    for shape in _pptx_shapes(tree) if tree is not None else ():
        ph = _placeholder(shape)
        body = shape.find(f"{P}txBody")
        if ph is not None and ph.get("type") == "body" and body is not None:
            notes += [p.text for p in _pptx_paragraphs(body, False)]
    return notes


def read_pptx(path: Path) -> list[Slide]:
    with zipfile.ZipFile(path) as zf:
        presentation = "ppt/presentation.xml"
        rels = _rels(zf, presentation)
        order = _xml(zf, presentation).find(f"{P}sldIdLst")
        slides = []
        for sld in order if order is not None else ():
            _type, part = rels.get(sld.get(f"{R}id", ""), ("", ""))
            if part not in zf.namelist():
                continue
            slide = Slide(len(slides) + 1)
            _pptx_slide(_xml(zf, part), slide)
            for rel_type, target in _rels(zf, part).values():
                if rel_type == NOTES_REL and target in zf.namelist():
                    slide.notes = _pptx_notes(_xml(zf, target))
            slides.append(slide)
        return slides


# --- ODP --------------------------------------------------------------------------------


def _odf_text(node: ET.Element) -> str:
    """The text of a ``text:p`` / ``text:h`` with spaces, tabs and line breaks."""
    parts = [node.text or ""]
    for child in node:
        if child.tag == f"{TEXT}s":
            parts.append(" " * int(child.get(f"{TEXT}c", "1")))
        elif child.tag == f"{TEXT}tab":
            parts.append("\t")
        elif child.tag == f"{TEXT}line-break":
            parts.append("\n")
        elif child.tag not in (f"{OFFICE}annotation", f"{TEXT}note"):
            parts.append(_odf_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _odf_blocks(container: ET.Element, *, bulleted: bool, level: int = 0) -> list[Paragraph]:
    out = []
    for node in container:
        if node.tag in (f"{TEXT}p", f"{TEXT}h"):
            text = _clean(_odf_text(node))
            if text:
                out.append(Paragraph(text, level, bulleted))
        elif node.tag == f"{TEXT}list":
            for item in node:
                if item.tag in (f"{TEXT}list-item", f"{TEXT}list-header"):
                    out += _odf_list_item(item, level)
    return out


def _odf_list_item(item: ET.Element, level: int) -> list[Paragraph]:
    """A list item's paragraphs at ``level``; a list inside it is one level deeper."""
    out = []
    for node in item:
        if node.tag in (f"{TEXT}p", f"{TEXT}h"):
            text = _clean(_odf_text(node))
            if text:
                out.append(Paragraph(text, level, True))
        elif node.tag == f"{TEXT}list":
            for sub in node:
                if sub.tag in (f"{TEXT}list-item", f"{TEXT}list-header"):
                    out += _odf_list_item(sub, level + 1)
    return out


def _odf_table(table: ET.Element) -> Table:
    rows = []
    for row in table.iter(f"{TABLE}table-row"):
        cells = []
        for cell in row:
            if cell.tag == f"{TABLE}covered-table-cell":
                cells.append("")
            elif cell.tag == f"{TABLE}table-cell":
                paragraphs = _odf_blocks(cell, bulleted=False)
                text = "\n".join(p.text for p in paragraphs)
                repeat = min(int(cell.get(f"{TABLE}number-columns-repeated", "1")), 64)
                cells += [text] * repeat
        rows.append(cells)
    return Table(rows)


def _odf_shapes(page: ET.Element) -> Iterator[ET.Element]:
    for node in page:
        if node.tag == f"{DRAW}g":
            yield from _odf_shapes(node)
        elif node.tag in (f"{DRAW}frame", f"{DRAW}custom-shape", f"{DRAW}rect"):
            yield node


def _odp_slide(page: ET.Element, slide: Slide) -> None:
    for shape in _odf_shapes(page):
        kind = shape.get(f"{PRES}class", "")
        if kind in ODP_SKIPPED:
            continue
        for table in shape.findall(f"{TABLE}table"):
            slide.blocks.append(_odf_table(table))
        box = shape.find(f"{DRAW}text-box")
        container = box if box is not None else shape
        paragraphs = _odf_blocks(container, bulleted=kind in ODP_LIST_CLASSES)
        if kind in ODP_TITLES and not slide.title:
            slide.title = " ".join(p.text.replace("\n", " ") for p in paragraphs)
        else:
            slide.blocks += paragraphs
    notes = page.find(f"{PRES}notes")
    if notes is not None:
        for frame in _odf_shapes(notes):
            if frame.get(f"{PRES}class") == "notes":
                box = frame.find(f"{DRAW}text-box")
                if box is not None:
                    slide.notes += [p.text for p in _odf_blocks(box, bulleted=False)]


def read_odp(path: Path) -> list[Slide]:
    with zipfile.ZipFile(path) as zf:
        root = _xml(zf, "content.xml")
    body = root.find(f"./{OFFICE}body/{OFFICE}presentation")
    slides = []
    for page in body.findall(f"{DRAW}page") if body is not None else ():
        slide = Slide(len(slides) + 1)
        _odp_slide(page, slide)
        slides.append(slide)
    return slides


def read(path: Path, format_id: str) -> list[Slide]:
    """The slides of a PPTX or ODP file; a broken file raises ``JobError``."""
    reader = {"pptx": read_pptx, "odp": read_odp}[format_id]
    try:
        return reader(path)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError) as exc:
        raise JobError(f"Cannot read the presentation ({exc})") from exc
