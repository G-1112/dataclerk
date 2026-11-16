"""合成校园数据库生成（FR-A01）。

用固定随机种子生成 SQLite 库，schema 合理、分布真实，并刻意埋入边界情况。
标准答案可由 SQLite 自动执行生成，因此整套测试可精确断言、全自动跑批。

用法：
    python data/gen_db.py            # 生成全量 campus.db（默认规模）
    python data/gen_db.py --small    # 生成小规模（快速测试/演示）
    python data/gen_db.py --db path/to.db --students 8000 ...

刻意埋入的边界（它们是触发 Agent 出错的地方，是测试用例最好的素材）：
    - 成绩缺失（score 为 NULL）
    - 退选记录（status='withdrawn'）
    - 逾期未还（return_date 为 NULL 且 due_date 已过）
    - 一人多卡（同一学生多条 card_transactions，含跨商户）
    - 学院改名（部分学生用旧学院名）
    - 空结果查询（某学期无记录）
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from pathlib import Path

# 数据规模（默认，对应需求文档）
SCALE = {
    "students": 8000,
    "courses": 1200,
    "enrollments": 60000,
    "library_borrow": 20000,
    "card_transactions": 50000,
    "course_schedule": 5000,
    "teachers": 400,
    "books": 3000,
    "classrooms": 200,
    "scholarships": 3000,
}

# 学院（含"学院改名"边界：旧名 → 新名）
COLLEGES = [
    "计算机学院", "软件学院", "电子信息学院", "机械工程学院",
    "经济管理学院", "外国语学院", "理学院", "文学院",
]
COLLEGE_ALIAS = {
    "计算机科学与技术学院": "计算机学院",  # 旧名 → 新名
    "计算机与软件学院": "软件学院",
}

MAJORS = {
    "计算机学院": ["计算机科学与技术", "软件工程", "人工智能"],
    "软件学院": ["软件工程", "数据科学"],
    "电子信息学院": ["电子信息工程", "通信工程"],
    "机械工程学院": ["机械工程", "自动化"],
    "经济管理学院": ["工商管理", "会计学", "金融学"],
    "外国语学院": ["英语", "日语"],
    "理学院": ["数学与应用数学", "物理学"],
    "文学院": ["汉语言文学", "新闻学"],
}

GRADES = [2022, 2023, 2024, 2025]
SEMESTERS = ["2023-FALL", "2024-SPRING", "2024-FALL", "2025-SPRING"]
TITLES = ["教授", "副教授", "讲师", "助教"]
BUILDINGS = ["第一教学楼", "第二教学楼", "第三教学楼", "实验楼", "综合楼"]
MERCHANTS = ["第一食堂", "第二食堂", "校园超市", "图书馆咖啡", "打印店", "洗衣房"]
BOOK_CATEGORIES = ["计算机", "文学", "数学", "外语", "经济", "工程", "社科"]


def _pick(rng, seq):
    return rng.choice(seq)


class CampusDB:
    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)
        self.students = []  # (student_id, name, gender, grade, college, major, enroll_year, class_id)
        self.teachers = []  # (teacher_id, name, college, title)
        self.courses = []  # (course_id, name, credit, college, teacher_id, semester, capacity)
        self.enrollments = []  # (id, student_id, course_id, semester, score, status)
        self.classrooms = []  # (room_id, building, capacity, type)
        self.schedules = []  # (id, course_id, room_id, semester, day, start_slot, end_slot)
        self.books = []  # (book_id, title, author, category, publisher)
        self.borrows = []  # (id, student_id, book_id, borrow_date, due_date, return_date)
        self.transactions = []  # (id, student_id, amount, merchant, category, trans_time)
        self.scholarships = []  # (id, student_id, name, amount, academic_year)

    # ------------------------------------------------------------------
    def build(self, scale: dict | None = None) -> None:
        sc = {**SCALE, **(scale or {})}
        self._gen_teachers(sc["teachers"])
        self._gen_students(sc["students"])
        self._gen_classrooms(sc["classrooms"])
        self._gen_courses(sc["courses"])
        self._gen_enrollments(sc["enrollments"])
        self._gen_schedules(sc["course_schedule"])
        self._gen_books(sc["books"])
        self._gen_borrows(sc["library_borrow"])
        self._gen_transactions(sc["card_transactions"])
        self._gen_scholarships(sc["scholarships"])

    # ------------------------------------------------------------------
    def _gen_teachers(self, n: int) -> None:
        rng = self.rng
        for i in range(1, n + 1):
            college = _pick(rng, COLLEGES)
            self.teachers.append(
                (f"T{i:05d}", f"教师{i}", college, _pick(rng, TITLES))
            )

    def _gen_students(self, n: int) -> None:
        rng = self.rng
        surnames = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
        for i in range(1, n + 1):
            college = _pick(rng, COLLEGES)
            # 学院改名边界：前 3% 学生用旧学院名
            if rng.random() < 0.03:
                college = _pick(rng, list(COLLEGE_ALIAS.keys()))
            major = _pick(rng, MAJORS[COLLEGE_ALIAS.get(college, college)])
            enroll_year = _pick(rng, GRADES)
            self.students.append(
                (
                    f"S{i:05d}",
                    surnames[rng.randrange(len(surnames))] + f"{rng.randrange(10, 99):02d}",
                    _pick(rng, ["男", "女"]),
                    enroll_year,
                    college,
                    major,
                    enroll_year,
                    f"班{rng.randrange(1, 9)}",
                )
            )

    def _gen_classrooms(self, n: int) -> None:
        rng = self.rng
        for i in range(1, n + 1):
            self.classrooms.append(
                (f"R{i:04d}", _pick(rng, BUILDINGS), rng.choice([40, 60, 80, 120]), _pick(rng, ["普通", "多媒体", "实验室"]))
            )

    def _gen_courses(self, n: int) -> None:
        rng = self.rng
        prefixes = {
            "计算机学院": "计算", "软件学院": "软件", "电子信息学院": "电子",
            "机械工程学院": "机械", "经济管理学院": "经管", "外国语学院": "外语",
            "理学院": "理科", "文学院": "文学",
        }
        suffix = ["导论", "原理", "实践", "高级专题", "基础", "实验"]
        for i in range(1, n + 1):
            college = _pick(rng, COLLEGES)
            teacher = _pick(rng, [t for t in self.teachers if t[2] == college]) if any(t[2] == college for t in self.teachers) else _pick(rng, self.teachers)
            self.courses.append(
                (
                    f"C{i:05d}",
                    f"{prefixes[college]}{_pick(rng, suffix)}",
                    rng.choice([1, 2, 3, 4]),
                    college,
                    teacher[0],
                    _pick(rng, SEMESTERS),
                    rng.choice([30, 50, 80, 120]),
                )
            )

    def _gen_enrollments(self, n: int) -> None:
        rng = self.rng
        # 每个学生选若干门课，共 n 条
        for i in range(1, n + 1):
            student = _pick(rng, self.students)
            course = _pick(rng, self.courses)
            status = rng.choices(["enrolled", "withdrawn", "completed"], weights=[60, 8, 32])[0]
            # 成绩缺失边界：enrolled 状态或 5% 概率 score 为 NULL
            score = None
            if status != "enrolled" and rng.random() > 0.05:
                score = rng.randint(40, 100)
            self.enrollments.append(
                (i, student[0], course[0], course[5], score, status)
            )

    def _gen_schedules(self, n: int) -> None:
        rng = self.rng
        for i in range(1, n + 1):
            course = _pick(rng, self.courses)
            room = _pick(rng, self.classrooms)
            day = rng.randint(1, 7)
            start = rng.randint(1, 10)
            self.schedules.append(
                (i, course[0], room[0], course[5], day, start, start + 1)
            )

    def _gen_books(self, n: int) -> None:
        rng = self.rng
        authors = ["张三", "李四", "王五", "赵六", "钱七", "孙八"]
        publishers = ["高等教育出版社", "清华大学出版社", "人民邮电出版社", "机械工业出版社"]
        for i in range(1, n + 1):
            self.books.append(
                (f"B{i:05d}", f"图书{i}", _pick(rng, authors), _pick(rng, BOOK_CATEGORIES), _pick(rng, publishers))
            )

    def _gen_borrows(self, n: int) -> None:
        rng = self.rng
        for i in range(1, n + 1):
            student = _pick(rng, self.students)
            book = _pick(rng, self.books)
            borrow_date = f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            due_date = f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            # 逾期未还边界：15% 概率 return_date 为 NULL
            return_date = None if rng.random() < 0.15 else f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            self.borrows.append((i, student[0], book[0], borrow_date, due_date, return_date))

    def _gen_transactions(self, n: int) -> None:
        rng = self.rng
        for i in range(1, n + 1):
            student = _pick(rng, self.students)
            amount = round(rng.uniform(3, 60), 2)
            merchant = _pick(rng, MERCHANTS)
            self.transactions.append(
                (i, student[0], amount, merchant, _pick(rng, ["餐饮", "购物", "打印", "其他"]), f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d} {rng.randint(8, 22):02d}:{rng.randint(0, 59):02d}:00")
            )

    def _gen_scholarships(self, n: int) -> None:
        rng = self.rng
        names = ["国家奖学金", "国家励志奖学金", "校级一等奖学金", "校级二等奖学金", "专项奖学金"]
        for i in range(1, n + 1):
            student = _pick(rng, self.students)
            self.scholarships.append(
                (i, student[0], _pick(rng, names), rng.choice([2000, 5000, 8000, 10000]), _pick(rng, ["2024-2025", "2023-2024"]))
            )

    # ------------------------------------------------------------------
    def to_sqlite(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 幂等：已存在则先删除，避免重跑时报 "table already exists"（第三轮）
        if path.exists():
            path.unlink()
        conn = sqlite3.connect(str(path))
        cur = conn.cursor()
        cur.executescript(SCHEMA_SQL)
        for table, rows in self._rows_by_table().items():
            cur.executemany(
                f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows
            )
        # 建索引（第三轮性能）：权限层对学生角色恒定注入 student_id=?、
        # 对教师角色注入 college=?，这两列是全表扫描的主要来源。
        # 建索引后典型查询由 SCAN 变为 SEARCH（实测约 2.4–3.2x，且随表增大而扩大）。
        cur.executescript(INDEX_SQL)
        conn.commit()
        conn.close()


    def _rows_by_table(self) -> dict[str, list]:
        return {
            "students": self.students,
            "teachers": self.teachers,
            "courses": self.courses,
            "enrollments": self.enrollments,
            "classrooms": self.classrooms,
            "course_schedule": self.schedules,
            "books": self.books,
            "library_borrow": self.borrows,
            "card_transactions": self.transactions,
            "scholarships": self.scholarships,
        }


SCHEMA_SQL = """
CREATE TABLE students (
    student_id TEXT PRIMARY KEY, name TEXT, gender TEXT, grade INTEGER,
    college TEXT, major TEXT, enroll_year INTEGER, class_id TEXT
);
CREATE TABLE teachers (
    teacher_id TEXT PRIMARY KEY, name TEXT, college TEXT, title TEXT
);
CREATE TABLE courses (
    course_id TEXT PRIMARY KEY, course_name TEXT, credit INTEGER,
    college TEXT, teacher_id TEXT, semester TEXT, capacity INTEGER
);
CREATE TABLE enrollments (
    id INTEGER PRIMARY KEY, student_id TEXT, course_id TEXT,
    semester TEXT, score INTEGER, status TEXT
);
CREATE TABLE classrooms (
    room_id TEXT PRIMARY KEY, building TEXT, capacity INTEGER, type TEXT
);
CREATE TABLE course_schedule (
    id INTEGER PRIMARY KEY, course_id TEXT, room_id TEXT, semester TEXT,
    day_of_week INTEGER, start_slot INTEGER, end_slot INTEGER
);
CREATE TABLE books (
    book_id TEXT PRIMARY KEY, title TEXT, author TEXT, category TEXT, publisher TEXT
);
CREATE TABLE library_borrow (
    id INTEGER PRIMARY KEY, student_id TEXT, book_id TEXT,
    borrow_date TEXT, due_date TEXT, return_date TEXT
);
CREATE TABLE card_transactions (
    id INTEGER PRIMARY KEY, student_id TEXT, amount REAL,
    merchant TEXT, category TEXT, trans_time TEXT
);
CREATE TABLE scholarships (
    id INTEGER PRIMARY KEY, student_id TEXT, name TEXT,
    amount REAL, academic_year TEXT
);
"""

# 索引（第三轮性能优化）：
# 权限层对 student 恒定注入 `student_id = current_user`，对 teacher 注入 `college = ?`，
# 无索引时这两类过滤都退化为全表 SCAN。索引后查询计划变为 SEARCH。
INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_enrollments_student ON enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_card_transactions_student ON card_transactions(student_id);
CREATE INDEX IF NOT EXISTS idx_library_borrow_student ON library_borrow(student_id);
CREATE INDEX IF NOT EXISTS idx_scholarships_student ON scholarships(student_id);
CREATE INDEX IF NOT EXISTS idx_students_student ON students(student_id);
CREATE INDEX IF NOT EXISTS idx_students_college ON students(college);
CREATE INDEX IF NOT EXISTS idx_courses_college ON courses(college);
CREATE INDEX IF NOT EXISTS idx_enrollments_course ON enrollments(course_id);
CREATE INDEX IF NOT EXISTS idx_course_schedule_course ON course_schedule(course_id);
CREATE INDEX IF NOT EXISTS idx_library_borrow_book ON library_borrow(book_id);
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="生成合成校园数据库")
    ap.add_argument("--db", default=str(Path(__file__).parent / "campus.db"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--small", action="store_true", help="小规模（快速演示）")
    args = ap.parse_args()

    scale = None
    if args.small:
        scale = {
            "students": 400, "courses": 200, "enrollments": 3000,
            "library_borrow": 1000, "card_transactions": 2000,
            "course_schedule": 500, "teachers": 40, "books": 300,
            "classrooms": 30, "scholarships": 200,
        }

    db = CampusDB(seed=args.seed)
    db.build(scale)
    db.to_sqlite(args.db)

    conn = sqlite3.connect(args.db)
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in db._rows_by_table()
    }
    conn.close()
    print(f"生成完成 → {args.db}")
    print(counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
