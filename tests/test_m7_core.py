from app.modules.m7_delivery.core import (
    ROOT_PARTITION, SAMPLE, build_batch, build_diff, group_partitions,
    partition_of, project_partition, run,
)


def test_partition_of_extracts_all_segments():
    assert partition_of("dt=2026-11-15/hour=00/a.parquet") == "dt=2026-11-15/hour=00"


def test_partition_of_root():
    assert partition_of("loose/root.parquet") == ROOT_PARTITION


def test_diff_keeps_full_cross_vendor_picture():
    out = build_diff(SAMPLE["manifest"], SAMPLE["objects"])
    statuses = {row["key"]: row["status"] for row in out}
    assert statuses["dt=2026-11-15/hour=00/b.parquet"] == "MISSING"
    assert statuses["dt=2026-11-15/hour=01/c.parquet"] == "CONFLICT"
    assert statuses["dt=2026-11-15/hour=03/orphan.parquet"] == "ORPHAN"
    assert statuses["dt=2026-11-15/hour=00/a.parquet"] == "MATCH"


def test_diff_conservation():
    out = build_diff(SAMPLE["manifest"], SAMPLE["objects"])
    assert len(out) == len({r["key"] for r in SAMPLE["manifest"]}
                           | {r["key"] for r in SAMPLE["objects"]})


def test_group_partitions_counts_and_missing_keys():
    grouped = group_partitions(build_diff(SAMPLE["manifest"], SAMPLE["objects"]))
    hour00 = grouped["dt=2026-11-15/hour=00"]
    assert hour00["missing"] == ["dt=2026-11-15/hour=00/b.parquet"]
    assert hour00["missing_count"] == 1
    assert hour00["matched_count"] == 1
    root = grouped[ROOT_PARTITION]
    assert root["missing"] == ["loose/root.parquet"]


def test_project_partition_only_missing_and_scoped():
    diff = build_diff(SAMPLE["manifest"], SAMPLE["objects"])
    rows = project_partition(diff, "dt=2026-11-15/hour=00")
    assert [r["key"] for r in rows] == ["dt=2026-11-15/hour=00/b.parquet"]
    assert all(r["status"] == "MISSING" for r in rows)
    assert all(r["partition"] == "dt=2026-11-15/hour=00" for r in rows)


def test_projection_never_leaks_other_partition_or_conflict():
    diff = build_diff(SAMPLE["manifest"], SAMPLE["objects"])
    rows = project_partition(diff, "dt=2026-11-15/hour=01")
    assert rows == []  # 只有 CONFLICT，不进入补货外发
    assert project_partition(diff, ROOT_PARTITION) == [
        r for r in diff if r["partition"] == ROOT_PARTITION and r["status"] == "MISSING"]


def test_batch_id_stable_for_same_input():
    first = build_batch(SAMPLE["manifest"], SAMPLE["objects"])
    second = build_batch(SAMPLE["manifest"], SAMPLE["objects"])
    assert first["batch_id"] == second["batch_id"]
    assert first["digest"] == second["digest"]


def test_batch_id_changes_when_ledger_changes():
    first = build_batch(SAMPLE["manifest"], SAMPLE["objects"])
    second = build_batch(SAMPLE["manifest"][:], SAMPLE["objects"][:-1])
    assert first["batch_id"] != second["batch_id"]


def test_run_reason_gap_when_missing_exists():
    assert run({})["reason"] == "GAP"


def test_run_ok_when_fully_matched():
    manifest = [{"key": "a", "size": 1, "etag": "x"}]
    assert run({"manifest": manifest, "objects": list(manifest)})["reason"] == "OK"
