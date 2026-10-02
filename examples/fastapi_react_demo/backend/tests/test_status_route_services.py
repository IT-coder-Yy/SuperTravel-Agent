"""验证 HTTP 契约，避免将内部包装层的调用顺序固化成测试。"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
for root in (BACKEND_ROOT, BACKEND_ROOT.parents[2]):
    if str(root) not in sys.path:
        sys.path.append(str(root))

import main
from services import mcp_service, powerpaint_status_service, system_service


class ToolManager:
    def list_tools(self):
        return [{"name": "calculate"}]

    def list_tools_simplified(self):
        return [{"name": "calculate", "description": "calculator"}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.runtime_state, "tool_manager", ToolManager())
    monkeypatch.setattr(main.runtime_state, "active_sessions", {"session-1": {}})
    monkeypatch.setattr(main.runtime_state, "controller", None)
    monkeypatch.setattr(system_service, "build_runtime_model_status_payload", lambda: {})
    # 不启动外部 MCP；请求仍完整经过 FastAPI 的校验、序列化和 CORS 中间件。
    return TestClient(main.app)


def assert_cors(response):
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_status_http_contract(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    assert_cors(response)
    payload = response.json()
    assert {key: payload[key] for key in ("status", "agents_count", "tools_count", "active_sessions", "version")} == {
        "status": "running", "agents_count": 7, "tools_count": 1,
        "active_sessions": 1, "version": "0.8",
    }


@pytest.mark.parametrize("description", [None, "", "   ", "english description", "中文说明。更多内容"])
def test_tool_catalog_handles_missing_or_empty_description(client, monkeypatch, description):
    monkeypatch.setattr(main.runtime_state.tool_manager, "list_tools_simplified", lambda: [
        {"name": "custom", "description": description}, {"name": "calculate"},
    ])
    response = client.get("/api/tools")
    assert response.status_code == 200
    assert_cors(response)
    tools = response.json()
    assert tools[0] == {
        "name": "custom", "parameters": {},
        "description": "中文说明。" if description == "中文说明。更多内容" else "为旅行规划提供结构化辅助能力。",
    }
    assert tools[1]["description"] == "执行基础数值计算。"


def test_skills_http_contract(client):
    response = client.get("/api/skills")
    assert response.status_code == 200
    assert_cors(response)
    skills = {skill["id"]: skill for skill in response.json()}
    assert len(skills) == 7
    assert skills["budget_optimizer"]["available"] is True
    assert skills["rail_transport"]["available"] is False
    assert "query_12306_realtime_tickets" in skills["rail_transport"]["missing_local_tools"]


def test_mcp_http_contract(client, monkeypatch):
    config = SimpleNamespace(mcp=SimpleNamespace(servers={}))
    monkeypatch.setattr(mcp_service, "get_app_config", lambda: config)
    response = client.get("/api/mcp-servers")
    assert response.status_code == 200
    assert_cors(response)
    assert response.json() == {"servers": [], "total_servers": 0, "active_servers": 0}


@pytest.mark.parametrize("available", [True, False])
def test_powerpaint_http_contract(client, monkeypatch, available):
    build_status = powerpaint_status_service.build_powerpaint_status
    monkeypatch.setenv("POWERPAINT_URL", "http://127.0.0.1:9000")
    monkeypatch.setattr(powerpaint_status_service, "build_powerpaint_status", lambda: build_status(checker=lambda _: available))
    response = client.get("/api/powerpaint/status")
    assert response.status_code == 200
    assert_cors(response)
    payload = response.json()
    assert payload["url"] == "http://127.0.0.1:9000"
    assert payload["reachable"] is available
    assert "可访问" in payload["message"] if available else "暂不可访问" in payload["message"]


@pytest.mark.parametrize("path,module,function,detail", [
    ("/api/status", system_service, "build_system_status", "broken"),
    ("/api/powerpaint/status", powerpaint_status_service, "build_powerpaint_status", "broken"),
    ("/api/mcp-servers", mcp_service, "build_mcp_servers_runtime_status", "获取MCP服务器状态失败: broken"),
])
def test_status_errors_keep_http_contract(client, monkeypatch, path, module, function, detail):
    monkeypatch.setattr(module, function, Mock(side_effect=RuntimeError("broken")))
    response = client.get(path)
    assert response.status_code == 500
    assert response.json() == {"detail": detail}


def test_tool_catalog_failure_is_http_500(client, monkeypatch):
    monkeypatch.setattr(main.runtime_state.tool_manager, "list_tools_simplified", Mock(side_effect=RuntimeError("tools-fail")))
    response = client.get("/api/tools")
    assert response.status_code == 500
    assert response.json() == {"detail": "tools-fail"}


@pytest.mark.parametrize("fails", [False, True])
def test_configure_updates_controller_only_on_success(client, monkeypatch, fails):
    old_controller, new_controller = object(), object()
    monkeypatch.setattr(main.runtime_state, "controller", old_controller)
    configure = Mock(side_effect=RuntimeError("cfg-fail")) if fails else Mock(return_value=new_controller)
    monkeypatch.setattr(system_service, "configure_runtime", configure)
    payload = {"api_key": "test-only", "model_name": "test-model", "base_url": "http://localhost/v1", "max_tokens": 256, "temperature": 0.1}
    response = client.post("/api/configure", json=payload)
    configure.assert_called_once_with(**payload)
    if fails:
        assert response.status_code == 400
        assert response.json() == {"detail": "cfg-fail"}
        assert main.runtime_state.controller is old_controller
    else:
        assert response.status_code == 200
        assert_cors(response)
        assert response.json() == {"status": "success", "message": "配置更新成功并已保存到文件"}
        assert main.runtime_state.controller is new_controller


def test_status_preflight_retains_development_origin(client):
    response = client.options("/api/status", headers={
        "Origin": "http://localhost:8080", "Access-Control-Request-Method": "GET",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8080"
    assert response.headers["access-control-allow-credentials"] == "true"
