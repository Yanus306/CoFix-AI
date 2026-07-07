import argparse
import json
import os
import sys
from pathlib import Path

#한글 폰트 읽기위함
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

#api 파일명,확장자명,모델버전
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_ENV_FILE = ".env"
DEFAULT_MODEL = "gemini-2.5-flash"

# .env 파일이 존재하면 파일 안의 KEY=VALUE 값을 읽어서 환경변수(os.environ)에 등록한다.
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

# .env 파일을 먼저 불러온 뒤 GEMINI_API_KEY 환경변수 값을 가져온다.
def get_gemini_api_key(env_file):
    load_env_file(env_file)
    return os.environ.get(GEMINI_API_KEY_ENV)

# 파일 경로가 있으면 해당 파일의 코드를 읽고, 없으면 터미널 표준입력(stdin)으로 코드를 받아 코드 내용과 입력 출처를 반환한다.
def read_code(args):
    if args.file:
        path = Path(args.file)
        return path.read_text(encoding=args.encoding), str(path)

    if not sys.stdin.isatty():
        return sys.stdin.read(), "stdin"

    print("Paste code, then press Ctrl+Z and Enter when finished.")
    return sys.stdin.read(), "stdin"

# Gemini에게 보낼 코드 수정 요청 프롬프트를 JSON 응답 형식에 맞게 생성한다.
def build_prompt(code, source_name, instruction, language):
    language_hint = f"\nLanguage: {language}" if language else ""
    user_instruction = instruction or "Fix with minimal changes."

    return f"""
Fix code. Return only JSON:
{{"fixed_code":"full fixed code","modified_parts":["changed parts"]}}
No explanations. Keep intent.
Request: {user_instruction}{language_hint}
Source: {source_name}

{code}
""".strip()

# Gemini API 응답 객체에서 실제 텍스트 결과를 추출한다.
def extract_text(response):
    if hasattr(response, "output_text") and response.output_text:
        return response.output_text
    if hasattr(response, "text") and response.text:
        return response.text
    return str(response)

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

# Gemini API 키를 확인하고, 코드를 수정 요청한 뒤 JSON 결과로 파싱해서 반환한다.
def request_fix(code, source_name, args):
    if not get_gemini_api_key(args.env_file):
        raise RuntimeError(
            f"{GEMINI_API_KEY_ENV} is not set. Set it in PowerShell or create a .env file with "
            f"{GEMINI_API_KEY_ENV}=YOUR_API_KEY."
        )

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt") from exc

    client = genai.Client()
    prompt = build_prompt(code, source_name, args.instruction, args.language)

    if hasattr(client, "interactions"):
        response = client.interactions.create(model=args.model, input=prompt)
    else:
        response = client.models.generate_content(model=args.model, contents=prompt)

    raw_text = extract_text(response)
    try:
        return json.loads(strip_json_fence(raw_text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse Gemini response as JSON.\n\nRaw response:\n{raw_text}") from exc

# Gemini가 반환한 수정 코드와 변경 내용을 사람이 읽기 좋은 Markdown 형식으로 변환한다.
def render_markdown(result):
    fixed_code = result.get("fixed_code", "")
    modified_parts = result.get("modified_parts", result.get("changes", []))
    if isinstance(modified_parts, str):
        modified_parts = [modified_parts]
    elif not isinstance(modified_parts, list):
        modified_parts = [modified_parts]

    output = [
        "## Fixed Code",
        "",
        "```",
        fixed_code.rstrip(),
        "```",
        "",
        "## Modified Parts",
        "",
    ]

    if not modified_parts:
        output.append("- No changes")
    else:
        for index, part in enumerate(modified_parts, start=1):
            if isinstance(part, dict):
                text = part.get("part") or part.get("change") or json.dumps(part, ensure_ascii=False)
            else:
                text = str(part)
            output.append(f"{index}. {text}")

    return "\n".join(output).rstrip() + "\n"

# 터미널에서 입력받을 파일 경로, 수정 지시, 언어, 모델, 출력 파일, env 파일, 인코딩 옵션을 설정하고 args로 반환한다.
def parse_args():
    #argparse.ArgumentParser 형태의 객체생성(터미널 명령)
    parser = argparse.ArgumentParser(
        #설명(python main.py --help)시 나올값
        description="Fix code with the Gemini 2.5 API and print fixed code plus modified parts."
    )
    # 터미널에서 입력한 파일 경로, 수정 지시, 언어, 모델, 출력 파일, env 파일, 인코딩 옵션을 받아 args 객체로 변환한다.
    parser.add_argument("file", nargs="?", help="Code file path. If omitted, stdin is used.")
    parser.add_argument("-i", "--instruction", help="Fix instruction, e.g. add error handling.")
    parser.add_argument("-l", "--language", help="Code language. If omitted, Gemini infers it.")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Gemini model. default: {DEFAULT_MODEL}")
    parser.add_argument("-o", "--output", help="Save Markdown result to this file.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"Env file path. default: {DEFAULT_ENV_FILE}")
    parser.add_argument("--encoding", default="utf-8", help="Input file encoding. default: utf-8")
    return parser.parse_args()

# 프로그램의 전체 실행 흐름을 담당하며, 입력 코드 읽기 → Gemini 수정 요청 → Markdown 출력/저장을 처리한다.
def main():
    args = parse_args()
    #에러가 날 수 있는 예외구문
    try:
        #read_code(args)를 호출해서 수정할 코드를 읽는다. -39~48
        code, source_name = read_code(args)
        if not code.strip():
            raise RuntimeError("Input code is empty.")

        result = request_fix(code, source_name, args)
        markdown = render_markdown(result)

        if args.output:
            Path(args.output).write_text(markdown, encoding="utf-8")
            print(f"Saved result: {args.output}")
        else:
            print(markdown)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0

#실행(메인)
if __name__ == "__main__":
    raise SystemExit(main())
