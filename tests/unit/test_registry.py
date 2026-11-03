import importlib
import sys
import textwrap
from pathlib import Path

import pytest

from quince.core import formats
from quince.core.config import WheelPrefs
from quince.core.deps import DEPENDENCIES, Availability, DependencyStatus
from quince.core.operations import Accepts, Kind, Operation
from quince.core.registry import Registry, apply_prefs, discover
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


def _without(*missing):
    return Availability(DependencyStatus(d, d.id not in missing) for d in DEPENDENCIES)


ROUTES = [
    Route("jpg", "png", "imaging"),
    Route("heic", "png", "imaging", requires=("pillow-heif",)),
    Route("png", "jpg", "imaging"),
    Route("heic", "jpg", "imaging"),
    Route("docx", "png", "libreoffice"),
    Route("docx", "pdf", "libreoffice"),
]
MERGE = Operation(
    "tool.merge_pdfs",
    Kind.TOOL,
    "Merge PDFs",
    "x",
    Accepts(formats=frozenset({"pdf"}), min_items=2),
)
COMPRESS = Operation(
    "tool.compress",
    Kind.TOOL,
    "Compress",
    "x",
    Accepts(categories=frozenset({formats.Category.IMAGE})),
)
COPY_PATH = Operation(
    "action.copy_path", Kind.ACTION, "Copy Path", "x", Accepts.anything(), order=30
)
MOVE = Operation("action.move", Kind.ACTION, "Move", "x", Accepts.anything(), order=20)


@pytest.fixture
def registry():
    return Registry.from_parts(ROUTES, [MERGE, COMPRESS, COPY_PATH, MOVE])


def _ids(ops):
    return [op.id for op in ops]


def test_convert_operations_one_per_target_in_format_order(registry):
    assert _ids(registry.operations(Kind.CONVERT)) == [
        "convert.image.jpg",
        "convert.image.png",
        "convert.pdf.pdf",
    ]
    png = registry.get("convert.image.png")
    assert png.label == "PNG" and png.title == "PNG image" and png.icon == "image-x-generic"
    assert {r.source for r in png.routes} == {"jpg", "heic", "docx"}


def test_query_by_selection(registry):
    assert _ids(registry.query(_sel("jpg"), Kind.CONVERT)) == ["convert.image.png"]
    assert _ids(registry.query(_sel("heic"), Kind.CONVERT)) == [
        "convert.image.jpg",
        "convert.image.png",
    ]
    assert _ids(registry.query(_sel("docx"), Kind.CONVERT)) == [
        "convert.image.png",
        "convert.pdf.pdf",
    ]
    assert _ids(registry.query(_sel("jpg"), Kind.TOOL)) == ["tool.compress"]
    assert _ids(registry.query(_sel("pdf"), Kind.TOOL)) == []
    assert _ids(registry.query(_sel("pdf", "pdf"), Kind.TOOL)) == ["tool.merge_pdfs"]


def test_multi_format_selection_targets_every_file(registry):
    # JPG + HEIC: PNG works for both; JPG is offered because HEIC needs converting.
    assert _ids(registry.query(_sel("jpg", "heic"), Kind.CONVERT)) == [
        "convert.image.jpg",
        "convert.image.png",
    ]
    # Already all JPG: nothing to convert to JPG.
    assert "convert.image.jpg" not in _ids(registry.query(_sel("jpg", "jpg"), Kind.CONVERT))
    # PNG has no route to PNG, JPG has none to JPG, but each has one to the other.
    assert _ids(registry.query(_sel("png", "jpg"), Kind.CONVERT)) == [
        "convert.image.jpg",
        "convert.image.png",
    ]


@pytest.mark.parametrize("specs", [("png", "pdf"), ("/",), ("png", "/"), (None,), ("png", None)])
def test_mixed_folders_and_unknown_get_actions_only(registry, specs):
    sel = _sel(*specs)
    assert registry.query(sel, Kind.CONVERT) == []
    assert registry.query(sel, Kind.TOOL) == []
    assert _ids(registry.query(sel, Kind.ACTION)) == ["action.move", "action.copy_path"]


def test_actions_in_default_order(registry):
    assert _ids(registry.query(_sel("png"), Kind.ACTION)) == ["action.move", "action.copy_path"]


def test_missing_dependency_hides_only_affected_routes(registry):
    no_office = _without("soffice")
    assert _ids(registry.query(_sel("docx"), Kind.CONVERT, available=no_office)) == []
    assert _ids(registry.query(_sel("jpg"), Kind.CONVERT, available=no_office)) == [
        "convert.image.png"
    ]
    no_heif = _without("pillow-heif")
    assert _ids(registry.query(_sel("heic"), Kind.CONVERT, available=no_heif)) == [
        "convert.image.jpg"
    ]
    # One file of the selection cannot be converted: the target is hidden for all.
    assert "convert.image.png" not in _ids(
        registry.query(_sel("jpg", "heic"), Kind.CONVERT, available=no_heif)
    )


def test_unavailable_lists_routes_and_operations_with_reason():
    reg = Registry.from_parts(
        ROUTES, [Operation("tool.ocr", Kind.TOOL, "OCR", "x", Accepts(), requires=("pandoc",))]
    )
    hidden = reg.unavailable(_without("soffice", "pandoc"))
    assert [(h.id, h.label) for h in hidden] == [
        ("convert.image.png", "DOCX → PNG"),
        ("convert.pdf.pdf", "DOCX → PDF"),
        ("tool.ocr", "OCR"),
    ]
    assert hidden[0].reason == "needs soffice (sudo apt install libreoffice)"
    assert reg.unavailable(Availability.everything()) == []


def test_prefs_disable_and_reorder(registry):
    prefs = WheelPrefs(order=("action.copy_path",), disabled=frozenset())
    assert _ids(registry.query(_sel("png"), Kind.ACTION, prefs=prefs)) == [
        "action.copy_path",
        "action.move",
    ]
    prefs = WheelPrefs(disabled=frozenset({"convert.image.png", "convert.unknown"}))
    assert _ids(registry.query(_sel("heic"), Kind.CONVERT, prefs=prefs)) == ["convert.image.jpg"]


def test_apply_prefs_keeps_unlisted_order():
    ops = [MOVE, COPY_PATH, MERGE, COMPRESS]
    prefs = WheelPrefs(order=("tool.compress", "tool.nope", "action.move"))
    assert _ids(apply_prefs(ops, prefs)) == [
        "tool.compress",
        "action.move",
        "action.copy_path",
        "tool.merge_pdfs",
    ]


def test_register_rejects_duplicates_and_unknown_dependencies():
    reg = Registry([COPY_PATH])
    with pytest.raises(ValueError, match="duplicate"):
        reg.register(COPY_PATH)
    bad = Operation("tool.bad", Kind.TOOL, "Bad", "x", Accepts(), requires=("photoshop",))
    with pytest.raises(ValueError, match="unknown dependencies"):
        reg.register(bad)
    assert "action.copy_path" in reg and len(reg) == 1


# --- discovery ----------------------------------------------------------------


def _write_package(root: Path, name: str, image_routes: list[str]) -> None:
    pkg = root / name
    for sub in ("", "convert", "tools", "actions"):
        (pkg / sub).mkdir(parents=True, exist_ok=True)
        (pkg / sub / "__init__.py").write_text("")
    rows = "\n".join(f"    {r}," for r in image_routes)
    (pkg / "convert" / "image.py").write_text(
        f"from quince.core.routes import Route\n\nROUTES = (\n{rows}\n)\n"
    )
    (pkg / "convert" / "_helpers.py").write_text("# skipped: leading underscore\n")
    (pkg / "actions" / "copy_path.py").write_text(
        textwrap.dedent(
            """
            from quince.core.operations import Accepts, Kind, Operation

            OPERATION = Operation(
                "action.copy_path", Kind.ACTION, "Copy Path", "edit-copy", Accepts.anything()
            )
            """
        )
    )


def _fresh_discover(root: Path, name: str):
    for mod in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[mod]
    importlib.invalidate_caches()
    return discover(name)


def test_adding_a_route_line_changes_the_registry(tmp_path, monkeypatch):
    """Acceptance criterion: one new route line, no other code change."""
    monkeypatch.syspath_prepend(str(tmp_path))
    lines = ['Route("png", "jpg", "imaging")']
    _write_package(tmp_path, "fakeops", lines)
    before = _fresh_discover(tmp_path, "fakeops")
    assert _ids(before.query(_sel("png"), Kind.CONVERT)) == ["convert.image.jpg"]
    assert _ids(before.query(_sel("png"), Kind.ACTION)) == ["action.copy_path"]

    _write_package(tmp_path, "fakeops", [*lines, 'Route("png", "webp", "imaging")'])
    after = _fresh_discover(tmp_path, "fakeops")
    assert _ids(after.query(_sel("png"), Kind.CONVERT)) == [
        "convert.image.jpg",
        "convert.image.webp",
    ]
    assert after.routes.targets("png") == ["jpg", "webp"]


def test_discovery_rejects_modules_without_declarations(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_package(tmp_path, "badops", ['Route("png", "jpg", "imaging")'])
    (tmp_path / "badops" / "tools" / "empty.py").write_text("X = 1\n")
    with pytest.raises(ValueError, match="must define OPERATION"):
        _fresh_discover(tmp_path, "badops")


def test_builtin_operations_load():
    reg = discover()
    assert len(reg) > 0
    for op in reg:
        assert op.id.startswith(f"{op.kind.value}.")
    png = Selection((Item(Path("/x.png"), format=formats.get("png")),))
    assert reg.query(png, Kind.CONVERT, available=Availability.everything())
    assert reg.query(png, Kind.ACTION)
