import logging

from quince.core import logs


def test_log_file_in_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = logs.setup_logging(to_file=True)
        assert path == tmp_path / "quince" / "quince.log"
        logging.getLogger("quince.test").info("hello from the test")
        for handler in root.handlers:
            handler.flush()
        assert "hello from the test" in path.read_text()
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
                handler.close()


def test_unwritable_log_dir_does_not_stop_quince(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert logs.setup_logging(to_file=True) is None
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
