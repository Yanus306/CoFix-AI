import unittest
from concurrent import futures
from pathlib import Path

import grpc

import cofix_ai_server as server_module
import learning_chatbot as chatbot
from generated_proto import code_analysis_input_pb2 as analysis_in
from generated_proto import code_analysis_input_pb2_grpc as analysis_grpc
from generated_proto import issue_quiz_input_pb2 as quiz_in
from generated_proto import issue_quiz_input_pb2_grpc as quiz_grpc
from generated_proto import learning_chat_input_pb2 as chat_in
from generated_proto import learning_chat_input_pb2_grpc as chat_grpc


def analysis_request():
    return analysis_in.AnalyzeCodeRequest(
        code=(
            "def calculate_average(values):\n"
            "    total = 0\n"
            "    for i in range(len(values) + 1):\n"
            "        total += values[i]\n"
            "    return total / len(values)"
        ),
        learning_context=analysis_in.LearningContext(
            recent_issues=[
                analysis_in.RecentIssue(
                    dataset="loop_control",
                    title="반복 범위 오류",
                    learning_directions=["오프바이원 오류"],
                    code="for i in range(len(values) + 1):",
                    guide="유효 인덱스까지만 반복해야 한다.",
                )
            ]
        ),
    )


def quiz_request():
    return quiz_in.GenerateIssueQuizRequest(
        selected_level="medium",
        problem_count=3,
        issues=[
            quiz_in.RecentIssue(
                dataset="loop_control",
                title="반복 범위 오류",
                learning_directions=["오프바이원 오류"],
                code="for i in range(len(values) + 1):",
                guide="유효 인덱스까지만 반복해야 한다.",
            )
        ],
    )


def quiz_problem(index):
    return {
        "question": f"반복문 문제 {index}",
        "code_block": (
            {"language": "python", "content": "for i in range(len(values)):\n    pass"}
            if index == 1
            else None
        ),
        "choices": [
            {"id": "A", "text": f"선택지 A-{index}"},
            {"id": "B", "text": f"선택지 B-{index}"},
            {"id": "C", "text": f"선택지 C-{index}"},
            {"id": "D", "text": f"선택지 D-{index}"},
        ],
        "answer": "A",
        "explanation": f"문제 {index} 정답 설명",
    }


def chat_request(message="반복문 오류를 고쳐줘"):
    counts = [
        chat_in.CategoryCount(
            dataset=item["key"],
            count=7 if item["key"] == "loop_control" else 0,
        )
        for item in chatbot.load_categories()
    ]
    return chat_in.ChatRequest(
        message=message,
        category_counts=counts,
        recent_issues=[
            chat_in.RecentIssue(
                dataset="loop_control",
                title="반복 범위 오류",
                learning_directions=["오프바이원 오류"],
                code="range(len(values) + 1)",
                guide="반복 범위를 리스트 길이까지만 사용한다.",
            )
        ],
        recent_conversation_summaries=[
            chat_in.ConversationSummary(
                turn_id="turn_001",
                summary="사용자는 반복문 오류 원인을 질문했다.",
            )
        ],
    )


class UnifiedGrpcServerTests(unittest.TestCase):
    def start_server(
        self,
        *,
        analysis_generator=None,
        quiz_generator=None,
        chat_answer_generator=None,
        chat_summary_generator=None,
    ):
        analysis_generator = analysis_generator or (
            lambda request: {
                "issues": [
                    {
                        "code": request["code"],
                        "label": "반복제어",
                        "title": "반복 범위가 리스트 길이를 초과함",
                        "description": "마지막 반복에서 유효하지 않은 인덱스에 접근합니다.",
                        "learning_directions": ["반복문 범위", "오프바이원 오류"],
                        "dataset": "loop_control",
                        "guide": "🚨 문제: 범위 초과\n💡 해결: 반복 범위 수정\n✨ 핵심 원리\n유효 인덱스를 확인한다.",
                    }
                ],
            }
        )
        quiz_generator = quiz_generator or (
            lambda _request: {"problems": [quiz_problem(1), quiz_problem(2), quiz_problem(3)]}
        )
        chat_answer_generator = chat_answer_generator or (
            lambda _request: "## 수정 방법\n\n반복 범위를 직접 줄여보세요."
        )
        chat_summary_generator = chat_summary_generator or (
            lambda _request, _markdown: "사용자는 반복 범위 수정법을 질문했고 힌트를 안내받았다."
        )
        grpc_server = server_module.create_server(
            analysis_servicer=server_module.CodeAnalysisServicer(
                feedback_generator=analysis_generator
            ),
            quiz_servicer=server_module.IssueQuizServicer(quiz_generator=quiz_generator),
            chatbot_servicer=server_module.LearningChatbotServicer(
                categories=chatbot.load_categories(),
                answer_generator=chat_answer_generator,
                summary_generator=chat_summary_generator,
            ),
            max_workers=4,
        )
        port = grpc_server.add_insecure_port("127.0.0.1:0")
        grpc_server.start()
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        grpc.channel_ready_future(channel).result(timeout=3)
        return grpc_server, channel

    def test_one_server_round_trips_all_three_feature_contracts(self):
        grpc_server, channel = self.start_server()
        try:
            analysis_response = analysis_grpc.CodeAnalysisServiceStub(channel).AnalyzeCode(
                analysis_request(), timeout=3
            )
            quiz_response = quiz_grpc.IssueQuizServiceStub(channel).GenerateIssueQuiz(
                quiz_request(), timeout=3
            )
            chat_responses = list(
                chat_grpc.LearningChatbotServiceStub(channel).Chat(
                    chat_request(), timeout=3
                )
            )
        finally:
            channel.close()
            grpc_server.stop(0).wait()

        self.assertEqual(analysis_response.issues[0].label, "반복제어")
        self.assertEqual(len(analysis_response.issues[0].learning_directions), 2)
        self.assertEqual(len(quiz_response.problems), 3)
        self.assertTrue(quiz_response.problems[0].HasField("code_block"))
        self.assertFalse(quiz_response.problems[1].HasField("code_block"))
        self.assertEqual([choice.id for choice in quiz_response.problems[0].choices], list("ABCD"))
        self.assertEqual(len(chat_responses), 2)
        self.assertEqual(chat_responses[0].WhichOneof("payload"), "markdown_answer")
        self.assertTrue(chat_responses[0].markdown_answer.markdown.startswith("## 수정 방법"))
        self.assertEqual(chat_responses[1].WhichOneof("payload"), "summary")
        self.assertTrue(chat_responses[1].summary.HasField("conversation_summary"))

    def test_invalid_input_maps_to_invalid_argument_for_each_service(self):
        grpc_server, channel = self.start_server()
        calls = [
            lambda: analysis_grpc.CodeAnalysisServiceStub(channel).AnalyzeCode(
                analysis_in.AnalyzeCodeRequest(code="print('x')"), timeout=3
            ),
            lambda: quiz_grpc.IssueQuizServiceStub(channel).GenerateIssueQuiz(
                quiz_in.GenerateIssueQuizRequest(selected_level="easy", problem_count=2),
                timeout=3,
            ),
            lambda: list(
                chat_grpc.LearningChatbotServiceStub(channel).Chat(
                    chat_in.ChatRequest(message="질문"), timeout=3
                )
            ),
        ]
        try:
            for call in calls:
                with self.subTest(call=call):
                    with self.assertRaises(grpc.RpcError) as caught:
                        call()
                    self.assertEqual(caught.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        finally:
            channel.close()
            grpc_server.stop(0).wait()

    def test_out_of_scope_chat_omits_optional_summary(self):
        grpc_server, channel = self.start_server(
            chat_answer_generator=lambda _request: (
                "코딩과 소프트웨어 개발 질문을 도와드릴 수 있습니다."
            ),
            chat_summary_generator=lambda _request, _markdown: None,
        )
        try:
            responses = list(
                chat_grpc.LearningChatbotServiceStub(channel).Chat(
                    chat_request("오늘 날씨 어때?"), timeout=3
                )
            )
        finally:
            channel.close()
            grpc_server.stop(0).wait()
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0].WhichOneof("payload"), "markdown_answer")
        self.assertEqual(responses[1].WhichOneof("payload"), "summary")
        self.assertFalse(responses[1].summary.HasField("conversation_summary"))

    def test_summary_failure_aborts_before_any_stream_message_is_sent(self):
        def fail_summary(_request, _markdown):
            raise RuntimeError("private summary failure")

        grpc_server, channel = self.start_server(
            chat_summary_generator=fail_summary,
        )
        try:
            responses = chat_grpc.LearningChatbotServiceStub(channel).Chat(
                chat_request(), timeout=3
            )
            with self.assertRaises(grpc.RpcError) as caught:
                next(responses)
            self.assertEqual(caught.exception.code(), grpc.StatusCode.INTERNAL)
            self.assertNotIn("private", caught.exception.details())
        finally:
            channel.close()
            grpc_server.stop(0).wait()

    def test_default_address_and_generated_dependency_floors(self):
        requirements = Path("requirements.txt").read_text(encoding="utf-8")
        self.assertEqual(server_module.DEFAULT_ADDRESS, "127.0.0.1:50051")
        self.assertIn("grpcio>=1.82.1", requirements)
        self.assertIn("grpcio-tools>=1.82.1", requirements)
        self.assertIn("protobuf>=7.35.0", requirements)

    def test_non_loopback_bind_requires_explicit_trusted_transport(self):
        for address in ("127.0.0.1:50051", "localhost:50051", "[::1]:50051"):
            with self.subTest(address=address):
                server_module.validate_bind_address(address, trusted_transport=False)

        for address in ("0.0.0.0:50051", "[::]:50051", "10.0.0.12:50051"):
            with self.subTest(address=address):
                with self.assertRaisesRegex(RuntimeError, "trusted transport"):
                    server_module.validate_bind_address(address, trusted_transport=False)
                server_module.validate_bind_address(address, trusted_transport=True)

    def test_google_sdk_transport_and_status_errors_map_to_public_grpc_statuses(self):
        class APITimeoutError(Exception):
            pass

        class APIConnectionError(Exception):
            pass

        class StatusError(Exception):
            def __init__(self, status_code):
                super().__init__(f"private upstream detail for {status_code}")
                self.status_code = status_code

        cases = [
            (
                "analysis",
                APITimeoutError("private timeout endpoint"),
                grpc.StatusCode.DEADLINE_EXCEEDED,
            ),
            (
                "quiz",
                APIConnectionError("private connection endpoint"),
                grpc.StatusCode.UNAVAILABLE,
            ),
            ("chat", StatusError(429), grpc.StatusCode.UNAVAILABLE),
            ("chat", StatusError(503), grpc.StatusCode.UNAVAILABLE),
            ("chat", StatusError(504), grpc.StatusCode.DEADLINE_EXCEEDED),
        ]

        for feature, error, expected in cases:
            with self.subTest(feature=feature, error=error):
                def fail(_request, error=error):
                    raise error

                kwargs = {
                    "analysis_generator": fail if feature == "analysis" else None,
                    "quiz_generator": fail if feature == "quiz" else None,
                    "chat_answer_generator": fail if feature == "chat" else None,
                }
                grpc_server, channel = self.start_server(**kwargs)
                try:
                    if feature == "analysis":
                        call = lambda: analysis_grpc.CodeAnalysisServiceStub(channel).AnalyzeCode(
                            analysis_request(), timeout=3
                        )
                    elif feature == "quiz":
                        call = lambda: quiz_grpc.IssueQuizServiceStub(channel).GenerateIssueQuiz(
                            quiz_request(), timeout=3
                        )
                    else:
                        call = lambda: list(
                            chat_grpc.LearningChatbotServiceStub(channel).Chat(
                                chat_request(), timeout=3
                            )
                        )
                    with self.assertRaises(grpc.RpcError) as caught:
                        call()
                    self.assertEqual(caught.exception.code(), expected)
                    self.assertNotIn("private", caught.exception.details())
                finally:
                    channel.close()
                    grpc_server.stop(0).wait()


if __name__ == "__main__":
    unittest.main()
