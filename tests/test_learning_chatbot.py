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


def model_payload(*, related=True, answer="## 반복문 오류\n\n반복 범위를 수정하세요.", summary=None):
    if summary is None and related:
        summary = "사용자는 loop_control 오류 수정법을 질문했고 반복 범위 수정 방법을 안내했다."
    return {
        "is_coding_related": related,
        "answer_markdown": answer,
        "conversation_summary": summary,
    }


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
        prompt = chatbot.build_prompt("{{chat_request_json}}", result)
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

    def test_prompt_contains_scope_personalization_hint_and_fix_rules(self):
        template = Path(chatbot.DEFAULT_PROMPT_FILE).read_text(encoding="utf-8")
        prompt = chatbot.build_prompt(template, self.compact_request)

        self.assertIn("소프트웨어 개발", prompt)
        self.assertIn("관련 없는", prompt)
        self.assertIn("힌트", prompt)
        self.assertIn("수정 코드", prompt)
        self.assertIn("이전 대화 요약", prompt)
        self.assertIn("현재 질문 해석에 필요한 경우", prompt)
        self.assertIn("loop_control", prompt)
        self.assertIn('"count": 7', prompt)
        self.assertNotIn("{{chat_request_json}}", prompt)

    def test_prompt_replacement_is_single_pass(self):
        request = dict(self.compact_request)
        request["message"] = "{{chat_request_json}}"
        prompt = chatbot.build_prompt("request={{chat_request_json}}", request)
        self.assertIn('"message": "{{chat_request_json}}"', prompt)

    def test_normalizes_coding_and_out_of_scope_responses(self):
        coding = chatbot.normalize_model_response(model_payload())
        outside = chatbot.normalize_model_response(
            model_payload(
                related=False,
                answer="코딩과 소프트웨어 개발 질문을 도와드릴 수 있습니다.",
                summary=None,
            )
        )

        self.assertTrue(coding["is_coding_related"])
        self.assertTrue(coding["conversation_summary"])
        self.assertFalse(outside["is_coding_related"])
        self.assertIsNone(outside["conversation_summary"])

    def test_rejects_wrong_summary_branch_length_and_extra_fields(self):
        missing = model_payload()
        missing["conversation_summary"] = None
        outside_with_summary = model_payload(related=False, summary="저장하면 안 됨")
        long_summary = model_payload(summary="가" * 301)
        extra = model_payload()
        extra["referenced_datasets"] = ["loop_control"]

        for payload in (missing, outside_with_summary, long_summary, extra):
            with self.subTest(payload=payload):
                with self.assertRaises(chatbot.ModelResponseError):
                    chatbot.normalize_model_response(payload)

    def test_parser_rejects_prose_fences_and_non_object_json(self):
        bare = json.dumps(model_payload(), ensure_ascii=False)
        self.assertEqual(chatbot.parse_model_json(bare), model_payload())

        for raw in (f"결과: {bare}", f"```json\n{bare}\n```", "[]"):
            with self.subTest(raw=raw):
                with self.assertRaises(chatbot.ModelResponseError):
                    chatbot.parse_model_json(raw)

    def test_successful_generation_calls_gemini_once_in_json_mode(self):
        client = FakeClient([json.dumps(model_payload(), ensure_ascii=False)])
        result = chatbot.generate_chat_reply(
            self.compact_request,
            client,
            model="gemini-test",
            template="{{chat_request_json}}",
        )

        self.assertEqual(result["answer_markdown"], model_payload()["answer_markdown"])
        self.assertEqual(len(client.models.calls), 1)
        self.assertEqual(
            client.models.calls[0]["config"]["response_mime_type"], "application/json"
        )
        self.assertEqual(client.models.calls[0]["config"]["max_output_tokens"], 4096)

    def test_invalid_format_retries_once_then_returns_valid_response(self):
        valid = json.dumps(model_payload(), ensure_ascii=False)
        client = FakeClient(["```json\n{}\n```", valid])
        result = chatbot.generate_chat_reply(
            self.compact_request, client, model="gemini-test", template="{{chat_request_json}}"
        )

        self.assertEqual(result["conversation_summary"], model_payload()["conversation_summary"])
        self.assertEqual(len(client.models.calls), 2)
        self.assertIn("형식 오류", client.models.calls[1]["contents"])

    def test_two_invalid_responses_fail_after_exactly_two_calls(self):
        client = FakeClient(["not json", "still not json"])
        with self.assertRaises(chatbot.ModelResponseError):
            chatbot.generate_chat_reply(
                self.compact_request,
                client,
                model="gemini-test",
                template="{{chat_request_json}}",
            )
        self.assertEqual(len(client.models.calls), 2)

    def test_transport_error_is_not_retried_as_format_error(self):
        client = FakeClient([ConnectionError("offline")])
        with self.assertRaises(ConnectionError):
            chatbot.generate_chat_reply(
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
                prompt = Path(chatbot.DEFAULT_PROMPT_FILE).read_text(encoding="utf-8")
            finally:
                os.chdir(original_cwd)

        self.assertEqual(len(categories), 46)
        self.assertIn("{{chat_request_json}}", prompt)

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
