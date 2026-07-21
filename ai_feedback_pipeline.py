import argparse
import json
import os
import re
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_ENV_FILE = ".env"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_CATEGORIES = "data/categories.json"
DEFAULT_PROMPT_FILE = "prompts/full_feedback_pipeline_prompt.md"

RECENT_ISSUE_FIELDS = (
    "dataset",
    "title",
    "learning_directions",
    "code",
    "guide",
)
ANALYSIS_REQUEST_FIELDS = {"code", "learning_context"}
LEARNING_CONTEXT_FIELDS = {"recent_issues"}
OUTPUT_ISSUE_FIELDS = {
    "code",
    "label",
    "title",
    "description",
    "learning_directions",
    "dataset",
    "guide",
}

CODE_CONTEXT_INSTRUCTION = """
`issues[].code` 범위 규칙:
- 코드를 무조건 한 줄로 줄이지 않는다.
- 오류를 독립적으로 이해할 수 있는 최소한의 완전한 코드 단위를 사용한다.
- 문제가 표현식이면 주변 문맥을, 조건문이나 반복문이면 해당 블록 전체를 포함한다.
- 함수 내부 흐름이 문제면 함수 전체를, UI 상태 흐름이 문제면 관련 컴포넌트 전체를 포함한다.
- 오류와 관계없는 파일 전체 코드는 포함하지 않는다.
""".strip()

DEFAULT_LEARNING_DIRECTIONS = {
    "syntax_structure": ["문법 구조", "코드 블록", "실행 오류"],
    "variable_type": ["자료형 변환", "타입 검사", "초기화"],
    "operator_logic": ["연산자 사용", "조건식 설계", "논리 검증"],
    "string_handling": ["문자열 포맷팅", "타입 변환", "출력 처리"],
    "array_collection": ["컬렉션 접근", "인덱스 검사", "자료구조 기초"],
    "null_missing_value": ["결측값 처리", "입력 검증", "방어 코드"],
    "data_validation": ["입력 검증", "예외 상황", "방어 코드"],
    "conditional": ["조건문 설계", "분기 처리", "경계값"],
    "loop_control": ["반복문 제어", "순회 패턴", "시간 복잡도"],
    "edge_case": ["경계값 처리", "예외 상황", "테스트 케이스"],
    "exception_handling": ["예외 처리", "런타임 오류", "안정성"],
    "time_complexity": ["시간 복잡도", "반복문 최적화", "알고리즘 효율"],
    "algo_selection": ["알고리즘 선택", "문제 유형 분석", "복잡도 계산"],
    "data_structure_choice": ["자료구조 선택", "탐색 최적화", "해시 활용"],
    "performance_runtime": ["런타임 성능", "불필요한 계산 제거", "최적화"],
    "ui_dom_rendering": ["UI 렌더링", "상태 관리", "렌더링 최적화"],
}

CATEGORY_IMPORTANCE = {
    "syntax_structure": 0,
    "exception_handling": 0,
    "buffer_boundary": 0,
    "security_input": 1,
    "auth_access_control": 1,
    "secret_handling": 1,
    "crypto_randomness": 1,
    "null_missing_value": 2,
    "variable_type": 2,
    "operator_logic": 2,
    "conditional": 2,
    "edge_case": 2,
    "data_validation": 2,
    "function_usage": 2,
    "api_misuse": 2,
    "io_network": 2,
    "database_query": 2,
    "time_complexity": 3,
    "performance_runtime": 3,
    "loop_control": 3,
    "algo_selection": 3,
    "data_structure_choice": 3,
    "space_complexity": 3,
    "ui_dom_rendering": 3,
    "state_management": 4,
    "async_handling": 4,
    "concurrency": 4,
    "transaction_atomicity": 4,
    "resource_management": 4,
    "memory_management": 4,
    "readability": 5,
    "clean_code": 5,
    "maintainability_design": 5,
    "test_coverage": 5,
}


def load_env_file(path):
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


def read_json(path, default=None):
    target = Path(path)
    if not target.exists():
        return default
    return json.loads(target.read_text(encoding="utf-8"))


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def read_code(args):
    if args.file:
        path = Path(args.file)
        return path.read_text(encoding=args.encoding), str(path)
    if not sys.stdin.isatty():
        return sys.stdin.read(), "stdin"
    print("Paste BE analysis request JSON, then press Ctrl+Z and Enter when finished.")
    return sys.stdin.read(), "stdin"


def normalize_recent_issue(data, index):
    if not isinstance(data, dict):
        raise RuntimeError(f"recent_issues[{index}] must be a JSON object.")
    if set(data) != set(RECENT_ISSUE_FIELDS):
        raise RuntimeError(
            f"recent_issues[{index}] fields must be exactly: {', '.join(RECENT_ISSUE_FIELDS)}."
        )

    issue = {}
    for field in ("dataset", "title", "code", "guide"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"recent_issues[{index}].{field} must be a non-empty string.")
        issue[field] = value.strip()

    raw_directions = data.get("learning_directions")
    if not isinstance(raw_directions, list):
        raise RuntimeError(f"recent_issues[{index}].learning_directions must be a non-empty array.")
    directions = []
    for value in raw_directions:
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(
                f"recent_issues[{index}].learning_directions must contain non-empty strings."
            )
        text = value.strip()
        if text not in directions:
            directions.append(text)
    if not directions:
        raise RuntimeError(f"recent_issues[{index}].learning_directions must be a non-empty array.")
    issue["learning_directions"] = directions

    return {field: issue[field] for field in RECENT_ISSUE_FIELDS}


def read_analysis_request(args):
    raw_text, source_name = read_code(args)
    if not raw_text.strip():
        raise RuntimeError("BE analysis request JSON is empty.")
    try:
        request = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("BE analysis request must be valid JSON.") from exc
    if not isinstance(request, dict):
        raise RuntimeError("BE analysis request must be one JSON object.")
    if set(request) != ANALYSIS_REQUEST_FIELDS:
        raise RuntimeError("BE analysis request fields must be exactly: code, learning_context.")

    code = request.get("code")
    learning_context = request.get("learning_context")
    if not isinstance(code, str) or not code.strip():
        raise RuntimeError("BE analysis request code must be a non-empty string.")
    if not isinstance(learning_context, dict):
        raise RuntimeError("BE analysis request learning_context must be an object.")
    if set(learning_context) != LEARNING_CONTEXT_FIELDS:
        raise RuntimeError("BE analysis request learning_context fields must be exactly: recent_issues.")

    raw_issues = learning_context.get("recent_issues")
    if not isinstance(raw_issues, list):
        raise RuntimeError("BE analysis request recent_issues must be an array.")
    recent_issues = [normalize_recent_issue(item, index) for index, item in enumerate(raw_issues)]
    return code, recent_issues, source_name


def add_line_numbers(code):
    lines = code.splitlines()
    width = max(2, len(str(len(lines))))
    return "\n".join(f"{index:>{width}} | {line}" for index, line in enumerate(lines, start=1))


def json_text(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_prompt(template, code, categories, recent_issues):
    replacements = {
        "{{categories_json}}": json_text(categories),
        "{{recent_issues_json}}": json_text(recent_issues),
        "{{code}}": add_line_numbers(code),
    }
    placeholder_pattern = re.compile(
        "|".join(re.escape(key) for key in sorted(replacements, key=len, reverse=True))
    )
    prompt = placeholder_pattern.sub(lambda match: replacements[match.group(0)], template)
    return f"{prompt.rstrip()}\n\n{CODE_CONTEXT_INSTRUCTION}"


def extract_text(response):
    if hasattr(response, "output_text") and response.output_text:
        return response.output_text
    if hasattr(response, "text") and response.text:
        return response.text
    return str(response)


def strip_json_fence(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def extract_json_object(text):
    stripped = strip_json_fence(text)
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def request_ai_feedback(code, categories, recent_issues, args):
    load_env_file(args.env_file)
    if not os.environ.get(GEMINI_API_KEY_ENV):
        raise RuntimeError(f"{GEMINI_API_KEY_ENV} is not set.")
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    prompt_template = read_text(args.prompt_file)
    prompt = build_prompt(prompt_template, code, categories, recent_issues)
    client = genai.Client()
    if hasattr(client, "interactions"):
        response = client.interactions.create(model=args.model, input=prompt)
    else:
        response = client.models.generate_content(model=args.model, contents=prompt)

    raw_text = extract_text(response)
    try:
        return json.loads(extract_json_object(raw_text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse Gemini response as JSON.\n\nRaw response:\n{raw_text}") from exc


def category_maps(categories):
    by_key = {
        item["key"]: item
        for item in categories
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    return by_key, set(by_key)


def normalize_text_list(value):
    if isinstance(value, list):
        return [item.strip().lstrip("#").strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip().lstrip("#").strip()]
    return []


def clean_learning_tag(value):
    tag = str(value).strip().lstrip("#").strip().replace("`", "")
    tag = re.sub(r"\([^)]*\)", "", tag).strip()
    tag = tag.replace("(", "").replace(")", "")
    for before, after in (
        ("Python ", ""),
        ("Python의 ", ""),
        ("파이썬의 ", ""),
        ("를 이용한 ", " "),
        ("을 이용한 ", " "),
        ("를 활용한 ", " "),
        ("을 활용한 ", " "),
        ("의 필요성", ""),
        ("의 중요성", ""),
        (" 처리 방법", " 처리"),
        (" 방법", ""),
        (" 학습", ""),
        (" 이해", ""),
        (" 숙지", ""),
        (" 개념", ""),
        (" 기법", ""),
    ):
        tag = tag.replace(before, after)
    for separator in (" 및 ", ",", " / "):
        if separator in tag:
            tag = tag.split(separator, 1)[0].strip()
    tag = re.sub(r"\s+", " ", tag).strip(" .,:;")
    return tag[:18].strip()


def normalize_learning_tags(value, fallback=None):
    raw_tags = normalize_text_list(value)
    if not raw_tags and fallback:
        raw_tags = normalize_text_list(fallback)
    tags = []
    for item in raw_tags:
        tag = clean_learning_tag(item)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 4:
            break
    return tags


def normalize_guide_text(title, description, guide, learning_directions):
    raw = guide.strip() if isinstance(guide, str) else ""
    if raw and all(marker in raw for marker in ("🚨 문제", "💡 해결", "✨ 핵심 원리")):
        return raw
    solution_title = learning_directions[0] if learning_directions else "권장 학습 방향 적용"
    return "\n".join(
        [
            f"🚨 문제: {title or '코드 문제'}",
            description or "코드 실행 결과에 영향을 주는 문제가 있습니다.",
            f"💡 해결: {solution_title}",
            description or "문제 원인과 실행 흐름을 확인하고 해당 상황을 처리해야 합니다.",
            "✨ 핵심 원리",
            description or "입력 조건과 실행 흐름을 먼저 확인하면 같은 오류의 반복을 줄일 수 있습니다.",
        ]
    )


def normalize_feedback(data, categories):
    by_key, valid_keys = category_maps(categories)
    if not isinstance(data, dict):
        data = {}
    raw_issues = data.get("issues", [])
    if not isinstance(raw_issues, list):
        raw_issues = []

    issues = []
    for index, item in enumerate(raw_issues):
        if not isinstance(item, dict):
            raise RuntimeError(f"Gemini issue[{index}] must be a JSON object.")
        if set(item) != OUTPUT_ISSUE_FIELDS:
            raise RuntimeError(f"Gemini issue[{index}] must contain exactly the 7 output fields.")
        dataset = item.get("dataset")
        if not isinstance(dataset, str) or dataset.strip() not in valid_keys:
            raise RuntimeError(f"Gemini issue[{index}].dataset is invalid.")
        dataset = dataset.strip()
        label = str(by_key[dataset].get("name", dataset)).strip()
        if not label:
            raise RuntimeError(f"Gemini issue[{index}] category label is empty.")
        for field in ("code", "label", "title", "description", "guide"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(f"Gemini issue[{index}].{field} must be a non-empty string.")
        title = item["title"].strip()
        description = item["description"].strip()
        learning_directions = normalize_learning_tags(item.get("learning_directions"))
        if not learning_directions:
            raise RuntimeError(
                f"Gemini issue[{index}].learning_directions must contain a valid learning tag."
            )
        issue = {
            "code": item["code"].strip(),
            "label": label,
            "title": title,
            "description": description,
            "learning_directions": learning_directions,
            "dataset": dataset,
            "guide": normalize_guide_text(title, description, item.get("guide"), learning_directions),
        }
        issues.append(issue)

    return {"issues": issues}


def build_api_response(feedback):
    return {"issues": feedback.get("issues", [])}


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze code from one BE JSON request.")
    parser.add_argument("file", nargs="?", help="BE request JSON file. If omitted, stdin is used.")
    parser.add_argument("--categories", default=DEFAULT_CATEGORIES, help=f"Categories JSON. default: {DEFAULT_CATEGORIES}")
    parser.add_argument("--prompt-file", default=DEFAULT_PROMPT_FILE, help=f"Prompt template. default: {DEFAULT_PROMPT_FILE}")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Gemini model. default: {DEFAULT_MODEL}")
    parser.add_argument("-o", "--output", help="Save result to this file.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"Env file. default: {DEFAULT_ENV_FILE}")
    parser.add_argument("--encoding", default="utf-8", help="Input file encoding. default: utf-8")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        categories = read_json(args.categories, [])
        code, recent_issues, _source_name = read_analysis_request(args)
        raw_feedback = request_ai_feedback(code, categories, recent_issues, args)
        output = json_text(build_api_response(normalize_feedback(raw_feedback, categories))) + "\n"
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
            print(f"Saved result: {args.output}")
        else:
            print(output)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
