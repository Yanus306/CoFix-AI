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
DEFAULT_PROBLEM_BANK = "data/problem_bank.json"

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

# 사용자 프로필에서 누적 오류 수가 높은 오답 유형을 정렬해 상위 limit개만 반환한다.
def top_issues(profile, categories, limit):
    if limit <= 0:
        return []

    rows = []
    for item in categories:
        key = item["key"]
        count = int(profile.get(key, 0))
        if count > 0:
            rows.append((key, count))
    rows.sort(key=lambda item: (-item[1], item[0]))
    return rows[:limit]

# 문제 은행에 해당 오류 유형 문제가 없을 때 사용할 기본 연습 문제를 생성한다.
def make_fallback_problem(key):
    return {
        "title": f"{key} 연습 문제",
        "level": "custom",
        "tags": [key],
        "statement": f"{key} 오류 유형을 연습할 수 있는 코딩 문제를 해결하라.",
        "input": "문제에 필요한 입력이 주어진다.",
        "output": "요구한 결과를 출력한다.",
        "constraints": ["입력 조건을 코드에서 검증할 것"],
        "examples": [{"input": "예시 입력", "output": "예시 출력"}],
    }

# 상위 오답 유형에 맞는 문제를 로컬 문제 은행에서 선택하고, 없으면 기본 문제를 대신 사용한다.
def pick_local_problems(issues, problems, per_issue):
    if per_issue <= 0:
        return []

    selected = []
    used_titles = set()
    for key, _count in issues:
        matches = [problem for problem in problems if key in problem.get("tags", [])]
        if not matches:
            matches = [make_fallback_problem(key)]

        picked = 0
        for problem in matches:
            title = problem["title"]
            if title in used_titles:
                continue
            selected.append((key, problem))
            used_titles.add(title)
            picked += 1
            if picked >= per_issue:
                break
    return selected

# 상위 오답 유형과 카테고리 정보를 바탕으로 Gemini에게 새 코딩 문제 생성을 요청할 프롬프트를 만든다.
def build_ai_prompt(issues, categories, per_issue):
    by_key = {item["key"]: item for item in categories}
    issue_lines = []
    for key, count in issues:
        item = by_key.get(key, {"name": key, "condition": ""})
        issue_lines.append(f"- key={key}, count={count}, name={item['name']}, condition={item['condition']}")

    return f"""
Create coding problems for these learner weakness categories.
Return only JSON:
{{"problems":[{{"issue_key":"category_key","title":"title","level":"easy|medium|hard","statement":"problem statement","input":"input format","output":"output format","constraints":["constraint"],"examples":[{{"input":"sample input","output":"sample output"}}]}}]}}
Rules:
- Make exactly {len(issues) * per_issue} problems.
- Use only the given issue_key values.
- Problems must be solvable by writing code, not explanation.
- Include concrete input, output, constraints, and sample I/O.
- Sample outputs must be correct for the sample inputs.
- Do not include solutions.

Issues:
{chr(10).join(issue_lines)}
""".strip()

# Gemini API에 약점 기반 코딩 문제 생성을 요청하고, 응답 문제들을 검증된 문제 리스트로 변환한다.
def request_ai_problems(issues, categories, args):
    load_env_file(args.env_file)
    if not os.environ.get(GEMINI_API_KEY_ENV):
        raise RuntimeError(f"{GEMINI_API_KEY_ENV} is not set.")

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    client = genai.Client()
    prompt = build_ai_prompt(issues, categories, args.per_issue)

    if hasattr(client, "interactions"):
        response = client.interactions.create(model=args.model, input=prompt)
    else:
        response = client.models.generate_content(model=args.model, contents=prompt)

    data = json.loads(strip_json_fence(extract_text(response)))
    problems = data.get("problems", [])
    valid_issue_keys = {key for key, _count in issues}
    selected = []

    for problem in problems:
        if not isinstance(problem, dict):
            continue
        issue_key = str(problem.get("issue_key", "")).strip()
        if issue_key not in valid_issue_keys:
            continue
        selected.append((issue_key, normalize_problem(problem, issue_key)))

    return selected

# Gemini나 문제 은행에서 가져온 문제 데이터를 일정한 문제 형식으로 정리한다.
def normalize_problem(problem, issue_key):
    examples = problem.get("examples", [])
    if not isinstance(examples, list):
        examples = []
    constraints = problem.get("constraints", [])
    if not isinstance(constraints, list):
        constraints = [str(constraints)]

    return {
        "title": str(problem.get("title", f"{issue_key} 연습 문제")),
        "level": str(problem.get("level", "custom")),
        "tags": problem.get("tags", [issue_key]) if isinstance(problem.get("tags", [issue_key]), list) else [issue_key],
        "statement": str(problem.get("statement", "")),
        "input": str(problem.get("input", "")),
        "output": str(problem.get("output", "")),
        "constraints": [str(item) for item in constraints],
        "examples": examples,
    }

# 문제 예시 입력과 출력 목록을 Markdown 코드블록 형식으로 변환한다.
def render_examples(examples):
    lines = []
    for index, example in enumerate(examples, start=1):
        if not isinstance(example, dict):
            continue
        lines.extend(
            [
                f"   예시 {index} 입력:",
                "   ```text",
                indent_block(example.get("input", "")),
                "   ```",
                f"   예시 {index} 출력:",
                "   ```text",
                indent_block(example.get("output", "")),
                "   ```",
            ]
        )
    return lines

# 여러 줄 텍스트 앞에 공백을 붙여 Markdown 안에서 보기 좋게 들여쓰기한다.
def indent_block(text):
    return "\n".join(f"   {line}" for line in str(text).splitlines())

# 상위 오답 유형과 추천 문제 목록을 사람이 읽기 좋은 Markdown 형식으로 만든다.
def render(issues, problems, categories, source, warning=None):
    names = {item["key"]: item["name"] for item in categories}
    output = ["## Top Issues", ""]
    if not issues:
        output.append("- No issue counts yet")
    else:
        for index, (key, count) in enumerate(issues, start=1):
            output.append(f"{index}. {key} ({names.get(key, key)}): {count}")

    output.extend(["", "## Coding Problems", "", f"- source: {source}"])
    if warning:
        output.append(f"- warning: {warning}")
    output.append("")

    if not problems:
        output.append("- No coding problems. Run classify_error.py first.")
    else:
        for index, (issue_key, problem) in enumerate(problems, start=1):
            constraints = problem.get("constraints", [])
            output.append(f"### {index}. {problem['title']}")
            output.append("")
            output.append(f"- 대상 오류: {names.get(issue_key, issue_key)}")
            output.append(f"- 난이도: {problem.get('level', 'custom')}")
            output.append("")
            output.append("문제")
            output.append(problem.get("statement", ""))
            output.append("")
            output.append("입력")
            output.append(problem.get("input", ""))
            output.append("")
            output.append("출력")
            output.append(problem.get("output", ""))
            output.append("")
            if constraints:
                output.append("조건")
                output.extend(f"- {item}" for item in constraints)
                output.append("")
            output.extend(render_examples(problem.get("examples", [])))
            output.append("")
    return "\n".join(output).rstrip() + "\n"

# 터미널에서 입력받을 프로필 파일, 카테고리 파일, 문제 은행, 추천 개수, AI 사용 여부 등의 옵션을 설정하고 args로 반환한다.
def parse_args():
    parser = argparse.ArgumentParser(description="Calculate top issues and give coding problems.")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help=f"User profile JSON. default: {DEFAULT_PROFILE}")
    parser.add_argument("--categories", default=DEFAULT_CATEGORIES, help=f"Categories JSON. default: {DEFAULT_CATEGORIES}")
    parser.add_argument("--problem-bank", default=DEFAULT_PROBLEM_BANK, help=f"Problem bank JSON. default: {DEFAULT_PROBLEM_BANK}")
    parser.add_argument("--top", type=int, default=3, help="Number of top issue categories. default: 3")
    parser.add_argument("--per-issue", type=int, default=1, help="Coding problems per issue. default: 1")
    parser.add_argument("--ai", action="store_true", help="Generate new coding problems with Gemini.")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Gemini model for --ai. default: {DEFAULT_MODEL}")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"Env file path for --ai. default: {DEFAULT_ENV_FILE}")
    return parser.parse_args()

# 프로그램의 전체 실행 흐름을 담당하며, 프로필 분석 → 약점 추출 → 문제 추천/생성 → 결과 출력을 처리한다.
def main():
    args = parse_args()
    try:
        categories = read_json(args.categories)
        profile = read_json(args.profile)
        problem_bank = read_json(args.problem_bank)
        issues = top_issues(profile, categories, args.top)

        warning = None
        source = "local"
        if args.per_issue <= 0:
            problems = []
        elif args.ai and issues:
            try:
                problems = request_ai_problems(issues, categories, args)
                source = "gemini"
                if not problems:
                    raise RuntimeError("Gemini returned no valid problems.")
            except Exception as exc:
                warning = f"AI generation failed; used local problem bank. ({exc})"
                problems = pick_local_problems(issues, problem_bank, args.per_issue)
                source = "local"
        else:
            problems = pick_local_problems(issues, problem_bank, args.per_issue)

        print(render(issues, problems, categories, source, warning))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0

# 이 파일을 직접 실행했을 때 main 함수를 실행하고 종료 코드를 시스템에 전달한다.
if __name__ == "__main__":
    raise SystemExit(main())
