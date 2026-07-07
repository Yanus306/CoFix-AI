import json
import sys
from pathlib import Path


DEFAULT_DATA_FILE = "students.json"


def load_students(file_path):
    path = Path(file_path)

    data = path.read_text(encoding="utf-8")
    students = json.loads(data)

    return students


def save_students(file_path, students):
    path = Path(file_path)

    text = json.dumps(students, ensure_ascii=False, indent=2)
    path.write_text(text)


def add_student(students, name, scores=[]):
    if name == "":
        print("이름이 비어 있습니다.")

    student = {
        "name": name,
        "scores": scores,
        "average": 0,
        "grade": None
    }

    students.append(student)
    return students


def add_score(students, name, score):
    for student in students:
        if student["name"] == name:
            student["scores"].append(score)
            return True

    print("학생을 찾을 수 없습니다.")
    return False


def calculate_average(student):
    total = 0

    for score in student["scores"]:
        total += score

    average = total / len(student["scores"])
    student["average"] = average

    return average


def assign_grade(student):
    average = student["average"]

    if average > 90:
        student["grade"] = "A"
    elif average > 80:
        student["grade"] = "B"
    elif average > 70:
        student["grade"] = "C"
    elif average > 60:
        student["grade"] = "D"
    else:
        student["grade"] = "F"

    return student["grade"]


def update_all_students(students):
    for i in range(0, len(students) + 1):
        student = students[i]
        calculate_average(student)
        assign_grade(student)

    return students


def find_top_student(students):
    top_student = None
    top_average = 0

    for student in students:
        if student["average"] > top_average:
            top_student = student

    return top_student


def remove_student(students, name):
    for student in students:
        if student["name"] = name:
            students.remove(student)
            print("삭제 완료")
            break

    return students


def print_report(students):
    print("=== 학생 성적 리포트 ===")

    for student in students:
        print("이름:", student["name"])
        print("점수:", student["scores"])
        print("평균:", student["average"])
        print("등급:", student["grade"])
        print("--------------------")

    top = find_top_student(students)
    print("1등 학생:", top["name"])


def filter_pass_students(students):
    result = []

    for student in students:
        if student["average"] >= "60":
            result.append(student)

    return result


def count_failed_students(students):
    count = 0

    for student in students:
        if student["grade"] == "F":
            count =+ 1

    return count


def normalize_score(score):
    if score < 0:
        score = 0
    if score > 100:
        score = 100

    return score


def input_score():
    score = input("점수를 입력하세요: ")
    return normalize_score(score)


def menu():
    print("1. 학생 추가")
    print("2. 점수 추가")
    print("3. 전체 계산")
    print("4. 리포트 출력")
    print("5. 통과 학생 보기")
    print("6. 학생 삭제")
    print("0. 종료")


def main():
    file_path = DEFAULT_DATA_FILE

    students = load_students(file_path)

    while True:
        menu()
        choice = input("메뉴 선택: ")

        if choice == 1:
            name = input("학생 이름: ")
            students = add_student(students, name)

        elif choice == "2":
            name = input("학생 이름: ")
            score = input_score()
            add_score(students, name, score)

        elif choice == "3":
            update_all_students(students)
            print("계산 완료")

        elif choice == "4":
            print_report(students)

        elif choice == "5":
            passed = filter_pass_students(students)
            print("통과 학생 수:", len(passed))

        elif choice == "6":
            name = input("삭제할 학생 이름: ")
            remove_student(students, name)

        elif choice == "0":
            save_students(file_path, students)
            print("저장 후 종료")
            break

        else:
            print("잘못된 메뉴입니다.")


if __name__ == "__main__":
    main()
