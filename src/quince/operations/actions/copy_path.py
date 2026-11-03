"""Copy Path: put the selected paths on the clipboard, one per line.

Declared in Phase 2 so the Actions wheel has content; the action runs from Phase 6.
"""

from quince.core.operations import Accepts, Kind, Operation

OPERATION = Operation(
    id="action.copy_path",
    kind=Kind.ACTION,
    label="Copy Path",
    icon="edit-copy",
    accepts=Accepts.anything(),
    order=30,
)
