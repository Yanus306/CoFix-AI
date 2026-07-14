import json
from pathlib import Path

from ai_feedback_pipeline import build_api_response, normalize_feedback
from issue_quiz import normalize_quiz


USER_ID = "user_042"
CODE = """def average(nums):
    total = 0
    for i in range(len(nums) + 1):
        total += nums[i]
    return total / len(nums)

numbers = input().split()
print(average(numbers))"""


def dump(title, value):
    print(f"=== {title} ===")
    print(json.dumps(value, ensure_ascii=False, indent=2))


categories = json.loads(Path("data/categories.json").read_text(encoding="utf-8"))

analysis_input = {
    "user_id": USER_ID,
    "code": CODE,
}

# Gemini 호출 대신, 동일한 프롬프트 규칙에 맞춰 작성한 다중 오류 분석 결과다.
simulated_analysis = {
    "user_id": USER_ID,
    "issues": [
        {
            "code": "total += nums[i]",
            "label": "자료형초기화",
            "title": "문자열과 정수를 더할 수 없음",
            "description": "input().split() 결과는 문자열이므로 정수 total에 바로 더하면 TypeError가 발생합니다.",
            "learning_directions": ["자료형 변환", "입력값 처리"],
            "dataset": "variable_type",
            "guide": "🚨 문제: 문자열과 정수를 더할 수 없음\n입력값이 문자열인 상태에서 정수 누적값에 더하고 있습니다.\n💡 해결: 정수 변환\n입력 직후 각 값을 int로 변환해야 합니다.\n✨ 핵심 원리\n산술 연산 전에 피연산자의 자료형을 일치시켜야 합니다.",
        },
        {
            "code": "return total / len(nums)",
            "label": "경계값",
            "title": "빈 입력에서 0으로 나눔",
            "description": "입력 목록이 비어 있으면 len(nums)가 0이 되어 ZeroDivisionError가 발생합니다.",
            "learning_directions": ["빈 입력 검사", "경계값 처리"],
            "dataset": "edge_case",
            "guide": "🚨 문제: 빈 입력에서 0으로 나눔\n빈 목록의 길이로 나누는 경로가 열려 있습니다.\n💡 해결: 빈 목록 검사\n평균 계산 전에 목록이 비었는지 확인해야 합니다.\n✨ 핵심 원리\n나눗셈의 분모가 0이 되는 경계 조건을 먼저 차단해야 합니다.",
        },
        {
            "code": "for i in range(len(nums) + 1):",
            "label": "경계검사",
            "title": "마지막 인덱스를 벗어남",
            "description": "반복 범위가 len(nums)까지 포함되어 nums[len(nums)] 접근에서 IndexError가 발생합니다.",
            "learning_directions": ["인덱스 범위", "반복문 경계"],
            "dataset": "buffer_boundary",
            "guide": "🚨 문제: 마지막 인덱스를 벗어남\n목록의 유효한 마지막 인덱스보다 한 칸 더 접근합니다.\n💡 해결: 반복 범위 수정\nrange(len(nums))까지만 순회해야 합니다.\n✨ 핵심 원리\n길이가 N인 목록의 유효한 인덱스는 0부터 N-1까지입니다.",
        },
    ],
    "db_update_json": {
        "category_updates": [
            {"category_key": "variable_type", "direction": "bad", "delta": 1},
            {"category_key": "edge_case", "direction": "bad", "delta": 1},
            {"category_key": "buffer_boundary", "direction": "bad", "delta": 1},
        ],
        "weakness_text_updates": {"add": [], "remove": []},
    },
    "recommended_problem": {},
}

normalized = normalize_feedback(simulated_analysis, categories, USER_ID)
analysis_output = build_api_response(normalized)
quiz_input = analysis_output["issues"][0]

# 첫 번째 이슈를 바탕으로 Gemini가 생성했다고 가정한 응답을 실제 정규화 함수에 통과시킨다.
simulated_quiz = {
    "question": "목록의 마지막 인덱스를 넘지 않도록 반복 범위에 사용할 식은?",
    "choices": [
        {"id": "A", "text": "range(len(nums)+1)"},
        {"id": "B", "text": "range(len(nums))"},
        {"id": "C", "text": "range(nums)"},
        {"id": "D", "text": "nums[-1]"},
    ],
    "answer": "B",
    "explanation": "길이가 N인 목록의 유효한 인덱스는 0부터 N-1이므로 range(len(nums))를 사용해야 합니다.",
}
quiz_output = normalize_quiz(simulated_quiz)

dump("ANALYSIS INPUT", analysis_input)
dump("ANALYSIS OUTPUT", analysis_output)
dump("QUIZ INPUT (issues[0])", quiz_input)
dump("QUIZ OUTPUT", quiz_output)
