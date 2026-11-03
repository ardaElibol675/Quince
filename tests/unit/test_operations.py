from pathlib import Path

import pytest

from quince.core import formats
from quince.core.formats import Category
from quince.core.operations import Accepts, Kind, Operation
from quince.core.routes import Route
from quince.core.selection import Item, Selection


def _sel(*specs):
    items = []
    for i, spec in enumerate(specs):
        if spec == "/":
            items.append(Item(Path(f"/d{i}"), is_dir=True))
        else:
            items.append(Item(Path(f"/f{i}"), format=formats.find(spec) if spec else None))
    return Selection(tuple(items))


def test_kind_wheels():
    assert [k.wheel for k in Kind] == ["convert", "tools", "actions"]
    assert Kind.from_wheel("tools") is Kind.TOOL
    with pytest.raises(ValueError):
        Kind.from_wheel("tool")


def test_accepts_formats_and_categories():
    pdf_only = Accepts(formats=frozenset({"pdf"}))
    assert pdf_only.matches(_sel("pdf"))
    assert not pdf_only.matches(_sel("png"))
    images = Accepts(categories=frozenset({Category.IMAGE}))
    assert images.matches(_sel("png", "heic"))
    assert not images.matches(_sel(None))
    assert not Accepts().matches(Selection())


def test_accepts_counts():
    two_plus = Accepts(formats=frozenset({"pdf"}), min_items=2)
    assert not two_plus.matches(_sel("pdf"))
    assert two_plus.matches(_sel("pdf", "pdf", "pdf"))
    single = Accepts(max_items=1)
    assert single.matches(_sel("png"))
    assert not single.matches(_sel("png", "png"))


def test_accepts_folders_and_mixed():
    assert not Accepts().matches(_sel("/"))
    assert not Accepts().matches(_sel("png", "pdf"))
    anything = Accepts.anything()
    for sel in (_sel("/"), _sel("png", "pdf"), _sel(None), _sel("png", "/", None)):
        assert anything.matches(sel)


def test_accepts_predicate():
    first_png = Accepts(predicate=lambda s: s.files[0].format_id == "png")
    assert first_png.matches(_sel("png", "jpg"))
    assert not first_png.matches(_sel("jpg", "png"))


def _op(**kw):
    base = {"id": "tool.x", "kind": Kind.TOOL, "label": "X", "icon": "x", "accepts": Accepts()}
    return Operation(**{**base, **kw})


def test_operation_validation():
    with pytest.raises(ValueError, match="must start with"):
        _op(id="action.x")
    with pytest.raises(ValueError, match="must start with"):
        _op(id="tool.")
    with pytest.raises(ValueError, match="either run or editor"):
        _op(run=lambda s: None, editor="crop")
    with pytest.raises(ValueError, match="only convert"):
        _op(routes=(Route("png", "jpg", "imaging"),))
    assert _op(editor="crop").editor == "crop"


def test_title_defaults_to_label():
    assert _op().title == "X"
    assert _op(title="Extra long name").title == "Extra long name"


def test_requirements_follow_the_routes_a_selection_uses():
    op = Operation(
        id="convert.image.png",
        kind=Kind.CONVERT,
        label="PNG",
        icon="image-x-generic",
        accepts=Accepts(formats=frozenset({"jpg", "docx", "png"})),
        routes=(Route("jpg", "png", "imaging"), Route("docx", "png", "libreoffice")),
    )
    assert op.requirements(_sel("jpg")) == ("pillow",)
    assert op.requirements(_sel("docx")) == ("soffice",)
    assert op.requirements(_sel("png")) == ()
    assert set(op.requirements()) == {"pillow", "soffice"}
    assert op.route_for("docx").backend == "libreoffice"
    assert op.route_for("pdf") is None
