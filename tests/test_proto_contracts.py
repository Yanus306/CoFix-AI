import importlib
import unittest
from pathlib import Path


EXPECTED_PROTO_FILES = {
    "code_analysis_input.proto",
    "code_analysis_output.proto",
    "issue_quiz_input.proto",
    "issue_quiz_output.proto",
    "learning_chat_input.proto",
    "learning_chat_output.proto",
}


class SplitProtoContractTests(unittest.TestCase):
    def test_schema_is_exactly_six_feature_input_output_files(self):
        actual = {path.name for path in Path("proto").glob("*.proto")}
        self.assertEqual(actual, EXPECTED_PROTO_FILES)

    def test_each_input_imports_its_output_and_declares_expected_service_shape(self):
        expected = {
            "code_analysis_input.proto": (
                'import "proto/code_analysis_output.proto";',
                "service CodeAnalysisService",
                "rpc AnalyzeCode(AnalyzeCodeRequest) returns (AnalyzeCodeResponse);",
            ),
            "issue_quiz_input.proto": (
                'import "proto/issue_quiz_output.proto";',
                "service IssueQuizService",
                "rpc GenerateIssueQuiz(GenerateIssueQuizRequest) returns (GenerateIssueQuizResponse);",
            ),
            "learning_chat_input.proto": (
                'import "proto/learning_chat_output.proto";',
                "service LearningChatbotService",
                "rpc Chat(ChatRequest) returns (stream ChatStreamResponse);",
            ),
        }
        for filename, fragments in expected.items():
            source = Path("proto", filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                for fragment in fragments:
                    self.assertIn(fragment, source)

    def test_generated_messages_preserve_existing_external_fields(self):
        analysis_in = importlib.import_module("proto.code_analysis_input_pb2")
        analysis_out = importlib.import_module("proto.code_analysis_output_pb2")
        quiz_in = importlib.import_module("proto.issue_quiz_input_pb2")
        quiz_out = importlib.import_module("proto.issue_quiz_output_pb2")
        chat_in = importlib.import_module("proto.learning_chat_input_pb2")
        chat_out = importlib.import_module("proto.learning_chat_output_pb2")

        self.assertEqual(
            list(analysis_in.AnalyzeCodeRequest.DESCRIPTOR.fields_by_name),
            ["code", "learning_context"],
        )
        self.assertEqual(
            list(analysis_out.CodeIssue.DESCRIPTOR.fields_by_name),
            [
                "code",
                "label",
                "title",
                "description",
                "learning_directions",
                "dataset",
                "guide",
            ],
        )
        self.assertEqual(
            list(analysis_out.AnalyzeCodeResponse.DESCRIPTOR.fields_by_name),
            ["issues"],
        )
        self.assertEqual(
            list(quiz_in.GenerateIssueQuizRequest.DESCRIPTOR.fields_by_name),
            ["selected_level", "problem_count", "issues"],
        )
        self.assertEqual(
            list(quiz_out.QuizProblem.DESCRIPTOR.fields_by_name),
            ["question", "code_block", "choices", "answer", "explanation"],
        )
        self.assertEqual(
            list(chat_in.ChatRequest.DESCRIPTOR.fields_by_name),
            [
                "message",
                "category_counts",
                "recent_issues",
                "recent_conversation_summaries",
            ],
        )
        self.assertFalse(hasattr(chat_out, "ChatResponse"))
        self.assertEqual(
            list(chat_out.MarkdownAnswer.DESCRIPTOR.fields_by_name),
            ["markdown"],
        )
        self.assertEqual(
            list(chat_out.ConversationSummaryResult.DESCRIPTOR.fields_by_name),
            ["conversation_summary"],
        )
        self.assertEqual(
            list(chat_out.ChatStreamResponse.DESCRIPTOR.fields_by_name),
            ["markdown_answer", "summary"],
        )
        self.assertIn(
            "payload",
            chat_out.ChatStreamResponse.DESCRIPTOR.oneofs_by_name,
        )

    def test_removed_identity_field_numbers_are_reserved(self):
        analysis_input = Path("proto/code_analysis_input.proto").read_text(encoding="utf-8")
        analysis_output = Path("proto/code_analysis_output.proto").read_text(encoding="utf-8")
        chat_input = Path("proto/learning_chat_input.proto").read_text(encoding="utf-8")

        self.assertIn("reserved 1;", analysis_input)
        self.assertIn("reserved 2;", analysis_output)
        self.assertIn("reserved 1;", chat_input)
        forbidden = "user" + "_id"
        for source in (analysis_input, analysis_output, chat_input):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
