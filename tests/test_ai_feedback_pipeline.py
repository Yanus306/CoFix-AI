import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ai_feedback_pipeline as pipeline


def recent_issue(**overrides):
    data = {
        "dataset": "edge_case",
        "title": "빈 입력 오류",
        "learning_directions": ["빈 입력 검사"],
        "code": "total / len(values)",
        "guide": "문제·해결·핵심 원리",
    }
    data.update(overrides)
    return data


class ReadAnalysisRequestTests(unittest.TestCase):
    def make_args(self, *, file=None):
        return SimpleNamespace(file=file, encoding="utf-8")

    def test_reads_current_code_and_multiple_recent_issues_from_be_json(self):
        recent_issues = [
            recent_issue(),
            recent_issue(
                dataset="loop_control",
                title="반복 범위 오류",
                learning_directions=["반복문 범위"],
                code="range(len(values) + 1)",
            ),
        ]
        request = {
            "code": "print('hello')",
            "learning_context": {"recent_issues": recent_issues},
        }

        with patch("sys.stdin", io.StringIO(json.dumps(request))):
            code, normalized_issues, source = pipeline.read_analysis_request(self.make_args())

        self.assertEqual(code, "print('hello')")
        self.assertEqual(normalized_issues, recent_issues)
        self.assertEqual(source, "stdin")

    def test_accepts_empty_recent_issue_array(self):
        request = {
            "code": "print('hello')",
            "learning_context": {"recent_issues": []},
        }

        with patch("sys.stdin", io.StringIO(json.dumps(request))):
            _code, recent_issues, _source = pipeline.read_analysis_request(self.make_args())

        self.assertEqual(recent_issues, [])

    def test_rejects_plain_code_and_missing_learning_context(self):
        invalid_inputs = [
            "print('plain code')",
            json.dumps({"code": "print('old')"}),
        ]

        for raw_text in invalid_inputs:
            with self.subTest(raw_text=raw_text):
                with patch("sys.stdin", io.StringIO(raw_text)):
                    with self.assertRaises(RuntimeError):
                        pipeline.read_analysis_request(self.make_args())

    def test_rejects_invalid_recent_issue(self):
        request = {
            "code": "print('hello')",
            "learning_context": {
                "recent_issues": [recent_issue(learning_directions=[])]
            },
        }

        with patch("sys.stdin", io.StringIO(json.dumps(request))):
            with self.assertRaisesRegex(RuntimeError, "learning_directions"):
                pipeline.read_analysis_request(self.make_args())

    def test_rejects_unknown_fields_at_every_request_level(self):
        base = {
            "code": "print('hello')",
            "learning_context": {"recent_issues": [recent_issue()]},
        }
        invalid_requests = []
        root_extra = json.loads(json.dumps(base))
        root_extra["unexpected"] = True
        invalid_requests.append(root_extra)
        context_extra = json.loads(json.dumps(base))
        context_extra["learning_context"]["unexpected"] = True
        invalid_requests.append(context_extra)
        issue_extra = json.loads(json.dumps(base))
        issue_extra["learning_context"]["recent_issues"][0]["unexpected"] = True
        invalid_requests.append(issue_extra)

        for request in invalid_requests:
            with self.subTest(request=request):
                with patch("sys.stdin", io.StringIO(json.dumps(request))):
                    with self.assertRaisesRegex(RuntimeError, "fields"):
                        pipeline.read_analysis_request(self.make_args())


class NormalizationTests(unittest.TestCase):
    def test_prompt_requires_complete_semantic_code_context(self):
        template = "code={{code}}\nhistory={{recent_issues_json}}"

        prompt = pipeline.build_prompt(
            template,
            "function Form() {}",
            [],
            [recent_issue()],
        )

        self.assertIn("완전한 코드 단위", prompt)
        self.assertIn("함수", prompt)
        self.assertIn("컴포넌트", prompt)
        self.assertIn("edge_case", prompt)
        self.assertNotIn("{{recent_issues_json}}", prompt)

    def test_prompt_has_no_local_learning_data_placeholders(self):
        prompt_template = pipeline.read_text(pipeline.DEFAULT_PROMPT_FILE)

        for placeholder in (
            "{{user_error_stats_json}}",
            "{{weakness_texts_json}}",
            "{{top_error_categories_json}}",
            "{{recent_weakness_texts_json}}",
            "{{duplicate_weakness_texts_json}}",
        ):
            self.assertNotIn(placeholder, prompt_template)

    def test_prompt_replacement_does_not_expand_placeholder_text_inside_history(self):
        template = "code={{code}}\nhistory={{recent_issues_json}}"

        prompt = pipeline.build_prompt(
            template,
            "print('current')",
            [],
            [recent_issue(title="{{code}}")],
        )

        self.assertIn('"title": "{{code}}"', prompt)

    def test_prompt_requires_unnumbered_code_in_output(self):
        prompt_template = pipeline.read_text(pipeline.DEFAULT_PROMPT_FILE)

        self.assertIn("줄 번호 접두사", prompt_template)

    def test_analysis_runtime_and_prompt_have_no_identity_field(self):
        forbidden = "user" + "_id"
        source = Path("ai_feedback_pipeline.py").read_text(encoding="utf-8")
        prompt = Path(pipeline.DEFAULT_PROMPT_FILE).read_text(encoding="utf-8")

        self.assertNotIn(forbidden, source)
        self.assertNotIn(forbidden, prompt)

    def test_api_output_contains_only_issues(self):
        categories = [
            {"id": 0, "key": "syntax_structure", "name": "문법구조", "condition": "문법 오류"}
        ]
        raw_feedback = {
            "issues": [
                {
                    "code": "print(",
                    "label": "임의 이름",
                    "title": "괄호가 닫히지 않음",
                    "description": "호출 괄호가 닫히지 않았습니다.",
                    "learning_directions": ["문법 구조"],
                    "dataset": "syntax_structure",
                    "guide": "안내",
                }
            ]
        }

        result = pipeline.normalize_feedback(raw_feedback, categories)
        output = pipeline.build_api_response(result)

        self.assertEqual(list(output), ["issues"])
        self.assertEqual(
            list(output["issues"][0]),
            ["code", "label", "title", "description", "learning_directions", "dataset", "guide"],
        )
        self.assertEqual(output["issues"][0]["label"], "문법구조")

    def test_rejects_malformed_gemini_issue_instead_of_exposing_it(self):
        categories = [
            {"id": 0, "key": "edge_case", "name": "경계값", "condition": "경계값 오류"}
        ]
        malformed = {
            "issues": [
                {
                    "code": "",
                    "title": "빈 입력 오류",
                    "description": "빈 입력을 처리하지 않습니다.",
                    "learning_directions": ["#"],
                    "dataset": "edge_case",
                    "guide": "안내",
                }
            ]
        }

        with self.assertRaisesRegex(RuntimeError, "Gemini issue"):
            pipeline.normalize_feedback(malformed, categories)

    def test_preserves_gemini_issue_order(self):
        categories = [
            {"id": 0, "key": "edge_case", "name": "경계값", "condition": "경계값 오류"},
            {"id": 1, "key": "loop_control", "name": "반복제어", "condition": "반복 오류"},
        ]
        raw_feedback = {
            "issues": [
                {
                    "code": "for value in values:",
                    "label": "반복제어",
                    "title": "반복 오류",
                    "description": "반복 오류 설명",
                    "learning_directions": ["반복문"],
                    "dataset": "loop_control",
                    "guide": "반복 안내",
                },
                {
                    "code": "values[0]",
                    "label": "경계값",
                    "title": "경계값 오류",
                    "description": "경계값 오류 설명",
                    "learning_directions": ["경계값"],
                    "dataset": "edge_case",
                    "guide": "경계 안내",
                },
            ]
        }

        output = pipeline.normalize_feedback(raw_feedback, categories)

        self.assertEqual(
            [item["dataset"] for item in output["issues"]],
            ["loop_control", "edge_case"],
        )


class ProtoContractTests(unittest.TestCase):
    def test_proto_includes_be_learning_context_and_issues_response(self):
        input_proto = Path("proto/code_analysis_input.proto").read_text(encoding="utf-8")
        output_proto = Path("proto/code_analysis_output.proto").read_text(encoding="utf-8")

        self.assertIn("message RecentIssue", input_proto)
        self.assertIn("message LearningContext", input_proto)
        self.assertIn("LearningContext learning_context", input_proto)
        self.assertRegex(output_proto, r"message AnalyzeCodeResponse\s*\{[^}]*CodeIssue issues")

    def test_manual_analysis_demo_is_absent(self):
        self.assertFalse(Path("tests/manual_flow_demo.py").exists())


if __name__ == "__main__":
    unittest.main()
