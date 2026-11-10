"""在隔离库中对照默认记忆注入与语义召回，每个固定案例只执行一次。"""

import argparse
import json
import os
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

from magi.personal import recall as recall_module
from magi.personal.context import ContextService
from magi.personal.memory import MemoryService
from magi.personal.store import PersonalStore

MEMORIES = (
    ("study", "备考时间", "global", "每周可学习10小时"),
    ("local_study", "备考时间", "session:study", "每周可学习2小时"),
    ("private", "私有项目", "session:private", "只在私有会话讨论的项目"),
    ("style", "讲解风格", "global", "先举例再解释"),
    ("commute", "通勤方式", "global", "平日乘坐地铁"),
)

# 固定主题轴供离线替身使用。同键的全局和会话记忆方向完全相同，
# 只有产品自身的作用域和版本过滤才能区分它们。
TOPICS = (
    ("备考时间", "复习可以投入多久"),
    ("私有项目", "专属项目"),
    ("讲解风格", "解释代码"),
    ("通勤方式", "上班出行"),
)


class FixedEmbedding:
    def embed(self, texts):
        """为问题和待索引正文生成固定向量，未知主题使用独立维度。"""
        vectors = []
        for text in texts:
            axis = next(
                (i for i, terms in enumerate(TOPICS) if any(t in text for t in terms)),
                len(TOPICS),
            )
            vector = [0.0] * 1024
            vector[axis] = 1.0
            vectors.append(vector)
        return vectors


def measure(method):
    """计时覆盖产品读取或检索调用，保留失败状态与实际返回记录。"""
    start = perf_counter()
    result = method()
    return {**result, "ms": round((perf_counter() - start) * 1000, 3)}


def summarize(attempts, arm):
    """按记忆 ID 与版本计分，检索失败与成功返回空结果分开统计。"""
    hits = returned = expected = empty_ok = boundary_violations = 0
    for attempt in attempts:
        result = attempt[arm]
        actual = {item["identity"] for item in result["items"]}
        target = set(attempt["expected"])
        hits += len(actual & target)
        returned += len(actual)
        expected += len(target)
        empty_ok += result["status"] == "OK" and not actual and not target
        boundary_violations += len(actual & set(attempt["forbidden"]))
    return {
        "hits": hits,
        "returned": returned,
        "expected": expected,
        "false_positives": returned - hits,
        "misses": expected - hits,
        "micro_precision": hits / returned if returned else None,
        "micro_recall": hits / expected if expected else None,
        "empty_cases": sum(not a["expected"] for a in attempts),
        "correct_empty_cases": empty_ok,
        "boundary_violations": boundary_violations,
        "failed_attempts": sum(a[arm]["status"] != "OK" for a in attempts),
        "sample_median_ms": median(a[arm]["ms"] for a in attempts),
    }


def evaluate(db_path, *, cloud=False):
    """从空合成库开始，对同一个问题分别运行产品的两条记忆读取路径。"""
    store = PersonalStore(db_path)
    memories = MemoryService(store)
    context = ContextService(store)
    settings = SimpleNamespace(business_db_path=store.path, embedding_min_score=0.45)
    ids = {}
    for label, key, scope, value in MEMORIES:
        receipt = memories.add(key=key, scope=scope, value=value, request_id=label)
        ids[label] = receipt.object_id
    labels = {identity: label for label, identity in ids.items()}
    attempts = []

    def run_case(name, chat, question, expected, forbidden, index_phase):
        """用同一业务库状态执行两组，输出稳定样本名和实际返回的版本。"""
        default = measure(
            lambda: {
                "status": "OK",
                "items": context.build_current_context(chat, include_identity=True)[1],
            }
        )
        semantic = measure(
            lambda: recall_module.recall(context, chat, question, settings)
        )
        for result in (default, semantic):
            result["items"] = [
                {
                    "identity": f"{labels[row['id']]}:v{row['version']}",
                    "value": row["value"],
                }
                for row in result["items"]
            ]
        attempts.append(
            dict(
                case=name,
                chat=chat,
                question=question,
                expected=expected,
                forbidden=forbidden,
                index_phase=index_phase,
                default=default,
                semantic=semantic,
            )
        )

    provider = (
        nullcontext()
        if cloud
        else patch.object(recall_module, "SiliconFlowEmbeddingProvider", FixedEmbedding)
    )
    with provider:
        run_case(
            "改写问法",
            "general",
            "复习可以投入多久",
            ["study:v1"],
            ["local_study:v1", "private:v1"],
            "cold",
        )
        run_case(
            "无匹配资料",
            "general",
            "解释黑洞蒸发",
            [],
            ["local_study:v1", "private:v1"],
            "warm",
        )
        run_case(
            "同键会话覆盖",
            "study",
            "复习可以投入多久",
            ["local_study:v1"],
            ["study:v1", "private:v1"],
            "session_index_add",
        )
        run_case(
            "其他会话的专属事实",
            "general",
            "专属项目是什么",
            [],
            ["local_study:v1", "private:v1"],
            "warm",
        )
        run_case(
            "本会话的专属事实",
            "private",
            "专属项目是什么",
            ["private:v1"],
            ["local_study:v1"],
            "session_index_add",
        )
        memories.update(
            ids["study"],
            expected_version=1,
            value="每周可学习4小时",
            request_id="update-study",
        )
        run_case(
            "记忆更新",
            "general",
            "复习可以投入多久",
            ["study:v2"],
            ["study:v1", "local_study:v1", "private:v1"],
            "version_update",
        )
        memories.delete(ids["study"], expected_version=2, request_id="delete-study")
        run_case(
            "记忆删除",
            "general",
            "复习可以投入多久",
            [],
            ["study:v1", "study:v2", "local_study:v1", "private:v1"],
            "delete_cleanup",
        )
        # 冻结评测时钟，在截止时刻仍保留 v1 向量，验证查询边界而非清理时机。
        with patch(
            "magi.personal.memory._now", return_value="2030-01-01T00:00:00.000000+00:00"
        ) as clock:
            deadline = datetime.fromisoformat("2030-01-02T00:00:00+00:00")
            timed = memories.add(
                key="备考时间",
                scope="global",
                value="这周可学习4小时",
                request_id="timed-study",
                expires_at=deadline,
            )
            labels[timed.object_id] = "timed"
            run_case(
                "有效期内",
                "general",
                "复习可以投入多久",
                ["timed:v1"],
                ["study:v2", "local_study:v1"],
                "deadline_index_add",
            )
            clock.return_value = "2030-01-02T00:00:00.000000+00:00"
            context = ContextService(PersonalStore(db_path))
            run_case(
                "到期后重开业务库",
                "general",
                "复习可以投入多久",
                [],
                ["timed:v1", "local_study:v1"],
                "expired_existing_vector",
            )
            memories.update(
                timed.object_id,
                expected_version=1,
                value="这周可学习4小时",
                request_id="renew-study",
                expires_at=datetime.fromisoformat("2030-01-03T00:00:00+00:00"),
            )
            run_case(
                "续期后恢复",
                "general",
                "复习可以投入多久",
                ["timed:v2"],
                ["timed:v1", "local_study:v1"],
                "renew_index_update",
            )
    return {
        "mode": "cloud_bge_m3" if cloud else "offline_fixed_vectors",
        "embedding_min_score": settings.embedding_min_score,
        "attempts": attempts,
        "summary": {arm: summarize(attempts, arm) for arm in ("default", "semantic")},
    }


def main():
    """离线默认使用固定向量，显式选择云端时只发送合成数据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cloud", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("拒绝覆盖已有评测，请指定新路径")
    if args.cloud and not os.getenv("SILICONFLOW_API_KEY", "").strip():
        parser.error("缺少 SILICONFLOW_API_KEY，尚未调用嵌入服务")
    with TemporaryDirectory(prefix="magi-memory-recall-") as temporary:
        report = evaluate(Path(temporary) / "business.db", cloud=args.cloud)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    semantic = report["summary"]["semantic"]
    boundary_failed = any(
        arm["failed_attempts"] or arm["boundary_violations"]
        for arm in report["summary"].values()
    )
    offline_mismatch = not args.cloud and (
        semantic["misses"] or semantic["false_positives"]
    )
    return int(bool(boundary_failed or offline_mismatch))


if __name__ == "__main__":
    raise SystemExit(main())
