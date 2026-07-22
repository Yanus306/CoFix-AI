import unittest
from pathlib import Path

from google.protobuf import text_format

from generated_proto import code_analysis_input_pb2 as analysis_in
from generated_proto import code_analysis_output_pb2 as analysis_out
from generated_proto import issue_quiz_input_pb2 as quiz_in
from generated_proto import issue_quiz_output_pb2 as quiz_out
from generated_proto import learning_chat_input_pb2 as chat_in
from generated_proto import learning_chat_output_pb2 as chat_out


EXAMPLES = {
    "code_analysis_request.textproto": analysis_in.AnalyzeCodeRequest,
    "code_analysis_response.textproto": analysis_out.AnalyzeCodeResponse,
    "issue_quiz_request.textproto": quiz_in.GenerateIssueQuizRequest,
    "issue_quiz_response.textproto": quiz_out.GenerateIssueQuizResponse,
    "learning_chat_request.textproto": chat_in.ChatRequest,
    "learning_chat_response.textproto": chat_out.ChatResponse,
}


class TextProtoExampleTests(unittest.TestCase):
    def parse_example(self, filename, message_type):
        source = Path("examples", "proto", filename).read_text(encoding="utf-8")
        self.assertNotIn("user" + "_id", source)
        message = text_format.Parse(source, message_type())
        restored = message_type.FromString(message.SerializeToString())
        self.assertEqual(restored, message)
        return message

    def test_all_examples_parse_and_round_trip(self):
        parsed = {
            filename: self.parse_example(filename, message_type)
            for filename, message_type in EXAMPLES.items()
        }

        analysis_request = parsed["code_analysis_request.textproto"]
        analysis_response = parsed["code_analysis_response.textproto"]
        quiz_request = parsed["issue_quiz_request.textproto"]
        quiz_response = parsed["issue_quiz_response.textproto"]
        chat_request = parsed["learning_chat_request.textproto"]
        chat_response = parsed["learning_chat_response.textproto"]

        self.assertTrue(analysis_request.code)
        self.assertNotIn("range(len(values) + 1)", analysis_request.code)
        self.assertIn("@@ -3 +3 @@", analysis_request.patch)
        self.assertGreaterEqual(len(analysis_request.learning_context.recent_issues), 1)
        self.assertEqual(
            [issue.dataset for issue in analysis_response.issues],
            ["edge_case"],
        )
        self.assertTrue(
            all(issue.code in analysis_request.code for issue in analysis_response.issues)
        )
        self.assertTrue(all(issue.label for issue in analysis_response.issues))
        self.assertEqual(quiz_request.problem_count, 3)
        self.assertEqual(len(quiz_response.problems), 3)
        self.assertTrue(any(problem.HasField("code_block") for problem in quiz_response.problems))
        self.assertTrue(all(len(problem.choices) == 4 for problem in quiz_response.problems))
        self.assertEqual(len(chat_request.category_counts), 46)
        self.assertLessEqual(len(chat_request.recent_issues), 5)
        self.assertLessEqual(len(chat_request.recent_conversation_summaries), 5)
        self.assertTrue(chat_response.HasField("title"))
        self.assertTrue(chat_response.HasField("conversation_summary"))
        self.assertTrue(chat_response.title)
        self.assertTrue(chat_response.conversation_summary)
        self.assertTrue(chat_response.markdown_answer.startswith("##"))


if __name__ == "__main__":
    unittest.main()
