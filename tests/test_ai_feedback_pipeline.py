import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ai_feedback_pipeline as pipeline


class ReadAnalysisRequestTests(unittest.TestCase):
    def make_args(self, *, file=None, user_id=None):
        return SimpleNamespace(file=file, user_id=user_id, encoding="utf-8")

    def test_reads_user_id_and_code_from_be_json_stdin(self):
        request = {"user_id": "user_002", "code": "print('hello')"}
        args = self.make_args()

        with patch("sys.stdin", io.StringIO(json.dumps(request))):
            user_id, code, source = pipeline.read_analysis_request(args, "user_001")

        self.assertEqual(user_id, "user_002")
        self.assertEqual(code, "print('hello')")
        self.assertEqual(source, "stdin")

    def test_keeps_existing_plain_code_input(self):
        args = self.make_args(user_id="user_003")

        with patch("sys.stdin", io.StringIO("print('plain code')")):
            user_id, code, source = pipeline.read_analysis_request(args, "user_001")

        self.assertEqual(user_id, "user_003")
        self.assertEqual(code, "print('plain code')")
        self.assertEqual(source, "stdin")

    def test_keeps_json_source_code_that_is_not_a_be_request(self):
        json_code = '{"code": "ordinary JSON source"}'
        args = self.make_args(user_id="user_003")

        with patch("sys.stdin", io.StringIO(json_code)):
            user_id, code, source = pipeline.read_analysis_request(args, "user_001")

        self.assertEqual(user_id, "user_003")
        self.assertEqual(code, json_code)
        self.assertEqual(source, "stdin")

    def test_rejects_conflicting_json_and_cli_user_ids(self):
        request = {"user_id": "user_002", "code": "print('hello')"}
        args = self.make_args(user_id="user_003")

        with patch("sys.stdin", io.StringIO(json.dumps(request))):
            with self.assertRaisesRegex(RuntimeError, "user_id"):
                pipeline.read_analysis_request(args, "user_001")

    def test_rejects_empty_be_json_fields(self):
        args = self.make_args()

        for request in ({"user_id": "", "code": "x"}, {"user_id": "u", "code": ""}):
            with self.subTest(request=request):
                with patch("sys.stdin", io.StringIO(json.dumps(request))):
                    with self.assertRaises(RuntimeError):
                        pipeline.read_analysis_request(args, "user_001")


class NormalizationTests(unittest.TestCase):
    def test_api_output_contains_only_user_id_and_issues(self):
        categories = [
            {"id": 0, "key": "syntax_structure", "name": "문법구조", "condition": "문법 오류"}
        ]
        raw_feedback = {
            "issues": [
                {
                    "code": "print(",
                    "label": "문법구조",
                    "title": "괄호가 닫히지 않음",
                    "description": "호출 괄호가 닫히지 않았습니다.",
                    "learning_directions": ["문법 구조"],
                    "dataset": "syntax_structure",
                    "guide": "안내",
                }
            ],
            "db_update_json": {},
            "recommended_problem": {},
        }

        result = pipeline.normalize_feedback(raw_feedback, categories, "user_002")
        output = pipeline.build_api_response(result)

        self.assertEqual(
            list(output),
            ["user_id", "issues"],
        )
        self.assertEqual(
            list(output["issues"][0]),
            ["code", "label", "title", "description", "learning_directions", "dataset", "guide"],
        )
        self.assertEqual(output["user_id"], "user_002")

    def test_delta_is_always_one_step(self):
        self.assertEqual(pipeline.normalize_delta("bad", 99), 1)
        self.assertEqual(pipeline.normalize_delta("good", 99), -1)

    def test_ui_rendering_is_included_in_rollup(self):
        profile = {"ui_dom_rendering": 2}

        pipeline.recompute_rollups(profile)

        self.assertEqual(profile["syntax_fail_count"], 2)


class JsonWritingTests(unittest.TestCase):
    def test_write_json_replaces_file_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "profile.json"
            target.write_text('{"old": true}', encoding="utf-8")

            pipeline.write_json(target, {"new": True})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"new": True})
            self.assertEqual(list(target.parent.glob("profile.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
