"""研究成果的不可变版本、明确确认和可核对文件发布。"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from magi.personal.documents import checksum


def now():
    return datetime.now(timezone.utc).isoformat()


class ArtifactError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ResearchArtifacts:
    def __init__(self, store, owner):
        self.store, self.owner = store, owner
        self.directory = store.path.parent / "research-artifacts"

    @staticmethod
    def _version_view(row):
        result = dict(row)
        for field in ("sources_json", "completed_json", "unknowns_json"):
            result[field.removesuffix("_json")] = json.loads(result.pop(field))
        return result

    def create(self, chat, lead_agent_id, report, sources, run_id, review_status):
        """初稿只写内部业务库，不创建成果目录或 Markdown 文件。"""

        artifact_id, timestamp = uuid4().hex, now()
        payload_hash = checksum(report.body)
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO research_artifacts VALUES (?,?,?,?,1,'DRAFT',?,?)",
                (artifact_id, self.owner, chat, lead_agent_id, timestamp, timestamp),
            )
            db.execute(
                """INSERT INTO research_artifact_versions
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    artifact_id,
                    1,
                    report.title,
                    report.body,
                    payload_hash,
                    json.dumps(sources, ensure_ascii=False),
                    json.dumps(report.completed_items, ensure_ascii=False),
                    json.dumps(report.unknowns, ensure_ascii=False),
                    run_id,
                    review_status,
                    timestamp,
                ),
            )
        return self.get(artifact_id)

    def add_version(
        self, artifact_id, expected_version, report, sources, run_id, review_status
    ):
        """竞争修改用当前版本检查拒绝；旧版本正文始终保留。"""

        timestamp = now()
        with self.store.transaction() as db:
            artifact = db.execute(
                "SELECT * FROM research_artifacts WHERE id=? AND owner=?",
                (artifact_id, self.owner),
            ).fetchone()
            if artifact is None:
                raise ArtifactError("NOT_FOUND", "研究成果不存在")
            if artifact["current_version"] != expected_version:
                raise ArtifactError("VERSION_CONFLICT", "成果已有新版本，请刷新后重试")
            version = expected_version + 1
            db.execute(
                """INSERT INTO research_artifact_versions
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    artifact_id,
                    version,
                    report.title,
                    report.body,
                    checksum(report.body),
                    json.dumps(sources, ensure_ascii=False),
                    json.dumps(report.completed_items, ensure_ascii=False),
                    json.dumps(report.unknowns, ensure_ascii=False),
                    run_id,
                    review_status,
                    timestamp,
                ),
            )
            # 旧稿触发了修改，不能继续显示为“评审进行中”；它的评审结果只
            # 对旧版本有效，新版本必须由原评审者重新给出结论。
            db.execute(
                """UPDATE research_artifact_versions SET review_status='UNRESOLVED'
                   WHERE artifact_id=? AND version=?""",
                (artifact_id, expected_version),
            )
            db.execute(
                """UPDATE research_artifacts
                   SET current_version=?,status='DRAFT',updated_at=? WHERE id=?""",
                (version, timestamp, artifact_id),
            )
        return self.get(artifact_id, version)

    def set_review_status(self, artifact_id, version, status):
        with self.store.transaction() as db:
            db.execute(
                """UPDATE research_artifact_versions SET review_status=?
                   WHERE artifact_id=? AND version=?""",
                (status, artifact_id, version),
            )

    def get(self, artifact_id, version=None):
        """返回指定不可变版本、评审、未解决项和发布回执。"""

        with self.store.connect() as db:
            artifact = db.execute(
                "SELECT * FROM research_artifacts WHERE id=? AND owner=?",
                (artifact_id, self.owner),
            ).fetchone()
            if artifact is None:
                raise ArtifactError("NOT_FOUND", "研究成果不存在")
            selected = version or artifact["current_version"]
            row = db.execute(
                """SELECT * FROM research_artifact_versions
                   WHERE artifact_id=? AND version=?""",
                (artifact_id, selected),
            ).fetchone()
            if row is None:
                raise ArtifactError("NOT_FOUND", "研究成果版本不存在")
            reviews = [
                json.loads(item["result_json"])
                | {
                    "id": item["id"],
                    "agent_id": item["agent_id"],
                    "round": item["round"],
                    "verdict": item["verdict"],
                    "run_id": item["run_id"],
                }
                for item in db.execute(
                    """SELECT * FROM research_reviews
                       WHERE owner=? AND artifact_id=? AND artifact_version=?
                       ORDER BY round,created_at,id""",
                    (self.owner, artifact_id, selected),
                )
            ]
            decision = db.execute(
                """SELECT * FROM research_artifact_decisions
                   WHERE owner=? AND artifact_id=? AND version=?
                   ORDER BY created_at DESC LIMIT 1""",
                (self.owner, artifact_id, selected),
            ).fetchone()
        version_data = self._version_view(row)
        return {
            **dict(artifact),
            **version_data,
            "current_version": artifact["current_version"],
            "reviews": reviews,
            "decision": dict(decision) if decision else None,
        }

    def list_chat(self, chat):
        with self.store.connect() as db:
            ids = [
                row["id"]
                for row in db.execute(
                    """SELECT id FROM research_artifacts
                       WHERE owner=? AND chat=? ORDER BY updated_at DESC""",
                    (self.owner, chat),
                )
            ]
        return [self.get(identity) for identity in ids]

    def _path(self, artifact_id, version):
        return self.directory / f"{artifact_id}-v{version}.md"

    def _publish(self, path: Path, content: bytes):
        if self.directory.is_symlink() or self.directory.resolve() != self.directory:
            raise ArtifactError("INVALID_PATH", "成果目录不能是链接")
        self.directory.mkdir(exist_ok=True)
        if path.is_symlink():
            raise ArtifactError("INVALID_PATH", "成果文件不能是链接")
        if path.exists():
            if path.is_file() and path.read_bytes() == content:
                return
            raise ArtifactError("RECONCILE_REQUIRED", "目标文件存在且内容不同，不覆盖")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.directory, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # Windows 的 os.link 可能被当前卷或安全策略拒绝；同目录 rename 在
            # Windows 上不会覆盖已有目标，仍保持“发布或失败”的原子边界。
            # POSIX rename 会覆盖，所以其他平台继续使用硬链接的 O_EXCL 语义。
            if os.name == "nt":
                os.rename(temporary, path)
                temporary = None
            else:
                os.link(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def decide(self, artifact_id, request_id, version, payload_hash, decision):
        """确认绑定版本和摘要；批准先落库，再发布固定文件并核对恢复。"""

        if decision not in {"approve", "reject"}:
            raise ArtifactError("INVALID_INPUT", "未知成果决定")
        artifact = self.get(artifact_id, version)
        if (
            artifact["current_version"] != version
            or artifact["payload_hash"] != payload_hash
        ):
            raise ArtifactError("VERSION_CONFLICT", "成果版本或摘要已变化，请重新核对")
        with self.store.transaction() as db:
            old = db.execute(
                "SELECT * FROM research_artifact_decisions WHERE request_id=? AND owner=?",
                (request_id, self.owner),
            ).fetchone()
            if old:
                if (
                    old["artifact_id"] != artifact_id
                    or old["version"] != version
                    or old["payload_hash"] != payload_hash
                    or old["decision"] != decision
                ):
                    raise ArtifactError(
                        "IDEMPOTENCY_CONFLICT", "确认请求已用于其他内容"
                    )
                receipt = dict(old)
            else:
                same = db.execute(
                    """SELECT * FROM research_artifact_decisions
                       WHERE owner=? AND artifact_id=? AND version=? AND decision=?""",
                    (self.owner, artifact_id, version, decision),
                ).fetchone()
                if same:
                    receipt = dict(same)
                else:
                    status = "APPROVED" if decision == "approve" else "REJECTED"
                    path = (
                        str(self._path(artifact_id, version))
                        if decision == "approve"
                        else None
                    )
                    db.execute(
                        """INSERT INTO research_artifact_decisions
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (
                            request_id,
                            self.owner,
                            artifact_id,
                            version,
                            payload_hash,
                            decision,
                            status,
                            path,
                            now(),
                        ),
                    )
                    db.execute(
                        "UPDATE research_artifacts SET status=?,updated_at=? WHERE id=?",
                        (status, now(), artifact_id),
                    )
                    receipt = dict(
                        request_id=request_id,
                        owner=self.owner,
                        artifact_id=artifact_id,
                        version=version,
                        payload_hash=payload_hash,
                        decision=decision,
                        status=status,
                        path=path,
                    )
        if decision == "reject":
            return receipt
        path = self._path(artifact_id, version)
        try:
            self._publish(path, artifact["body"].encode("utf-8"))
        except ArtifactError as exc:
            if exc.code == "RECONCILE_REQUIRED":
                with self.store.transaction() as db:
                    db.execute(
                        """UPDATE research_artifact_decisions
                           SET status='RECONCILE_REQUIRED' WHERE owner=? AND artifact_id=? AND version=?""",
                        (self.owner, artifact_id, version),
                    )
                    db.execute(
                        """UPDATE research_artifacts SET status='RECONCILE_REQUIRED',updated_at=?
                           WHERE id=?""",
                        (now(), artifact_id),
                    )
            raise
        with self.store.transaction() as db:
            db.execute(
                """UPDATE research_artifact_decisions SET status='SAVED',path=?
                   WHERE owner=? AND artifact_id=? AND version=? AND decision='approve'""",
                (str(path), self.owner, artifact_id, version),
            )
            db.execute(
                "UPDATE research_artifacts SET status='SAVED',updated_at=? WHERE id=?",
                (now(), artifact_id),
            )
        return self.get(artifact_id, version)["decision"]
