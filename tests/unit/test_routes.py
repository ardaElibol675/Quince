import pytest

from quince.core.routes import Route, RouteTable, matrix


def test_route_validation():
    with pytest.raises(ValueError, match="unknown format"):
        Route("png", "nope", "imaging")
    with pytest.raises(ValueError, match="source equals target"):
        Route("png", "png", "imaging")
    with pytest.raises(ValueError, match="unknown backend"):
        Route("png", "jpg", "magic")


def test_route_requires_and_options():
    route = Route("svg", "png", "imaging", {"dpi": 96}, requires=("rsvg-convert",))
    assert route.all_requires == ("pillow", "rsvg-convert")
    assert route.options["dpi"] == 96
    with pytest.raises(TypeError):
        route.options["dpi"] = 1  # read-only
    assert str(route) == "SVG → PNG"
    assert hash(route) == hash(Route("svg", "png", "imaging", requires=("rsvg-convert",)))


def test_matrix():
    routes = matrix(("jpg", "png", "webp"), "imaging")
    assert len(routes) == 6
    assert {(r.source, r.target) for r in routes} >= {("jpg", "png"), ("webp", "jpg")}


def test_table_lookup_in_format_order():
    table = RouteTable([Route("png", "webp", "imaging"), Route("png", "jpg", "imaging")])
    table.add(Route("heic", "jpg", "imaging"))
    assert len(table) == 3
    assert table.find("png", "jpg").backend == "imaging"
    assert table.find("jpg", "png") is None
    assert table.targets("png") == ["jpg", "webp"]
    assert table.sources("jpg") == ["png", "heic"]
    assert list(table.by_target()) == ["jpg", "webp"]


def test_table_rejects_duplicate_pairs():
    table = RouteTable([Route("png", "jpg", "imaging")])
    with pytest.raises(ValueError, match="duplicate route"):
        table.add(Route("png", "jpg", "libreoffice"))
