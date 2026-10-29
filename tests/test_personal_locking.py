import pytest

from magi.personal.locking import ApplicationLock


def test_second_application_lock_is_rejected_and_release_allows_next(tmp_path):
    path = tmp_path / "magi.lock"
    with ApplicationLock(path):
        with pytest.raises(RuntimeError, match="already running"):
            with ApplicationLock(path):
                pass
    with ApplicationLock(path):
        pass
