from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (digest TEXT PRIMARY KEY, run_id TEXT NOT NULL, result TEXT NOT NULL)")
            self._create_delivery_tables(db)

    def connect(self):
        # 设忙等待：收回持写锁时，并发读取阻塞至锁释放后按最新授权状态裁决。
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _tx(self):
        """立即取写锁的事务，保证“收回与读取并发以收回为准”的可串行裁决。"""
        db = self.connect()
        db.execute("BEGIN IMMEDIATE")
        return db

    def _create_delivery_tables(self, db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS delivery_batches (
                batch_id TEXT PRIMARY KEY,
                digest TEXT NOT NULL,
                manifest TEXT NOT NULL,
                objects TEXT NOT NULL,
                diff TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery_grants (
                grant_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                vendor TEXT NOT NULL,
                partition TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_grants_active
                ON delivery_grants(batch_id, vendor, partition)
                WHERE status = 'ACTIVE';
            CREATE TABLE IF NOT EXISTS delivery_access (
                request_token TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                vendor TEXT NOT NULL,
                partition TEXT NOT NULL,
                kind TEXT NOT NULL,
                allowed INTEGER NOT NULL,
                ticket_id TEXT,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery_tickets (
                ticket_id TEXT PRIMARY KEY,
                grant_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                vendor TEXT NOT NULL,
                partition TEXT NOT NULL,
                request_token TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ISSUED',
                created_at TEXT NOT NULL,
                redeemed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                request_token TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                auditor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    # ---- M6 既有能力（保持不变） ---------------------------------------
    def record(self, digest: str, run_id: str, result: dict) -> dict:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO runs VALUES (?, ?, ?)",
                       (digest, run_id, json.dumps(result, ensure_ascii=False, sort_keys=True)))
            row = db.execute("SELECT run_id, result FROM runs WHERE digest=?", (digest,)).fetchone()
        return {"run_id": row[0], "result": json.loads(row[1])}

    def count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def all(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT run_id, digest, result FROM runs ORDER BY run_id").fetchall()
        return [{"run_id": r[0], "digest": r[1], "result": json.loads(r[2])} for r in rows]

    # ---- M7 批次登记 ----------------------------------------------------
    def save_batch(self, batch: dict, manifest: list[dict], objects: list[dict]) -> dict:
        with self._tx() as db:
            db.execute(
                "INSERT OR IGNORE INTO delivery_batches VALUES (?, ?, ?, ?, ?, ?)",
                (batch["batch_id"], batch["digest"],
                 json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                 json.dumps(objects, ensure_ascii=False, sort_keys=True),
                 json.dumps(batch["diff"], ensure_ascii=False, sort_keys=True),
                 utc_now()),
            )
            row = db.execute(
                "SELECT batch_id, digest, diff, created_at FROM delivery_batches WHERE batch_id=?",
                (batch["batch_id"],),
            ).fetchone()
        return {"batch_id": row[0], "digest": row[1],
                "diff": json.loads(row[2]), "created_at": row[3]}

    def get_batch(self, batch_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT batch_id, digest, manifest, objects, diff, created_at"
                " FROM delivery_batches WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
        if not row:
            return None
        return {"batch_id": row[0], "digest": row[1],
                "manifest": json.loads(row[2]), "objects": json.loads(row[3]),
                "diff": json.loads(row[4]), "created_at": row[5]}

    # ---- M7 授权与收回 --------------------------------------------------
    def upsert_active_grant(self, grant: dict) -> dict:
        with self._tx() as db:
            db.execute(
                "INSERT INTO delivery_grants (grant_id, batch_id, vendor, partition, status, created_at)"
                " VALUES (?, ?, ?, ?, 'ACTIVE', ?)"
                " ON CONFLICT DO NOTHING",
                (grant["grant_id"], grant["batch_id"], grant["vendor"],
                 grant["partition"], utc_now()),
            )
            row = db.execute(
                "SELECT grant_id, batch_id, vendor, partition, status, created_at, revoked_at"
                " FROM delivery_grants WHERE batch_id=? AND vendor=? AND partition=?"
                " AND status='ACTIVE'",
                (grant["batch_id"], grant["vendor"], grant["partition"]),
            ).fetchone()
        return self._grant_row(row)

    def revoke_grant(self, batch_id: str, vendor: str, partition: str) -> dict | None:
        with self._tx() as db:
            row = db.execute(
                "SELECT grant_id FROM delivery_grants"
                " WHERE batch_id=? AND vendor=? AND partition=? AND status='ACTIVE'",
                (batch_id, vendor, partition),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE delivery_grants SET status='REVOKED', revoked_at=?"
                " WHERE grant_id=?",
                (utc_now(), row[0]),
            )
            current = db.execute(
                "SELECT grant_id, batch_id, vendor, partition, status, created_at, revoked_at"
                " FROM delivery_grants WHERE grant_id=?",
                (row[0],),
            ).fetchone()
        return self._grant_row(current)

    def list_grants(self, batch_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT grant_id, batch_id, vendor, partition, status, created_at, revoked_at"
                " FROM delivery_grants WHERE batch_id=? ORDER BY vendor, partition",
                (batch_id,),
            ).fetchall()
        return [self._grant_row(row) for row in rows]

    def get_active_grant(self, db: sqlite3.Connection, batch_id: str,
                         vendor: str, partition: str) -> tuple | None:
        return db.execute(
            "SELECT grant_id, status FROM delivery_grants"
            " WHERE batch_id=? AND vendor=? AND partition=? AND status='ACTIVE'",
            (batch_id, vendor, partition),
        ).fetchone()

    # ---- M7 外发访问登记（幂等）与下载凭据 ------------------------------
    def get_access(self, request_token: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT request_token, batch_id, vendor, partition, kind, allowed,"
                " ticket_id, detail, created_at FROM delivery_access WHERE request_token=?",
                (request_token,),
            ).fetchone()
        if not row:
            return None
        return {"request_token": row[0], "batch_id": row[1], "vendor": row[2],
                "partition": row[3], "kind": row[4], "allowed": bool(row[5]),
                "ticket_id": row[6], "detail": json.loads(row[7]), "created_at": row[8]}

    def register_access(self, db: sqlite3.Connection, entry: dict) -> None:
        db.execute(
            "INSERT OR IGNORE INTO delivery_access VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry["request_token"], entry["batch_id"], entry["vendor"],
             entry["partition"], entry["kind"], int(entry["allowed"]),
             entry.get("ticket_id"),
             json.dumps(entry["detail"], ensure_ascii=False, sort_keys=True),
             utc_now()),
        )

    def issue_ticket(self, db: sqlite3.Connection, ticket: dict) -> None:
        db.execute(
            "INSERT OR IGNORE INTO delivery_tickets VALUES (?, ?, ?, ?, ?, ?, 'ISSUED', ?, NULL)",
            (ticket["ticket_id"], ticket["grant_id"], ticket["batch_id"],
             ticket["vendor"], ticket["partition"], ticket["request_token"], utc_now()),
        )

    def redeem_ticket(self, ticket_id: str) -> dict | None:
        """在同一立即事务内复核凭据与授权状态：授权一旦收回，旧地址立即失效。"""
        with self._tx() as db:
            ticket = db.execute(
                "SELECT ticket_id, grant_id, batch_id, vendor, partition, status"
                " FROM delivery_tickets WHERE ticket_id=?",
                (ticket_id,),
            ).fetchone()
            if not ticket:
                return None
            grant = db.execute(
                "SELECT status FROM delivery_grants WHERE grant_id=?",
                (ticket[1],),
            ).fetchone()
            active = bool(grant and grant[0] == "ACTIVE" and ticket[5] == "ISSUED")
            if active:
                db.execute(
                    "UPDATE delivery_tickets SET status='REDEEMED', redeemed_at=?"
                    " WHERE ticket_id=? AND status='ISSUED'",
                    (utc_now(), ticket_id),
                )
            row = db.execute(
                "SELECT t.ticket_id, t.grant_id, t.batch_id, t.vendor, t.partition,"
                " g.status FROM delivery_tickets t JOIN delivery_grants g"
                " ON g.grant_id=t.grant_id WHERE t.ticket_id=?",
                (ticket_id,),
            ).fetchone()
        if not row:
            return None
        return {"ticket_id": row[0], "grant_id": row[1], "batch_id": row[2],
                "vendor": row[3], "partition": row[4],
                "active": row[5] == "ACTIVE"}

    def request_delivery(self, *, batch_id: str, vendor: str, partition: str,
                         request_token: str, make_ticket_id) -> dict:
        """一次立即事务完成：授权校验 → 访问登记 → 凭据签发。

        重试凭 request_token 命中既有登记，直接回放原结果，不重复登记、不重复发凭据。
        无 ACTIVE 授权即拒绝并登记拒绝，绝不签发下载地址。
        """
        with self._tx() as db:
            batch = db.execute(
                "SELECT 1 FROM delivery_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if not batch:
                return {"batch_missing": True}
            existing = db.execute(
                "SELECT request_token, batch_id, vendor, partition, kind, allowed,"
                " ticket_id, detail, created_at FROM delivery_access WHERE request_token=?",
                (request_token,),
            ).fetchone()
            if existing:
                return {"batch_missing": False, "replayed": True,
                        "entry": self._access_row(existing)}
            grant = self.get_active_grant(db, batch_id, vendor, partition)
            if not grant:
                entry = {"request_token": request_token, "batch_id": batch_id,
                         "vendor": vendor, "partition": partition, "kind": "DELIVERY",
                         "allowed": False, "ticket_id": None,
                         "detail": {"reason": "GRANT_REQUIRED"}}
                self.register_access(db, entry)
                return {"batch_missing": False, "replayed": False,
                        "allowed": False, "entry": entry}
            ticket_id = make_ticket_id()
            entry = {"request_token": request_token, "batch_id": batch_id,
                     "vendor": vendor, "partition": partition, "kind": "DELIVERY",
                     "allowed": True, "ticket_id": ticket_id,
                     "detail": {"grant_id": grant[0]}}
            self.register_access(db, entry)
            self.issue_ticket(db, {"ticket_id": ticket_id, "grant_id": grant[0],
                                   "batch_id": batch_id, "vendor": vendor,
                                   "partition": partition, "request_token": request_token})
            return {"batch_missing": False, "replayed": False,
                    "allowed": True, "entry": entry, "ticket_id": ticket_id}

    def list_access(self, batch_id: str | None = None) -> list[dict]:
        with self.connect() as db:
            if batch_id:
                rows = db.execute(
                    "SELECT request_token, batch_id, vendor, partition, kind, allowed,"
                    " ticket_id, detail, created_at FROM delivery_access"
                    " WHERE batch_id=? ORDER BY created_at",
                    (batch_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT request_token, batch_id, vendor, partition, kind, allowed,"
                    " ticket_id, detail, created_at FROM delivery_access ORDER BY created_at"
                ).fetchall()
        return [self._access_row(r) for r in rows]

    @staticmethod
    def _access_row(r: tuple) -> dict:
        return {"request_token": r[0], "batch_id": r[1], "vendor": r[2],
                "partition": r[3], "kind": r[4], "allowed": bool(r[5]),
                "ticket_id": r[6], "detail": json.loads(r[7]), "created_at": r[8]}

    # ---- M7 内部审计（留痕失败则不返回原件） ----------------------------
    def get_audit(self, request_token: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT request_token, batch_id, auditor, reason, created_at"
                " FROM audit_logs WHERE request_token=?",
                (request_token,),
            ).fetchone()
        if not row:
            return None
        return {"request_token": row[0], "batch_id": row[1], "auditor": row[2],
                "reason": row[3], "created_at": row[4]}

    def write_audit(self, entry: dict) -> dict:
        with self._tx() as db:
            db.execute(
                "INSERT OR IGNORE INTO audit_logs VALUES (?, ?, ?, ?, ?)",
                (entry["request_token"], entry["batch_id"], entry["auditor"],
                 entry["reason"], utc_now()),
            )
            row = db.execute(
                "SELECT request_token, batch_id, auditor, reason, created_at"
                " FROM audit_logs WHERE request_token=?",
                (entry["request_token"],),
            ).fetchone()
        return {"request_token": row[0], "batch_id": row[1], "auditor": row[2],
                "reason": row[3], "created_at": row[4]}

    def list_audits(self, batch_id: str | None = None) -> list[dict]:
        with self.connect() as db:
            if batch_id:
                rows = db.execute(
                    "SELECT request_token, batch_id, auditor, reason, created_at"
                    " FROM audit_logs WHERE batch_id=? ORDER BY created_at",
                    (batch_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT request_token, batch_id, auditor, reason, created_at"
                    " FROM audit_logs ORDER BY created_at"
                ).fetchall()
        return [{"request_token": r[0], "batch_id": r[1], "auditor": r[2],
                 "reason": r[3], "created_at": r[4]} for r in rows]

    @staticmethod
    def _grant_row(row: tuple) -> dict:
        return {"grant_id": row[0], "batch_id": row[1], "vendor": row[2],
                "partition": row[3], "status": row[4], "created_at": row[5],
                "revoked_at": row[6]}
