import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l5_event_candidate_extractor import run_l5_event_candidate_extractor
from scripts.l5_event_candidate_reporter import run_l5_event_candidate_reporter
from scripts.l5_verify_event_candidate_index import FAIL_MESSAGE, PASS_MESSAGE, run_l5_event_candidate_verification


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_rules(project_dir: Path) -> None:
    path = project_dir / "config" / "event_trigger_rules.seed.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "test",
        "rules": [
            {
                "rule_id": "arrival_test",
                "event_family": "character_event",
                "event_type": "character_arrival",
                "trigger_terms": ["enter", "returned"],
                "negative_terms": [],
                "confidence_level": "medium",
                "state_change_hint": {"state_type": "location_state", "from_state": "absent", "to_state": "arrived"},
                "note": "test",
            },
            {
                "rule_id": "injury_test",
                "event_family": "character_event",
                "event_type": "character_injury",
                "trigger_terms": ["injured", "blood"],
                "negative_terms": [],
                "confidence_level": "high",
                "state_change_hint": {"state_type": "life_state", "from_state": "normal", "to_state": "injured"},
                "note": "test",
            },
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def seed_project(
    project_dir: Path,
    chapters: dict[int, list[str]],
    *,
    with_character_source: bool = False,
    scene_source: str | None = None,
) -> Path:
    write_rules(project_dir)
    db_path = project_dir / "index" / "novel_story_bible.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE current_chapters_fixture (
                chapter_id TEXT PRIMARY KEY,
                chapter_num INTEGER NOT NULL,
                chapter_title_current TEXT NOT NULL,
                latest_version_id TEXT NOT NULL,
                content_full_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                content_length INTEGER NOT NULL
            );
            CREATE TABLE l2_current_paragraphs_fixture (
                para_id TEXT PRIMARY KEY,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                para_index INTEGER NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                char_length INTEGER NOT NULL,
                para_hash TEXT NOT NULL,
                para_kind TEXT NOT NULL,
                para_text TEXT NOT NULL,
                chapter_title_current TEXT NOT NULL
            );
            CREATE TABLE l2_current_sentences_fixture (
                sentence_id TEXT PRIMARY KEY,
                para_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                para_index INTEGER NOT NULL,
                sentence_index INTEGER NOT NULL,
                global_sentence_index INTEGER NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                char_length INTEGER NOT NULL,
                sentence_hash TEXT NOT NULL,
                sentence_text TEXT NOT NULL,
                chapter_title_current TEXT NOT NULL
            );
            CREATE VIEW v_current_chapters AS SELECT * FROM current_chapters_fixture;
            CREATE VIEW v_l2_current_paragraphs AS SELECT * FROM l2_current_paragraphs_fixture;
            CREATE VIEW v_l2_current_sentences AS SELECT * FROM l2_current_sentences_fixture;
            """
        )
        global_sentence = 0
        chapter_meta: dict[int, tuple[str, str, int]] = {}
        for chapter_num, sentences in chapters.items():
            chapter_id = f"ch_{chapter_num:04d}"
            version_id = f"ver_{chapter_num:04d}"
            title = f"chapter {chapter_num}"
            content = "\n".join(sentences)
            chapter_meta[chapter_num] = (chapter_id, version_id, len(content))
            conn.execute(
                """
                INSERT INTO current_chapters_fixture (
                    chapter_id, chapter_num, chapter_title_current, latest_version_id,
                    content_full_text, content_hash, content_length
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (chapter_id, chapter_num, title, version_id, content, sha256_text(content), len(content)),
            )
            cursor = 0
            for index, sentence in enumerate(sentences, start=1):
                start = content.index(sentence, cursor)
                end = start + len(sentence)
                para_id = f"{chapter_id}_p{index:04d}"
                sentence_id = f"{chapter_id}_s{index:04d}"
                conn.execute(
                    """
                    INSERT INTO l2_current_paragraphs_fixture (
                        para_id, chapter_id, chapter_num, version_id, para_index,
                        start_offset, end_offset, char_length, para_hash, para_kind,
                        para_text, chapter_title_current
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'body', ?, ?)
                    """,
                    (para_id, chapter_id, chapter_num, version_id, index, start, end, end - start, sha256_text(sentence), sentence, title),
                )
                conn.execute(
                    """
                    INSERT INTO l2_current_sentences_fixture (
                        sentence_id, para_id, chapter_id, chapter_num, version_id,
                        para_index, sentence_index, global_sentence_index,
                        start_offset, end_offset, char_length, sentence_hash,
                        sentence_text, chapter_title_current
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sentence_id, para_id, chapter_id, chapter_num, version_id, index, index, global_sentence, start, end, end - start, sha256_text(sentence), sentence, title),
                )
                cursor = end
                global_sentence += 1

        if scene_source in {"l3", "both"}:
            conn.executescript(
                """
                CREATE TABLE l3_scene_blocks (
                    scene_id INTEGER PRIMARY KEY,
                    scene_key TEXT NOT NULL UNIQUE,
                    chapter_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    chapter_num INTEGER NOT NULL,
                    scene_index_in_chapter INTEGER NOT NULL,
                    start_para_id TEXT NOT NULL,
                    end_para_id TEXT NOT NULL,
                    start_para_index INTEGER NOT NULL,
                    end_para_index INTEGER NOT NULL,
                    start_sentence_id TEXT,
                    end_sentence_id TEXT,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    length INTEGER NOT NULL,
                    scene_kind TEXT NOT NULL,
                    split_reason TEXT NOT NULL,
                    summary_short TEXT,
                    source_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            for scene_id, chapter_num in enumerate(sorted(chapter_meta), start=1):
                chapter_id, version_id, content_length = chapter_meta[chapter_num]
                conn.execute(
                    """
                    INSERT INTO l3_scene_blocks (
                        scene_id, scene_key, chapter_id, version_id, chapter_num, scene_index_in_chapter,
                        start_para_id, end_para_id, start_para_index, end_para_index,
                        start_sentence_id, end_sentence_id, start_offset, end_offset, length,
                        scene_kind, split_reason, summary_short, source_hash
                    )
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, 1, 1, ?, ?, 0, ?, ?, 'normal', 'fixture', NULL, ?)
                    """,
                    (
                        scene_id,
                        f"{chapter_id}:{version_id}:scene:1",
                        chapter_id,
                        version_id,
                        chapter_num,
                        f"{chapter_id}_p0001",
                        f"{chapter_id}_p0001",
                        f"{chapter_id}_s0001",
                        f"{chapter_id}_s0001",
                        content_length,
                        content_length,
                        sha256_text(f"{chapter_id}:{version_id}:{content_length}"),
                    ),
                )

        if scene_source in {"l4", "both"}:
            conn.executescript(
                """
                CREATE TABLE l4_scene_blocks (
                    scene_block_id TEXT PRIMARY KEY,
                    chapter_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    chapter_num INTEGER NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL
                );
                """
            )
            for chapter_num in sorted(chapter_meta):
                chapter_id, version_id, content_length = chapter_meta[chapter_num]
                conn.execute(
                    """
                    INSERT INTO l4_scene_blocks (
                        scene_block_id, chapter_id, version_id, chapter_num, start_offset, end_offset
                    )
                    VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (f"l4:{chapter_id}:scene:1", chapter_id, version_id, chapter_num, content_length),
                )

        if with_character_source:
            first_sentence = chapters[1][0]
            conn.executescript(
                """
                CREATE TABLE l3_character_appearance (
                    appearance_id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    alias_id TEXT NOT NULL,
                    matched_text TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    chapter_num INTEGER NOT NULL,
                    version_id TEXT NOT NULL,
                    para_id TEXT NOT NULL,
                    sentence_id TEXT NOT NULL,
                    sentence_start_offset INTEGER NOT NULL,
                    sentence_end_offset INTEGER NOT NULL,
                    match_start_offset INTEGER NOT NULL,
                    match_end_offset INTEGER NOT NULL,
                    sentence_hash TEXT NOT NULL,
                    paragraph_hash TEXT,
                    l1_backcut_matched INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    indexer_version TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                """
                INSERT INTO l3_character_appearance (
                    appearance_id, character_id, alias_id, matched_text, chapter_id, chapter_num,
                    version_id, para_id, sentence_id, sentence_start_offset, sentence_end_offset,
                    match_start_offset, match_end_offset, sentence_hash, paragraph_hash,
                    l1_backcut_matched, status, indexer_version
                )
                VALUES ('app_1', 'char_chen', 'alias_chen', 'Chen', 'ch_0001', 1,
                        'ver_0001', 'ch_0001_p0001', 'ch_0001_s0001', 0, ?,
                        0, 4, ?, ?, 1, 'candidate', 'fixture')
                """,
                (len(first_sentence), sha256_text(first_sentence), sha256_text(first_sentence)),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


class L5EventStateChangeCandidateIndexTests(unittest.TestCase):
    def test_schema_trigger_offsets_status_and_verifier_pass_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(
                project_dir,
                {1: ["Chen enter room, with blood on his face."], 2: ["He returned."], 3: ["This enter is outside sample."]},
                with_character_source=True,
            )

            stats = run_l5_event_candidate_extractor(project_dir, sample_chapters="1,2", rebuild=True)
            run_l5_event_candidate_reporter(project_dir)
            verification = run_l5_event_candidate_verification(project_dir, sample_chapters="1,2")

            self.assertGreaterEqual(stats.candidate_count, 3)
            self.assertEqual(verification.final_message, PASS_MESSAGE)
            self.assertTrue(verification.ok)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"l5_event_candidate", "l5_event_argument_candidate", "l5_event_state_change_candidate", "l5_event_evidence_span", "l5_event_extraction_run"}.issubset(tables))
                rows = conn.execute("SELECT trigger_text, evidence_text, trigger_start_offset, trigger_end_offset, status FROM l5_event_candidate ORDER BY trigger_start_offset").fetchall()
                self.assertTrue(all(row[4] == "candidate" for row in rows))
                for trigger_text, evidence_text, start, end, _status in rows:
                    chapter_row = conn.execute("SELECT content_full_text FROM v_current_chapters WHERE content_full_text LIKE ?", (f"%{evidence_text}%",)).fetchone()
                    self.assertIsNotNone(chapter_row)
                    self.assertEqual(chapter_row[0][start:end], trigger_text)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM l5_event_candidate WHERE chapter_num = 3").fetchone()[0], 0)
                self.assertGreater(conn.execute("SELECT COUNT(*) FROM l5_event_argument_candidate WHERE entity_layer='l3_character'").fetchone()[0], 0)
            finally:
                conn.close()

    def test_duplicate_prevention_rebuild_idempotency_and_no_l1_l2_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, {1: ["Chen enter room, then enter kitchen."], 2: ["No trigger here."]})
            conn = sqlite3.connect(db_path)
            try:
                before_counts = (
                    conn.execute("SELECT COUNT(*) FROM current_chapters_fixture").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM l2_current_sentences_fixture").fetchone()[0],
                )
            finally:
                conn.close()

            run_l5_event_candidate_extractor(project_dir, sample_chapters="1,2", rebuild=True)
            first_json = (project_dir / "outputs" / "l5_event_candidates_sample.json").read_text(encoding="utf-8")
            run_l5_event_candidate_extractor(project_dir, sample_chapters="1,2", rebuild=True)
            second_json = (project_dir / "outputs" / "l5_event_candidates_sample.json").read_text(encoding="utf-8")

            conn = sqlite3.connect(db_path)
            try:
                after_counts = (
                    conn.execute("SELECT COUNT(*) FROM current_chapters_fixture").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM l2_current_sentences_fixture").fetchone()[0],
                )
                duplicate_events = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT COUNT(*) AS n
                        FROM l5_event_candidate
                        GROUP BY chapter_id, version_id, sentence_id, trigger_start_offset, trigger_end_offset, trigger_text, event_type
                        HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(before_counts, after_counts)
            self.assertEqual(duplicate_events, 0)
            self.assertEqual(first_json, second_json)

    def test_missing_scene_block_source_is_warning_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, {1: ["Chen injured."]})

            run_l5_event_candidate_extractor(project_dir, sample_chapters="1", rebuild=True)
            run_l5_event_candidate_reporter(project_dir)
            verification = run_l5_event_candidate_verification(project_dir, sample_chapters="1")
            payload = json.loads((project_dir / "outputs" / "l5_event_candidates_sample.json").read_text(encoding="utf-8"))

            self.assertIn("l3_character_appearance", payload["missing_optional_sources"])
            self.assertIn("scene_block_source", payload["missing_optional_sources"])
            self.assertFalse(payload["detected_scene_block_source"])
            self.assertEqual(payload["scene_block_source_status"], "missing_optional")
            self.assertEqual(payload["scene_block_linked_event_count"], 0)
            self.assertTrue(verification.ok)
            self.assertEqual(verification.final_message, PASS_MESSAGE)

    def test_enum_checks_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, {1: ["Chen injured."]})

            run_l5_event_candidate_extractor(project_dir, sample_chapters="1", rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                event_id = conn.execute("SELECT event_candidate_id FROM l5_event_candidate LIMIT 1").fetchone()[0]
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        """
                        INSERT INTO l5_event_argument_candidate (
                            argument_id, event_candidate_id, argument_role, entity_layer, entity_id,
                            entity_text, entity_status, distance_scope, status, source_note, created_at
                        )
                        VALUES ('bad', ?, 'bad_role', 'raw_text', NULL, 'x', 'candidate', 'same_sentence', 'candidate', 'test', 'now')
                        """,
                        (event_id,),
                    )
            finally:
                conn.close()

    def test_l3_scene_blocks_are_used_as_compatible_source_without_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, {1: ["Chen enter room."]}, scene_source="l3")
            conn = sqlite3.connect(db_path)
            try:
                before_count = conn.execute("SELECT COUNT(*) FROM l3_scene_blocks").fetchone()[0]
            finally:
                conn.close()

            run_l5_event_candidate_extractor(project_dir, sample_chapters="1", rebuild=True)
            run_l5_event_candidate_reporter(project_dir)
            verification = run_l5_event_candidate_verification(project_dir, sample_chapters="1")
            payload = json.loads((project_dir / "outputs" / "l5_event_candidates_sample.json").read_text(encoding="utf-8"))

            conn = sqlite3.connect(db_path)
            try:
                after_count = conn.execute("SELECT COUNT(*) FROM l3_scene_blocks").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(payload["scene_block_table_name"], "l3_scene_blocks")
            self.assertEqual(payload["scene_block_source_status"], "compatible_fallback")
            self.assertGreater(payload["scene_block_linked_event_count"], 0)
            self.assertNotIn("scene_block_source", payload["missing_optional_sources"])
            self.assertTrue(verification.ok)
            self.assertEqual(verification.checks["invalid_scene_block_reference"], 0)
            self.assertEqual(verification.checks["source_tables_modified_by_l5"], 0)
            self.assertEqual(before_count, after_count)

    def test_l4_scene_blocks_are_preferred_when_both_sources_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, {1: ["Chen enter room."]}, scene_source="both")

            run_l5_event_candidate_extractor(project_dir, sample_chapters="1", rebuild=True)
            payload = json.loads((project_dir / "outputs" / "l5_event_candidates_sample.json").read_text(encoding="utf-8"))

            self.assertTrue(payload["detected_scene_block_source"])
            self.assertEqual(payload["scene_block_table_name"], "l4_scene_blocks")
            self.assertEqual(payload["scene_block_source_status"], "compatible_preferred")
            self.assertGreater(payload["scene_block_linked_event_count"], 0)

    def test_verifier_fail_message_when_tables_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, {1: ["Chen enter room."]})

            result = run_l5_event_candidate_verification(project_dir, sample_chapters="1")

            self.assertFalse(result.ok)
            self.assertEqual(result.final_message, FAIL_MESSAGE)

    def test_verifier_cli_prints_exact_pass_and_fail_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, {1: ["Chen enter room."]})
            fail = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "l5_verify_event_candidate_index.py"), "--project-dir", str(project_dir), "--sample-chapters", "1"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(fail.stdout.strip(), FAIL_MESSAGE)

            run_l5_event_candidate_extractor(project_dir, sample_chapters="1", rebuild=True)
            ok = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "l5_verify_event_candidate_index.py"), "--project-dir", str(project_dir), "--sample-chapters", "1"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(ok.stdout.strip(), PASS_MESSAGE)


if __name__ == "__main__":
    unittest.main()
