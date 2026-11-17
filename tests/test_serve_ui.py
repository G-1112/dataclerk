"""服务入口与可视化控制台的契约测试。

覆盖两件事：
1. `GET /` 返回的是**可直接阅读的端点清单**（此前访问根路径是 404，评委打开浏览器
   只看到 `{"detail":"Not Found"}`，会误以为服务没起来）；
2. `GET /ui` 真的返回可用的控制台页面，且页面里确实调用了 /chat 与 /trace
   —— 避免"路由存在但页面引用错接口"这类假绿灯。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from agent.serve import app


def test_root_returns_endpoint_catalog():
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["service"].startswith("问数官")
    # 端点清单必须包含关键接口，且能被人类读懂
    joined = " ".join(d["endpoints"].keys())
    for path in ("POST /chat", "GET /trace/{trace_id}", "GET /health", "GET /ui"):
        assert path in joined, f"缺少端点：{path}"
    assert d["ui"] == "/ui"
    assert d["docs"] == "/docs"


def test_ui_served_as_html():
    client = TestClient(app)
    r = client.get("/ui")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    html = r.text
    assert "问数官" in html
    # 页面必须真正调用后端两个接口，否则控制台是空壳
    assert '"/chat"' in html or "'/chat'" in html or '/chat"' in html
    assert "/trace/" in html
    # 三条演示问题要在页面里可一键点击
    assert "软件学院有多少学生" in html
