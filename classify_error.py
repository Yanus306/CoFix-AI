import argparse
import json
import os
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_ENV_FILE = ".env"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_CATEGORIES = "data/categories.json"
DEFAULT_PROFILE = "data/user_profile.json"


SYNTAX_GROUP = {
    "syntax_structure", "variable_type", "scope_lifetime", "operator_logic",
    "assignment_mutability", "type_annotation", "string_handling",
    "array_collection", "data_format_parsing", "null_missing_value",
    "data_validation", "function_usage", "api_misuse", "side_effect",
    "conditional", "loop_control", "edge_case", "state_management",
    "exception_handling", "error_propagation", "logging_diagnostics",
    "dependency_config", "io_network",
}

ALGORITHM_GROUP = {
    "time_complexity", "space_complexity", "algo_selection",
    "data_structure_choice", "recursion", "memory_management",
    "resource_management", "buffer_boundary", "tensor_matrix_shape",
    "concurrency", "async_handling", "transaction_atomicity",
    "database_query", "performance_runtime",
}

CLEANCODE_GROUP = {
    "security_input", "auth_access_control", "secret_handling",
    "crypto_randomness", "test_coverage", "readability", "clean_code",
    "maintainability_design",
}

# .env 파일이 존재하면 KEY=VALUE 값을 읽어서 환경변수(os.environ)에 등록한다.
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

# 지정한 JSON 파일을 읽어서 Python 데이터(dict/list)로 변환한다.
def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

# Python 데이터를 JSON 문자열로 변환해서 지정한 파일에 저장한다.
def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# 파일 경로가 있으면 파일에서 코드를 읽고, 없으면 터미널 표준입력(stdin)으로 코드를 입력받는다.
def read_text_input(path, encoding):
    if path:
        return Path(path).read_text(encoding=encoding)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    print("Paste the old code part, then press Ctrl+Z and Enter.")
    return sys.stdin.read()

# 코드와 오답 유형 목록을 바탕으로 Gemini에게 보낼 분류 요청 프롬프트를 생성한다.
def build_prompt(code, categories, instruction):
    category_text = "\n".join(f"{item['key']}: {item['condition']}" for item in categories)
    extra = f"\nRequest: {instruction}" if instruction else ""
    return f"""
Classify old code. Return only JSON:
{{"labels":["category_key"]}}
Multiple labels allowed. Use only valid keys.{extra}
Categories:
{category_text}
Code:
{code}
""".strip()

# Gemini 응답이 ```json 같은 코드블록으로 감싸져 있으면 제거하고 순수 JSON 문자열만 남긴다.
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

# Gemini API 응답 객체에서 실제 텍스트 결과를 추출한다.
def extract_text(response):
    if hasattr(response, "output_text") and response.output_text:
        return response.output_text
    if hasattr(response, "text") and response.text:
        return response.text
    return str(response)

# Gemini API에 코드를 보내 오답 유형 라벨을 요청하고, 응답을 labels 리스트로 변환한다.
def request_labels(code, categories, args):
    load_env_file(args.env_file)
    if not os.environ.get(GEMINI_API_KEY_ENV):
        raise RuntimeError(f"{GEMINI_API_KEY_ENV} is not set.")

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    client = genai.Client()
    prompt = build_prompt(code, categories, args.instruction)

    if hasattr(client, "interactions"):
        response = client.interactions.create(model=args.model, input=prompt)
    else:
        response = client.models.generate_content(model=args.model, contents=prompt)

    data = json.loads(strip_json_fence(extract_text(response)))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        labels = data.get("labels", [])
        if isinstance(labels, str):
            return [labels]
        return labels if isinstance(labels, list) else []
    if isinstance(data, str):
        return [data]
    return []

# Gemini가 반환한 라벨 중 categories에 존재하는 유효한 key만 남기고 중복을 제거한다.
def normalize_labels(labels, categories):
    valid_keys = {item["key"] for item in categories}
    normalized = []
    seen = set()
    for label in labels:
        key = str(label).strip()
        if key in valid_keys and key not in seen:
            normalized.append(key)
            seen.add(key)
    return normalized

# 개별 오답 key들의 누적값을 기준으로 문법/알고리즘/클린코드 그룹 합계를 다시 계산한다.
def recompute_rollups(profile):
    profile["syntax_fail_count"] = sum(int(profile.get(key, 0)) for key in SYNTAX_GROUP)
    profile["algorithm_fail_count"] = sum(int(profile.get(key, 0)) for key in ALGORITHM_GROUP)
    profile["cleancode_fail_count"] = sum(int(profile.get(key, 0)) for key in CLEANCODE_GROUP)

# 분류된 라벨을 사용자 프로필에 누적하고 전체 제출 수, 전체 오류 수, 그룹별 오류 수를 갱신한다.
def update_profile(profile, labels):
    for key in labels:
        profile[key] = int(profile.get(key, 0)) + 1
    profile["total_submit_count"] = int(profile.get("total_submit_count", 0)) + 1
    profile["total_error_count"] = int(profile.get("total_error_count", 0)) + len(labels)
    recompute_rollups(profile)
    return profile

# 분류된 라벨과 갱신된 프로필 통계를 사람이 읽기 좋은 Markdown 형식으로 만든다.
def render(labels, categories, profile, updated):
    names = {item["key"]: item["name"] for item in categories}
    output = ["## Labels", ""]
    output.extend(f"- {key}: {names.get(key, key)}" for key in labels) if labels else output.append("- No labels")
    output.extend(["", "## Profile", "", f"- updated: {str(updated).lower()}"])
    output.append(f"- total_submit_count: {profile.get('total_submit_count', 0)}")
    output.append(f"- total_error_count: {profile.get('total_error_count', 0)}")
    output.append(f"- syntax_fail_count: {profile.get('syntax_fail_count', 0)}")
    output.append(f"- algorithm_fail_count: {profile.get('algorithm_fail_count', 0)}")
    output.append(f"- cleancode_fail_count: {profile.get('cleancode_fail_count', 0)}")
    return "\n".join(output) + "\n"

# 터미널에서 입력받을 파일 경로, 추가 지시, 모델, 카테고리 파일, 프로필 파일, env 파일, 인코딩, 업데이트 여부 옵션을 설정하고 args로 반환한다.
def parse_args():
    parser = argparse.ArgumentParser(description="Classify old code parts into multi-hot error labels.")
    parser.add_argument("file", nargs="?", help="Old code part file. If omitted, stdin is used.")
    parser.add_argument("-i", "--instruction", help="Extra classification instruction.")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Gemini model. default: {DEFAULT_MODEL}")
    parser.add_argument("--categories", default=DEFAULT_CATEGORIES, help=f"Categories JSON. default: {DEFAULT_CATEGORIES}")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help=f"User profile JSON. default: {DEFAULT_PROFILE}")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"Env file path. default: {DEFAULT_ENV_FILE}")
    parser.add_argument("--encoding", default="utf-8", help="Input file encoding. default: utf-8")
    parser.add_argument("--no-update", action="store_true", help="Classify only; do not update the profile JSON.")
    return parser.parse_args()

# 프로그램의 전체 실행 흐름을 담당하며, 코드 입력 → 라벨 분류 → 프로필 갱신 → 결과 출력을 처리한다.
def main():
    args = parse_args()
    try:
        categories = read_json(args.categories)
        profile = read_json(args.profile)
        code = read_text_input(args.file, args.encoding)
        if not code.strip():
            raise RuntimeError("Input code is empty.")

        labels = normalize_labels(request_labels(code, categories, args), categories)
        if not args.no_update:
            profile = update_profile(profile, labels)
            write_json(args.profile, profile)

        print(render(labels, categories, profile, not args.no_update))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0

# 이 파일을 직접 실행했을 때 main 함수를 실행하고 종료 코드를 시스템에 전달한다.
if __name__ == "__main__":
    raise SystemExit(main())
