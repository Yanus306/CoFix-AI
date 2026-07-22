import argparse
import ipaddress
from concurrent import futures
from pathlib import Path
from types import SimpleNamespace

import grpc

import ai_feedback_pipeline as analysis
import issue_quiz as quiz
import learning_chatbot as chatbot
from generated_proto import code_analysis_input_pb2_grpc as analysis_grpc
from generated_proto import code_analysis_output_pb2 as analysis_out
from generated_proto import issue_quiz_input_pb2_grpc as quiz_grpc
from generated_proto import issue_quiz_output_pb2 as quiz_out
from generated_proto import learning_chat_input_pb2_grpc as chat_grpc
from generated_proto import learning_chat_output_pb2 as chat_out


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ADDRESS = "127.0.0.1:50051"


def validate_bind_address(address, *, trusted_transport=False):
    if not isinstance(address, str) or not address.strip():
        raise RuntimeError("gRPC bind address must be a non-empty host:port string.")
    address = address.strip()
    if address.startswith("["):
        closing = address.find("]")
        if closing < 0 or closing + 1 >= len(address) or address[closing + 1] != ":":
            raise RuntimeError("gRPC bind address must be a valid host:port string.")
        host = address[1:closing]
        port_text = address[closing + 2 :]
    else:
        host, separator, port_text = address.rpartition(":")
        if not separator:
            raise RuntimeError("gRPC bind address must be a valid host:port string.")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError("gRPC bind address port must be an integer.") from exc
    if not 0 <= port <= 65535:
        raise RuntimeError("gRPC bind address port must be between 0 and 65535.")

    normalized_host = host.strip().lower()
    is_loopback = normalized_host == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(normalized_host).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback and not trusted_transport:
        raise RuntimeError(
            "Non-loopback gRPC binding requires trusted transport such as "
            "TLS/mTLS or a protected service mesh; pass --trusted-transport "
            "only when that boundary is configured."
        )


def _recent_issues_to_mappings(items):
    return [
        {
            "dataset": item.dataset,
            "title": item.title,
            "learning_directions": list(item.learning_directions),
            "code": item.code,
            "guide": item.guide,
        }
        for item in items
    ]


def _analysis_request_to_mapping(request):
    if not request.HasField("learning_context"):
        raise RuntimeError("learning_context is required.")
    code = request.code.strip()
    if not code:
        raise RuntimeError("code must be a non-empty string.")
    patch_text = request.patch
    recent_issues = [
        analysis.normalize_recent_issue(item, index)
        for index, item in enumerate(
            _recent_issues_to_mappings(request.learning_context.recent_issues)
        )
    ]
    return {
        "code": code,
        "patch": patch_text,
        "learning_context": {"recent_issues": recent_issues},
    }


def _quiz_request_to_mapping(request):
    return quiz.validate_request(
        {
            "selected_level": request.selected_level,
            "problem_count": request.problem_count,
            "issues": _recent_issues_to_mappings(request.issues),
        }
    )


def _chat_request_to_mapping(request):
    return {
        "title": request.title,
        "message": request.message,
        "category_counts": [
            {"dataset": item.dataset, "count": item.count}
            for item in request.category_counts
        ],
        "recent_issues": _recent_issues_to_mappings(request.recent_issues),
        "recent_conversation_summaries": [
            {"turn_id": item.turn_id, "summary": item.summary}
            for item in request.recent_conversation_summaries
        ],
    }


def _analysis_response_from_mapping(result):
    response = analysis_out.AnalyzeCodeResponse()
    for item in result["issues"]:
        response.issues.add(
            code=item["code"],
            label=item["label"],
            title=item["title"],
            description=item["description"],
            learning_directions=item["learning_directions"],
            dataset=item["dataset"],
            guide=item["guide"],
        )
    return response


def _quiz_response_from_mapping(result):
    response = quiz_out.GenerateIssueQuizResponse()
    for item in result["problems"]:
        problem = response.problems.add(
            question=item["question"],
            answer=item["answer"],
            explanation=item["explanation"],
        )
        if item["code_block"] is not None:
            problem.code_block.language = item["code_block"]["language"]
            problem.code_block.content = item["code_block"]["content"]
        for choice in item["choices"]:
            problem.choices.add(id=choice["id"], text=choice["text"])
    return response


def _chat_response(result):
    response = chat_out.ChatResponse(markdown_answer=result.markdown_answer)
    if result.title is not None:
        response.title = result.title
    if result.conversation_summary is not None:
        response.conversation_summary = result.conversation_summary
    return response


def _abort_dependency_error(context, exc):
    error_name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in error_name:
        context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "Gemini request timed out.")
    if (
        isinstance(exc, ConnectionError)
        or "connection" in error_name
        or "connecterror" in error_name
        or "networkerror" in error_name
    ):
        context.abort(grpc.StatusCode.UNAVAILABLE, "Gemini service is unavailable.")
    api_code = getattr(exc, "status_code", None)
    if api_code is None:
        api_code = getattr(exc, "code", None)
    if api_code in {408, 504}:
        context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "Gemini request timed out.")
    if api_code == 429 or (isinstance(api_code, int) and api_code >= 500):
        context.abort(grpc.StatusCode.UNAVAILABLE, "Gemini service is unavailable.")
    context.abort(grpc.StatusCode.INTERNAL, "AI response processing failed.")


class CodeAnalysisServicer(analysis_grpc.CodeAnalysisServiceServicer):
    def __init__(self, *, categories=None, feedback_generator=None):
        self._categories = categories if categories is not None else analysis.read_json(
            PROJECT_ROOT / "data" / "categories.json", []
        )
        self._feedback_generator = feedback_generator or self._build_default_generator()

    def _build_default_generator(self):
        args = SimpleNamespace(
            env_file=PROJECT_ROOT / ".env",
            prompt_file=PROJECT_ROOT / "prompts" / "full_feedback_pipeline_prompt.md",
            model=analysis.DEFAULT_MODEL,
        )

        def generate(request):
            code = request["code"]
            patch_text = request["patch"]
            recent_issues = request["learning_context"]["recent_issues"]
            raw = analysis.request_ai_feedback(
                code,
                self._categories,
                recent_issues,
                args,
                patch=patch_text,
            )
            return analysis.build_api_response(
                analysis.normalize_feedback(raw, self._categories)
            )

        return generate

    def AnalyzeCode(self, request, context):
        try:
            normalized = _analysis_request_to_mapping(request)
        except RuntimeError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        try:
            return _analysis_response_from_mapping(self._feedback_generator(normalized))
        except Exception as exc:
            _abort_dependency_error(context, exc)


class IssueQuizServicer(quiz_grpc.IssueQuizServiceServicer):
    def __init__(self, *, quiz_generator=None):
        self._quiz_generator = quiz_generator or self._build_default_generator()

    @staticmethod
    def _build_default_generator():
        args = SimpleNamespace(
            env_file=PROJECT_ROOT / ".env",
            model=quiz.DEFAULT_MODEL,
        )

        def generate(request):
            return quiz.request_quiz_set(request, args)

        return generate

    def GenerateIssueQuiz(self, request, context):
        try:
            normalized = _quiz_request_to_mapping(request)
        except RuntimeError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        try:
            result = quiz.normalize_quiz_set(
                self._quiz_generator(normalized), normalized["problem_count"]
            )
            return _quiz_response_from_mapping(result)
        except Exception as exc:
            _abort_dependency_error(context, exc)


class LearningChatbotServicer(chat_grpc.LearningChatbotServiceServicer):
    def __init__(
        self,
        *,
        categories=None,
        response_generator=None,
    ):
        self._categories = categories if categories is not None else chatbot.load_categories()
        self._response_generator = response_generator or self._build_default_generator()

    @staticmethod
    def _build_default_generator():
        client = chatbot.create_gemini_client()
        template = Path(chatbot.DEFAULT_ANSWER_PROMPT_FILE).read_text(
            encoding="utf-8"
        )

        def generate_response(request):
            return chatbot.generate_chat_response(
                request,
                client,
                model=chatbot.DEFAULT_MODEL,
                template=template,
            )

        return generate_response

    def Chat(self, request, context):
        try:
            normalized = chatbot.validate_and_compact_request(
                _chat_request_to_mapping(request), self._categories
            )
        except chatbot.RequestValidationError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        try:
            result = self._response_generator(normalized)
            return _chat_response(result)
        except Exception as exc:
            _abort_dependency_error(context, exc)


def create_server(
    analysis_servicer=None,
    quiz_servicer=None,
    chatbot_servicer=None,
    *,
    max_workers=8,
):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    analysis_grpc.add_CodeAnalysisServiceServicer_to_server(
        analysis_servicer or CodeAnalysisServicer(), server
    )
    quiz_grpc.add_IssueQuizServiceServicer_to_server(
        quiz_servicer or IssueQuizServicer(), server
    )
    chat_grpc.add_LearningChatbotServiceServicer_to_server(
        chatbot_servicer or LearningChatbotServicer(), server
    )
    return server


def serve(address=DEFAULT_ADDRESS, *, trusted_transport=False):
    validate_bind_address(address, trusted_transport=trusted_transport)
    server = create_server()
    bound_port = server.add_insecure_port(address)
    if bound_port == 0:
        raise RuntimeError(f"Could not bind gRPC server to {address}.")
    server.start()
    print(f"CoFix AI gRPC server listening on {address}")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(grace=2).wait()


def parse_args():
    parser = argparse.ArgumentParser(description="Run all CoFix AI gRPC services.")
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument(
        "--trusted-transport",
        action="store_true",
        help=(
            "Allow a non-loopback bind only when TLS/mTLS or a protected "
            "service-mesh boundary is already configured."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    serve(args.address, trusted_transport=args.trusted_transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
