"""备份只复制受管理数据，检查锁、WAL、损坏归档和恢复边界。"""

import json
import sqlite3
from contextlib import closing
from zipfile import ZipFile

import pytest

from magi.deployment import BUSINESS, MANIFEST, backup, install
from magi.harness.config import load_config
from magi.personal.locking import ApplicationLock
from tests.test_web_mvp import env as env


def test_backup_lock_and_restore_committed_wal(env, tmp_path):
    settings, service, _, _ = env
    archive = tmp_path / "backup.zip"
    with ApplicationLock(settings.business_db_path.with_suffix(".lock")):
        with pytest.raises(RuntimeError):
            backup(settings, archive)
    assert not archive.exists()

    with closing(sqlite3.connect(settings.business_db_path)) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        db.execute("BEGIN")
        db.execute("SELECT context_epoch FROM app_state").fetchone()
        saved = service.memories.add(
            key="time", value="每周4小时", scope="global", request_id="memory"
        )
        assert settings.business_db_path.with_name("personal.db-wal").exists()
        index = settings.business_db_path.with_suffix(".memory-index")
        index.mkdir()
        (index / "cache").write_text("derived", encoding="utf8")
        manifest = backup(settings, archive)
        assert set(manifest["files"]) == {BUSINESS}
    restored_path = install(tmp_path / "restored", settings, archive)
    restored = load_config(restored_path)
    assert restored.business_db_path != settings.business_db_path
    with closing(sqlite3.connect(restored.business_db_path)) as db:
        assert db.execute(
            "SELECT value FROM memories WHERE id=?", (saved.object_id,)
        ).fetchone() == ("每周4小时",)
    with pytest.raises(ValueError, match="新目录"):
        install(tmp_path / "restored", settings, archive)
    with pytest.raises(ValueError, match="新文件"):
        backup(settings, archive)


@pytest.mark.parametrize("kind", ["path", "digest"])
def test_restore_rejects_unsafe_or_damaged_archive_before_publish(env, tmp_path, kind):
    settings, _, _, _ = env
    source = tmp_path / "valid.zip"
    backup(settings, source)
    invalid = tmp_path / "invalid.zip"
    with ZipFile(source) as previous, ZipFile(invalid, "w") as output:
        manifest = json.loads(previous.read(MANIFEST))
        data = previous.read(BUSINESS)
        if kind == "path":
            manifest["files"]["../escape"] = manifest["files"][BUSINESS]
            output.writestr("../escape", data)
        else:
            manifest["files"][BUSINESS]["sha256"] = "0" * 64
        output.writestr(BUSINESS, data)
        output.writestr(MANIFEST, json.dumps(manifest))
    target = tmp_path / "restore"
    with pytest.raises(ValueError):
        install(target, settings, invalid)
    assert not target.exists()
    assert not (tmp_path / "escape").exists()
