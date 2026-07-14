import argparse
import json
import os
import sys
from pathlib import Path

from ai_feedback_pipeline import (
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
MAX_QUESTION_LENGTH = 120
MAX_CHOICE_LENGTH = 20


def compact_text(value, max_length):
    text = " ".join(str(value).split()).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def validate_issue(issue):
    if not issue["title"]:
        raise RuntimeError("Issue title is required.")
    if not issue["description"] and not issue["guide"]:
        raise RuntimeError("Issue description or guide is required.")
    if not issue["label"] and not issue["dataset"]:
        raise RuntimeError("Issue label or dataset is required.")
    return issue


def read_issue(args):
    if args.file:
        raw_text = Path(args.file).read_text(encoding=args.encoding)
    elif not sys.stdin.isatty():
        raw_text = sys.stdin.read()
    else:
        print("Paste one issue JSON, then press Ctrl+Z and Enter when finished.")
        raw_text = sys.stdin.read()

    if not raw_text.strip():
        raise RuntimeError("Input issue JSON is empty.")

    data = json.loads(raw_text)
    if isinstance(data, list):
        if not data:
            raise RuntimeError("Input issue list is empty.")
        data = data[0]
    if isinstance(data, dict) and isinstance(data.get("issue"), dict):
        data = data["issue"]
    if not isinstance(data, dict):
        raise RuntimeError("Input must be one issue JSON object.")

    learning_directions = data.get("learning_directions") or data.get("학습방향") or []
    if isinstance(learning_directions, str):
        learning_directions = [learning_directions]

    issue = {
        "code": str(data.get("code", "")).strip(),
        "label": str(data.get("label", "")).strip(),
        "title": str(data.get("title", "")).strip(),
        "description": str(data.get("description", "")).strip(),
        "learning_directions": [str(item).strip() for item in learning_directions if str(item).strip()],
        "dataset": str(data.get("dataset", "")).strip(),
        "guide": str(data.get("guide", "")).strip(),
    }
    return validate_issue(issue)


def build_prompt(issue):
    return f"""
너는 코드 학습용 단답형 4지선다 문제 생성 API다.
아래 코드 문제 카드 1개를 보고, 사용자가 같은 실수를 다시 하지 않도록 학습 문제 1개를 만든다.

반드시 JSON 객체만 출력한다.
마크다운, 코드블록, 추가 설명은 출력하지 않는다.

출력 규칙:
- 문제는 1개만 만든다.
- 선택지는 반드시 4개다.
- 정답은 반드시 1개다.
- 선택지는 모두 그럴듯해야 한다.
- 오답은 정답과 의미가 겹치면 안 된다.
- 오답은 정답의 상위 개념이나 하위 개념이면 안 된다.
- 정답은 입력 문제 카드의 `guide`가 직접 말한 해결책과 가장 정확히 일치해야 한다.
- 문제는 단순 암기가 아니라 원리 이해를 확인해야 한다.
- 원본 코드 전체 수정안은 출력하지 않는다.
- `question`은 UI 카드에 들어가는 짧은 단답형 문제로 쓴다.
- `question`은 120자 이내로 쓴다.
- `question`은 보통 "빈칸 [A]에 들어갈 값은?", "이 오류를 막기 위해 먼저 확인할 값은?" 같은 형태로 만든다.
- 긴 상황 설명, 장황한 배경 설명, 해설성 문단을 question에 넣지 않는다.
- `choices.text`는 단답형이어야 한다.
- `choices.text`는 20자 이내의 짧은 답 후보로 쓴다.
- 선택지에는 문장형 설명을 넣지 않는다.
- 좋은 선택지 예: `is not None`, `None 검사`, `Set`, `has`, `str(result)`
- 나쁜 선택지 예: `None 타입은 숫자 타입과 직접적인 산술 연산을 지원하지 않아 타입 에러가 발생합니다.`
- `choices`는 A, B, C, D 순서의 객체 배열로 만든다.
- `answer`는 A, B, C, D 중 하나만 쓴다.
- `explanation`은 왜 그 선택지가 정답인지 1~2문장으로 짧게 설명한다.

출력 JSON 형식:
{{
  "question": "짧은 단답형 문제",
  "choices": [
    {{"id": "A", "text": "짧은 답 A"}},
    {{"id": "B", "text": "짧은 답 B"}},
    {{"id": "C", "text": "짧은 답 C"}},
    {{"id": "D", "text": "짧은 답 D"}}
  ],
  "answer": "A",
  "explanation": "정답 설명"
}}

입력 문제 카드:
{json_text(issue)}
""".strip()


def request_quiz(issue, args):
    load_env_file(args.env_file)
    if not os.environ.get(GEMINI_API_KEY_ENV):
        raise RuntimeError(f"{GEMINI_API_KEY_ENV} is not set.")

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    client = genai.Client()
    prompt = build_prompt(issue)
    if hasattr(client, "interactions"):
        response = client.interactions.create(model=args.model, input=prompt)
    else:
        response = client.models.generate_content(model=args.model, contents=prompt)

    raw_text = extract_text(response)
    try:
        return json.loads(extract_json_object(raw_text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse Gemini response as JSON.\n\nRaw response:\n{raw_text}") from exc


def normalize_quiz(data):
    if not isinstance(data, dict):
        data = {}

    question = compact_text(data.get("question", ""), MAX_QUESTION_LENGTH)
    answer = str(data.get("answer", "")).strip().upper()
    explanation = str(data.get("explanation", "")).strip()

    raw_choices = data.get("choices", [])
    if not isinstance(raw_choices, list):
        raw_choices = []
    if len(raw_choices) != 4:
        raise RuntimeError("Quiz response must contain exactly 4 choices.")

    choices = []
    for item in raw_choices:
        if not isinstance(item, dict):
            raise RuntimeError("Quiz choice must be an object with id and text.")
        choice_id = str(item.get("id", "")).strip().upper()
        if choice_id not in CHOICE_IDS:
            raise RuntimeError("Quiz choice id must be one of A, B, C, D.")
        text = compact_text(item.get("text", ""), MAX_CHOICE_LENGTH)
        choices.append({"id": choice_id, "text": text})

    if {choice["id"] for choice in choices} != set(CHOICE_IDS):
        raise RuntimeError("Quiz choice ids must contain A, B, C, D exactly once.")
    if answer not in CHOICE_IDS:
        raise RuntimeError("Quiz answer must be one of A, B, C, D.")
    if not question:
        raise RuntimeError("Quiz question is empty.")
    if not explanation:
        raise RuntimeError("Quiz explanation is empty.")

    by_id = {choice["id"]: choice for choice in choices}
    choices = [by_id.get(choice_id, {"id": choice_id, "text": ""}) for choice_id in CHOICE_IDS]
    if any(not choice["text"] for choice in choices):
        raise RuntimeError("Quiz choices must not be empty.")
    if len({choice["text"] for choice in choices}) != 4:
        raise RuntimeError("Quiz choices must be distinct.")

    return {
        "question": question,
        "choices": choices,
        "answer": answer,
        "explanation": explanation,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Generate one short-answer 4-choice quiz from one code issue JSON.")
    parser.add_argument("file", nargs="?", help="Issue JSON file path. If omitted, stdin is used.")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Gemini model. default: {DEFAULT_MODEL}")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"Env file path. default: {DEFAULT_ENV_FILE}")
    parser.add_argument("--encoding", default="utf-8", help="Input file encoding. default: utf-8")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        issue = read_issue(args)
        quiz = normalize_quiz(request_quiz(issue, args))
        print(json_text(quiz))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
