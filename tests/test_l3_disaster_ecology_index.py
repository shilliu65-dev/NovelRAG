import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_apply_disaster_ecology_review import run_l3_apply_disaster_ecology_review
from scripts.l3_disaster_ecology_candidate_extractor import (
    load_disaster_seed,
    run_l3_disaster_ecology_candidate_extractor,
)
from scripts.l3_verify_disaster_ecology_index import run_l3_disaster_ecology_verification


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def seed_project(project_dir: Path, rows: list[tuple[int, str]]) -> Path:
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
        global_sentence_index = 0
        for chapter_num, text in rows:
            chapter_id = f"ch_{chapter_num:04d}"
            version_id = f"ver_{chapter_num:04d}"
            conn.execute(
                """
                INSERT INTO current_chapters_fixture VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (chapter_id, chapter_num, f"Chapter {chapter_num}", version_id, text, sha256_text(text), len(text)),
            )
            conn.execute(
                """
                INSERT INTO l2_current_paragraphs_fixture VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'body', ?, ?)
                """,
                (
                    f"p_{chapter_num}",
                    chapter_id,
                    chapter_num,
                    version_id,
                    1,
                    0,
                    len(text),
                    len(text),
                    sha256_text(text),
                    text,
                    f"Chapter {chapter_num}",
                ),
            )
            for sentence_index, sentence in enumerate([part for part in text.split("。") if part], start=1):
                sentence_text = sentence + "。"
                start = text.index(sentence_text)
                end = start + len(sentence_text)
                conn.execute(
                    """
                    INSERT INTO l2_current_sentences_fixture VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"s_{chapter_num}_{sentence_index}",
                        f"p_{chapter_num}",
                        chapter_id,
                        chapter_num,
                        version_id,
                        1,
                        sentence_index,
                        global_sentence_index,
                        start,
                        end,
                        end - start,
                        sha256_text(sentence_text),
                        sentence_text,
                        f"Chapter {chapter_num}",
                    ),
                )
                global_sentence_index += 1
        conn.commit()
    finally:
        conn.close()
    return db_path


def sqlite_table_count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def sqlite_objects(db_path: Path) -> set[tuple[str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        return set(conn.execute("SELECT type, name FROM sqlite_master").fetchall())
    finally:
        conn.close()


def write_review(project_dir: Path, candidates: list[dict]) -> Path:
    path = project_dir / "outputs" / "review.json"
    payload = {
        "meta": {"layer": "L3.6", "review_type": "disaster_ecology_review"},
        "review_candidates": candidates,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def first_candidate(project_dir: Path, candidate_type: str) -> dict:
    result = run_l3_disaster_ecology_candidate_extractor(
        project_dir=project_dir,
        sample_chapters="1,2,1697",
        top_k_per_type=30,
    )
    for candidate in result["candidates"]:
        if candidate["candidate_type"] == candidate_type:
            return candidate
    raise AssertionError(f"missing candidate_type={candidate_type}")


class L3DisasterEcologyIndexTests(unittest.TestCase):
    def test_seed_schema_valid(self) -> None:
        seed = load_disaster_seed(PROJECT_ROOT / "config" / "l3_disaster_ecology_seed.json")
        for key in (
            "seed_version",
            "strict_no_llm",
            "strict_no_embedding",
            "strict_read_only_sources",
            "zone_defs",
            "disaster_defs",
            "habitat_defs",
            "zone_patterns",
            "disaster_patterns",
            "habitat_patterns",
            "invasion_patterns",
            "affected_location_triggers",
            "global_negative_patterns",
        ):
            self.assertIn(key, seed)
        self.assertTrue(seed["strict_no_llm"])
        self.assertTrue(seed["strict_no_embedding"])

    def test_seed_forbid_single_gray_character(self) -> None:
        seed = load_disaster_seed(PROJECT_ROOT / "config" / "l3_disaster_ecology_seed.json")
        pattern_lists = []
        for key in ("zone_patterns", "disaster_patterns", "habitat_patterns", "invasion_patterns"):
            pattern_lists.extend(seed[key].values() if isinstance(seed[key], dict) else seed[key])
        for zone in seed["zone_defs"]:
            pattern_lists.extend(zone.get("positive_patterns", []))
        self.assertNotIn("灰", [item for group in pattern_lists for item in (group if isinstance(group, list) else [group])])

    def test_seven_disasters_complete(self) -> None:
        seed = load_disaster_seed(PROJECT_ROOT / "config" / "l3_disaster_ecology_seed.json")
        self.assertEqual(
            {item["disaster_id"] for item in seed["disaster_defs"]},
            {
                "disaster_chao",
                "disaster_ji",
                "disaster_xi",
                "disaster_zhuo",
                "disaster_wang",
                "disaster_si",
                "disaster_ji_mie",
            },
        )

    def test_chao_special_case(self) -> None:
        seed = load_disaster_seed(PROJECT_ROOT / "config" / "l3_disaster_ecology_seed.json")
        chao = next(item for item in seed["disaster_defs"] if item["disaster_id"] == "disaster_chao")
        self.assertTrue(chao["is_special_case"])

    def test_ji_and_ji_mie_not_confused(self) -> None:
        seed = load_disaster_seed(PROJECT_ROOT / "config" / "l3_disaster_ecology_seed.json")
        by_id = {item["disaster_id"]: item["disaster_name"] for item in seed["disaster_defs"]}
        self.assertEqual(by_id["disaster_ji"], "忌灾")
        self.assertEqual(by_id["disaster_ji_mie"], "寂灾")

    def test_placeholder_habitats_complete(self) -> None:
        seed = load_disaster_seed(PROJECT_ROOT / "config" / "l3_disaster_ecology_seed.json")
        self.assertEqual(
            {item["habitat_id"] for item in seed["habitat_defs"]},
            {
                "habitat_chao_unknown",
                "habitat_ji_unknown",
                "habitat_xi_unknown",
                "habitat_zhuo_unknown",
                "habitat_wang_unknown",
                "habitat_si_unknown",
                "habitat_ji_mie_unknown",
            },
        )

    def test_candidate_extractor_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, [(1697, "灰界的灰潮正在污染天枢界域。嘲灾在灰界深处出现。")])
            before = sqlite_objects(db_path)
            result = run_l3_disaster_ecology_candidate_extractor(project_dir=project_dir, sample_chapters="1697")
            after = sqlite_objects(db_path)
            self.assertEqual(before, after)
            self.assertFalse(result["meta"]["writes_sqlite"])

    def test_candidate_output_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, [(1697, "灰界的灰潮正在污染天枢界域。嘲灾在灰界深处出现。")])
            result = run_l3_disaster_ecology_candidate_extractor(project_dir=project_dir, sample_chapters="1697")
            self.assertEqual(result["meta"]["layer"], "L3.6")
            self.assertTrue(result["candidates"])
            for key in ("candidate_id", "candidate_type", "chapter_id", "chapter_num", "evidence_text", "status", "needs_human_review"):
                self.assertIn(key, result["candidates"][0])

    def test_review_template_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, [(1697, "灰界的灰潮正在污染天枢界域。嘲灾在灰界深处出现。")])
            run_l3_disaster_ecology_candidate_extractor(project_dir=project_dir, sample_chapters="1697", emit_review_template=True)
            template = json.loads((project_dir / "outputs" / "l3_disaster_ecology_review_template_sample.json").read_text(encoding="utf-8"))
            self.assertEqual(template["meta"]["review_type"], "disaster_ecology_review")
            self.assertIn("human_status", template["review_candidates"][0])
            self.assertIn("human_note", template["review_candidates"][0])

    def test_apply_accepts_only_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, [(1697, "灰界的灰潮正在污染天枢界域。")])
            candidate = first_candidate(project_dir, "zone_appearance_candidate")
            candidate["human_status"] = "accepted"
            review_path = write_review(project_dir, [candidate])
            report = run_l3_apply_disaster_ecology_review(project_dir=project_dir, review_file=review_path, rebuild=True)
            self.assertEqual(report["accepted_count"], 1)
            self.assertEqual(sqlite_table_count(db_path, "l3_disaster_zone_appearance"), 1)

    def test_apply_skips_rejected_needs_more_null(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, [(1697, "灰界的灰潮正在污染天枢界域。")])
            base = first_candidate(project_dir, "zone_appearance_candidate")
            candidates = []
            for status in ("rejected", "needs_more", None):
                item = dict(base)
                item["candidate_id"] = f"{base['candidate_id']}_{status}"
                item["human_status"] = status
                candidates.append(item)
            review_path = write_review(project_dir, candidates)
            report = run_l3_apply_disaster_ecology_review(project_dir=project_dir, review_file=review_path, rebuild=True)
            self.assertEqual(report["accepted_count"], 0)
            self.assertEqual(sqlite_table_count(db_path, "l3_disaster_zone_appearance"), 0)

    def test_apply_rejects_invalid_human_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, [(1697, "灰界的灰潮正在污染天枢界域。")])
            candidate = first_candidate(project_dir, "zone_appearance_candidate")
            candidate["human_status"] = "bad"
            review_path = write_review(project_dir, [candidate])
            with self.assertRaises(ValueError):
                run_l3_apply_disaster_ecology_review(project_dir=project_dir, review_file=review_path, rebuild=True)

    def test_apply_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, [(1697, "灰界的灰潮正在污染天枢界域。")])
            candidate = first_candidate(project_dir, "zone_appearance_candidate")
            candidate["human_status"] = "accepted"
            review_path = write_review(project_dir, [candidate])
            run_l3_apply_disaster_ecology_review(project_dir=project_dir, review_file=review_path, rebuild=True)
            first = sqlite_table_count(db_path, "l3_disaster_zone_appearance")
            run_l3_apply_disaster_ecology_review(project_dir=project_dir, review_file=review_path)
            second = sqlite_table_count(db_path, "l3_disaster_zone_appearance")
            self.assertEqual(first, second)

    def test_verify_detects_invalid_zone_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, [(1697, "灰界的灰潮正在污染天枢界域。")])
            candidate = first_candidate(project_dir, "zone_appearance_candidate")
            candidate["human_status"] = "accepted"
            run_l3_apply_disaster_ecology_review(project_dir=project_dir, review_file=write_review(project_dir, [candidate]), rebuild=True)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            conn.execute("UPDATE l3_disaster_zone_def SET zone_type='ordinary_city'")
            conn.commit()
            conn.close()
            result = run_l3_disaster_ecology_verification(project_dir=project_dir)
            self.assertGreater(result["invalid_zone_type_count"], 0)

    def test_verify_detects_invalid_habitat_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, [(1697, "嘲灾的巢穴仍在灰界深处。")])
            run_l3_apply_disaster_ecology_review(project_dir=project_dir, review_file=write_review(project_dir, []), rebuild=True)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            conn.execute("UPDATE l3_disaster_habitat_def SET habitat_type='city'")
            conn.commit()
            conn.close()
            result = run_l3_disaster_ecology_verification(project_dir=project_dir)
            self.assertGreater(result["invalid_habitat_type_count"], 0)

    def test_verify_detects_invalid_invasion_type(self) -> None:
        self._verify_detects_invalid_invasion_column("invasion_type", "bad_type", "invalid_invasion_type_count")

    def test_verify_detects_invalid_invasion_state(self) -> None:
        self._verify_detects_invalid_invasion_column("invasion_state", "bad_state", "invalid_invasion_state_count")

    def _verify_detects_invalid_invasion_column(self, column: str, value: str, count_key: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, [(1697, "灰潮正在污染天枢界域。")])
            candidate = first_candidate(project_dir, "disaster_invasion_candidate")
            candidate["human_status"] = "accepted"
            run_l3_apply_disaster_ecology_review(project_dir=project_dir, review_file=write_review(project_dir, [candidate]), rebuild=True)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            conn.execute(f"UPDATE l3_disaster_invasion_event SET {column}=?", (value,))
            conn.commit()
            conn.close()
            result = run_l3_disaster_ecology_verification(project_dir=project_dir)
            self.assertGreater(result[count_key], 0)

    def test_verify_detects_backcut_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, [(1697, "灰界的灰潮正在污染天枢界域。")])
            candidate = first_candidate(project_dir, "zone_appearance_candidate")
            candidate["human_status"] = "accepted"
            run_l3_apply_disaster_ecology_review(project_dir=project_dir, review_file=write_review(project_dir, [candidate]), rebuild=True)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            conn.execute("UPDATE l3_disaster_zone_appearance SET evidence_text='不匹配'")
            conn.commit()
            conn.close()
            result = run_l3_disaster_ecology_verification(project_dir=project_dir)
            self.assertGreater(result["backcut_error_count"], 0)

    def test_l36_excludes_ordinary_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, [(1, "天枢界域和极光城都很安静。普通建筑没有异常。")])
            result = run_l3_disaster_ecology_candidate_extractor(project_dir=project_dir, sample_chapters="1")
            self.assertFalse(result["candidates"])

    def test_compile_scripts(self) -> None:
        import py_compile

        for path in (
            PROJECT_ROOT / "scripts" / "l3_disaster_ecology_candidate_extractor.py",
            PROJECT_ROOT / "scripts" / "l3_apply_disaster_ecology_review.py",
            PROJECT_ROOT / "scripts" / "l3_verify_disaster_ecology_index.py",
        ):
            py_compile.compile(str(path), doraise=True)


if __name__ == "__main__":
    unittest.main()
