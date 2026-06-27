import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l5_review_decision_intake import run_l5_review_decision_intake
from scripts.l5_review_decision_reporter import run_l5_review_decision_reporter
from scripts.l5_review_decision_template_exporter import REQUIRED_TEMPLATE_COLUMNS, run_l5_review_decision_template_exporter


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def seed_project(project_dir: Path) -> Path:
    (project_dir / "index").mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (project_dir / "inputs").mkdir(parents=True, exist_ok=True)
    db_path = project_dir / "index" / "novel_story_bible.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE l5_normalized_event_candidate (
                normalized_event_candidate_id TEXT PRIMARY KEY,
                source_event_candidate_id TEXT,
                source_event_candidate_hash TEXT,
                source_review_row_id TEXT,
                source_layer TEXT,
                chapter_id TEXT,
                chapter_num INTEGER,
                chapter_title TEXT,
                version_id TEXT,
                scene_block_id TEXT,
                scene_block_table_name TEXT,
                scene_block_source_status TEXT,
                scene_block_validity TEXT,
                trigger_text TEXT,
                trigger_rule_id TEXT,
                trigger_category TEXT,
                event_type_candidate TEXT,
                event_subtype_candidate TEXT,
                l5_2_event_type TEXT,
                l5_2_event_subtype TEXT,
                normalization_status TEXT,
                normalization_rule_id TEXT,
                subject_text TEXT,
                subject_entity_kind TEXT,
                subject_is_confirmed INTEGER,
                confidence_score REAL,
                normalized_confidence_score REAL,
                importance_level TEXT,
                evidence_backcut_status TEXT,
                evidence_backcut_hash TEXT,
                evidence_text_backcut TEXT,
                l5_2_seed_checksum TEXT,
                stable_hash TEXT,
                created_at TEXT
            );
            CREATE TABLE l5_normalized_event_argument (
                normalized_argument_id TEXT PRIMARY KEY,
                normalized_event_candidate_id TEXT,
                source_event_candidate_id TEXT,
                argument_role TEXT,
                entity_kind TEXT,
                argument_text TEXT,
                confidence REAL,
                source_kind TEXT,
                source_rule TEXT,
                evidence_text TEXT,
                is_confirmed INTEGER,
                l5_2_seed_checksum TEXT,
                stable_hash TEXT,
                created_at TEXT
            );
            CREATE TABLE l5_normalized_event_evidence (
                normalized_evidence_id TEXT PRIMARY KEY,
                normalized_event_candidate_id TEXT,
                source_event_candidate_id TEXT,
                evidence_source_kind TEXT,
                l2_sentence_id TEXT,
                l2_paragraph_id TEXT,
                start_offset INTEGER,
                end_offset INTEGER,
                evidence_text TEXT,
                evidence_hash TEXT,
                evidence_backcut_status TEXT,
                l5_2_seed_checksum TEXT,
                stable_hash TEXT,
                created_at TEXT
            );
            """
        )
        for idx in (1, 2, 3):
            event_id = f"l5n_evt_{idx}"
            evidence = f"Chen event {idx} evidence text."
            conn.execute(
                """
                INSERT INTO l5_normalized_event_candidate VALUES (
                    ?, ?, ?, ?, 'L5.1a', 'ch_0001', 1, 'chapter one', 'ver_1',
                    'scene_1', 'l3_scene_blocks', 'compatible_fallback', 'valid',
                    'trigger', 'rule', 'movement', 'character_arrival', 'arrive',
                    'movement', 'arrive', 'normalized_candidate', 'mapping',
                    ?, 'character', 0, 0.7, ?, 'normal', 'ok', ?, ?, 'seed', ?, '1970-01-01T00:00:00'
                )
                """,
                (event_id, f"evt_{idx}", sha256_text(f"evt_{idx}"), f"review_{idx}", f"Chen{idx}", 0.8, sha256_text(evidence), evidence, sha256_text(event_id)),
            )
            conn.execute(
                "INSERT INTO l5_normalized_event_argument VALUES (?, ?, ?, 'subject', 'character', ?, 0.8, 'fixture', 'fixture', '', 0, 'seed', ?, '1970-01-01T00:00:00')",
                (f"arg_{idx}", event_id, f"evt_{idx}", f"Chen{idx}", sha256_text(f"arg_{idx}")),
            )
            conn.execute(
                "INSERT INTO l5_normalized_event_evidence VALUES (?, ?, ?, 'l2_sentence', 's1', 'p1', 0, 10, ?, ?, 'ok', 'seed', ?, '1970-01-01T00:00:00')",
                (f"evd_{idx}", event_id, f"evt_{idx}", evidence, sha256_text(evidence), sha256_text(f"evd_{idx}")),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def write_filled(project_dir: Path, rows: list[dict[str, str]], name: str = "l5_review_decision_filled.csv") -> Path:
    path = project_dir / "inputs" / name
    columns = [
        "review_batch_id",
        "normalized_event_id",
        "human_decision",
        "human_confidence",
        "human_notes",
        "duplicate_of_normalized_event_id",
        "needs_context_reason",
        "reject_reason",
        "reviewer_name",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            full = {column: "" for column in columns}
            full.update(row)
            full.setdefault("review_batch_id", "batch_1")
            writer.writerow(full)
    return path


class L5ReviewDecisionIntakeTests(unittest.TestCase):
    def test_template_export_has_required_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_review_decision_template_exporter(project_dir)

            rows = read_csv_rows(project_dir / "outputs" / "l5_review_decision_template.csv")
            self.assertEqual(len(rows), 3)
            self.assertTrue(set(REQUIRED_TEMPLATE_COLUMNS).issubset(rows[0].keys()))
            self.assertEqual(manifest["row_count"], 3)

    def test_decision_import_creates_l5_4_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            input_file = write_filled(project_dir, [{"normalized_event_id": "l5n_evt_1", "human_decision": "approved_candidate", "human_confidence": "0.9"}])

            result = run_l5_review_decision_intake(project_dir, input_file=input_file, rebuild=True)

            self.assertEqual(result["validation"]["error_count"], 0)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"l5_review_decision_import", "l5_review_decision_current", "l5_review_decision_conflict_audit", "l5_review_decision_run"}.issubset(tables))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM l5_review_decision_current").fetchone()[0], 1)
            finally:
                conn.close()

    def test_invalid_decision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            input_file = write_filled(project_dir, [{"normalized_event_id": "l5n_evt_1", "human_decision": "bad_decision", "human_confidence": "high"}])

            result = run_l5_review_decision_intake(project_dir, input_file=input_file, rebuild=True)

            self.assertEqual(result["validation"]["error_count"], 1)
            self.assertEqual(result["row_counts"]["current_decision_count"], 0)

    def test_duplicate_candidate_requires_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            input_file = write_filled(project_dir, [{"normalized_event_id": "l5n_evt_1", "human_decision": "duplicate_candidate", "human_confidence": "medium"}])

            result = run_l5_review_decision_intake(project_dir, input_file=input_file, rebuild=True)

            self.assertEqual(result["validation"]["error_count"], 1)
            self.assertIn("duplicate_target_missing", result["conflict_type_counts"])

    def test_needs_context_requires_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            input_file = write_filled(project_dir, [{"normalized_event_id": "l5n_evt_1", "human_decision": "needs_context", "human_confidence": "0.4"}])

            result = run_l5_review_decision_intake(project_dir, input_file=input_file, rebuild=True)

            self.assertEqual(result["validation"]["error_count"], 1)

    def test_rejected_requires_reason_or_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            input_file = write_filled(project_dir, [{"normalized_event_id": "l5n_evt_1", "human_decision": "rejected", "human_confidence": "0.8"}])

            result = run_l5_review_decision_intake(project_dir, input_file=input_file, rebuild=True)

            self.assertEqual(result["validation"]["error_count"], 1)

    def test_current_snapshot_unique_per_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            input_file = write_filled(
                project_dir,
                [
                    {"normalized_event_id": "l5n_evt_1", "human_decision": "approved_candidate", "human_confidence": "0.9"},
                    {"normalized_event_id": "l5n_evt_1", "human_decision": "approved_candidate", "human_confidence": "0.9"},
                ],
            )

            result = run_l5_review_decision_intake(project_dir, input_file=input_file, rebuild=True)

            self.assertEqual(result["validation"]["error_count"], 0)
            self.assertEqual(result["row_counts"]["current_decision_count"], 1)

    def test_conflict_audit_detects_conflicting_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            input_file = write_filled(
                project_dir,
                [
                    {"normalized_event_id": "l5n_evt_1", "human_decision": "approved_candidate", "human_confidence": "0.9"},
                    {"normalized_event_id": "l5n_evt_1", "human_decision": "weak_candidate", "human_confidence": "0.5"},
                ],
            )

            result = run_l5_review_decision_intake(project_dir, input_file=input_file, rebuild=True)

            self.assertIn("conflicting_decisions", result["conflict_type_counts"])
            self.assertEqual(result["row_counts"]["current_decision_count"], 0)

    def test_readiness_export_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            input_file = write_filled(
                project_dir,
                [
                    {"normalized_event_id": "l5n_evt_1", "human_decision": "approved_candidate", "human_confidence": "0.9"},
                    {"normalized_event_id": "l5n_evt_2", "human_decision": "rejected", "human_confidence": "0.8", "reject_reason": "not event"},
                ],
            )

            run_l5_review_decision_intake(project_dir, input_file=input_file, rebuild=True)
            report = run_l5_review_decision_reporter(project_dir)

            readiness = {row["normalized_event_id"]: row["readiness_status"] for row in read_csv_rows(project_dir / "outputs" / "l5_confirmed_event_candidate_readiness.csv")}
            self.assertEqual(readiness["l5n_evt_1"], "ready_for_l5_5")
            self.assertEqual(readiness["l5n_evt_2"], "blocked_by_rejected")
            self.assertEqual(readiness["l5n_evt_3"], "missing_review_decision")
            self.assertEqual(report["readiness_status_counts"]["ready_for_l5_5"], 1)

    def test_rebuild_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            input_file = write_filled(project_dir, [{"normalized_event_id": "l5n_evt_1", "human_decision": "approved_candidate", "human_confidence": "high"}])

            first = run_l5_review_decision_intake(project_dir, input_file=input_file, rebuild=True)
            second = run_l5_review_decision_intake(project_dir, input_file=input_file, rebuild=True)

            self.assertEqual(first["stable_output_hashes"], second["stable_output_hashes"])
            self.assertFalse(second["source_mutation_detected"])
            self.assertFalse(second["input_mutation_detected"])


if __name__ == "__main__":
    unittest.main()
