import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CATEGORIES_FILE = PROJECT_ROOT / "data" / "categories.json"
DEFAULT_ANSWER_PROMPT_FILE = PROJECT_ROOT / "prompts" / "coding_learning_chatbot_prompt.md"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
MAX_OUTPUT_TOKENS = 4096
MAX_RECENT_ISSUES = 5
MAX_CONVERSATION_SUMMARIES = 5
MAX_CHAT_TITLE_LENGTH = 50
MAX_SUMMARY_LENGTH = 300
MAX_MESSAGE_LENGTH = 4000
MAX_ISSUE_TITLE_LENGTH = 300
MAX_LEARNING_DIRECTIONS = 20
MAX_LEARNING_DIRECTION_LENGTH = 300
MAX_ISSUE_CODE_LENGTH = 20000
MAX_ISSUE_GUIDE_LENGTH = 5000
MAX_TURN_ID_LENGTH = 128

REQUEST_FIELDS = {
    "title",
    "message",
    "category_counts",
    "recent_issues",
    "recent_conversation_summaries",
}
CATEGORY_COUNT_FIELDS = {"dataset", "count"}
RECENT_ISSUE_FIELDS = {
    "dataset",
    "title",
    "learning_directions",
    "code",
    "guide",
}
CONVERSATION_SUMMARY_FIELDS = {"turn_id", "summary"}


class RequestValidationError(ValueError):
    """The BE request does not satisfy the chatbot contract."""


class ModelResponseError(RuntimeError):
    """Gemini returned data that does not satisfy the internal contract."""


@dataclass(frozen=True)
class ChatResponseParts:
    title: str | None
    conversation_summary: str | None
    markdown_answer: str


def load_categories(path=DEFAULT_CATEGORIES_FILE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _require_object(value, path):
    if not isinstance(value, dict):
        raise RequestValidationError(f"{path} must be an object.")
    return value


def _require_exact_fields(value, fields, path):
    _require_object(value, path)
    if set(value) != set(fields):
        raise RequestValidationError(
            f"{path} fields must be exactly: {', '.join(sorted(fields))}."
        )


def _require_text(value, path, *, max_length=None):
    if not isinstance(value, str) or not value.strip():
        raise RequestValidationError(f"{path} must be a non-empty string.")
    value = value.strip()
    if max_length is not None and len(value) > max_length:
        raise RequestValidationError(
            f"{path} must be at most {max_length} characters."
        )
    return value


def _normalize_chat_title(value):
    if not isinstance(value, str):
        raise RequestValidationError("title must be a string.")
    title = value.strip()
    if len(title) > MAX_CHAT_TITLE_LENGTH:
        raise RequestValidationError(
            f"title must be at most {MAX_CHAT_TITLE_LENGTH} characters."
        )
    return title


def _category_map(categories):
    if not isinstance(categories, list) or not categories:
        raise RequestValidationError("category table must be a non-empty array.")
    result = {}
    for index, item in enumerate(categories):
        if not isinstance(item, dict):
            raise RequestValidationError(f"category table row {index} must be an object.")
        key = item.get("key")
        name = item.get("name")
        condition = item.get("condition")
        if not all(isinstance(value, str) and value.strip() for value in (key, name, condition)):
            raise RequestValidationError(f"category table row {index} is invalid.")
        key = key.strip()
        if key in result:
            raise RequestValidationError(f"category table contains duplicate key: {key}.")
        result[key] = {
            "name": name.strip(),
            "condition": condition.strip(),
            "order": index,
        }
    return result


def _normalize_category_counts(raw_counts, categories_by_key):
    if not isinstance(raw_counts, list):
        raise RequestValidationError("category_counts must be an array.")
    received = {}
    for index, item in enumerate(raw_counts):
        path = f"category_counts[{index}]"
        _require_exact_fields(item, CATEGORY_COUNT_FIELDS, path)
        dataset = _require_text(item["dataset"], f"{path}.dataset")
        count = item["count"]
        if dataset not in categories_by_key:
            raise RequestValidationError(f"category_counts contains unknown category: {dataset}.")
        if dataset in received:
            raise RequestValidationError(f"category_counts contains duplicate category: {dataset}.")
        if type(count) is not int or count < 0:
            raise RequestValidationError(f"{path}.count must be a non-negative integer.")
        received[dataset] = count

    expected_keys = set(categories_by_key)
    received_keys = set(received)
    if received_keys != expected_keys:
        missing = sorted(expected_keys - received_keys)
        extra = sorted(received_keys - expected_keys)
        raise RequestValidationError(
            f"category_counts must contain every category exactly once; "
            f"missing={missing}, extra={extra}."
        )

    positive = []
    for dataset, count in received.items():
        if count == 0:
            continue
        definition = categories_by_key[dataset]
        positive.append(
            {
                "dataset": dataset,
                "count": count,
                "name": definition["name"],
                "condition": definition["condition"],
                "_order": definition["order"],
            }
        )
    positive.sort(key=lambda item: (-item["count"], item["_order"]))
    for item in positive:
        item.pop("_order")
    return positive


def _normalize_recent_issues(raw_issues, categories_by_key):
    if not isinstance(raw_issues, list):
        raise RequestValidationError("recent_issues must be an array.")
    if len(raw_issues) > MAX_RECENT_ISSUES:
        raise RequestValidationError(
            f"recent_issues must contain at most {MAX_RECENT_ISSUES} items."
        )
    issues = []
    for index, item in enumerate(raw_issues):
        path = f"recent_issues[{index}]"
        _require_exact_fields(item, RECENT_ISSUE_FIELDS, path)
        dataset = _require_text(item["dataset"], f"{path}.dataset")
        if dataset not in categories_by_key:
            raise RequestValidationError(f"{path}.dataset is not registered.")
        raw_directions = item["learning_directions"]
        if not isinstance(raw_directions, list) or not raw_directions:
            raise RequestValidationError(f"{path}.learning_directions must be a non-empty array.")
        if len(raw_directions) > MAX_LEARNING_DIRECTIONS:
            raise RequestValidationError(
                f"{path}.learning_directions must contain at most "
                f"{MAX_LEARNING_DIRECTIONS} items."
            )
        directions = [
            _require_text(
                value,
                f"{path}.learning_directions[{direction_index}]",
                max_length=MAX_LEARNING_DIRECTION_LENGTH,
            )
            for direction_index, value in enumerate(raw_directions)
        ]
        issues.append(
            {
                "dataset": dataset,
                "title": _require_text(
                    item["title"],
                    f"{path}.title",
                    max_length=MAX_ISSUE_TITLE_LENGTH,
                ),
                "learning_directions": directions,
                "code": _require_text(
                    item["code"],
                    f"{path}.code",
                    max_length=MAX_ISSUE_CODE_LENGTH,
                ),
                "guide": _require_text(
                    item["guide"],
                    f"{path}.guide",
                    max_length=MAX_ISSUE_GUIDE_LENGTH,
                ),
            }
        )
    return issues


def _normalize_conversation_summaries(raw_summaries):
    if not isinstance(raw_summaries, list):
        raise RequestValidationError("recent_conversation_summaries must be an array.")
    if len(raw_summaries) > MAX_CONVERSATION_SUMMARIES:
        raise RequestValidationError(
            "recent_conversation_summaries must contain at most "
            f"{MAX_CONVERSATION_SUMMARIES} items."
        )
    summaries = []
    for index, item in enumerate(raw_summaries):
        path = f"recent_conversation_summaries[{index}]"
        _require_exact_fields(item, CONVERSATION_SUMMARY_FIELDS, path)
        summary = _require_text(item["summary"], f"{path}.summary")
        if len(summary) > MAX_SUMMARY_LENGTH:
            raise RequestValidationError(
                f"{path}.summary must be at most {MAX_SUMMARY_LENGTH} characters."
            )
        summaries.append(
            {
                "turn_id": _require_text(
                    item["turn_id"],
                    f"{path}.turn_id",
                    max_length=MAX_TURN_ID_LENGTH,
                ),
                "summary": summary,
            }
        )
    return summaries


def validate_and_compact_request(request, categories):
    _require_exact_fields(request, REQUEST_FIELDS, "request")
    categories_by_key = _category_map(categories)
    return {
        "title": _normalize_chat_title(request["title"]),
        "message": _require_text(
            request["message"], "message", max_length=MAX_MESSAGE_LENGTH
        ),
        "category_counts": _normalize_category_counts(
            request["category_counts"], categories_by_key
        ),
        "recent_issues": _normalize_recent_issues(
            request["recent_issues"], categories_by_key
        ),
        "recent_conversation_summaries": _normalize_conversation_summaries(
            request["recent_conversation_summaries"]
        ),
    }


def _replace_placeholders_once(template, replacements):
    missing = [placeholder for placeholder in replacements if placeholder not in template]
    if missing:
        raise RuntimeError(
            f"Prompt template must contain: {', '.join(missing)}."
        )
    pattern = re.compile(
        "|".join(
            re.escape(key)
            for key in sorted(replacements, key=len, reverse=True)
        )
    )
    return pattern.sub(lambda match: replacements[match.group(0)], template)


def build_answer_prompt(template, compact_request):
    return _replace_placeholders_once(
        template,
        {
            "{{chat_request_json}}": json.dumps(
                compact_request,
                ensure_ascii=False,
                indent=2,
            )
        },
    )


def _reject_duplicate_json_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ModelResponseError(
                f"Gemini response contains duplicate JSON field: {key}."
            )
        data[key] = value
    return data


def parse_model_json(raw_text):
    if not isinstance(raw_text, str):
        raise ModelResponseError("Gemini response must be text containing one bare JSON object.")
    stripped = raw_text.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        raise ModelResponseError(
            "Gemini response must be one bare JSON object without prose or code fences."
        )
    try:
        data = json.loads(stripped, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise ModelResponseError("Gemini response is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ModelResponseError("Gemini response must be one JSON object.")
    return data


def normalize_summary_response(data):
    expected_fields = ["title", "conversation_summary"]
    if not isinstance(data, dict) or list(data) != expected_fields:
        raise ModelResponseError(
            "Gemini summary fields must be exactly: title, conversation_summary."
        )
    title = data["title"]
    summary = data["conversation_summary"]
    if title is None and summary is None:
        return {"title": None, "conversation_summary": None}
    if title is None or summary is None:
        raise ModelResponseError(
            "title and conversation_summary must both be strings or both be null."
        )
    if not isinstance(title, str) or not title.strip():
        raise ModelResponseError("title must be a non-empty string or null.")
    if not isinstance(summary, str) or not summary.strip():
        raise ModelResponseError(
            "conversation_summary must be a non-empty string or null."
        )
    if len(title) > MAX_CHAT_TITLE_LENGTH:
        raise ModelResponseError(
            f"title must be at most {MAX_CHAT_TITLE_LENGTH} characters."
        )
    if len(summary) > MAX_SUMMARY_LENGTH:
        raise ModelResponseError(
            f"conversation_summary must be at most {MAX_SUMMARY_LENGTH} characters."
        )
    if title != title.strip():
        raise ModelResponseError(
            "title must not have leading or trailing whitespace."
        )
    if summary != summary.strip():
        raise ModelResponseError(
            "conversation_summary must not have leading or trailing whitespace."
        )
    if title.lower() == "null":
        raise ModelResponseError("title must use JSON null, not the string 'null'.")
    if summary.lower() == "null":
        raise ModelResponseError(
            "conversation_summary must use JSON null, not the string 'null'."
        )
    return {"title": title, "conversation_summary": summary}


def validate_chat_response(raw_text):
    if not isinstance(raw_text, str):
        raise ModelResponseError("Gemini chat response must be text.")
    json_line, separator, markdown = raw_text.partition("\n")
    if not separator:
        raise ModelResponseError(
            "Gemini chat response must contain a JSON first line and Markdown body."
        )
    metadata = normalize_summary_response(parse_model_json(json_line))
    if not markdown.strip():
        raise ModelResponseError("Markdown answer must be non-empty text.")
    return ChatResponseParts(
        title=metadata["title"],
        conversation_summary=metadata["conversation_summary"],
        markdown_answer=markdown,
    )


def _extract_response_text(response):
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise ModelResponseError("Gemini response text is empty.")
    return text


def _request_chat(client, model, prompt):
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "response_mime_type": "text/plain",
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        },
    )
    return validate_chat_response(_extract_response_text(response))


def generate_chat_response(
    compact_request,
    client,
    *,
    model=DEFAULT_MODEL,
    template=None,
):
    if template is None:
        template = Path(DEFAULT_ANSWER_PROMPT_FILE).read_text(encoding="utf-8")
    return _request_chat(
        client,
        model,
        build_answer_prompt(template, compact_request),
    )


def load_env_file(path=DEFAULT_ENV_FILE):
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def create_gemini_client(env_file=DEFAULT_ENV_FILE):
    load_env_file(env_file)
    if not os.environ.get(GEMINI_API_KEY_ENV):
        raise RuntimeError(f"{GEMINI_API_KEY_ENV} is not set.")
    from google import genai

    return genai.Client()
