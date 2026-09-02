"""结果集规范化 + SHA256 断言测试（FR-T03 / NFR-05：不受行序影响）。"""

from testsuite.assert_result import assert_result_hash, normalize_rows, result_hash


def test_hash_order_independent():
    a = [[1, "张三"], [2, "李四"]]
    b = [[2, "李四"], [1, "张三"]]
    assert result_hash(a) == result_hash(b)


def test_hash_sensitive_to_content():
    a = [[1, "张三"]]
    b = [[2, "张三"]]
    assert result_hash(a) != result_hash(b)


def test_none_normalized_to_empty():
    assert normalize_rows([[None]]) == [("",)]


def test_float_precision_normalized():
    a = [[3.0]]
    b = [[3.0000001]]
    assert result_hash(a) == result_hash(b)


def test_assert_result_hash():
    assert assert_result_hash([[1]], [[1]])
    assert not assert_result_hash([[1]], [[2]])
