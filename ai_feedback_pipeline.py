import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_ENV_FILE = ".env"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_CATEGORIES = "data/categories.json"
DEFAULT_PROFILE = "data/user_profile.json"
DEFAULT_WEAKNESS_TEXTS = "data/weakness_texts.json"
DEFAULT_PROMPT_FILE = "prompts/full_feedback_pipeline_prompt.md"


SYNTAX_GROUP = {
    "syntax_structure", "variable_type", "scope_lifetime", "operator_logic",
    "assignment_mutability", "type_annotation", "string_handling",
    "array_collection", "data_format_parsing", "null_missing_value",
    "data_validation", "function_usage", "api_misuse", "side_effect",
    "conditional", "loop_control", "edge_case", "state_management",
    "exception_handling", "error_propagation", "logging_diagnostics",
    "dependency_config", "io_network", "ui_dom_rendering",
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


STYLE_ONLY_MARKERS = ("불필요", "관례", "스타일", "권장")
REAL_ERROR_MARKERS = ("SyntaxError", "실행 불가능", "컴파일 불가능", "파싱 실패", "오류가 발생")

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
        return copy.deepcopy(default)
    return json.loads(target.read_text(encoding="utf-8"))


def write_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(target)
    finally:
        temp_path.unlink(missing_ok=True)


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def read_code(args):
    if args.file:
        path = Path(args.file)
        return path.read_text(encoding=args.encoding), str(path)

    if not sys.stdin.isatty():
        return sys.stdin.read(), "stdin"

    print("Paste code, then press Ctrl+Z and Enter when finished.")
    return sys.stdin.read(), "stdin"


def read_analysis_request(args, default_user_id=None):
    raw_text, source_name = read_code(args)
    stripped = raw_text.strip()
    request = None

    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and "user_id" in parsed and "code" in parsed:
            request = parsed

    if request is None:
        user_id = str(args.user_id or default_user_id or "").strip()
        return user_id, raw_text, source_name

    user_id = request.get("user_id")
    code = request.get("code")
    if not isinstance(user_id, str) or not user_id.strip():
        raise RuntimeError("BE request user_id must be a non-empty string.")
    if not isinstance(code, str) or not code.strip():
        raise RuntimeError("BE request code must be a non-empty string.")

    user_id = user_id.strip()
    cli_user_id = str(args.user_id or "").strip()
    if cli_user_id and cli_user_id != user_id:
        raise RuntimeError("BE request user_id does not match --user-id.")
    return user_id, code, source_name


def add_line_numbers(code):
    lines = code.splitlines()
    width = max(2, len(str(len(lines))))
    return "\n".join(f"{index:>{width}} | {line}" for index, line in enumerate(lines, start=1))


def json_text(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


def weakness_list_for_user(weakness_db, user_id):
    if isinstance(weakness_db, dict):
        items = weakness_db.get(user_id, [])
    elif isinstance(weakness_db, list):
        items = [item for item in weakness_db if item.get("user_id") in (None, user_id)]
    else:
        items = []

    return [item for item in items if item.get("active", True)]


def top_category_stats(profile, categories, limit=5):
    rows = []
    for item in categories:
        key = item.get("key", "")
        try:
            count = int(profile.get(key, 0))
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            rows.append({
                "category_key": key,
                "category_name": item.get("name", key),
                "count": count,
            })

    rows.sort(key=lambda item: (-item["count"], item["category_key"]))
    return rows[:limit]


def recent_weakness_texts(weakness_texts, limit=5):
    rows = [dict(item) for item in weakness_texts if isinstance(item, dict)]
    rows.sort(
        key=lambda item: str(item.get("last_seen_at") or item.get("created_at") or ""),
        reverse=True,
    )
    return rows[:limit]


def duplicate_weakness_texts(weakness_texts, limit=5):
    rows = []
    for item in weakness_texts:
        if not isinstance(item, dict):
            continue
        try:
            count = int(item.get("count", 1))
        except (TypeError, ValueError):
            count = 1
        if count > 1:
            row = dict(item)
            row["count"] = count
            rows.append(row)

    rows.sort(key=lambda item: str(item.get("last_seen_at") or item.get("created_at") or ""), reverse=True)
    rows.sort(key=lambda item: -int(item.get("count", 1)))
    return rows[:limit]


def build_prompt(template, user_id, code, categories, profile, weakness_texts):
    replacements = {
        "{{categories_json}}": json_text(categories),
        "{{user_error_stats_json}}": json_text(profile),
        "{{weakness_texts_json}}": json_text(weakness_texts),
        "{{top_error_categories_json}}": json_text(top_category_stats(profile, categories)),
        "{{recent_weakness_texts_json}}": json_text(recent_weakness_texts(weakness_texts)),
        "{{duplicate_weakness_texts_json}}": json_text(duplicate_weakness_texts(weakness_texts)),
        "{{user_id}}": user_id,
        "{{code}}": add_line_numbers(code),
    }

    prompt = template
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)
    return prompt


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


def request_ai_feedback(user_id, code, categories, profile, weakness_texts, args):
    load_env_file(args.env_file)
    if not os.environ.get(GEMINI_API_KEY_ENV):
        raise RuntimeError(f"{GEMINI_API_KEY_ENV} is not set.")

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    prompt_template = read_text(args.prompt_file)
    prompt = build_prompt(prompt_template, user_id, code, categories, profile, weakness_texts)

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
    by_key = {item["key"]: item for item in categories}
    valid_keys = set(by_key)
    return by_key, valid_keys


def normalize_delta(direction, raw_delta):
    if direction == "good":
        return -1
    if direction == "bad":
        return 1
    return 0


def is_style_only_semicolon_issue(category_key, evidence="", reason="", text=""):
    combined = f"{evidence} {reason} {text}"
    if category_key != "syntax_structure":
        return False
    if ";" not in combined and "세미콜론" not in combined:
        return False
    if any(marker in combined for marker in REAL_ERROR_MARKERS):
        return False
    return any(marker in combined for marker in STYLE_ONLY_MARKERS)


def normalize_text_list(value):
    if isinstance(value, list):
        return [str(item).strip().lstrip("#").strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip().lstrip("#").strip()]
    return []


def clean_learning_tag(value):
    tag = str(value).strip().lstrip("#").strip()
    tag = tag.replace("`", "")
    tag = re.sub(r"\([^)]*\)", "", tag).strip()
    tag = tag.replace("(", "").replace(")", "")

    replacements = [
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
    ]
    for before, after in replacements:
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


def normalize_guide_text(title, description, report, guide):
    raw = str(guide or "").strip()
    if raw and all(marker in raw for marker in ("🚨 문제", "💡 해결", "✨ 핵심 원리")):
        raw = re.sub(r"\s*💡 해결:", "\n💡 해결:", raw)
        raw = re.sub(r"\s*✨ 핵심 원리\s*", "\n✨ 핵심 원리\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()

    learning_note = str(report.get("learning_direction_description") or report.get("learning_note") or "").strip()
    tags = normalize_learning_tags(report.get("learning_directions"))
    solution_title = tags[0] if tags else "권장 학습 방향 적용"
    solution_body = learning_note or "문제 원인을 먼저 분리한 뒤, 해당 상황을 처리하는 조건과 자료구조를 적용해야 합니다."
    principle = learning_note or description or "같은 유형의 오류는 입력 조건과 실행 흐름을 먼저 확인하면 반복을 줄일 수 있습니다."

    return "\n".join([
        f"🚨 문제: {title or '코드 문제'}",
        description or "코드 실행 결과에 영향을 주는 문제가 있습니다.",
        f"💡 해결: {solution_title}",
        solution_body,
        "✨ 핵심 원리",
        principle,
    ]).strip()


def default_problem_category(category_key, category_name):
    if category_key in {"time_complexity", "performance_runtime", "loop_control"}:
        return "성능 저하 우려"
    if category_key in {"null_missing_value", "variable_type", "exception_handling", "edge_case"}:
        return "런타임 오류 가능성"
    if category_key in {"data_validation", "security_input", "auth_access_control"}:
        return "검증 로직 부족"
    if category_key in {"readability", "clean_code", "maintainability_design"}:
        return "유지보수성 저하"
    if category_key in {"ui_dom_rendering", "state_management"}:
        return "상태 관리 문제"
    return category_name or "코드 문제"


def default_learning_directions(category_key, category_name):
    return DEFAULT_LEARNING_DIRECTIONS.get(category_key, [category_name or category_key])


def sort_reports_by_importance(reports):
    indexed_reports = list(enumerate(reports))
    indexed_reports.sort(
        key=lambda item: (
            CATEGORY_IMPORTANCE.get(str(item[1].get("category_key", "")).strip(), 50),
            item[0],
        )
    )
    return [report for _index, report in indexed_reports]


def normalize_feedback(data, categories, user_id):
    by_key, valid_keys = category_maps(categories)
    key_by_name = {str(item.get("name", "")).strip(): item.get("key") for item in categories}

    def resolve_category_key(item):
        raw_key = str(item.get("category_key") or item.get("dataset") or "").strip()
        if raw_key in valid_keys:
            return raw_key
        raw_label = str(item.get("label") or item.get("category_name") or "").strip()
        return key_by_name.get(raw_label, "")

    if not isinstance(data, dict):
        data = {}

    db_update = data.get("db_update_json", {})
    if not isinstance(db_update, dict):
        db_update = {}

    raw_updates = db_update.get("category_updates", [])
    if not isinstance(raw_updates, list):
        raw_updates = []

    category_updates = []
    for item in raw_updates:
        if not isinstance(item, dict):
            continue
        key = resolve_category_key(item)
        if key not in valid_keys:
            continue
        direction = str(item.get("direction", "")).strip().lower()
        delta = normalize_delta(direction, item.get("delta", 1))
        if delta == 0:
            continue
        evidence = str(item.get("evidence", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if is_style_only_semicolon_issue(key, evidence=evidence, reason=reason):
            continue

        category_updates.append({
            "category_key": key,
            "category_name": by_key[key].get("name", key),
            "direction": "good" if delta < 0 else "bad",
            "delta": delta,
            "line": str(item.get("line", "")).strip(),
            "evidence": evidence,
            "reason": reason,
        })

    weakness_updates = db_update.get("weakness_text_updates", {})
    if not isinstance(weakness_updates, dict):
        weakness_updates = {}

    add_items = []
    for item in weakness_updates.get("add", []):
        if not isinstance(item, dict):
            continue
        key = resolve_category_key(item)
        if key not in valid_keys:
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if is_style_only_semicolon_issue(key, text=text):
            continue
        add_items.append({
            "category_key": key,
            "category_name": by_key[key].get("name", key),
            "text": text[:35],
            "source_line": str(item.get("source_line", "")).strip(),
            "duplicate_of": item.get("duplicate_of"),
        })

    remove_items = []
    for item in weakness_updates.get("remove", []):
        if not isinstance(item, dict):
            continue
        remove_items.append({
            "id": str(item.get("id", "")).strip(),
            "text": str(item.get("text", "")).strip(),
            "reason": str(item.get("reason", "")).strip(),
        })

    raw_reports = data.get("error_part_reports", [])
    if not isinstance(raw_reports, list):
        raw_reports = []
    if not raw_reports and isinstance(data.get("issues"), list):
        raw_reports = data["issues"]

    reports = []
    for item in raw_reports:
        if not isinstance(item, dict):
            continue
        key = resolve_category_key(item)
        if key not in valid_keys:
            continue
        category_name = by_key[key].get("name", key)
        wrong_reason = str(item.get("wrong_reason", "")).strip()
        learning_note = str(item.get("learning_note", "")).strip()
        problem_category = str(item.get("problem_category", "")).strip() or default_problem_category(key, category_name)
        problem_title = str(item.get("problem_title") or item.get("title") or "").strip() or wrong_reason.splitlines()[0][:40] or category_name
        problem_content = (
            str(item.get("problem_content") or item.get("description") or "").strip()
            or wrong_reason
            or learning_note
            or f"{category_name} 문제가 코드 실행 결과에 영향을 줄 수 있습니다."
        )
        learning_direction_title = str(item.get("learning_direction_title", "")).strip() or "권장하는 학습 방향"
        learning_directions = normalize_learning_tags(item.get("learning_directions") or item.get("학습방향"))
        if not learning_directions:
            learning_directions = normalize_learning_tags(default_learning_directions(key, category_name))
        learning_direction_description = (
            str(item.get("learning_direction_description") or "").strip()
            or learning_note
        )
        guide = str(item.get("guide", "")).strip()
        reports.append({
            "line": str(item.get("line", "")).strip(),
            "category_key": key,
            "category_name": category_name,
            "error_part": str(item.get("error_part") or item.get("code") or "").strip(),
            "problem_category": problem_category,
            "problem_title": problem_title,
            "problem_content": problem_content,
            "learning_direction_title": learning_direction_title,
            "learning_directions": learning_directions,
            "learning_direction_description": learning_direction_description,
            "wrong_reason": wrong_reason,
            "learning_note": learning_note,
            "guide": guide,
            "display_text": str(item.get("display_text", "")).strip(),
        })

    reports = sort_reports_by_importance(reports)

    problem = data.get("recommended_problem", {})
    if not isinstance(problem, dict):
        problem = {}

    feedback = {
        "user_id": user_id,
        "db_update_json": {
            "category_updates": category_updates,
            "weakness_text_updates": {
                "add": add_items,
                "remove": remove_items,
            },
        },
        "error_part_reports": reports,
        "recommended_problem": problem,
    }
    feedback["issues"] = build_backend_issues(reports)
    return feedback


def build_backend_issues(reports):
    issues = []
    for report in reports:
        dataset = str(report.get("category_key", "")).strip()
        label = str(report.get("category_name", "")).strip()
        title = str(report.get("problem_title", "")).strip() or label or "코드 문제"
        description = (
            str(report.get("problem_content", "")).strip()
            or str(report.get("wrong_reason", "")).strip()
            or str(report.get("learning_note", "")).strip()
            or f"{title} 문제가 코드 실행 결과에 영향을 줄 수 있습니다."
        )
        learning_directions = normalize_learning_tags(
            report.get("learning_directions"),
            fallback=default_learning_directions(dataset, label),
        )
        guide = normalize_guide_text(
            title,
            description,
            report,
            str(report.get("guide") or report.get("display_text") or "").strip(),
        )

        issues.append({
            "code": str(report.get("error_part", "")).strip(),
            "label": label,
            "title": title,
            "description": description,
            "learning_directions": learning_directions,
            "dataset": dataset,
            "guide": guide,
        })
    return issues


def build_api_response(feedback):
    return {
        "user_id": str(feedback.get("user_id", "")).strip(),
        "issues": feedback.get("issues", []),
    }


def recompute_rollups(profile):
    profile["syntax_fail_count"] = sum(int(profile.get(key, 0)) for key in SYNTAX_GROUP)
    profile["algorithm_fail_count"] = sum(int(profile.get(key, 0)) for key in ALGORITHM_GROUP)
    profile["cleancode_fail_count"] = sum(int(profile.get(key, 0)) for key in CLEANCODE_GROUP)


def apply_profile_updates(profile, updates, categories):
    updated = copy.deepcopy(profile)
    _by_key, valid_keys = category_maps(categories)
    new_error_count = 0

    for item in updates:
        key = item["category_key"]
        if key not in valid_keys:
            continue
        delta = int(item.get("delta", 0))
        updated[key] = max(0, int(updated.get(key, 0)) + delta)
        if delta > 0:
            new_error_count += delta

    updated["total_submit_count"] = int(updated.get("total_submit_count", 0)) + 1
    updated["total_error_count"] = int(updated.get("total_error_count", 0)) + new_error_count
    recompute_rollups(updated)
    return updated


def ensure_weakness_db_shape(weakness_db):
    if isinstance(weakness_db, dict):
        return copy.deepcopy(weakness_db)
    return {}


def next_weakness_id(items):
    numbers = []
    for item in items:
        raw_id = str(item.get("id", ""))
        if raw_id.startswith("w_"):
            suffix = raw_id[2:]
            if suffix.isdigit():
                numbers.append(int(suffix))
    return f"w_{max(numbers, default=0) + 1:04d}"


def find_weakness(items, weakness_id):
    for item in items:
        if str(item.get("id", "")) == str(weakness_id):
            return item
    return None


def find_same_active_weakness(items, category_key, text):
    for item in items:
        if not item.get("active", True):
            continue
        if item.get("category_key") == category_key and item.get("text") == text:
            return item
    return None


def apply_weakness_updates(weakness_db, user_id, updates):
    db = ensure_weakness_db_shape(weakness_db)
    items = [dict(item) for item in db.get(user_id, []) if isinstance(item, dict)]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    for item in updates.get("remove", []):
        target = find_weakness(items, item.get("id"))
        if not target:
            continue
        target["active"] = False
        target["removed_at"] = now
        target["remove_reason"] = item.get("reason", "")

    for item in updates.get("add", []):
        duplicate_of = item.get("duplicate_of")
        target = find_weakness(items, duplicate_of) if duplicate_of else None
        if not target:
            target = find_same_active_weakness(items, item.get("category_key"), item.get("text"))

        if target:
            target["count"] = int(target.get("count", 1)) + 1
            target["last_seen_at"] = now
            target["active"] = True
            continue

        items.append({
            "id": next_weakness_id(items),
            "user_id": user_id,
            "category_key": item.get("category_key", ""),
            "category_name": item.get("category_name", ""),
            "text": item.get("text", ""),
            "source_line": item.get("source_line", ""),
            "count": 1,
            "active": True,
            "created_at": now,
            "last_seen_at": now,
        })

    db[user_id] = items
    return db


def render_json_block(title, data):
    return [title, "", "```json", json_text(data), "```", ""]


def render_error_reports(reports):
    output = ["## Error Part Reports", ""]
    if not reports:
        output.append("- 에러 파트가 없습니다.")
        output.append("")
        return output

    for index, report in enumerate(reports, start=1):
        output.append(f"### {index}. {report.get('problem_category') or report.get('category_name', '')}")
        output.append("")
        if report.get("line"):
            output.append(f"- 위치: {report['line']}")
        output.append(f"- 분류: {report.get('category_name', '')} ({report.get('category_key', '')})")
        output.append("")
        output.append("에러 파트:")
        output.append("")
        output.append("```")
        output.append(report.get("error_part", ""))
        output.append("```")
        output.append("")
        output.append(report.get("problem_title") or "문제 설명")
        if report.get("problem_content"):
            output.append(report["problem_content"])
        output.append("")
        output.append(report.get("learning_direction_title") or "권장하는 학습 방향")
        for item in report.get("learning_directions", []):
            output.append(f"# {item}")
        if report.get("learning_direction_description"):
            output.append(report["learning_direction_description"])
        output.append("")
        if report.get("display_text"):
            output.append("표시용 설명:")
            output.append(report["display_text"])
            output.append("")
    return output


def render_problem(problem):
    output = ["## Recommended Coding Problem", ""]
    if not problem:
        output.append("- 추천 문제가 없습니다.")
        output.append("")
        return output

    output.append(f"### {problem.get('title', '추천 문제')}")
    output.append("")
    if problem.get("level"):
        output.append(f"- 난이도: {problem.get('level')}")
    if problem.get("based_on_categories"):
        output.append(f"- 기반 분류: {', '.join(map(str, problem.get('based_on_categories', [])))}")
    output.append("")

    fields = [
        ("학습 목표", "learning_goal"),
        ("문제 설명", "statement"),
        ("입력 형식", "input"),
        ("출력 형식", "output"),
    ]
    for title, key in fields:
        value = str(problem.get(key, "")).strip()
        if value:
            output.extend([title, value, ""])

    for title, key in [
        ("제약 조건", "constraints"),
        ("채점 포인트", "grading_points"),
        ("주의할 실수", "common_mistakes"),
    ]:
        values = problem.get(key, [])
        if isinstance(values, list) and values:
            output.append(title)
            output.extend(f"- {value}" for value in values)
            output.append("")

    examples = problem.get("examples", [])
    if isinstance(examples, list) and examples:
        output.append("입출력 예시")
        for index, example in enumerate(examples, start=1):
            if not isinstance(example, dict):
                continue
            output.append(f"예시 {index} 입력:")
            output.append("```text")
            output.append(str(example.get("input", "")))
            output.append("```")
            output.append(f"예시 {index} 출력:")
            output.append("```text")
            output.append(str(example.get("output", "")))
            output.append("```")
            if example.get("explanation"):
                output.append("예시 설명:")
                output.append(str(example.get("explanation", "")))
            output.append("")

    return output


def render_markdown(feedback):
    output = []
    output.extend(render_json_block("## Backend Issues", feedback.get("issues", [])))
    output.extend(render_json_block("## DB Update JSON", feedback.get("db_update_json", {})))
    output.extend(render_error_reports(feedback.get("error_part_reports", [])))
    output.extend(render_problem(feedback.get("recommended_problem", {})))
    return "\n".join(output).rstrip() + "\n"


def parse_args():
    parser = argparse.ArgumentParser(description="Run the AI feedback pipeline for code learning.")
    parser.add_argument("file", nargs="?", help="Error code file path. If omitted, stdin is used.")
    parser.add_argument("--user-id", help="User id. default: profile user_id")
    parser.add_argument("--categories", default=DEFAULT_CATEGORIES, help=f"Categories JSON. default: {DEFAULT_CATEGORIES}")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help=f"User profile JSON. default: {DEFAULT_PROFILE}")
    parser.add_argument(
        "--weakness-texts",
        default=DEFAULT_WEAKNESS_TEXTS,
        help=f"Weakness text DB JSON. default: {DEFAULT_WEAKNESS_TEXTS}",
    )
    parser.add_argument(
        "--prompt-file",
        default=DEFAULT_PROMPT_FILE,
        help=f"Prompt template path. default: {DEFAULT_PROMPT_FILE}",
    )
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Gemini model. default: {DEFAULT_MODEL}")
    parser.add_argument("-o", "--output", help="Save result to this file.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"Env file path. default: {DEFAULT_ENV_FILE}")
    parser.add_argument("--encoding", default="utf-8", help="Input file encoding. default: utf-8")
    parser.add_argument("--no-update", action="store_true", help="Do not write profile/weakness JSON files.")
    parser.add_argument("--raw-json", action="store_true", help="Print normalized raw JSON instead of Markdown.")
    parser.add_argument("--issues-json", action="store_true", help="Print backend issues array only.")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        categories = read_json(args.categories, [])
        profile = read_json(args.profile, {})
        weakness_db = read_json(args.weakness_texts, {})
        user_id, code, _source_name = read_analysis_request(args, profile.get("user_id"))
        if not user_id:
            raise RuntimeError("user_id is required. Pass --user-id or set user_id in profile JSON.")
        if not code.strip():
            raise RuntimeError("Input code is empty.")

        weakness_texts = weakness_list_for_user(weakness_db, user_id)
        raw_feedback = request_ai_feedback(user_id, code, categories, profile, weakness_texts, args)
        feedback = normalize_feedback(raw_feedback, categories, user_id)

        if not args.no_update:
            updated_profile = apply_profile_updates(
                profile,
                feedback["db_update_json"]["category_updates"],
                categories,
            )
            updated_weakness_db = apply_weakness_updates(
                weakness_db,
                user_id,
                feedback["db_update_json"]["weakness_text_updates"],
            )
            write_json(args.profile, updated_profile)
            write_json(args.weakness_texts, updated_weakness_db)

        output = json_text(build_api_response(feedback)) + "\n"
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
