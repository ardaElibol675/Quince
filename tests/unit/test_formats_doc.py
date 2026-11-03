import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def test_formats_md_is_up_to_date():
    """docs/FORMATS.md must be regenerated after changing formats or routes."""
    sys.path.insert(0, str(SCRIPTS))
    try:
        import gen_formats_table
    finally:
        sys.path.remove(str(SCRIPTS))
    expected = gen_formats_table.render()
    assert gen_formats_table.OUT.read_text(encoding="utf-8") == expected, (
        "run: python scripts/gen_formats_table.py"
    )
    assert "| PNG → JPG | imaging | pillow |" in expected
