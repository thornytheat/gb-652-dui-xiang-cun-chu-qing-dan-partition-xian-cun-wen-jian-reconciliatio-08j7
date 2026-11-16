"""M7 分区授权交付 · 纯领域逻辑。

边界约定（与 M1-M6 一致，本文件不触碰数据库与 Web）：
- 分区身份从对象键的分区段（形如 ``k=v`` 的路径段）推导，无分区段归 ``_ROOT``。
- 对账批次由“实际登记（manifest）”与“现存对象（objects）”形成，保留跨供应商完整差异。
- 外发内容只裁剪出授权分区的缺失对象，冲突/孤儿等完整证据仅留给内部审计。
- 摘要沿用 app.manifest.canonical_digest，本模块不改写其验收语义。
"""
from __future__ import annotations

from app.manifest import canonical_digest, run_id

ROOT_PARTITION = "_ROOT"

SAMPLE = {
    "manifest": [
        {"key": "dt=2026-11-15/hour=00/a.parquet", "size": 120, "etag": "aaa"},
        {"key": "dt=2026-11-15/hour=00/b.parquet", "size": 80, "etag": "bbb"},
        {"key": "dt=2026-11-15/hour=01/c.parquet", "size": 60, "etag": "ccc"},
        {"key": "dt=2026-11-15/hour=02/d.parquet", "size": 40, "etag": "ddd"},
        {"key": "loose/root.parquet", "size": 10, "etag": "eee"},
    ],
    "objects": [
        {"key": "dt=2026-11-15/hour=00/a.parquet", "size": 120, "etag": "aaa"},
        {"key": "dt=2026-11-15/hour=01/c.parquet", "size": 61, "etag": "ccc"},
        {"key": "dt=2026-11-15/hour=03/orphan.parquet", "size": 20, "etag": "zzz"},
    ],
}


def partition_of(key: str) -> str:
    """从对象键推导分区身份；无分区段的键归入根分区。"""
    parts = [segment for segment in str(key).split("/") if segment]
    partition_segments = [segment for segment in parts if "=" in segment]
    return "/".join(partition_segments) if partition_segments else ROOT_PARTITION


def build_diff(manifest: list[dict], objects: list[dict]) -> list[dict]:
    """形成跨供应商完整差异，逐行保留原值与裁决。

    状态：MATCH / MISSING / ORPHAN / CONFLICT。
    MISSING 是供应商补货的唯一外发对象；其余状态只进内部完整证据。
    """
    left = {row["key"]: row for row in manifest}
    right = {row["key"]: row for row in objects}
    diff: list[dict] = []
    for key in sorted(left.keys() | right.keys()):
        if key in left and key not in right:
            status, evidence = "MISSING", "manifest"
        elif key not in left and key in right:
            status, evidence = "ORPHAN", "objects"
        else:
            expected, actual = left[key], right[key]
            if (expected.get("size"), expected.get("etag")) != (
                actual.get("size"),
                actual.get("etag"),
            ):
                status, evidence = "CONFLICT", "size+etag"
            else:
                status, evidence = "MATCH", "size+etag"
        row = dict(left.get(key) or right.get(key))
        diff.append(
            {
                "key": key,
                "partition": partition_of(key),
                "status": status,
                "evidence": evidence,
                "size": row.get("size"),
                "etag": row.get("etag"),
            }
        )
    return diff


def group_partitions(diff: list[dict]) -> dict[str, dict]:
    """把完整差异按分区聚合，回读时给出每个分区的差异计数与缺失键。"""
    grouped: dict[str, dict] = {}
    for row in diff:
        bucket = grouped.setdefault(
            row["partition"],
            {"partition": row["partition"], "missing": [], "conflicts": [],
             "orphans": [], "matched": [], "missing_count": 0, "conflict_count": 0,
             "orphan_count": 0, "matched_count": 0, "total": 0},
        )
        bucket["total"] += 1
        if row["status"] == "MISSING":
            bucket["missing"].append(row["key"])
            bucket["missing_count"] += 1
        elif row["status"] == "CONFLICT":
            bucket["conflicts"].append(row["key"])
            bucket["conflict_count"] += 1
        elif row["status"] == "ORPHAN":
            bucket["orphans"].append(row["key"])
            bucket["orphan_count"] += 1
        else:
            bucket["matched"].append(row["key"])
            bucket["matched_count"] += 1
    return dict(sorted(grouped.items()))


def project_partition(diff: list[dict], partition: str) -> list[dict]:
    """授权裁剪：仅输出指定分区的 MISSING 行，绝不携带其他分区或其他状态的对象键。"""
    return [
        dict(row)
        for row in diff
        if row["partition"] == partition and row["status"] == "MISSING"
    ]


def batch_digest(manifest: list[dict], objects: list[dict]) -> str:
    """批次摘要：同一登记+现存输入重跑复用同一编号（沿用 M6 摘要，不改验收）。"""
    return canonical_digest({"manifest": manifest, "objects": objects})


def make_batch_id(manifest: list[dict], objects: list[dict]) -> str:
    return run_id({"manifest": manifest, "objects": objects})


def build_batch(manifest: list[dict], objects: list[dict]) -> dict:
    diff = build_diff(manifest, objects)
    partitions = group_partitions(diff)
    return {
        "batch_id": make_batch_id(manifest, objects),
        "digest": batch_digest(manifest, objects),
        "input_count": len(manifest),
        "object_count": len(objects),
        "diff_count": len(diff),
        "missing_count": sum(row["status"] == "MISSING" for row in diff),
        "conflict_count": sum(row["status"] == "CONFLICT" for row in diff),
        "orphan_count": sum(row["status"] == "ORPHAN" for row in diff),
        "matched_count": sum(row["status"] == "MATCH" for row in diff),
        "partitions": partitions,
        "diff": diff,
    }


def run(payload: dict | None = None) -> dict:
    data = dict(SAMPLE)
    if payload:
        data.update({k: v for k, v in payload.items() if k in SAMPLE})
    batch = build_batch(data["manifest"], data["objects"])
    return {"status": "READY", "reason": "GAP" if batch["missing_count"] else "OK", **batch}
