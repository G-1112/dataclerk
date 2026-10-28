"""结果集语义等价判定：给「多条正确写法」留出空间。

问题
----
原来的功能准确性判定只有一条：`result_hash(actual) == result_hash(expected)`，
即**行序无关的精确多重集相等**。它严格、无歧义，但会把"同样正确、写法不同"的
答案判成错：

| 情况 | 例子 | 严格判定 | 其实对不对 |
|---|---|---|---|
| 数值类型/精度差异 | `943` vs `943.0`、`0.8000001` vs `0.8` | ✗ | 对 |
| 多选了列 | 问「有多少学生」答 `(college, COUNT(*))` | ✗ | 对（信息更全） |
| 重复行 | 问「有哪些学院」带/不带 `DISTINCT` | ✗ | 对 |
| 行数不同 | `LIMIT 10` 的采样 vs 全量 | ✗ | 看业务语义 |

于是本模块提供**分级等价**：`exact → numeric → projection → column_subset → set → subset`，
逐级放宽，命中哪一级就报哪一级。这样报告里能说清"这条用例是在什么口径下算对的"，
而不是笼统地说"宽松也算对"。

其中 `column_subset`（少列）与 `projection`（多列/列序不同）必须分开计：
外接真实模型时"问『有哪些老师』只答了姓名、没答职称"属**少列**，
它可能是合理写法，也可能是漏答关键列（问「成绩」只给姓名就彻底错了），
所以只能在用例显式声明 `allow_missing_columns` 时放行，且单独报一个档位。

纪律
----
1. **默认最严**：`DEFAULT_SPEC` 只放开数值容差（`943` 与 `943.0` 属于同一事实），
   其余放宽必须由用例显式声明 `equiv`，不允许全局放开——否则准确率是靠改口径刷出来的；
2. **主指标不变**：`run_functional` 的 `passed` 仍是严格判定，
   `passed_semantic` 是**附加**指标，两者都进报告，谁也不能替代谁；
3. **行子集（subset）最危险**：`allow_missing_rows` 会把"漏查了几行"也算对，
   只允许在用例写明业务理由（如"取前 N 条"）时打开。
"""

from __future__ import annotations

from typing import Any

DEFAULT_SPEC: dict[str, Any] = {
    "numeric_tolerance": 1e-6,
    "allow_extra_columns": False,
    "allow_missing_columns": False,
    "ignore_duplicates": False,
    "allow_missing_rows": False,
    "column_aliases": {},
}

# 常见列名同义（不同写法指向同一列）；只做规范化，不做语义猜测
_ALIAS_GROUPS = [
    ("student_id", "stu_no", "sno", "学号"),
    ("course_id", "course_no", "cno", "课程号"),
    ("teacher_id", "teacher_no", "tno", "工号"),
    ("course_name", "course_title", "课程名"),
    ("college", "dept_name", "department", "学院"),
    ("name", "student_name", "姓名"),
    ("score", "grade_point", "成绩"),
]


def _norm_name(name: str, spec: dict) -> str:
    key = str(name or "").strip().lower()
    for group in _ALIAS_GROUPS:
        if key in group:
            return group[0]
    return spec.get("column_aliases", {}).get(key, key)


def _cell_eq(a: Any, b: Any, tol: float) -> bool:
    """单元格等价：数值容差优先，其次字符串（去空白）比较。"""
    if a is None and b is None:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    sa = "" if a is None else str(a).strip()
    sb = "" if b is None else str(b).strip()
    if sa == sb:
        return True
    try:
        return abs(float(sa) - float(sb)) <= tol
    except (TypeError, ValueError):
        return False


def _row_eq(a: list, b: list, tol: float) -> bool:
    return len(a) == len(b) and all(_cell_eq(x, y, tol) for x, y in zip(a, b))


def _row_key(row: list, tol: float) -> tuple:
    """把行压成可比较的键：数值按容差取整，其余按字符串。"""
    out = []
    for v in row:
        if isinstance(v, bool):
            out.append(str(v))
        elif isinstance(v, (int, float)):
            out.append(round(float(v), 6) if tol < 1 else round(float(v) / tol))
        else:
            s = "" if v is None else str(v).strip()
            try:
                out.append(round(float(s), 6))
            except (TypeError, ValueError):
                out.append(s)
    return tuple(out)


def _project(rows: list[list], idx: list[int]) -> list[list]:
    return [[row[i] if i < len(row) else None for i in idx] for row in rows]


def _align_by_columns(
    expected: list[list], actual: list[list], expected_cols: list[str], actual_cols: list[str], spec: dict
) -> tuple[list[list] | None, str]:
    """按列名对齐：把 actual 投影成 expected 的列形状。返回 (对齐后的 actual, 说明)。"""
    e_map = {_norm_name(c, spec): i for i, c in enumerate(expected_cols)}
    a_map = {_norm_name(c, spec): i for i, c in enumerate(actual_cols)}
    if len(e_map) != len(expected_cols) or len(a_map) != len(actual_cols):
        return None, "列名归一后重名，无法对齐"

    missing = [c for c in e_map if c not in a_map]
    extra = [c for c in a_map if c not in e_map]
    if missing and not spec.get("allow_missing_columns"):
        return None, f"缺少列 {missing[:3]}"
    if extra and not spec.get("allow_extra_columns"):
        return None, f"多出列 {extra[:3]}"

    keep = [c for c in e_map if c in a_map]  # 以 expected 列序为准
    e_idx = [e_map[c] for c in keep]
    a_idx = [a_map[c] for c in keep]
    return (
        [_project(expected, e_idx), _project(actual, a_idx)],
        f"按列对齐 {len(keep)} 列" + (f"，缺列 {missing[:3]}" if missing else "") + (f"，多列 {extra[:3]}" if extra else ""),
    )


def _has_missing_columns(expected_cols: list[str], actual_cols: list[str], spec: dict) -> bool:
    """actual 是否缺了 expected 的列（列名归一后比较；缺列必须已被 spec 放行才可能走到判定）。"""
    e = {_norm_name(c, spec) for c in expected_cols}
    a = {_norm_name(c, spec) for c in actual_cols}
    return bool(e - a)


def compare(
    expected_rows: list[list],
    actual_rows: list[list],
    expected_cols: list[str] | None = None,
    actual_cols: list[str] | None = None,
    spec: dict | None = None,
) -> dict:
    """分级比较结果集。

    返回 {equivalent, level, reason, expected_size, actual_size}
    level ∈ exact / numeric / projection / column_subset / set / subset / none
    """
    s = {**DEFAULT_SPEC, **(spec or {})}
    tol = float(s["numeric_tolerance"])
    exp, act = [list(r) for r in expected_rows or []], [list(r) for r in actual_rows or []]

    if not exp and not act:
        return _ok("exact", "两侧均为空")
    if not exp or not act:
        return _bad(f"一侧为空（期望 {len(exp)} 行 / 实际 {len(act)} 行）", exp, act)

    # 0) 严格：行序无关的多重集相等（与 result_hash 同口径）
    if sorted(_row_key(r, tol) for r in exp) == sorted(_row_key(r, tol) for r in act):
        return _ok("exact", "行序无关完全一致")

    # 1) 数值级：形状相同，逐格按容差比较（排序后一一配对）
    if all(len(r) == len(exp[0]) for r in exp + act):
        e_sorted = sorted(exp, key=lambda r: [_sort_key(v) for v in r])
        a_sorted = sorted(act, key=lambda r: [_sort_key(v) for v in r])
        if len(e_sorted) == len(a_sorted) and all(
            _row_eq(x, y, tol) for x, y in zip(e_sorted, a_sorted)
        ):
            return _ok("numeric", "允许数值精度/类型差异")

    # 2) 列级：按列名对齐后再比（容忍多选列 / 少选列，需 spec 放行）
    col_reason = ""
    if expected_cols and actual_cols:
        aligned, why = _align_by_columns(exp, act, expected_cols, actual_cols, s)
        if aligned is not None:
            if sorted(_row_key(r, tol) for r in aligned[0]) == sorted(_row_key(r, tol) for r in aligned[1]):
                # 少列（column_subset）单独成一个档位：它比"多列"更危险，
                # 报告里必须能一眼看出这条是靠"允许少列"才算对的。
                if _has_missing_columns(expected_cols, actual_cols, s):
                    return _ok("column_subset", why)
                return _ok("projection", why)
        else:
            # 列对不上是最具体的失败原因，优先于"列数不同/值不同"这类笼统描述
            col_reason = why

    # 3) 集合语义：忽略重复行（DISTINCT 与否都算对）
    if s["ignore_duplicates"]:
        if {_row_key(r, tol) for r in exp} == {_row_key(r, tol) for r in act}:
            return _ok("set", "去重后一致（DISTINCT 与否视为等价）")

    # 4) 子集语义：实际结果是被期望结果覆盖的子集（如 LIMIT/采样），需显式放行
    if s["allow_missing_rows"]:
        from collections import Counter

        ce, ca = Counter(_row_key(r, tol) for r in exp), Counter(_row_key(r, tol) for r in act)
        if all(ca[k] <= ce[k] for k in ca):
            return _ok("subset", f"实际 {len(act)} 行 ⊆ 期望 {len(exp)} 行（按业务理由放行）")

    return _bad(col_reason or _diagnose(exp, act, expected_cols, actual_cols, tol), exp, act)


def _sort_key(v: Any) -> tuple:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (0, float(v))
    return (1, "" if v is None else str(v))


def _diagnose(exp, act, ecols, acols, tol) -> str:
    """给出人可读的差异说明（报告里直接引用）。"""
    if len(exp) != len(act):
        return f"行数不同（期望 {len(exp)}，实际 {len(act)}）"
    widths = {len(r) for r in exp} | {len(r) for r in act}
    if len(widths) > 1:
        return f"列数不同（期望 {len(exp[0])}，实际 {len(act[0])}）"
    ek = {_row_key(r, tol) for r in exp}
    ak = {_row_key(r, tol) for r in act}
    miss, extra = len(ek - ak), len(ak - ek)
    if ecols and acols:
        return f"行数相同但值不同（{miss} 行仅期望中有 / {extra} 行仅实际中有）；列：期望 {ecols} vs 实际 {acols}"
    return f"行数相同但值不同（{miss} 行仅期望中有 / {extra} 行仅实际中有）"


def _ok(level: str, reason: str) -> dict:
    return {"equivalent": True, "level": level, "reason": reason}


def _bad(reason: str, exp, act) -> dict:
    return {
        "equivalent": False,
        "level": "none",
        "reason": reason,
        "expected_size": f"{len(exp)}×{len(exp[0]) if exp else 0}",
        "actual_size": f"{len(act)}×{len(act[0]) if act else 0}",
    }
