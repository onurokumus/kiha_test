"""Resumable multipart upload protocol: lifecycle, integrity, and recovery.

These tests deliberately use tiny server-selected chunks.  They exercise the
same protocol used for multi-GiB browser uploads without creating large test
fixtures:

    POST   /api/uploads
    GET    /api/uploads/{upload_id}?name=...
    PUT    /api/uploads/{upload_id}/chunks/{index}?name=...
    POST   /api/uploads/{upload_id}/complete?name=...
    DELETE /api/uploads/{upload_id}?name=...

FastAPI's TestClient runs ``BackgroundTasks`` before returning from a request.
Consequently the completion response still says ``ingesting`` (the API
contract), while status.json is normally already ``ready`` when the assertion
after ``.post()`` runs.
"""

from __future__ import annotations

import hashlib
import errno
import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import polars as pl
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main, store, uploads
from ._base import DataDirTestCase


TEST_CHUNK_BYTES = 32


def small_csv(rows: int = 64, fs: float = 10.0) -> bytes:
    lines = ["time,thrust,rpm"]
    for i in range(rows):
        lines.append(f"{i / fs},{i * 0.5},{1000 + i}")
    return ("\n".join(lines) + "\n").encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class UploadTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        # The production value is intentionally large. Tiny chunks make every
        # boundary/order/retry case cheap enough to run in the normal suite.
        chunk_patcher = patch.object(
            uploads, "UPLOAD_CHUNK_BYTES", TEST_CHUNK_BYTES)
        chunk_patcher.start()
        self.addCleanup(chunk_patcher.stop)
        self.client = TestClient(main.app)

    def init_upload(
        self,
        body: bytes,
        *,
        name: str = "alpha",
        source_file: str = "alpha.csv",
        last_modified_ms: int = 1_785_340_000_000,
        fs_hz: float | None = 10.0,
        time_mode: str = "auto",
        time_column: str | None = None,
        uploader_name: str | None = None,
    ):
        payload = {
            "name": name,
            "source_file": source_file,
            "size_bytes": len(body),
            "last_modified_ms": last_modified_ms,
            "fs_hz": fs_hz,
            "time_mode": time_mode,
            "time_column": time_column,
        }
        if uploader_name is not None:
            payload["uploader_name"] = uploader_name
        response = self.client.post("/api/uploads", json=payload)
        return response

    def init_ok(self, body: bytes, **kwargs) -> dict:
        response = self.init_upload(body, **kwargs)
        self.assertIn(response.status_code, (200, 201), response.text)
        session = response.json()
        self.assertEqual(session["chunk_size"], TEST_CHUNK_BYTES)
        self.assertEqual(
            session["total_chunks"],
            (len(body) + TEST_CHUNK_BYTES - 1) // TEST_CHUNK_BYTES,
        )
        return session

    def chunk_bytes(self, body: bytes, index: int) -> bytes:
        start = index * TEST_CHUNK_BYTES
        return body[start:start + TEST_CHUNK_BYTES]

    def put_chunk(
        self,
        upload_id: str,
        index: int,
        data: bytes,
        *,
        name: str = "alpha",
        checksum: str | None = None,
        include_checksum: bool = True,
    ):
        headers = {}
        if include_checksum:
            headers["X-Chunk-SHA256"] = checksum or digest(data)
        return self.client.put(
            f"/api/uploads/{upload_id}/chunks/{index}",
            params={"name": name},
            headers=headers,
            files={"file": ("chunk.bin", data, "application/octet-stream")},
        )

    def put_ok(
        self,
        upload_id: str,
        index: int,
        data: bytes,
        *,
        name: str = "alpha",
    ) -> dict:
        response = self.put_chunk(
            upload_id, index, data, name=name)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def get_session(self, upload_id: str, *, name: str = "alpha"):
        return self.client.get(
            f"/api/uploads/{upload_id}", params={"name": name})

    def complete(self, upload_id: str, *, name: str = "alpha"):
        return self.client.post(
            f"/api/uploads/{upload_id}/complete", params={"name": name})

    # ----- initiation and name reservation -----

    def test_init_reserves_name_and_publishes_receiving_manifest(self):
        body = small_csv()
        session = self.init_ok(
            body, source_file="My Test (1).csv", fs_hz=12.5)

        self.assertEqual(session["name"], "alpha")
        self.assertEqual(session["source_file"], "My Test (1).csv")
        self.assertEqual(session["size_bytes"], len(body))
        self.assertEqual(session["last_modified_ms"], 1_785_340_000_000)
        self.assertEqual(session["state"], "receiving")
        self.assertEqual(session["received_bytes"], 0)
        self.assertEqual(session["received_chunks"], 0)
        self.assertEqual(session["chunks"], [])
        self.assertEqual(session["time_mode"], "auto")
        self.assertIsNone(session["time_column"])
        uuid.UUID(session["upload_id"])  # opaque id must at least be canonical

        directory = self.tests / "alpha"
        self.assertTrue(directory.is_dir())
        self.assertEqual(store.get_status("alpha")["status"], "receiving")
        manifest = json.loads(
            (directory / ".upload" / "manifest.json").read_text())
        self.assertEqual(manifest["upload_id"], session["upload_id"])
        self.assertEqual(manifest["size_bytes"], len(body))
        self.assertEqual(manifest["fs_hz"], 12.5)
        self.assertEqual(manifest["time_mode"], "auto")
        self.assertIsNone(manifest["time_column"])

    def test_init_is_idempotent_for_same_file_identity(self):
        body = small_csv()
        first = self.init_ok(body)
        second_response = self.init_upload(body)
        self.assertEqual(second_response.status_code, 200, second_response.text)
        second = second_response.json()

        self.assertEqual(second["upload_id"], first["upload_id"])
        self.assertEqual(second["size_bytes"], first["size_bytes"])
        self.assertEqual(
            [p.name for p in self.tests.iterdir()], ["alpha"])

    def test_init_normalizes_and_persists_uploader_provenance(self):
        body = small_csv()
        session = self.init_ok(
            body, uploader_name="  Jose\u0301 / Лабораторія  ")
        expected = "José / Лабораторія"

        self.assertEqual(session["uploader_name"], expected)
        fetched = self.get_session(session["upload_id"])
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["uploader_name"], expected)

        directory = self.tests / "alpha"
        manifest = json.loads(
            (directory / ".upload" / "manifest.json").read_text())
        self.assertEqual(manifest["uploader_name"], expected)
        self.assertEqual(store.get_status("alpha")["uploader_name"], expected)
        row = next(
            row for row in store.list_tests() if row["name"] == "alpha")
        self.assertEqual(row["uploader_name"], expected)

    def test_uploader_provenance_is_part_of_resumable_identity(self):
        body = small_csv()
        first = self.init_ok(body, uploader_name="Alex Kim")

        same = self.init_upload(body, uploader_name="  Alex Kim  ")
        self.assertEqual(same.status_code, 200, same.text)
        self.assertEqual(same.json()["upload_id"], first["upload_id"])

        changed = self.init_upload(body, uploader_name="Test Lab")
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertEqual(
            self.get_session(first["upload_id"]).json()["uploader_name"],
            "Alex Kim",
        )

    def test_init_validates_uploader_provenance(self):
        body = small_csv()
        for bad in ("", " \u2003 ", "Alex\nKim", "名" * 81):
            with self.subTest(uploader_name=repr(bad)):
                response = self.init_upload(body, uploader_name=bad)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertFalse((self.tests / "alpha").exists())

        accepted = self.init_upload(body, uploader_name="名" * 80)
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(accepted.json()["uploader_name"], "名" * 80)

    def test_init_persists_time_setup_and_includes_it_in_identity(self):
        body = small_csv()
        first = self.init_ok(
            body,
            fs_hz=500.0,
            time_mode="generated",
            time_column="elapsed_s",
        )
        self.assertEqual(first["fs_hz"], 500.0)
        self.assertEqual(first["time_mode"], "generated")
        self.assertEqual(first["time_column"], "elapsed_s")

        same = self.init_upload(
            body,
            fs_hz=500.0,
            time_mode="generated",
            time_column="elapsed_s",
        )
        self.assertEqual(same.status_code, 200, same.text)
        self.assertEqual(same.json()["upload_id"], first["upload_id"])

        changed_rate = self.init_upload(
            body,
            fs_hz=1000.0,
            time_mode="generated",
            time_column="elapsed_s",
        )
        self.assertEqual(changed_rate.status_code, 409, changed_rate.text)

    def test_existing_column_name_preserves_significant_header_spaces(self):
        body = small_csv()
        session = self.init_ok(
            body,
            time_mode="column",
            time_column=" TIME ",
        )
        self.assertEqual(session["time_column"], " TIME ")
        fetched = self.get_session(session["upload_id"])
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["time_column"], " TIME ")

    def test_init_validates_time_setup(self):
        body = small_csv()
        missing_column = self.init_upload(
            body, time_mode="column", time_column=None)
        self.assertEqual(missing_column.status_code, 400, missing_column.text)
        self.assertFalse((self.tests / "alpha").exists())

        auto_with_column = self.init_upload(
            body, time_mode="auto", time_column="time")
        self.assertEqual(auto_with_column.status_code, 400, auto_with_column.text)
        self.assertFalse((self.tests / "alpha").exists())

        invalid_mode = self.init_upload(body, time_mode="other")
        self.assertEqual(invalid_mode.status_code, 422, invalid_mode.text)
        self.assertFalse((self.tests / "alpha").exists())

    def test_init_conflicts_when_reserved_name_describes_another_file(self):
        body = small_csv()
        first = self.init_ok(body)
        manifest_path = self.tests / "alpha" / ".upload" / "manifest.json"
        before = manifest_path.read_bytes()

        conflicts = [
            {"body": body + b"x"},
            {"body": body, "source_file": "different.csv"},
            {"body": body, "last_modified_ms": 123},
        ]
        for case in conflicts:
            with self.subTest(case=case):
                response = self.init_upload(
                    case.pop("body"), **case)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(manifest_path.read_bytes(), before)
                self.assertEqual(
                    self.get_session(first["upload_id"]).status_code, 200)

    def test_init_conflicts_with_existing_ready_test_without_touching_it(self):
        directory = self.tests / "alpha"
        directory.mkdir()
        marker = b"time,a\n0,1\n"
        (directory / "raw.csv").write_bytes(marker)
        store.write_json_atomic(
            directory / "status.json", {"status": "ready"})
        store.write_json_atomic(
            directory / "meta.json", {"name": "alpha"})

        response = self.init_upload(small_csv())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual((directory / "raw.csv").read_bytes(), marker)
        self.assertEqual(store.get_status("alpha")["status"], "ready")

    def test_init_rejects_invalid_names_without_reserving_a_directory(self):
        for bad in ("", "has space", "semi;colon", "..", "___", "../escape"):
            with self.subTest(name=bad):
                response = self.init_upload(small_csv(), name=bad)
                self.assertIn(
                    response.status_code, (400, 422), response.text)
        self.assertEqual(list(self.tests.iterdir()), [])

    def test_init_rejects_nonpositive_and_over_limit_sizes(self):
        base = {
            "name": "alpha",
            "source_file": "alpha.csv",
            "last_modified_ms": 1,
            "fs_hz": 10.0,
        }
        for size in (0, -1):
            with self.subTest(size=size):
                response = self.client.post(
                    "/api/uploads", json={**base, "size_bytes": size})
                self.assertIn(
                    response.status_code, (400, 422), response.text)
                self.assertFalse((self.tests / "alpha").exists())

        with patch.object(uploads, "MAX_UPLOAD_BYTES", 100):
            response = self.client.post(
                "/api/uploads", json={**base, "size_bytes": 101})
        self.assertEqual(response.status_code, 413, response.text)
        self.assertFalse((self.tests / "alpha").exists())

        with patch.object(uploads, "UPLOAD_CHUNK_BYTES", 1):
            response = self.client.post(
                "/api/uploads",
                json={
                    **base,
                    "size_bytes": uploads._MAX_UPLOAD_CHUNKS + 1,
                },
            )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertIn("increase KIHA_UPLOAD_CHUNK_BYTES", response.text)
        self.assertFalse((self.tests / "alpha").exists())

    # ----- chunk validation, integrity, and idempotence -----

    def test_chunk_commit_is_durable_and_progress_is_reported(self):
        body = small_csv()
        session = self.init_ok(
            body, source_file="Rig export.csv")
        first = self.chunk_bytes(body, 0)
        progress = self.put_ok(session["upload_id"], 0, first)

        self.assertEqual(progress["received_bytes"], len(first))
        self.assertEqual(progress["received_chunks"], 1)
        self.assertEqual(progress["chunks"], [{
            "index": 0,
            "size": len(first),
            "sha256": digest(first),
        }])

        # GET reconstructs progress from durable commit state, not an XHR-local
        # counter. This is what makes browser/server restart resumption real.
        fetched = self.get_session(session["upload_id"])
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["chunks"], progress["chunks"])
        self.assertEqual(fetched.json()["received_bytes"], len(first))

        row = next(
            row for row in self.client.get("/api/tests").json()
            if row["name"] == "alpha"
        )
        self.assertEqual(row["status"], "receiving")
        self.assertEqual(row["source_file"], "Rig export.csv")
        self.assertEqual(row["size_bytes"], len(first))

    def test_chunk_rejects_missing_invalid_and_mismatched_checksums(self):
        body = small_csv()
        session = self.init_ok(body)
        first = self.chunk_bytes(body, 0)

        cases = [
            {"include_checksum": False},
            {"checksum": "not-a-sha256"},
            {"checksum": "0" * 64},
        ]
        for case in cases:
            with self.subTest(case=case):
                response = self.put_chunk(
                    session["upload_id"], 0, first, **case)
                self.assertIn(
                    response.status_code, (400, 422), response.text)
                fetched = self.get_session(session["upload_id"]).json()
                self.assertEqual(fetched["received_chunks"], 0)
                self.assertEqual(fetched["chunks"], [])

    def test_chunk_rejects_out_of_bounds_indexes(self):
        body = small_csv()
        session = self.init_ok(body)
        total = session["total_chunks"]
        first = self.chunk_bytes(body, 0)

        for index in (-1, total, total + 100):
            with self.subTest(index=index):
                response = self.put_chunk(
                    session["upload_id"], index, first)
                self.assertIn(
                    response.status_code, (400, 404, 422), response.text)
        self.assertEqual(
            self.get_session(session["upload_id"]).json()["received_chunks"],
            0,
        )

    def test_upload_id_is_bound_to_the_reserved_name(self):
        body = small_csv()
        alpha = self.init_ok(body)
        beta = self.init_ok(
            body, name="beta", source_file="beta.csv",
            last_modified_ms=2)
        first = self.chunk_bytes(body, 0)

        wrong_get = self.get_session(alpha["upload_id"], name="beta")
        self.assertIn(wrong_get.status_code, (404, 409), wrong_get.text)
        wrong_put = self.put_chunk(
            alpha["upload_id"], 0, first, name="beta")
        self.assertIn(wrong_put.status_code, (404, 409), wrong_put.text)
        wrong_complete = self.complete(alpha["upload_id"], name="beta")
        self.assertIn(
            wrong_complete.status_code, (404, 409), wrong_complete.text)

        self.assertEqual(
            self.get_session(alpha["upload_id"]).json()["received_chunks"], 0)
        self.assertEqual(
            self.get_session(
                beta["upload_id"], name="beta").json()["received_chunks"],
            0,
        )

    def test_unknown_and_malformed_upload_ids_do_not_touch_sessions(self):
        body = small_csv()
        session = self.init_ok(body)
        first = self.chunk_bytes(body, 0)

        for unknown in (str(uuid.uuid4()), "not-a-uuid", "../alpha"):
            with self.subTest(upload_id=unknown):
                fetched = self.get_session(unknown)
                self.assertIn(
                    fetched.status_code, (404, 422), fetched.text)
                sent = self.put_chunk(unknown, 0, first)
                self.assertIn(sent.status_code, (404, 422), sent.text)

        self.assertEqual(
            self.get_session(session["upload_id"]).json()["received_chunks"],
            0,
        )

    def test_chunk_requires_exact_nonfinal_and_final_lengths(self):
        body = b"x" * (TEST_CHUNK_BYTES * 2 + 7)
        session = self.init_ok(body)

        for index, wrong in (
            (0, b"x" * (TEST_CHUNK_BYTES - 1)),
            (0, b"x" * (TEST_CHUNK_BYTES + 1)),
            (2, b"x" * 6),
            (2, b"x" * 8),
        ):
            with self.subTest(index=index, length=len(wrong)):
                response = self.put_chunk(
                    session["upload_id"], index, wrong)
                self.assertIn(
                    response.status_code, (400, 413, 422), response.text)
        self.assertEqual(
            self.get_session(session["upload_id"]).json()["received_chunks"],
            0,
        )

        self.put_ok(
            session["upload_id"], 0, b"x" * TEST_CHUNK_BYTES)
        final_progress = self.put_ok(session["upload_id"], 2, b"x" * 7)
        self.assertEqual(final_progress["received_chunks"], 2)

    def test_nul_in_first_csv_sniff_is_rejected_but_session_can_resume(self):
        body = b"\x00" + b"x" * (TEST_CHUNK_BYTES * 2)
        session = self.init_ok(body)
        first = self.chunk_bytes(body, 0)

        response = self.put_chunk(session["upload_id"], 0, first)
        self.assertEqual(response.status_code, 400, response.text)
        fetched = self.get_session(session["upload_id"])
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["received_chunks"], 0)
        self.assertEqual(store.get_status("alpha")["status"], "receiving")

    def test_nul_outside_the_sniff_prefix_does_not_reject_a_later_chunk(self):
        body = b"a" * TEST_CHUNK_BYTES + b"\x00" + b"z" * 7
        session = self.init_ok(body)
        later = self.chunk_bytes(body, 1)

        response = self.put_chunk(session["upload_id"], 1, later)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["received_chunks"], 1)

    def test_same_chunk_retry_is_idempotent(self):
        body = small_csv()
        session = self.init_ok(body)
        first = self.chunk_bytes(body, 0)
        initial = self.put_ok(session["upload_id"], 0, first)
        retry = self.put_ok(session["upload_id"], 0, first)

        self.assertEqual(retry["received_chunks"], 1)
        self.assertEqual(retry["received_bytes"], len(first))
        self.assertEqual(retry["chunks"], initial["chunks"])

    def test_conflicting_chunk_retry_cannot_replace_committed_data(self):
        body = small_csv()
        session = self.init_ok(body)
        first = self.chunk_bytes(body, 0)
        self.put_ok(session["upload_id"], 0, first)

        replacement = b"q" * len(first)
        response = self.put_chunk(
            session["upload_id"], 0, replacement)
        self.assertEqual(response.status_code, 409, response.text)
        chunk = self.get_session(session["upload_id"]).json()["chunks"][0]
        self.assertEqual(chunk["sha256"], digest(first))
        self.assertEqual(chunk["size"], len(first))

    def test_out_of_order_chunks_are_committed_and_sorted_in_status(self):
        body = b"".join(
            bytes([65 + i]) * TEST_CHUNK_BYTES for i in range(4)
        ) + b"tail"
        session = self.init_ok(body)
        order = [4, 2, 0, 3, 1]
        for index in order:
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))

        fetched = self.get_session(session["upload_id"]).json()
        self.assertEqual(
            [chunk["index"] for chunk in fetched["chunks"]],
            list(range(session["total_chunks"])),
        )
        self.assertEqual(fetched["received_bytes"], len(body))

    def test_parallel_distinct_chunks_do_not_lose_commit_metadata(self):
        body = b"".join(
            bytes([65 + i]) * TEST_CHUNK_BYTES for i in range(4))
        session = self.init_ok(body)

        # Separate clients model separate browser connections. The production
        # backend is deliberately single-process, but chunk requests overlap.
        def send(index: int):
            client = TestClient(main.app)
            try:
                data = self.chunk_bytes(body, index)
                return client.put(
                    f"/api/uploads/{session['upload_id']}/chunks/{index}",
                    params={"name": "alpha"},
                    headers={"X-Chunk-SHA256": digest(data)},
                    files={
                        "file": (
                            "chunk.bin", data, "application/octet-stream")
                    },
                )
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(send, range(4)))
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)

        fetched = self.get_session(session["upload_id"]).json()
        self.assertEqual(fetched["received_chunks"], 4)
        self.assertEqual(fetched["received_bytes"], len(body))
        self.assertEqual(
            [chunk["index"] for chunk in fetched["chunks"]], [0, 1, 2, 3])

    def test_concurrent_conflicting_same_index_has_exactly_one_winner(self):
        body = b"x" * TEST_CHUNK_BYTES
        session = self.init_ok(body)
        candidates = (
            b"a" * TEST_CHUNK_BYTES,
            b"b" * TEST_CHUNK_BYTES,
        )

        def send(data: bytes):
            client = TestClient(main.app)
            try:
                return client.put(
                    f"/api/uploads/{session['upload_id']}/chunks/0",
                    params={"name": "alpha"},
                    headers={"X-Chunk-SHA256": digest(data)},
                    files={
                        "file": (
                            "chunk.bin", data, "application/octet-stream")
                    },
                )
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send, candidates))
        self.assertEqual(
            sorted(response.status_code for response in responses),
            [200, 409],
            [response.text for response in responses],
        )

        fetched = self.get_session(session["upload_id"]).json()
        self.assertEqual(fetched["received_chunks"], 1)
        self.assertEqual(len(fetched["chunks"]), 1)
        self.assertIn(
            fetched["chunks"][0]["sha256"],
            {digest(candidate) for candidate in candidates},
        )

    # ----- completion, ingestion, and cancellation -----

    def test_complete_rejects_missing_chunks_without_destroying_session(self):
        body = small_csv()
        session = self.init_ok(body)
        first = self.chunk_bytes(body, 0)
        self.put_ok(session["upload_id"], 0, first)

        response = self.complete(session["upload_id"])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse((self.tests / "alpha" / "raw.csv").exists())
        fetched = self.get_session(session["upload_id"])
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["received_chunks"], 1)
        self.assertEqual(store.get_status("alpha")["status"], "receiving")

    def test_complete_assembles_exact_raw_bytes_and_runs_existing_ingest(self):
        body = small_csv()
        session = self.init_ok(
            body,
            source_file="Original Rig Export.csv",
            fs_hz=10.0,
            uploader_name="Test Lab",
        )
        for index in reversed(range(session["total_chunks"])):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))

        response = self.complete(session["upload_id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(), {"name": "alpha", "status": "ingesting"})

        raw_path = self.tests / "alpha" / "raw.csv"
        self.assertEqual(raw_path.read_bytes(), body)
        self.assertEqual(store.get_status("alpha")["status"], "ready")
        meta = store.get_meta("alpha")
        self.assertEqual(meta["source_file"], "Original Rig Export.csv")
        self.assertEqual(meta["uploader_name"], "Test Lab")
        self.assertEqual(meta["n_rows"], 64)
        self.assertEqual(meta["time_column"], "time")
        self.assertEqual(meta["fs_hz"], 10.0)
        self.assertEqual(store.get_status("alpha")["uploader_name"], "Test Lab")
        row = next(
            row for row in store.list_tests() if row["name"] == "alpha")
        self.assertEqual(row["uploader_name"], "Test Lab")
        self.assertTrue((self.tests / "alpha" / "data.parquet").is_file())

    def test_complete_forwards_generated_time_column_and_rate(self):
        body = small_csv(rows=8, fs=10.0)
        session = self.init_ok(
            body,
            fs_hz=500.0,
            time_mode="generated",
            time_column="elapsed_s",
        )
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))

        response = self.complete(session["upload_id"])
        self.assertEqual(response.status_code, 200, response.text)
        meta = store.get_meta("alpha")
        self.assertEqual(meta["time_column"], "elapsed_s")
        self.assertEqual(meta["time_source"], "generated")
        self.assertEqual(meta["fs_hz"], 500.0)
        self.assertIn("time", meta["columns"])
        stored = pl.read_parquet(
            self.tests / "alpha" / "data.parquet",
            columns=["elapsed_s", "time"],
        )
        self.assertEqual(
            stored["elapsed_s"].to_list(),
            [i / 500.0 for i in range(8)],
        )
        self.assertEqual(stored["time"].to_list(), [i / 10.0 for i in range(8)])

    def test_cancel_removes_only_the_matching_receiving_session(self):
        body = small_csv()
        alpha = self.init_ok(body)
        beta = self.init_ok(
            body, name="beta", source_file="beta.csv",
            last_modified_ms=2)
        self.put_ok(
            alpha["upload_id"], 0, self.chunk_bytes(body, 0))

        wrong_name = self.client.delete(
            f"/api/uploads/{alpha['upload_id']}", params={"name": "beta"})
        self.assertIn(wrong_name.status_code, (404, 409), wrong_name.text)
        self.assertTrue((self.tests / "alpha").is_dir())
        self.assertTrue((self.tests / "beta").is_dir())

        wrong_id = self.client.delete(
            f"/api/uploads/{uuid.uuid4()}", params={"name": "alpha"})
        self.assertIn(wrong_id.status_code, (404, 409), wrong_id.text)
        self.assertTrue((self.tests / "alpha").is_dir())

        cancelled = self.client.delete(
            f"/api/uploads/{alpha['upload_id']}", params={"name": "alpha"})
        self.assertIn(cancelled.status_code, (200, 204), cancelled.text)
        self.assertFalse((self.tests / "alpha").exists())
        self.assertTrue((self.tests / "beta").is_dir())
        self.assertEqual(
            self.get_session(beta["upload_id"], name="beta").status_code, 200)

        # The reservation was actually released, not merely hidden.
        replacement = self.init_upload(body)
        self.assertIn(replacement.status_code, (200, 201), replacement.text)
        self.assertNotEqual(
            replacement.json()["upload_id"], alpha["upload_id"])

    def test_cancel_cannot_delete_a_completed_ready_test(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))
        complete = self.complete(session["upload_id"])
        self.assertEqual(complete.status_code, 200, complete.text)

        raw = (self.tests / "alpha" / "raw.csv").read_bytes()
        response = self.client.delete(
            f"/api/uploads/{session['upload_id']}",
            params={"name": "alpha"},
        )
        self.assertIn(response.status_code, (404, 409), response.text)
        self.assertEqual((self.tests / "alpha" / "raw.csv").read_bytes(), raw)
        self.assertEqual(store.get_status("alpha")["status"], "ready")

    def test_delete_and_rename_reject_receiving_upload(self):
        session = self.init_ok(small_csv())
        self.assertTrue(session["upload_id"])

        for operation in (
            lambda: main.api_delete_test("alpha"),
            lambda: main.api_rename_test("alpha", "beta"),
        ):
            with self.assertRaises(HTTPException) as caught:
                operation()
            self.assertEqual(caught.exception.status_code, 409)
            self.assertIn("receiving", caught.exception.detail)
        self.assertTrue((self.tests / "alpha").is_dir())

    # ----- process-restart recovery -----

    def test_restart_recovery_preserves_valid_resumable_session(self):
        body = small_csv()
        session = self.init_ok(body)
        first = self.chunk_bytes(body, 0)
        self.put_ok(session["upload_id"], 0, first)
        manifest_path = self.tests / "alpha" / ".upload" / "manifest.json"
        manifest_before = manifest_path.read_bytes()

        uploads.recover_uploads()

        self.assertEqual(store.get_status("alpha")["status"], "receiving")
        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        fetched = self.get_session(session["upload_id"])
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["received_bytes"], len(first))
        self.assertEqual(fetched.json()["received_chunks"], 1)

    def test_restart_recovery_marks_legacy_receiving_directory_as_error(self):
        directory = self.tests / "legacy"
        directory.mkdir()
        (directory / "raw.csv").write_bytes(b"partial legacy raw body")
        store.write_json_atomic(
            directory / "status.json", {"status": "receiving"})

        uploads.recover_uploads()

        status = store.get_status("legacy")
        self.assertEqual(status["status"], "error")
        self.assertIn("interrupted", status["error"].lower())
        self.assertTrue((directory / "raw.csv").is_file())

    def test_restart_recovers_manifest_written_before_first_status(self):
        body = small_csv()
        session = self.init_ok(body)
        (self.tests / "alpha" / "status.json").unlink()

        jobs = uploads.recover_uploads()

        self.assertEqual(jobs, [])
        self.assertEqual(store.get_status("alpha")["status"], "receiving")
        fetched = self.get_session(session["upload_id"])
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["received_chunks"], 0)
        self.assertIsNone(fetched.json()["uploader_name"])
        manifest = json.loads(
            (self.tests / "alpha" / ".upload" / "manifest.json").read_text())
        self.assertNotIn("uploader_name", manifest)
        self.assertNotIn("uploader_name", store.get_status("alpha"))
        row = next(
            row for row in store.list_tests() if row["name"] == "alpha")
        self.assertIsNone(row["uploader_name"])

    def test_restart_reconciles_ready_status_with_ingesting_manifest(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))
        self.assertEqual(self.complete(session["upload_id"]).status_code, 200)
        self.assertEqual(store.get_status("alpha")["status"], "ready")

        manifest_path = self.tests / "alpha" / ".upload" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["state"] = "ingesting"
        store.write_json_atomic(manifest_path, manifest)

        jobs = uploads.recover_uploads()

        self.assertEqual(jobs, [])
        reconciled = json.loads(manifest_path.read_text())
        self.assertEqual(reconciled["state"], "ready")
        self.assertEqual(store.get_status("alpha")["status"], "ready")

    def test_restart_reconciles_ingest_error_with_ingesting_manifest(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))

        # Assemble the immutable raw.csv without running the background ingest.
        # This models a crash after ingest_csv recorded its error but before the
        # wrapper could update the upload audit manifest.
        payload, schedule = uploads._complete_upload(
            "alpha", session["upload_id"])
        self.assertTrue(schedule)
        self.assertEqual(payload["status"], "ingesting")
        directory = self.tests / "alpha"
        manifest_path = directory / ".upload" / "manifest.json"
        self.assertEqual(
            json.loads(manifest_path.read_text())["state"], "ingesting")
        store.write_json_atomic(
            directory / "status.json",
            {"status": "error", "error": "synthetic ingest failure"},
        )

        jobs = uploads.recover_uploads()

        self.assertEqual(jobs, [])
        self.assertEqual(
            json.loads(manifest_path.read_text())["state"], "error")
        status = store.get_status("alpha")
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["error"], "synthetic ingest failure")

    def test_renamed_completed_upload_survives_restart_recovery(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))
        self.assertEqual(self.complete(session["upload_id"]).status_code, 200)
        self.assertEqual(store.get_status("alpha")["status"], "ready")

        self.assertEqual(
            main.api_rename_test("alpha", "beta"), {"ok": True, "name": "beta"})
        manifest_path = self.tests / "beta" / ".upload" / "manifest.json"
        self.assertEqual(
            json.loads(manifest_path.read_text())["name"], "beta")

        jobs = uploads.recover_uploads()

        self.assertEqual(jobs, [])
        self.assertEqual(store.get_status("beta")["status"], "ready")
        self.assertFalse((self.tests / "alpha").exists())

    # ----- resource bounds and crash windows -----

    def test_multipart_limit_runs_before_parsing_even_for_bad_index(self):
        body = small_csv()
        session = self.init_ok(body)
        oversized = b"x" * (
            uploads.UPLOAD_MULTIPART_OVERHEAD_BYTES + TEST_CHUNK_BYTES + 1)

        response = self.put_chunk(
            session["upload_id"], -1, oversized)

        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(
            self.get_session(session["upload_id"]).json()["received_chunks"],
            0,
        )

    def test_multipart_limit_counts_body_when_content_length_lies(self):
        body = small_csv()
        session = self.init_ok(body)
        boundary = "ptt-body-limit"
        oversized = b"x" * (
            uploads.UPLOAD_MULTIPART_OVERHEAD_BYTES + TEST_CHUNK_BYTES + 1)
        multipart = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="x.bin"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + oversized + f"\r\n--{boundary}--\r\n".encode()

        response = self.client.put(
            f"/api/uploads/{session['upload_id']}/chunks/0",
            params={"name": "alpha"},
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": "1",
                "X-Chunk-SHA256": digest(oversized),
            },
            content=multipart,
        )

        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(
            self.get_session(session["upload_id"]).json()["received_chunks"],
            0,
        )

    def test_multipart_rejects_more_than_one_file_part(self):
        body = small_csv()
        session = self.init_ok(body)
        first = self.chunk_bytes(body, 0)
        response = self.client.put(
            f"/api/uploads/{session['upload_id']}/chunks/0",
            params={"name": "alpha"},
            headers={"X-Chunk-SHA256": digest(first)},
            files=[
                ("file", ("first.bin", first, "application/octet-stream")),
                ("file", ("second.bin", b"x", "application/octet-stream")),
            ],
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            self.get_session(session["upload_id"]).json()["received_chunks"],
            0,
        )

    def test_active_session_keeps_manifest_chunk_limit_after_config_change(self):
        body = b"x" * 4096
        with patch.object(uploads, "UPLOAD_CHUNK_BYTES", 2048):
            initiated = self.init_upload(body)
        self.assertEqual(initiated.status_code, 201, initiated.text)
        session = initiated.json()
        self.assertEqual(session["chunk_size"], 2048)
        first = body[:2048]

        with (
            patch.object(uploads, "UPLOAD_CHUNK_BYTES", 32),
            patch.object(uploads, "UPLOAD_MULTIPART_OVERHEAD_BYTES", 512),
        ):
            response = self.put_chunk(session["upload_id"], 0, first)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["received_bytes"], 2048)

    def test_init_rejects_non_finite_fallback_sample_rate(self):
        response = self.client.post(
            "/api/uploads",
            content=(
                '{"name":"alpha","source_file":"alpha.csv",'
                f'"size_bytes":{len(small_csv())},'
                '"last_modified_ms":1,"fs_hz":1e999}'
            ),
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse((self.tests / "alpha").exists())

    def test_disk_full_during_commit_leaves_session_resumable(self):
        body = small_csv()
        session = self.init_ok(body)
        first = self.chunk_bytes(body, 0)
        real_open = open

        def fail_staging(path, *args, **kwargs):
            if Path(path).name == "raw.csv.uploading":
                raise OSError(errno.ENOSPC, "simulated full disk")
            return real_open(path, *args, **kwargs)

        with patch("app.uploads.open", side_effect=fail_staging, create=True):
            response = self.put_chunk(session["upload_id"], 0, first)

        self.assertEqual(response.status_code, 507, response.text)
        fetched = self.get_session(session["upload_id"]).json()
        self.assertEqual(fetched["received_chunks"], 0)
        self.assertEqual(store.get_status("alpha")["status"], "receiving")
        self.put_ok(session["upload_id"], 0, first)

    def test_stale_receiving_session_is_purged_on_next_initiation(self):
        body = small_csv()
        alpha = self.init_ok(body)
        manifest_path = self.tests / "alpha" / ".upload" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["updated_at_epoch"] = 0
        store.write_json_atomic(manifest_path, manifest)

        with patch.object(uploads, "UPLOAD_STALE_AGE_S", 1):
            beta = self.init_ok(
                body,
                name="beta",
                source_file="beta.csv",
                last_modified_ms=2,
            )

        self.assertFalse((self.tests / "alpha").exists())
        self.assertEqual(
            self.get_session(beta["upload_id"], name="beta").status_code, 200)
        self.assertNotEqual(alpha["upload_id"], beta["upload_id"])

    def test_restart_finishes_finalizing_session_and_returns_ingest_job(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))

        manifest_path = self.tests / "alpha" / ".upload" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["state"] = "finalizing"
        store.write_json_atomic(manifest_path, manifest)

        jobs = uploads.recover_uploads()

        self.assertEqual(jobs, [("alpha", session["upload_id"])])
        self.assertEqual(store.get_status("alpha")["status"], "ingesting")
        self.assertEqual(
            (self.tests / "alpha" / "raw.csv").read_bytes(), body)
        uploads.ingest_completed_upload(*jobs[0])
        self.assertEqual(store.get_status("alpha")["status"], "ready")

    def test_restart_recovers_crash_after_final_rename(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))
        directory = self.tests / "alpha"
        manifest_path = directory / ".upload" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["state"] = "finalizing"
        store.write_json_atomic(manifest_path, manifest)
        os.replace(
            directory / ".upload" / "raw.csv.uploading",
            directory / "raw.csv",
        )

        jobs = uploads.recover_uploads()

        self.assertEqual(jobs, [("alpha", session["upload_id"])])
        self.assertEqual((directory / "raw.csv").read_bytes(), body)
        self.assertEqual(store.get_status("alpha")["status"], "ingesting")

    def test_completion_rehash_rejects_corrupted_staging_bytes(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))
        staging = (
            self.tests / "alpha" / ".upload" / "raw.csv.uploading")
        with staging.open("r+b") as handle:
            handle.seek(TEST_CHUNK_BYTES + 3)
            original = handle.read(1)
            handle.seek(TEST_CHUNK_BYTES + 3)
            handle.write(bytes([original[0] ^ 0xFF]))

        response = self.complete(session["upload_id"])

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("checksum", response.text.lower())
        self.assertFalse((self.tests / "alpha" / "raw.csv").exists())
        self.assertEqual(store.get_status("alpha")["status"], "error")
        cancelled = self.client.delete(
            f"/api/uploads/{session['upload_id']}", params={"name": "alpha"})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertFalse((self.tests / "alpha").exists())

    def test_completion_retry_repairs_failed_integrity_status_write(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))
        staging = (
            self.tests / "alpha" / ".upload" / "raw.csv.uploading")
        with staging.open("r+b") as handle:
            handle.seek(1)
            original = handle.read(1)
            handle.seek(1)
            handle.write(bytes([original[0] ^ 0xFF]))

        with patch.object(
            uploads,
            "write_status",
            side_effect=OSError(errno.EIO, "synthetic status write failure"),
        ):
            with self.assertRaises(OSError):
                uploads._complete_upload("alpha", session["upload_id"])

        manifest = json.loads(
            (self.tests / "alpha" / ".upload" / "manifest.json").read_text())
        self.assertEqual(manifest["state"], "error")
        self.assertIn("checksum", manifest["error"])
        self.assertEqual(store.get_status("alpha")["status"], "receiving")

        with self.assertRaises(HTTPException) as caught:
            uploads._complete_upload("alpha", session["upload_id"])

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(store.get_status("alpha")["status"], "error")
        self.assertEqual(main.api_delete_test("alpha")["deleted"], "alpha")

    def test_completion_retry_repairs_failed_ingesting_status_write(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))

        with patch.object(
            uploads,
            "write_status",
            side_effect=OSError(errno.EIO, "synthetic status write failure"),
        ):
            with self.assertRaises(OSError):
                uploads._complete_upload("alpha", session["upload_id"])

        directory = self.tests / "alpha"
        manifest = json.loads(
            (directory / ".upload" / "manifest.json").read_text())
        self.assertEqual(manifest["state"], "ingesting")
        self.assertEqual(store.get_status("alpha")["status"], "receiving")
        self.assertTrue((directory / "raw.csv").is_file())

        payload, schedule = uploads._complete_upload(
            "alpha", session["upload_id"])

        self.assertEqual(payload, {"name": "alpha", "status": "ingesting"})
        self.assertTrue(schedule)
        self.assertEqual(store.get_status("alpha")["status"], "ingesting")
        uploads.ingest_completed_upload("alpha", session["upload_id"])
        self.assertEqual(store.get_status("alpha")["status"], "ready")

    def test_ingest_wrapper_repairs_missing_error_status(self):
        body = small_csv()
        session = self.init_ok(body, uploader_name="Failure Lab")
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))
        payload, schedule = uploads._complete_upload(
            "alpha", session["upload_id"])
        self.assertEqual(payload["status"], "ingesting")
        self.assertTrue(schedule)

        with patch.object(
            uploads,
            "ingest_csv",
            side_effect=OSError(errno.EIO, "synthetic ingest failure"),
        ):
            uploads.ingest_completed_upload("alpha", session["upload_id"])

        directory = self.tests / "alpha"
        manifest = json.loads(
            (directory / ".upload" / "manifest.json").read_text())
        self.assertEqual(manifest["state"], "error")
        self.assertIn("synthetic ingest failure", manifest["error"])
        status = store.get_status("alpha")
        self.assertEqual(status["status"], "error")
        self.assertIn("synthetic ingest failure", status["error"])
        self.assertEqual(status["uploader_name"], "Failure Lab")
        row = next(
            row for row in store.list_tests() if row["name"] == "alpha")
        self.assertEqual(row["uploader_name"], "Failure Lab")
        self.assertEqual(main.api_delete_test("alpha")["deleted"], "alpha")

    def test_ingest_wrapper_does_not_recreate_concurrently_deleted_test(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))
        _, schedule = uploads._complete_upload(
            "alpha", session["upload_id"])
        self.assertTrue(schedule)

        def fail_after_delete(*_args, **_kwargs):
            uploads.write_status(
                self.tests / "alpha", "error", "synthetic ingest failure")
            self.assertEqual(
                main.api_delete_test("alpha")["deleted"], "alpha")
            raise OSError(errno.EIO, "synthetic failure after delete")

        with patch.object(
            uploads, "ingest_csv", side_effect=fail_after_delete
        ):
            uploads.ingest_completed_upload("alpha", session["upload_id"])

        self.assertFalse((self.tests / "alpha").exists())
        entries = main.api_list_trash()["entries"]
        self.assertEqual([entry["name"] for entry in entries], ["alpha"])
        self.assertTrue((main.TRASH_DIR / entries[0]["id"] / "data").is_dir())

    def test_complete_transition_is_idempotent_before_ingest_runs(self):
        body = small_csv()
        session = self.init_ok(body)
        for index in range(session["total_chunks"]):
            self.put_ok(
                session["upload_id"], index, self.chunk_bytes(body, index))

        first, first_schedule = uploads._complete_upload(
            "alpha", session["upload_id"])
        second, second_schedule = uploads._complete_upload(
            "alpha", session["upload_id"])

        self.assertEqual(first, {"name": "alpha", "status": "ingesting"})
        self.assertEqual(second, first)
        self.assertTrue(first_schedule)
        self.assertFalse(second_schedule)
        uploads.ingest_completed_upload("alpha", session["upload_id"])
        self.assertEqual(store.get_status("alpha")["status"], "ready")


if __name__ == "__main__":
    unittest.main()
