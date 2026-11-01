"""汇总 Yggdrasil 复用审查证据；重算历史排名，不调用模型或访问正式数据库。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import xml.etree.ElementTree as ET
from pathlib import Path


def load_json(path: Path):
    """读取调用者指定仓库内的公开评测文件，不枚举个人资料或环境配置。"""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    """计算实际读取文件的摘要，供复核版本；摘要不能证明内容正确。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def historical_review(source: Path) -> dict:
    """用上游评分器重算已存排名；明确这是历史结果复核，不是重新检索。"""
    dataset_path = source / "quality/evaluation/chinese-retrieval-v1.json"
    ranking_path = source / "quality/results/20261003T174800-bge-cpu-safe/results.json"
    report_path = source / "quality/results/20261003T174800-bge-cpu-safe/report.json"
    scorer_path = source / "deploy/tests/retrieval-quality.py"
    release_path = (
        source / "quality/results/20261004-rag-release-candidate-gpu/report.json"
    )
    dataset, ranking = load_json(dataset_path), load_json(ranking_path)
    # 执行的是已检查的本机项目评分函数，只做 JSON 排名计算；不会进入其 CLI main。
    scorer = runpy.run_path(str(scorer_path))["evaluate"]
    recalculated = scorer(dataset, ranking)
    saved = load_json(report_path)
    release = load_json(release_path)
    return {
        "scope": "historical_saved_rankings_only_not_fresh_model_or_retrieval",
        "dataset": {
            "documents": len(dataset["documents"]),
            "cases": len(dataset["cases"]),
            "no_answer_cases": sum(
                not case["relevantDocumentIds"] for case in dataset["cases"]
            ),
            "cases_with_forbidden_documents": sum(
                bool(case.get("forbiddenDocumentIds")) for case in dataset["cases"]
            ),
        },
        "system": recalculated["system"],
        "recalculated": recalculated["candidate"]["overall"],
        "matches_saved_candidate_metrics": recalculated["candidate"]
        == saved["candidate"],
        "retrieval_gate_passed": recalculated["passed"],
        "gates": recalculated["gates"],
        "historical_answer_release": {
            "passed": release["passed"],
            "iterations": release["iterations"],
            "embeddingProviders": release["embeddingProviders"],
            "rerankerModels": release["rerankerModels"],
            "runs": release["runs"],
        },
        "file_sha256": {
            str(path.relative_to(source)).replace("\\", "/"): sha256(path)
            for path in (
                dataset_path,
                ranking_path,
                report_path,
                scorer_path,
                release_path,
            )
        },
    }


def test_review(audit_dir: Path) -> dict:
    """读取隔离测试的数量与用例名；不导出 Surefire 中可能含环境信息的 properties。"""
    suites = []
    for path in sorted(
        (audit_dir / "core-api/target/surefire-reports").glob("TEST-*.xml")
    ):
        root = ET.parse(path).getroot()
        suites.append(
            {
                "name": root.attrib["name"],
                **{
                    key: int(root.attrib[key])
                    for key in ("tests", "failures", "errors", "skipped")
                },
                "seconds": float(root.attrib["time"]),
                "cases": [case.attrib["name"] for case in root.findall("testcase")],
                "xml_sha256": sha256(path),
            }
        )
    if not suites:
        raise ValueError("未找到测试 XML，不能生成空的通过报告")
    attempts = []
    pattern = r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)"
    for path in sorted(audit_dir.glob("maven-attempt-*.log")):
        log = path.read_text(encoding="utf-8", errors="replace")
        counts = re.findall(pattern, log)
        attempts.append(
            {
                "log": path.name,
                "sha256": sha256(path),
                "build_success": "BUILD SUCCESS" in log,
                "reported_totals": dict(
                    zip(
                        ("tests", "failures", "errors", "skipped"), map(int, counts[-1])
                    )
                )
                if counts
                else None,
                "database_connection_refused": "Connection to localhost:55435 refused"
                in log,
                "container_missing": "No such container: magi-rag-audit-025" in log,
                "fixture_object_key_rejected": 'violates check constraint "artifact_key_check"'
                in log,
            }
        )
    return {
        "scope": "isolated_java_tests_with_mocks_and_optional_real_postgresql_no_model",
        "final_suites": suites,
        "final_totals": {
            key: sum(suite[key] for suite in suites)
            for key in ("tests", "failures", "errors", "skipped")
        },
        "attempts": attempts,
    }


def source_review(source: Path, audit_dir: Path) -> dict:
    """比对编译副本和原仓库核心文件，避免将另一份源码的测试算到当前版本。"""
    relative_root = Path("services/core-api/src/main/java/com/yggdrasil/knowledge")
    names = (
        "RetrievalService.java",
        "RetrievalRepository.java",
        "RetrievalController.java",
        "RetrievalResult.java",
        "EvidencePackageService.java",
        "EvidencePackageRepository.java",
        "EvidencePackage.java",
        "EvidenceItem.java",
        "BgeHttpEmbeddingProvider.java",
        "BgeHttpReranker.java",
        "FeatureHashEmbeddingProvider.java",
        "TenantContextResolver.java",
    )
    fingerprints = {}
    for name in names:
        original = source / relative_root / name
        copied = audit_dir / "core-api/src/main/java/com/yggdrasil/knowledge" / name
        if sha256(original) != sha256(copied):
            raise ValueError(f"测试副本与原仓库不一致：{name}")
        fingerprints[str(relative_root / name).replace("\\", "/")] = sha256(original)
    core = source / "services/core-api"
    files = sorted(path for path in (core / "src").rglob("*") if path.is_file())
    files.append(core / "pom.xml")
    tree = hashlib.sha256()
    for original in files:
        relative = original.relative_to(core)
        digest = sha256(original)
        if digest != sha256(audit_dir / "core-api" / relative):
            raise ValueError(f"测试副本与原仓库不一致：{relative}")
        tree.update((relative.as_posix() + "\0" + digest + "\n").encode())
    return {
        "copied_sources_match": True,
        "compared_file_count": len(files),
        "source_tree_sha256": tree.hexdigest(),
        "file_sha256": fingerprints,
    }


def main() -> None:
    """根据显式路径生成新证据文件；拒绝覆盖既有结果，保留每次评测尝试。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument(
        "--revision", required=True, help="已通过 git rev-parse 核对的上游提交"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("revision 必须是完整 Git 提交摘要")
    report = {
        "audit": "025-yggdrasil-retrieval-reuse",
        "source_revision": args.revision,
        "source_review": source_review(args.source, args.audit_dir),
        "historical_review": historical_review(args.source),
        "fresh_tests": test_review(args.audit_dir),
        "not_performed": [
            "fresh_bge_retrieval_quality",
            "real_llm_generation",
            "browser",
            "magi_service_integration",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "tests": report["fresh_tests"]["final_totals"],
                "historical_retrieval_gate": report["historical_review"][
                    "retrieval_gate_passed"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
