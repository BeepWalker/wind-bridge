"""
FastAPI 服务器端点测试
======================
使用 mocked WindPy 测试所有 API 端点。

覆盖:
  GET  /api/health          — 健康检查
  GET  /api/stats           — 服务器统计
  POST /api/reconnect       — 重连
  GET  /api/cache/clear     — 清除缓存
  POST /api/wsd             — 时间序列
  POST /api/wss             — 快照
  POST /api/wset            — 数据集
  POST /api/wsi             — 分钟线
  POST /api/wsq             — 实时行情 (NEW)
  POST /api/wse             — 板块日内统计 (NEW)
  POST /api/wupf            — 通用推送 (NEW)
  POST /api/tdays           — 交易日
  POST /api/tdayscount      — 交易日计数
  GET  /docs                — OpenAPI 文档
"""

import sys
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

server_dir = Path(__file__).parent.parent / "wind_server"
sys.path.insert(0, str(server_dir))

# ---------------------------------------------------------------------------
# Mock WindPy — 在导入 server 模块之前注入 mock
# ---------------------------------------------------------------------------

mock_windpy = MagicMock()
mock_windpy_module = MagicMock()
mock_windpy_module.w = mock_windpy

sys.modules["WindPy"] = mock_windpy_module


# 模拟 WindResult（WindPy 的返回对象类型）
class MockWindPyResult:
    def __init__(self, error=0, data=None, codes=None, fields=None, times=None):
        self.ErrorCode = error
        self.Data = data or []
        self.Codes = codes or []
        self.Fields = fields or []
        self.Times = times or []


from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    """每次测试重新加载 server 模块以重置状态。"""
    # 清除模块缓存，确保每次测试重新导入
    for mod in list(sys.modules.keys()):
        if "wind_api_server" in mod:
            del sys.modules[mod]
    from wind_api_server import app

    return app


@pytest.fixture
def client(app):
    """FastAPI TestClient。"""
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_mocks():
    """每次测试前重置所有 mock。"""
    mock_windpy.reset_mock()

    # 默认 WindPy.isconnected() 返回 True
    mock_windpy.isconnected.return_value = True

    # 给 WindResult 设置返回类型
    def make_wsd_result(*args, **kwargs):
        return MockWindPyResult(
            error=0,
            data=[[9.52], [9.48], [9.55], [9.50], [9.53]],
            codes=["000001.SZ"],
            fields=["close"],
            times=["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"],
        )

    mock_windpy.wsd.side_effect = make_wsd_result

    mock_windpy.wss.return_value = MockWindPyResult(
        error=0,
        data=[[9.52], [8.15], [1.23]],
        codes=["000001.SZ"],
        fields=["close", "pe", "pb"],
    )

    mock_windpy.wset.return_value = MockWindPyResult(
        error=0,
        data=[["000001.SZ", "000002.SZ", "600519.SH"]],
        codes=["code"],
        fields=["value"],
    )

    mock_windpy.wsi.return_value = MockWindPyResult(
        error=0,
        data=[[9.50, 9.51], [9.52, 9.53]],
        codes=["000001.SZ"],
        fields=["close", "open"],
        times=["2024-01-10 09:31:00", "2024-01-10 09:32:00"],
    )

    mock_windpy.wsq.return_value = MockWindPyResult(
        error=0,
        data=[[9.52], [1234567], [-0.42]],
        codes=["000001.SZ"],
        fields=["rt_last", "rt_vol", "rt_pct_chg"],
    )

    mock_windpy.wse.return_value = MockWindPyResult(
        error=0,
        data=[[-0.35], [1.23e8], [8.76e9]],
        codes=["881001.WI"],
        fields=["rt_pct_chg", "rt_vol", "rt_amt"],
    )

    mock_windpy.wupf.return_value = MockWindPyResult(
        error=0,
        data=[[9.52], [1234567]],
        codes=["000001.SZ"],
        fields=["rt_last", "rt_vol"],
    )

    mock_windpy.tdays.return_value = MockWindPyResult(
        error=0,
        data=[["2024-01-02", "2024-01-03", "2024-01-04"]],
        codes=[""],
        fields=[""],
        times=["2024-01-02", "2024-01-03", "2024-01-04"],
    )

    mock_windpy.tdayscount.return_value = MockWindPyResult(
        error=0,
        data=[7],
        codes=[""],
        fields=[""],
    )

    yield


# ---------------------------------------------------------------------------
# Health & Status
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["wind_connected"] is True
        assert data["version"] == "1.0.0"

    def test_health_degraded(self, client):
        mock_windpy.isconnected.return_value = False
        resp = client.get("/api/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["wind_connected"] is False


class TestStats:
    def test_stats(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "cache_entries" in data
        assert "rate_limit" in data


class TestReconnect:
    def test_reconnect_success(self, client):
        # mock_windpy.start() return 的 MagicMock 会被当成连接成功
        with patch("wind_api_server.wind_conn.connect", return_value=True):
            resp = client.post("/api/reconnect")
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data


# ---------------------------------------------------------------------------
# Core Data Endpoints
# ---------------------------------------------------------------------------


class TestWsdEndpoint:
    """POST /api/wsd — 时间序列。"""

    def test_basic(self, client):
        resp = client.post("/api/wsd", json={
            "codes": "000001.SZ",
            "fields": "close",
            "begin_time": "2024-01-01",
            "end_time": "2024-01-10",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] == 0
        assert len(data["codes"]) == 1
        assert mock_windpy.wsd.called

    def test_multi_codes(self, client):
        resp = client.post("/api/wsd", json={
            "codes": ["000001.SZ", "600519.SH"],
            "fields": "close,volume",
            "begin_time": "2024-01-01",
            "end_time": "2024-01-10",
        })
        assert resp.status_code == 200

    def test_options(self, client):
        resp = client.post("/api/wsd", json={
            "codes": "000001.SZ",
            "fields": "close",
            "begin_time": "2024-01-01",
            "end_time": "2024-01-10",
            "options": "PriceAdj=F",
        })
        assert resp.status_code == 200
        # 验证 WindPy 被传入了 options
        call_args = mock_windpy.wsd.call_args
        assert len(call_args[0]) >= 5  # codes, fields, begin, end, options

    def test_wind_disconnected(self, client):
        mock_windpy.isconnected.return_value = False
        mock_windpy.start.return_value = MockWindPyResult(error=-1)
        with patch("wind_api_server.time.sleep"):  # 跳过重连等待
            resp = client.post("/api/wsd", json={
                "codes": "000001.SZ",
                "fields": "close",
                "begin_time": "2024-01-01",
                "end_time": "2024-01-10",
            })
        assert resp.status_code == 503

    def test_too_many_codes(self, client):
        resp = client.post("/api/wsd", json={
            "codes": [f"000{i:03d}.SZ" for i in range(5001)],
            "fields": "close",
            "begin_time": "2024-01-01",
            "end_time": "2024-01-10",
        })
        assert resp.status_code == 400


class TestWssEndpoint:
    """POST /api/wss — 快照。"""

    def test_basic(self, client):
        resp = client.post("/api/wss", json={
            "codes": "000001.SZ",
            "fields": "close,pe,pb",
            "date": "",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] == 0

    def test_with_date(self, client):
        resp = client.post("/api/wss", json={
            "codes": "000001.SZ",
            "fields": "close",
            "date": "2024-01-05",
        })
        assert resp.status_code == 200

    def test_too_many_codes(self, client):
        resp = client.post("/api/wss", json={
            "codes": [f"000{i:03d}.SZ" for i in range(5001)],
            "fields": "close",
        })
        assert resp.status_code == 400


class TestWsetEndpoint:
    """POST /api/wset — 数据集。"""

    def test_basic(self, client):
        resp = client.post("/api/wset", json={
            "report_name": "sectorconstituent",
            "options": "date=2024-01-01;sector=沪深300",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] == 0

    def test_no_options(self, client):
        resp = client.post("/api/wset", json={
            "report_name": "indexconstituent",
        })
        assert resp.status_code == 200


class TestWsiEndpoint:
    """POST /api/wsi — 分钟线。"""

    def test_basic(self, client):
        resp = client.post("/api/wsi", json={
            "codes": "000001.SZ",
            "fields": "close",
            "begin_time": "2024-01-01 09:30:00",
            "end_time": "2024-01-01 15:00:00",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] == 0
        assert len(data["times"]) == 2

    def test_options(self, client):
        resp = client.post("/api/wsi", json={
            "codes": "000001.SZ",
            "fields": "close",
            "begin_time": "2024-01-01 09:30:00",
            "end_time": "2024-01-01 15:00:00",
            "options": "BarSize=5",
        })
        assert resp.status_code == 200

    def test_too_many_codes(self, client):
        resp = client.post("/api/wsi", json={
            "codes": [f"000{i:03d}.SZ" for i in range(501)],
            "fields": "close",
            "begin_time": "2024-01-01 09:30:00",
            "end_time": "2024-01-01 15:00:00",
        })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Real-time (T+0) HTTP Endpoints
# ---------------------------------------------------------------------------


class TestWsqEndpoint:
    """POST /api/wsq — 实时行情快照 (NEW)。"""

    def test_basic(self, client):
        resp = client.post("/api/wsq", json={
            "codes": "000001.SZ,510050.SH",
            "fields": "rt_last,rt_vol,rt_pct_chg",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] == 0
        assert "rt_last" in data["fields"] or any("rt_last" in str(f) for f in data["fields"])

    def test_single_code(self, client):
        resp = client.post("/api/wsq", json={
            "codes": "000001.SZ",
            "fields": "rt_last",
        })
        assert resp.status_code == 200

    def test_too_many_codes(self, client):
        resp = client.post("/api/wsq", json={
            "codes": [f"000{i:03d}.SZ" for i in range(501)],
            "fields": "rt_last",
        })
        assert resp.status_code == 400

    def test_windpy_called(self, client):
        client.post("/api/wsq", json={
            "codes": "000001.SZ",
            "fields": "rt_last,rt_vol",
        })
        assert mock_windpy.wsq.called
        args, kwargs = mock_windpy.wsq.call_args
        assert "000001.SZ" in str(args[0])


class TestWseEndpoint:
    """POST /api/wse — 板块日内统计 (NEW)。"""

    def test_basic(self, client):
        resp = client.post("/api/wse", json={
            "codes": "881001.WI,000300.SH",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] == 0

    def test_custom_indicators(self, client):
        resp = client.post("/api/wse", json={
            "codes": "881001.WI",
            "indicators": "rt_pct_chg,rt_net_mf_amt,rt_rise,rt_fall",
            "start_time": "09:30:00",
            "end_time": "11:30:00",
        })
        assert resp.status_code == 200

    def test_too_many_codes(self, client):
        resp = client.post("/api/wse", json={
            "codes": [f"WI{i:04d}.WI" for i in range(51)],
        })
        assert resp.status_code == 400

    def test_windpy_called(self, client):
        client.post("/api/wse", json={
            "codes": "881001.WI",
            "indicators": "rt_pct_chg,rt_vol",
            "start_time": "",
            "end_time": "",
        })
        assert mock_windpy.wse.called


class TestWupfEndpoint:
    """POST /api/wupf — 通用推送框架 (NEW)。"""

    def test_basic(self, client):
        resp = client.post("/api/wupf", json={
            "codes": "000001.SZ",
            "fields": "rt_last,rt_vol,rt_pct_chg",
            "cycle": "tick",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] == 0

    def test_custom_cycle(self, client):
        resp = client.post("/api/wupf", json={
            "codes": "000001.SZ",
            "fields": "rt_last",
            "cycle": "1s",
        })
        assert resp.status_code == 200

    def test_too_many_codes(self, client):
        resp = client.post("/api/wupf", json={
            "codes": [f"000{i:03d}.SZ" for i in range(201)],
            "fields": "rt_last",
            "cycle": "tick",
        })
        assert resp.status_code == 400

    def test_windpy_called(self, client):
        client.post("/api/wupf", json={
            "codes": "000001.SZ",
            "fields": "rt_last",
            "cycle": "tick",
        })
        assert mock_windpy.wupf.called


# ---------------------------------------------------------------------------
# Utility Endpoints
# ---------------------------------------------------------------------------


class TestTdaysEndpoint:
    """POST /api/tdays — 交易日。"""

    def test_basic(self, client):
        resp = client.post("/api/tdays", json={
            "begin_time": "2024-01-01",
            "end_time": "2024-01-10",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "times" in data


class TestTdayscountEndpoint:
    """POST /api/tdayscount — 交易日计数。"""

    def test_basic(self, client):
        resp = client.post("/api/tdayscount", json={
            "begin_time": "2024-01-01",
            "end_time": "2024-01-10",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data


# ---------------------------------------------------------------------------
# Other Endpoints
# ---------------------------------------------------------------------------


class TestCacheClear:
    def test_clear(self, client):
        resp = client.get("/api/cache/clear")
        assert resp.status_code == 200
        data = resp.json()
        assert "cleared" in data


class TestOpenAPI:
    def test_docs(self, client):
        resp = client.get("/docs")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_openapi_json(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["info"]["title"] == "Wind Bridge API"
        paths = data["paths"]
        # 验证所有端点都在 OpenAPI 文档中
        assert "/api/wsd" in paths
        assert "/api/wss" in paths
        assert "/api/wset" in paths
        assert "/api/wsi" in paths
        assert "/api/wsq" in paths
        assert "/api/wse" in paths
        assert "/api/wupf" in paths
        assert "/api/tdays" in paths
        assert "/api/tdayscount" in paths
        assert "/api/health" in paths
        assert "/api/stats" in paths
        assert "/api/reconnect" in paths
        assert "/api/cache/clear" in paths


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """错误处理测试。"""

    def test_windpy_exception(self, client):
        mock_windpy.wsd.side_effect = Exception("Wind connection lost")
        resp = client.post("/api/wsd", json={
            "codes": "000001.SZ",
            "fields": "close",
            "begin_time": "2024-01-01",
            "end_time": "2024-01-10",
        })
        assert resp.status_code == 500

    def test_invalid_json_body(self, client):
        """无效的 JSON 应返回 422。"""
        resp = client.post(
            "/api/wsd",
            data="not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_missing_required_field(self, client):
        """缺少必填字段应返回 422。"""
        resp = client.post("/api/wsd", json={
            "codes": "000001.SZ",
            # 缺少 fields, begin_time, end_time
        })
        assert resp.status_code == 422
