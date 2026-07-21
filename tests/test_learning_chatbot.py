import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import learning_chatbot as chatbot


CATEGORIES = json.loads(Path("data/categories.json").read_text(encoding="utf-8"))
CATEGORY_KEYS = [item["key"] for item in CATEGORIES]


def all_category_counts(**positive_counts):
    return [
        {"dataset": key, "count": positive_counts.get(key, 0)}
        for key in CATEGORY_KEYS
    ]


def recent_issue(dataset="loop_control", **overrides):
    value = {
        "dataset": dataset,
        "title": "반복 범위가 리스트 길이를 초과함",
        "learning_directions": ["반복문 범위", "오프바이원 오류"],
        "code": "for i in range(len(values) + 1):",
        "guide": "문제: 유효 인덱스를 초과함\n해결: 올바른 반복 범위 사용",
    }
    value.update(overrides)
    return value


def conversation_summary(index=1, **overrides):
    value = {
        "turn_id": f"turn_{index:03d}",
        "summary": "사용자는 반복문 오류의 원인을 질문했다.",
    }
    value.update(overrides)
    return value


def valid_request(**overrides):
    value = {
        "message": "반복문 오류를 고쳐줘",
        "category_counts": all_category_counts(loop_control=7, variable_type=3),
        "recent_issues": [recent_issue()],
        "recent_conversation_summaries": [conversation_summary()],
    }
    value.update(overrides)
    return value


def summary_payload(summary="사용자는 반복 범위 오류의 수정 방향을 질문했다."):
    return {"conversation_summary": summary}


class FakeModels:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return SimpleNamespace(text=output)


class FakeClient:
    def __init__(self, outputs):
        self.models = FakeModels(outputs)


class RequestValidationTests(unittest.TestCase):
    def test_compact_payload_contains_only_learning_and_question_data(self):
        result = chatbot.validate_and_compact_request(valid_request(), CATEGORIES)

        forbidden = "user" + "_id"
        self.assertNotIn(forbidden, result)
        prompt = chatbot.build_answer_prompt("{{chat_request_json}}", result)
        self.assertNotIn(forbidden, prompt)

    def test_compacts_all_categories_to_positive_counts_sorted_descending(self):
        result = chatbot.validate_and_compact_request(valid_request(), CATEGORIES)

        self.assertEqual(
            [item["dataset"] for item in result["category_counts"]],
            ["loop_control", "variable_type"],
        )
        self.assertEqual([item["count"] for item in result["category_counts"]], [7, 3])
        self.assertEqual(result["category_counts"][0]["name"], "반복제어")
        self.assertTrue(result["category_counts"][0]["condition"])

    def test_accepts_zero_to_five_recent_issues(self):
        for count in (0, 1, 5):
            with self.subTest(count=count):
                request = valid_request(recent_issues=[recent_issue() for _ in range(count)])
                result = chatbot.validate_and_compact_request(request, CATEGORIES)
                self.assertEqual(len(result["recent_issues"]), count)

    def test_accepts_zero_to_five_conversation_summaries_in_given_order(self):
        for count in (0, 1, 5):
            with self.subTest(count=count):
                summaries = [conversation_summary(index) for index in range(count)]
                request = valid_request(recent_conversation_summaries=summaries)
                result = chatbot.validate_and_compact_request(request, CATEGORIES)
                self.assertEqual(result["recent_conversation_summaries"], summaries)

    def test_rejects_missing_duplicate_and_unknown_category_keys(self):
        missing = valid_request(category_counts=all_category_counts()[:-1])
        duplicate_counts = all_category_counts()
        duplicate_counts[-1] = dict(duplicate_counts[0])
        duplicate = valid_request(category_counts=duplicate_counts)
        unknown_counts = all_category_counts()
        unknown_counts[-1] = {"dataset": "unknown_category", "count": 0}
        unknown = valid_request(category_counts=unknown_counts)

        for request in (missing, duplicate, unknown):
            with self.subTest(request=request):
                with self.assertRaisesRegex(chatbot.RequestValidationError, "category"):
                    chatbot.validate_and_compact_request(request, CATEGORIES)

    def test_rejects_negative_or_boolean_category_count(self):
        for invalid_count in (-1, True):
            counts = all_category_counts()
            counts[0]["count"] = invalid_count
            with self.subTest(invalid_count=invalid_count):
                with self.assertRaisesRegex(chatbot.RequestValidationError, "count"):
                    chatbot.validate_and_compact_request(
                        valid_request(category_counts=counts), CATEGORIES
                    )

    def test_rejects_more_than_five_issues_or_summaries(self):
        too_many_issues = valid_request(recent_issues=[recent_issue() for _ in range(6)])
        too_many_summaries = valid_request(
            recent_conversation_summaries=[conversation_summary(i) for i in range(6)]
        )

        with self.assertRaisesRegex(chatbot.RequestValidationError, "recent_issues"):
            chatbot.validate_and_compact_request(too_many_issues, CATEGORIES)
        with self.assertRaisesRegex(
            chatbot.RequestValidationError, "recent_conversation_summaries"
        ):
            chatbot.validate_and_compact_request(too_many_summaries, CATEGORIES)

    def test_rejects_summary_over_three_hundred_characters(self):
        request = valid_request(
            recent_conversation_summaries=[conversation_summary(summary="가" * 301)]
        )

        with self.assertRaisesRegex(chatbot.RequestValidationError, "300"):
            chatbot.validate_and_compact_request(request, CATEGORIES)

    def test_rejects_oversized_text_fields(self):
        cases = [
            valid_request(message="m" * (chatbot.MAX_MESSAGE_LENGTH + 1)),
            valid_request(
                recent_issues=[
                    recent_issue(title="t" * (chatbot.MAX_ISSUE_TITLE_LENGTH + 1))
                ]
            ),
            valid_request(
                recent_issues=[
                    recent_issue(code="c" * (chatbot.MAX_ISSUE_CODE_LENGTH + 1))
                ]
            ),
            valid_request(
                recent_issues=[
                    recent_issue(guide="g" * (chatbot.MAX_ISSUE_GUIDE_LENGTH + 1))
                ]
            ),
            valid_request(
                recent_conversation_summaries=[
                    conversation_summary(
                        turn_id="t" * (chatbot.MAX_TURN_ID_LENGTH + 1)
                    )
                ]
            ),
        ]

        for request in cases:
            with self.subTest(request=list(request)):
                with self.assertRaisesRegex(chatbot.RequestValidationError, "at most"):
                    chatbot.validate_and_compact_request(request, CATEGORIES)

    def test_rejects_invalid_issue_dataset_and_empty_question(self):
        with self.assertRaisesRegex(chatbot.RequestValidationError, "dataset"):
            chatbot.validate_and_compact_request(
                valid_request(recent_issues=[recent_issue("not_registered")]), CATEGORIES
            )
        with self.assertRaisesRegex(chatbot.RequestValidationError, "message"):
            chatbot.validate_and_compact_request(valid_request(message="   "), CATEGORIES)

    def test_rejects_unknown_fields_in_request_and_nested_rows(self):
        root = valid_request(unexpected=True)
        category = valid_request()
        category["category_counts"][0]["unexpected"] = True
        issue = valid_request()
        issue["recent_issues"][0]["unexpected"] = True
        summary = valid_request()
        summary["recent_conversation_summaries"][0]["unexpected"] = True

        for request in (root, category, issue, summary):
            with self.subTest(request=request):
                with self.assertRaisesRegex(chatbot.RequestValidationError, "fields"):
                    chatbot.validate_and_compact_request(request, CATEGORIES)


class PromptAndModelTests(unittest.TestCase):
    def setUp(self):
        self.compact_request = chatbot.validate_and_compact_request(valid_request(), CATEGORIES)

    def test_answer_prompt_requires_direct_markdown_and_forbids_complete_solution(self):
        template = Path(chatbot.DEFAULT_ANSWER_PROMPT_FILE).read_text(encoding="utf-8")
        prompt = chatbot.build_answer_prompt(template, self.compact_request)

        self.assertIn("Markdown", prompt)
        self.assertIn("JSON", prompt)
        self.assertIn("완전히 수정된 전체 코드", prompt)
        self.assertIn("제공하지 않는다", prompt)
        self.assertIn("수정 방향", prompt)
        self.assertIn("짧은 부분 코드", prompt)
        self.assertIn("loop_control", prompt)
        self.assertIn('"count": 7', prompt)
        self.assertNotIn("{{chat_request_json}}", prompt)
        self.assertNotIn("answer_markdown", prompt)
        self.assertNotIn("is_coding_related", prompt)

    def test_answer_prompt_replacement_is_single_pass(self):
        request = dict(self.compact_request)
        request["message"] = "{{chat_request_json}}"
        prompt = chatbot.build_answer_prompt("request={{chat_request_json}}", request)
        self.assertIn('"message": "{{chat_request_json}}"', prompt)

    def test_summary_prompt_contains_request_and_direct_markdown_answer(self):
        template = "request={{chat_request_json}}\n\nanswer={{markdown_answer}}"
        markdown = "## 수정 방향\n\n반복 범위를 확인하세요."
        prompt = chatbot.build_summary_prompt(
            template,
            self.compact_request,
            markdown,
        )

        self.assertIn('"message"', prompt)
        self.assertIn(markdown, prompt)
        self.assertNotIn("{{chat_request_json}}", prompt)
        self.assertNotIn("{{markdown_answer}}", prompt)

    def test_normalizes_summary_string_and_null(self):
        self.assertEqual(
            chatbot.normalize_summary_response(summary_payload()),
            summary_payload()["conversation_summary"],
        )
        self.assertIsNone(
            chatbot.normalize_summary_response(summary_payload(None))
        )

    def test_rejects_wrong_summary_shape_type_length_and_extra_fields(self):
        invalid = [
            {},
            {"conversation_summary": 1},
            {"conversation_summary": "null"},
            {"conversation_summary": "가" * 301},
            {"conversation_summary": "요약", "extra": True},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(chatbot.ModelResponseError):
                    chatbot.normalize_summary_response(payload)

    def test_parser_rejects_prose_fences_and_non_object_json(self):
        bare = json.dumps(summary_payload(), ensure_ascii=False)
        self.assertEqual(chatbot.parse_model_json(bare), summary_payload())

        for raw in (f"결과: {bare}", f"```json\n{bare}\n```", "[]"):
            with self.subTest(raw=raw):
                with self.assertRaises(chatbot.ModelResponseError):
                    chatbot.parse_model_json(raw)

    def test_markdown_generation_returns_model_text_without_json_parsing(self):
        markdown = "## 수정 방향\n\n직접 범위를 수정해보세요."
        client = FakeClient([markdown])
        result = chatbot.generate_markdown_answer(
            self.compact_request,
            client,
            model="gemini-test",
            template="{{chat_request_json}}",
        )

        self.assertEqual(result, markdown)
        self.assertEqual(len(client.models.calls), 1)
        self.assertEqual(
            client.models.calls[0]["config"]["response_mime_type"], "text/plain"
        )
        self.assertEqual(client.models.calls[0]["config"]["max_output_tokens"], 4096)

    def test_markdown_generation_rejects_old_json_answer_shape(self):
        client = FakeClient(
            [json.dumps({"answer_markdown": "## 잘못된 이전 형식"}, ensure_ascii=False)]
        )
        with self.assertRaises(chatbot.ModelResponseError):
            chatbot.generate_markdown_answer(
                self.compact_request,
                client,
                model="gemini-test",
                template="{{chat_request_json}}",
            )

    def test_summary_generation_calls_gemini_separately_in_json_mode(self):
        payload = summary_payload()
        client = FakeClient([json.dumps(payload, ensure_ascii=False)])
        result = chatbot.generate_conversation_summary(
            self.compact_request,
            "## 수정 방향\n\n반복 범위를 확인하세요.",
            client,
            model="gemini-test",
            template="{{chat_request_json}}\n{{markdown_answer}}",
        )

        self.assertEqual(result, payload["conversation_summary"])
        self.assertEqual(len(client.models.calls), 1)
        self.assertEqual(
            client.models.calls[0]["config"]["response_mime_type"],
            "application/json",
        )

    def test_invalid_summary_json_fails_without_changing_markdown(self):
        client = FakeClient(["not json"])
        with self.assertRaises(chatbot.ModelResponseError):
            chatbot.generate_conversation_summary(
                self.compact_request,
                "## 이미 생성된 Markdown",
                client,
                model="gemini-test",
                template="{{chat_request_json}}\n{{markdown_answer}}",
            )
        self.assertEqual(len(client.models.calls), 1)

    def test_markdown_transport_error_is_not_retried(self):
        client = FakeClient([ConnectionError("offline")])
        with self.assertRaises(ConnectionError):
            chatbot.generate_markdown_answer(
                self.compact_request,
                client,
                model="gemini-test",
                template="{{chat_request_json}}",
            )
        self.assertEqual(len(client.models.calls), 1)


class ReplacementCleanupTests(unittest.TestCase):
    def test_default_resources_resolve_outside_project_working_directory(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as other_directory:
            try:
                os.chdir(other_directory)
                categories = chatbot.load_categories()
                answer_prompt = Path(chatbot.DEFAULT_ANSWER_PROMPT_FILE).read_text(encoding="utf-8")
                summary_prompt = Path(chatbot.DEFAULT_SUMMARY_PROMPT_FILE).read_text(encoding="utf-8")
            finally:
                os.chdir(original_cwd)

        self.assertEqual(len(categories), 46)
        self.assertIn("{{chat_request_json}}", answer_prompt)
        self.assertIn("{{chat_request_json}}", summary_prompt)
        self.assertIn("{{markdown_answer}}", summary_prompt)

    def test_old_chatbot_contract_and_cross_feature_imports_are_absent(self):
        source = Path("learning_chatbot.py").read_text(encoding="utf-8")
        forbidden = "user" + "_id"
        proto_source = "\n".join(
            path.read_text(encoding="utf-8") for path in Path("proto").glob("*.proto")
        )

        self.assertFalse(Path("prompts/personalized_learning_chatbot_prompt.md").exists())
        self.assertFalse(
            Path(
                "docs/superpowers/specs/2026-07-20-personalized-learning-chatbot-design.md"
            ).exists()
        )
        self.assertFalse(
            Path(
                "docs/superpowers/plans/2026-07-20-personalized-learning-chatbot.md"
            ).exists()
        )
        self.assertNotIn("ai_feedback_pipeline", source)
        self.assertNotIn("learning_summary", source)
        self.assertNotIn("relevant_issues", source)
        self.assertNotIn("conversation_memory", source)
        self.assertNotIn("write_text", source)
        self.assertNotIn("--output", source)
        self.assertNotIn(forbidden, source)
        self.assertNotIn("message LearningChatRequest", proto_source)
        self.assertNotIn("message ConversationMemory", proto_source)
        self.assertNotIn("memory_update", proto_source)
        self.assertFalse(Path("proto/code_analysis.proto").exists())
        self.assertFalse(Path("proto/learning_chatbot.proto").exists())
        self.assertFalse(Path("learning_chatbot_server.py").exists())

    def test_manual_demo_and_legacy_artifacts_are_absent(self):
        self.assertFalse(Path("tests/manual_chatbot_demo.py").exists())
        self.assertFalse(Path("data/user_profile.json").exists())
        self.assertFalse(Path("data/weakness_texts.json").exists())
        self.assertFalse(Path("docs/superpowers").exists())


if __name__ == "__main__":
    unittest.main()
