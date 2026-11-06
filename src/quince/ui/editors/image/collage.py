"""Create collage: several pictures on one canvas (``backends/collage.py``).

Pictures can be put in order by dragging them in the list. Layout, columns, gap, background
and the "fill cells" choice are remembered; the width is suggested from the pictures' sizes.
The result is named after the first picture (``name-collage``): a JPG when every picture is a
JPG and the background is not transparent, a PNG otherwise.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
)

from quince.backends import collage, imageedit, painting
from quince.backends.collage import CollageStyle
from quince.core import forms
from quince.core.forms import natural_order
from quince.core.jobs import JobContext, JobError
from quince.core.operations import JobRequest
from quince.core.selection import Selection
from quince.ui.editors.base import EditorWindow, Slider, Swatches, debounced, heading, tool_button

REMEMBER_KEY = "editor.collage"
THUMB_SIDE = 900  # picture size used for the preview
PREVIEW_WIDTH = 1400
ICON = 48
BACKGROUNDS = [
    ("White", (255, 255, 255, 255)),
    ("Light gray", (236, 236, 236, 255)),
    ("Dark gray", (40, 40, 44, 255)),
    ("Black", (0, 0, 0, 255)),
    ("Transparent", (0, 0, 0, 0)),
]


@dataclass(frozen=True)
class CollageState:
    order: tuple[int, ...]
    style: CollageStyle
    width: int


def saved_style() -> CollageStyle:
    data = forms.load_section(REMEMBER_KEY)
    default = CollageStyle()
    try:
        background = tuple(int(v) for v in data.get("background", default.background))
        if len(background) != 4 or not all(0 <= v <= 255 for v in background):
            raise ValueError
        return CollageStyle(
            layout=str(data.get("layout", default.layout)),
            columns=min(12, max(0, int(data.get("columns", default.columns)))),
            gap=min(0.05, max(0.0, float(data.get("gap", default.gap)))),
            background=background,  # type: ignore[arg-type]
            fill=bool(data.get("fill", default.fill)),
        )
    except (TypeError, ValueError):
        return default


class CollageEditor(EditorWindow):
    name = "image.collage"
    title = "Create Collage"
    compare_label = ""
    save_label = "Create Collage"
    side_panel = True

    @classmethod
    def load(cls, selection: Selection) -> list[imageedit.Loaded]:
        files = [f for f in selection.files if f.format_id in imageedit.EDITABLE]
        if len(files) < 2:
            raise JobError("A collage needs at least two images")
        if len(files) > collage.MAX_PICTURES:
            raise JobError(f"A collage can have at most {collage.MAX_PICTURES} images")
        by_path = {f.path: f for f in files}
        ordered = [by_path[p] for p in natural_order(list(by_path))]
        return [imageedit.load(f.path, f.format_id, THUMB_SIDE) for f in ordered]

    def __init__(self, selection, data, parent=None) -> None:
        super().__init__(selection, data, parent)
        self.pictures: list[imageedit.Loaded] = data
        self.collage_style = saved_style()
        self.order: tuple[int, ...] = tuple(range(len(self.pictures)))
        self.out_width = collage.default_width(self._sizes(), self.collage_style)
        self._updating = False
        self._refresh = debounced(self, self.refresh, 30)

        self.side.addWidget(heading("Pictures"))
        self.list = QListWidget()
        self.list.setIconSize(QSize(ICON, ICON))
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.model().rowsMoved.connect(lambda *_a: self._on_reordered())
        self.list.setMinimumHeight(140)
        self.side.addWidget(self.list, 1)
        moves = QHBoxLayout()
        for text, icon, step in (("Up", "go-up", -1), ("Down", "go-down", 1)):
            button = tool_button(
                text, icon=icon, tip=f"Move the picked picture {text.lower()}", raised=True
            )
            button.clicked.connect(lambda _c=False, s=step: self.move_current(s))
            moves.addWidget(button)
        moves.addStretch(1)
        self.side.addLayout(moves)

        self.side.addWidget(heading("Layout"))
        form = QFormLayout()
        self.layout_combo = QComboBox()
        for key, label in collage.LAYOUTS:
            self.layout_combo.addItem(label, key)
        self.layout_combo.currentIndexChanged.connect(lambda _i: self._on_controls())
        form.addRow("Arrange", self.layout_combo)
        self.columns = QSpinBox()
        self.columns.setRange(0, 12)
        self.columns.setSpecialValueText("Auto")
        self.columns.valueChanged.connect(lambda _v: self._on_controls("columns"))
        form.addRow("Per row", self.columns)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(collage.MIN_WIDTH, collage.MAX_SIDE)
        self.width_spin.setSuffix(" px")
        self.width_spin.setSingleStep(100)
        self.width_spin.setKeyboardTracking(False)
        self.width_spin.valueChanged.connect(lambda _v: self._on_controls("width"))
        form.addRow("Width", self.width_spin)
        self.side.addLayout(form)
        self.fill = QCheckBox("Crop pictures to fill the cells")
        self.fill.toggled.connect(lambda _on: self._on_controls())
        self.side.addWidget(self.fill)
        self.gap = Slider("Gap", 0, 50, 15, default=15, show=lambda v: f"{v / 10:.1f} %")
        self.gap.changed.connect(lambda _v: self._on_controls("gap"))
        self.side.addWidget(self.gap)
        self.side.addWidget(heading("Background"))
        self.background = Swatches(BACKGROUNDS, self.collage_style.background, alpha=True)
        self.background.picked.connect(lambda _c: self._on_controls())
        self.side.addWidget(self.background)

        self.set_hint("Drag pictures in the list to change the order")
        self._fill_list()
        self._sync_controls()
        self.refresh()
        self.start_history()

    def _sizes(self) -> list[tuple[int, int]]:
        return [self.pictures[i].size for i in self.order]

    # --- state ------------------------------------------------------------------------

    def state(self) -> CollageState:
        return CollageState(self.order, self.collage_style, self.out_width)

    def set_state(self, state: CollageState) -> None:
        self.order, self.collage_style, self.out_width = state.order, state.style, state.width
        self._fill_list()
        self._sync_controls()
        self.refresh()

    def can_save(self) -> bool:
        return True

    def remember(self) -> None:
        data = asdict(self.collage_style)
        data["background"] = list(self.collage_style.background)
        forms.save_section(REMEMBER_KEY, data)

    # --- controls -----------------------------------------------------------------------

    def _fill_list(self) -> None:
        self._updating = True
        current = self.list.currentRow()
        self.list.clear()
        for index in self.order:
            loaded = self.pictures[index]
            thumb = loaded.preview.copy()
            thumb.thumbnail((ICON * 2, ICON * 2))
            item = QListWidgetItem(QIcon(QPixmap.fromImage(painting.to_qimage(thumb))), "")
            item.setText(loaded.path.name)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(f"{loaded.path.name} · {loaded.size[0]} × {loaded.size[1]}")
            self.list.addItem(item)
        if 0 <= current < self.list.count():
            self.list.setCurrentRow(current)
        self._updating = False

    def _sync_controls(self) -> None:
        self._updating = True
        style = self.collage_style
        self.layout_combo.setCurrentIndex(self.layout_combo.findData(style.layout))
        self.columns.setValue(style.columns)
        self.columns.setEnabled(style.layout in ("grid", "mosaic"))
        self.fill.setChecked(style.fill)
        self.fill.setEnabled(style.layout == "grid")
        self.gap.set_value(round(style.gap * 1000))
        self.background.set_color(style.background)
        self.width_spin.setValue(self.out_width)
        self._updating = False

    def _on_controls(self, merge: str | None = None) -> None:
        if self._updating:
            return
        layout = self.layout_combo.currentData()
        style = replace(
            self.collage_style,
            layout=layout,
            columns=self.columns.value(),
            gap=self.gap.value() / 1000,
            fill=self.fill.isChecked(),
            background=tuple(self.background.color()),
        )
        width = self.width_spin.value()
        if merge != "width" and (style.layout, style.columns) != (
            self.collage_style.layout,
            self.collage_style.columns,
        ):
            width = collage.default_width(self._sizes(), style)  # a new arrangement
        self.collage_style = style
        self.out_width = collage.fitted_width(self._sizes(), style, width)
        self._sync_controls()
        self._refresh.start()
        self.commit(merge)

    def _on_reordered(self) -> None:
        if self._updating:
            return
        self.order = tuple(
            self.list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.list.count())
        )
        self._refresh.start()
        self.commit()

    def move_current(self, step: int) -> None:
        row = self.list.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < len(self.order):
            return
        order = list(self.order)
        order[row], order[target] = order[target], order[row]
        self.order = tuple(order)
        self._fill_list()
        self.list.setCurrentRow(target)
        self.refresh()
        self.commit()

    # --- result -------------------------------------------------------------------------

    def result_size(self) -> tuple[float, float]:
        aspects = [w / h for w, h in self._sizes()]
        size, _cells = collage.layout(aspects, self.collage_style, self.out_width)
        return size

    def refresh(self) -> None:
        pictures = [self.pictures[i].preview for i in self.order]
        preview = collage.render(pictures, self.collage_style, PREVIEW_WIDTH)
        size = self.result_size()
        self.canvas.set_image(painting.to_qimage(preview), size)
        fmt = collage.output_format(
            [self.pictures[i].format_id for i in self.order], self.collage_style
        )
        self.info.setText(f"{round(size[0])} × {round(size[1])} · {fmt.upper()}")

    def build_job(self) -> JobRequest:
        sources = [(self.pictures[i].path, self.pictures[i].format_id) for i in self.order]
        style, width = self.collage_style, self.out_width
        target = collage.output_format([f for _p, f in sources], style)

        def run(ctx: JobContext) -> None:
            out = ctx.new_output(sources[0][0], extension=target, suffix="collage")
            collage.save_collage(ctx, sources, style, width, out)

        return JobRequest(run, f"Collage of {len(sources)} images")


EDITOR = CollageEditor
