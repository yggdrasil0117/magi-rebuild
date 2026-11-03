"""独立实例初始化与离线备份。归档只包含明确选中的业务文件。"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from magi.harness.config import load_config
from magi.personal.locking import ApplicationLock
from magi.personal.store import SCHEMA_VERSION

BUSINESS = "business.sqlite3"
CHECKPOINT = "checkpoint.sqlite3"
SUMMARY = "checkpoint.summaries.db"
APPROVAL = "business.approvals.sqlite3"
DATABASES = {BUSINESS, CHECKPOINT, SUMMARY, APPROVAL}
DOCUMENT = re.compile(r"task-documents/[0-9a-f]{32}\.md")
MANIFEST = "manifest.json"
MAX_BYTES = 2 * 1024**3
MAX_MANIFEST = 1024**2
CHUNK = 1024**2


def file_record(path):
    """返回文件的实际字节数及摘要，按块读取以限制内存使用。"""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(CHUNK):
            digest.update(block)
            size += len(block)
    return {"size": size, "sha256": digest.hexdigest()}


def check_database(path, *, business=False):
    """检查 SQLite 页与业务版本；读取模式不会迁移原库。"""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError(f"SQLite 完整性检查失败：{path.name}")
        if business:
            row = db.execute("SELECT version FROM schema_meta").fetchone()
            if not row or not 1 <= row[0] <= SCHEMA_VERSION:
                raise ValueError("业务库版本超出本程序支持范围")
            return row[0]
    return None


def copy_database(source, target):
    """使用 SQLite 备份 API 读取一致副本，包含已提交的 WAL 数据。"""
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as current,
        closing(sqlite3.connect(target)) as snapshot,
    ):
        current.backup(snapshot)
        # 归档只依赖这一份文件，不依赖源库的 WAL 或 SHM。
        snapshot.execute("PRAGMA journal_mode=DELETE").fetchone()


def snapshot(settings, staging):
    """持有原实例的应用锁时调用；只复制已知数据库与有业务记录的工作单。"""
    source_paths = {
        BUSINESS: settings.business_db_path,
        CHECKPOINT: settings.db_path,
        SUMMARY: settings.db_path.with_suffix(".summaries.db"),
        APPROVAL: settings.business_db_path.with_suffix(".approvals.sqlite3"),
    }
    if len(set(source_paths.values())) != len(source_paths):
        raise ValueError("数据文件路径相互重叠")
    if not source_paths[BUSINESS].is_file():
        raise ValueError("没有可备份的业务库，请先启动该实例")
    records = {}
    for name, path in source_paths.items():
        if path.is_symlink():
            raise ValueError("数据文件不能是符号链接")
        if path.exists():
            if not path.is_file():
                raise ValueError("数据路径应指向文件")
            target = staging / name
            copy_database(path, target)
            check_database(target, business=name == BUSINESS)
            records[name] = file_record(target)
    missing = []
    directory = settings.business_db_path.parent / "task-documents"
    if directory.exists() and directory.resolve() != directory:
        raise ValueError("工作单目录不能是链接")
    with closing(sqlite3.connect(staging / BUSINESS)) as db:
        has_documents = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_documents'"
        ).fetchone()
        rows = (
            db.execute("SELECT id,status FROM task_documents").fetchall()
            if has_documents
            else []
        )
    for document_id, status in rows:
        name = f"task-documents/{document_id}.md"
        if not DOCUMENT.fullmatch(name):
            raise ValueError("业务库中存在非法工作单编号")
        path = directory / f"{document_id}.md"
        if path.is_symlink():
            raise ValueError("工作单文件不能是链接")
        if not path.exists():
            if status == "CREATED":
                missing.append(document_id)
            continue
        if not path.is_file():
            raise ValueError("工作单应为普通文件")
        target = staging / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, target)
        records[name] = file_record(target)
    return {"format": 1, "files": records, "missing_documents": missing}


def backup(settings, archive):
    """在同卷暂存、核对后再发布归档，不覆盖现有备份。"""
    archive = Path(archive)
    if archive.is_symlink():
        raise ValueError("归档目标不能是链接")
    archive = archive.resolve()
    if archive.exists() or not archive.parent.is_dir():
        raise ValueError("备份文件必须为已存在目录中的新文件")
    with TemporaryDirectory(prefix=".magi-backup-", dir=archive.parent) as directory:
        staging = Path(directory)
        with ApplicationLock(settings.business_db_path.with_suffix(".lock")):
            manifest = snapshot(settings, staging)
        raw_manifest = json.dumps(manifest, ensure_ascii=False).encode()
        if (
            len(raw_manifest) > MAX_MANIFEST
            or sum(record["size"] for record in manifest["files"].values()) > MAX_BYTES
        ):
            raise ValueError("备份超过归档上限")
        temporary = staging / "archive.zip"
        with ZipFile(temporary, "w", ZIP_DEFLATED) as output:
            output.writestr(MANIFEST, raw_manifest)
            for name in sorted(manifest["files"]):
                output.write(staging / name, name)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        if os.name == "nt":
            # Windows 同卷重命名拒绝覆盖，且不依赖目标文件系统支持硬链接。
            temporary.rename(archive)
        else:
            os.link(temporary, archive)
    return manifest


def read_manifest(source):
    """只接受本模块格式与固定条目名，拒绝重复、额外和路径越界条目。"""
    entries = source.namelist()
    if len(entries) != len(set(entries)) or MANIFEST not in entries:
        raise ValueError("归档缺少清单或条目重复")
    if source.getinfo(MANIFEST).file_size > MAX_MANIFEST:
        raise ValueError("清单过大")
    manifest = json.loads(source.read(MANIFEST))
    if not isinstance(manifest, dict) or manifest.get("format") != 1:
        raise ValueError("未知的备份格式")
    files = manifest.get("files")
    if (
        not isinstance(files, dict)
        or BUSINESS not in files
        or set(entries) != {MANIFEST, *files}
    ):
        raise ValueError("清单与归档条目不一致")
    size = 0
    for name, record in files.items():
        if name not in DATABASES and not DOCUMENT.fullmatch(name):
            raise ValueError("归档含未知数据路径")
        if (
            not isinstance(record, dict)
            or type(record.get("size")) is not int
            or record["size"] < 0
            or record["size"] != source.getinfo(name).file_size
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256")))
        ):
            raise ValueError("归档条目元数据不一致")
        size += record["size"]
    if size > MAX_BYTES:
        raise ValueError("归档超过还原上限")
    return files


def unpack(archive, data):
    """将验证过的条目写入暂存数据目录，再核对每个文件和数据库。"""
    with ZipFile(archive) as source:
        files = read_manifest(source)
        for name, expected in files.items():
            target = data / name
            target.parent.mkdir(exist_ok=True)
            with source.open(name) as current, target.open("xb") as output:
                shutil.copyfileobj(current, output, CHUNK)
            if file_record(target) != expected:
                raise ValueError(f"归档校验失败：{name}")
            if name in DATABASES:
                check_database(target, business=name == BUSINESS)


def write_profile(root, settings):
    """输出新实例的相对路径配置；只保留模型选项与环境变量名。"""

    def q(value):
        return json.dumps(value, ensure_ascii=False)

    profile = f"""[model]
provider = {q(settings.provider)}
name = {q(settings.model)}
base_url = {q(settings.base_url)}
api_key_env = {q(settings.api_key_env)}
timeout_seconds = {settings.timeout_seconds}
num_ctx = {settings.num_ctx}
num_predict = {settings.num_predict}

[tools]
root = "docs"

[storage]
business_db = "data/{BUSINESS}"
checkpoint_db = "data/{CHECKPOINT}"
event_log = "data/events.jsonl"

[limits]
model_calls = {settings.max_model_calls}
tool_calls = {settings.max_tool_calls}

[embedding]
enabled = {str(settings.embedding_enabled).lower()}
min_score = {settings.embedding_min_score}
"""
    (root / "config.toml").write_text(profile, encoding="utf8")
    load_config(root / "config.toml")


def install(directory, template, archive=None):
    """初始化或还原到新目录；所有校验通过后才发布可启动目录。"""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("实例目标不能是链接")
    directory = directory.resolve()
    if directory.exists() or not directory.parent.is_dir():
        raise ValueError("实例目录必须为已存在目录下的新目录")
    with TemporaryDirectory(prefix=".magi-install-", dir=directory.parent) as temporary:
        root = Path(temporary) / "instance"
        (root / "docs").mkdir(parents=True)
        data = root / "data"
        data.mkdir()
        if archive is not None:
            unpack(archive, data)
        write_profile(root, template)
        # 同卷重命名，在校验失败时目标始终不可见。
        if directory.exists():
            raise ValueError("目标实例目录已存在")
        root.rename(directory)
    return directory / "config.toml"


def main():
    """三个子命令共享模型配置文件，使用者显式选择源、目标与归档。"""
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    init = subcommands.add_parser("init")
    export = subcommands.add_parser("backup")
    restore = subcommands.add_parser("restore")
    for subcommand in (init, export, restore):
        subcommand.add_argument("--config", required=True, type=Path)
    for subcommand in (init, restore):
        subcommand.add_argument("--directory", required=True, type=Path)
    for subcommand in (export, restore):
        subcommand.add_argument("--archive", required=True, type=Path)
    args = parser.parse_args()
    try:
        settings = load_config(args.config)
        if args.action == "backup":
            manifest = backup(settings, args.archive)
            print(f"备份完成：{len(manifest['files'])} 个文件")
            if manifest["missing_documents"]:
                print("源实例已缺失工作单：" + ", ".join(manifest["missing_documents"]))
        else:
            print(install(args.directory, settings, getattr(args, "archive", None)))
    except (ValueError, OSError, RuntimeError, sqlite3.Error, BadZipFile) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
