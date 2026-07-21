import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from ai_feedback_pipeline import (
    CATEGORY_IMPORTANCE,
    DEFAULT_ENV_FILE,
    DEFAULT_MODEL,
    GEMINI_API_KEY_ENV,
    extract_json_object,
    extract_text,
    json_text,
    load_env_file,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


CHOICE_IDS = ["A", "B", "C", "D"]
LEVELS = ["easy", "medium", "hard"]
TOPIC_ANGLES = ["concept", "application", "prevention"]
DEFAULT_PROBLEM_COUNT = 3
REPEATED_WEAKNESS_THRESHOLD = 3
MAX_QUESTION_LENGTH = 120
MAX_CHOICE_LENGTH = 20


def compact_text(value, max_length):
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def normalize_level(value):
    level = str(value or "").strip().lower()
    if level not in LEVELS:
        raise RuntimeError("selected_level must be one of easy, medium, hard.")
    return level


def lower_level(level):
    index = LEVELS.index(normalize_level(level))
    return LEVELS[max(0, index - 1)]


def normalize_learning_directions(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_source_issue(data):
    if not isinstance(data, dict):
        raise RuntimeError("Each issue must be a JSON object.")

    issue = {
        "dataset": data.get("dataset", "").strip() if isinstance(data.get("dataset"), str) else "",
        "title": data.get("title", "").strip() if isinstance(data.get("title"), str) else "",
        "learning_directions": normalize_learning_directions(data.get("learning_directions", [])),
        "code": data.get("code", "").strip() if isinstance(data.get("code"), str) else "",
        "guide": data.get("guide", "").strip() if isinstance(data.get("guide"), str) else "",
    }
    for field in ("dataset", "title", "learning_directions", "code", "guide"):
        if not issue[field]:
            raise RuntimeError(f"Issue {field} is required.")
    return issue


def validate_request(data):
    if not isinstance(data, dict):
        raise RuntimeError("Input must be one JSON object.")

    selected_level = normalize_level(data.get("selected_level"))
    problem_count = data.get("problem_count")
    if type(problem_count) is not int or problem_count != DEFAULT_PROBLEM_COUNT:
        raise RuntimeError("problem_count must be 3 in V1.")

    raw_issues = data.get("issues", [])
    if not isinstance(raw_issues, list) or not raw_issues:
        raise RuntimeError("issues must be a non-empty array.")

    return {
        "selected_level": selected_level,
        "problem_count": problem_count,
        "issues": [normalize_source_issue(item) for item in raw_issues],
    }


def read_request(args):
    if args.file:
        raw_text = Path(args.file).read_text(encoding=args.encoding)
    elif not sys.stdin.isatty():
        raw_text = sys.stdin.read()
    else:
        print("Paste quiz request JSON, then press Ctrl+Z and Enter when finished.")
        raw_text = sys.stdin.read()

    if not raw_text.strip():
        raise RuntimeError("Input quiz request JSON is empty.")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Input quiz request is not valid JSON.") from exc
    return validate_request(data)


def rank_issues(issues, selected_level):
    level = normalize_level(selected_level)
    counts = Counter(str(item.get("dataset", "")).strip() for item in issues)
    ranked = []
    for index, item in enumerate(issues):
        row = dict(item)
        dataset = str(row.get("dataset", "")).strip()
        frequency = counts[dataset]
        row["frequency"] = frequency
        row["effective_level"] = (
            lower_level(level) if frequency >= REPEATED_WEAKNESS_THRESHOLD else level
        )
        row["source_index"] = index
        ranked.append(row)

    ranked.sort(
        key=lambda item: (
            -item["frequency"],
            CATEGORY_IMPORTANCE.get(item.get("dataset", ""), 50),
            item["source_index"],
        )
    )
    return ranked


def build_topic_plan(ranked_issues, problem_count=DEFAULT_PROBLEM_COUNT):
    if not ranked_issues:
        raise RuntimeError("At least one ranked issue is required.")
    if problem_count != DEFAULT_PROBLEM_COUNT:
        raise RuntimeError("problem_count must be 3 in V1.")

    if len(ranked_issues) == 1:
        sources = [ranked_issues[0]] * problem_count
    else:
        sources = [ranked_issues[0]]
        top_dataset = sources[0].get("dataset")

        second = next(
            (item for item in ranked_issues[1:] if item.get("dataset") == top_dataset),
            ranked_issues[1],
        )
        sources.append(second)

        used_indexes = {item.get("source_index") for item in sources}
        used_datasets = {item.get("dataset") for item in sources}
        severity_order = sorted(
            ranked_issues,
            key=lambda item: (
                CATEGORY_IMPORTANCE.get(item.get("dataset", ""), 50),
                -item.get("frequency", 0),
                item.get("source_index", 0),
            ),
        )
        third = next(
            (item for item in severity_order if item.get("dataset") not in used_datasets),
            next(
                (item for item in severity_order if item.get("source_index") not in used_indexes),
                severity_order[0],
            ),
        )
        sources.append(third)

    return [
        {
            "angle": TOPIC_ANGLES[index],
            "dataset": source.get("dataset", ""),
            "title": source.get("title", ""),
            "learning_directions": source.get("learning_directions", []),
            "code": source.get("code", ""),
            "guide": source.get("guide", ""),
            "frequency": source.get("frequency", 1),
            "effective_level": source.get("effective_level", "easy"),
        }
        for index, source in enumerate(sources[:problem_count])
    ]


def build_prompt(request):
    ranked = rank_issues(request["issues"], request["selected_level"])
    topic_plan = build_topic_plan(ranked, request["problem_count"])
    return f"""
너는 사용자 약점 기반 개인화 코딩 학습 문제 생성 API다.
아래 주제 계획에 따라 단답형 4지선다 문제를 정확히 {request['problem_count']}개 만든다.

반드시 JSON 객체만 출력한다. 마크다운이나 추가 설명은 출력하지 않는다.

공통 규칙:
- 각 문제의 선택지는 정확히 A, B, C, D 네 개다.
- 정답은 정확히 하나이며 answer에는 선택지 ID만 쓴다.
- 같은 입력 이슈를 사용하더라도 문제의 질문 관점과 정답을 그대로 반복하지 않는다.
- 오답도 모두 그럴듯해야 하며 정답과 의미가 겹치면 안 된다.
- 오답은 정답의 상위 개념이나 하위 개념으로 만들지 않는다.
- 정답은 해당 주제의 guide가 말한 해결책과 직접 일치해야 한다.
- 단순 암기보다 오류 원인과 해결 원리의 이해를 확인한다.
- 원본 코드를 그대로 복사해 답을 노출하지 말고 같은 원리를 묻는 새로운 상황으로 바꾼다.
- 원본 코드 전체의 수정안은 출력하지 않는다.
- effective_level은 문제를 만드는 내부 기준이며 출력에는 난이도를 넣지 않는다.
- concept는 핵심 개념, application은 코드 적용이나 빈칸, prevention은 오류 예측이나 예방을 묻는다.
- 코드가 필요 없으면 code_block은 null이다.
- 코드가 필요하면 code_block에 language와 content를 넣는다.
- 함수나 컴포넌트 전체가 필요하면 content를 여러 줄로 충분히 길게 만든다.
- 코드 빈칸은 {{{{BLANK}}}} 토큰을 사용한다.
- question은 120자 이내, choices.text는 20자 이내다.
- explanation은 정답의 이유를 1~2문장으로 설명한다.

출력 JSON 형식:
{{
  "problems": [
    {{
      "question": "질문",
      "code_block": {{"language": "javascript", "content": "코드 또는 {{{{BLANK}}}}"}},
      "choices": [
        {{"id": "A", "text": "선택지 A"}},
        {{"id": "B", "text": "선택지 B"}},
        {{"id": "C", "text": "선택지 C"}},
        {{"id": "D", "text": "선택지 D"}}
      ],
      "answer": "A",
      "explanation": "정답 설명"
    }}
  ]
}}

주제 계획:
{json_text(topic_plan)}
""".strip()


def request_quiz_set(request, args):
    load_env_file(args.env_file)
    if not os.environ.get(GEMINI_API_KEY_ENV):
        raise RuntimeError(f"{GEMINI_API_KEY_ENV} is not set.")

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    client = genai.Client()
    prompt = build_prompt(request)
    if hasattr(client, "interactions"):
        response = client.interactions.create(model=args.model, input=prompt)
    else:
        response = client.models.generate_content(model=args.model, contents=prompt)

    raw_text = extract_text(response)
    try:
        return json.loads(extract_json_object(raw_text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse Gemini response as JSON.\n\nRaw response:\n{raw_text}") from exc


def normalize_code_block(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeError("Problem code_block must be null or an object.")
    raw_language = value.get("language")
    raw_content = value.get("content")
    language = raw_language.strip().lower() if isinstance(raw_language, str) else ""
    content = raw_content.strip() if isinstance(raw_content, str) else ""
    if not language or not content:
        raise RuntimeError("Problem code_block language and content are required.")
    return {"language": language, "content": content}


def normalize_problem(data):
    if not isinstance(data, dict):
        raise RuntimeError("Each problem must be a JSON object.")

    question = compact_text(data.get("question", ""), MAX_QUESTION_LENGTH)
    raw_answer = data.get("answer")
    raw_explanation = data.get("explanation")
    answer = raw_answer.strip().upper() if isinstance(raw_answer, str) else ""
    explanation = raw_explanation.strip() if isinstance(raw_explanation, str) else ""
    code_block = normalize_code_block(data.get("code_block"))

    raw_choices = data.get("choices", [])
    if not isinstance(raw_choices, list) or len(raw_choices) != 4:
        raise RuntimeError("Problem response must contain exactly 4 choices.")

    choices = []
    for item in raw_choices:
        if not isinstance(item, dict):
            raise RuntimeError("Problem choice must be an object with id and text.")
        raw_choice_id = item.get("id")
        choice_id = raw_choice_id.strip().upper() if isinstance(raw_choice_id, str) else ""
        if choice_id not in CHOICE_IDS:
            raise RuntimeError("Problem choice id must be one of A, B, C, D.")
        text = compact_text(item.get("text", ""), MAX_CHOICE_LENGTH)
        choices.append({"id": choice_id, "text": text})

    if {choice["id"] for choice in choices} != set(CHOICE_IDS):
        raise RuntimeError("Problem choice ids must contain A, B, C, D exactly once.")
    if answer not in CHOICE_IDS:
        raise RuntimeError("Problem answer must be one of A, B, C, D.")
    if not question:
        raise RuntimeError("Problem question is empty.")
    if not explanation:
        raise RuntimeError("Problem explanation is empty.")

    by_id = {choice["id"]: choice for choice in choices}
    choices = [by_id[choice_id] for choice_id in CHOICE_IDS]
    if any(not choice["text"] for choice in choices):
        raise RuntimeError("Problem choices must not be empty.")
    if len({choice["text"] for choice in choices}) != 4:
        raise RuntimeError("Problem choices must be distinct.")

    return {
        "question": question,
        "code_block": code_block,
        "choices": choices,
        "answer": answer,
        "explanation": explanation,
    }


def normalize_quiz_set(data, problem_count=DEFAULT_PROBLEM_COUNT):
    if type(problem_count) is not int or problem_count != DEFAULT_PROBLEM_COUNT:
        raise RuntimeError("problem_count must be 3 in V1.")
    if not isinstance(data, dict):
        raise RuntimeError("Quiz response must be one JSON object.")
    raw_problems = data.get("problems", [])
    if not isinstance(raw_problems, list) or len(raw_problems) != problem_count:
        raise RuntimeError(f"Quiz response must contain exactly {problem_count} problems.")
    problems = [normalize_problem(item) for item in raw_problems]
    signatures = {
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for item in problems
    }
    if len(signatures) != problem_count:
        raise RuntimeError("Quiz response problems must be distinct.")
    return {"problems": problems}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate three personalized 4-choice coding problems from recent issue JSON."
    )
    parser.add_argument("file", nargs="?", help="Quiz request JSON file. If omitted, stdin is used.")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Gemini model. default: {DEFAULT_MODEL}")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"Env file path. default: {DEFAULT_ENV_FILE}")
    parser.add_argument("--encoding", default="utf-8", help="Input file encoding. default: utf-8")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        request = read_request(args)
        raw_quiz_set = request_quiz_set(request, args)
        quiz_set = normalize_quiz_set(raw_quiz_set, request["problem_count"])
        print(json_text(quiz_set))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
