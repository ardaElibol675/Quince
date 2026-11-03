"""File type detection on the real generated fixtures (scripts/make_fixtures.py)."""

import shutil
from pathlib import Path

import pytest

from quince.core import filetype, formats

pytestmark = pytest.mark.integration

GENERATED = Path(__file__).resolve().parent.parent / "fixtures" / "generated"
FIXTURES = sorted(GENERATED.glob("sample*")) if GENERATED.is_dir() else []
# Plain-text formats cannot be told apart by content; without an extension they are text.
TEXT_ONLY = {"md", "csv", "tsv", "txt"}


def _ids(paths):
    return [p.name for p in paths]


@pytest.mark.skipif(not FIXTURES, reason="run scripts/make_fixtures.py first")
@pytest.mark.parametrize("path", FIXTURES, ids=_ids(FIXTURES))
def test_fixture_detected_by_name_and_by_content(path, tmp_path):
    expected = formats.from_filename(path.name)
    assert expected is not None
    assert filetype.detect(path) == expected

    bare = tmp_path / "file-without-extension"
    shutil.copyfile(path, bare)
    by_content = filetype.detect(bare)
    want = "txt" if expected.id in TEXT_ONLY else expected.id
    assert by_content is not None and by_content.id == want
