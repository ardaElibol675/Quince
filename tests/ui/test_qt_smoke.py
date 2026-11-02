from PySide6.QtWidgets import QLabel


def test_qt_widget_can_be_created(qtbot):
    label = QLabel("Quince")
    qtbot.addWidget(label)
    label.show()
    assert label.isVisible()
    assert label.text() == "Quince"
