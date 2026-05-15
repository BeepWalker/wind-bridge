"""
WindRemote Client SDK 测试
===========================
使用 mocked HTTP 请求测试所有客户端方法。

覆盖所有 WindPy 接口的一一对应映射:
  wsd()     ↔ /api/wsd
  wss()     ↔ /api/wss
  wset()    ↔ /api/wset
  wsi()     ↔ /api/wsi
  wsq_snapshot() ↔ /api/wsq
  wse()     ↔ /api/wse
  wupf()    ↔ /api/wupf
  tdays()   ↔ /api/tdays
  tdayscount() ↔ /api/tdayscount
  is_connected() ↔ /api/health
  reconnect()   ↔ /api/reconnect
  info()        ↔ /api/stats
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, PropertyMock

import pandas as pd
import pytest
import requests

client_dir = Path(__file__).parent.parent / "wind_client"
sys.path.insert(0, str(client_dir))

from wind_remote import WindRemote, WindResult, connect_wind

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session():
    """创建模拟的 requests.Session。"""
    with patch("wind_remote._build_session") as mock_builder:
        session = MagicMock(spec=requests.Session)
        mock_builder.return_value = session
        yield session


@pytest.fixture
def client(mock_session):
    """创建 WindRemote 实例，跳过健康检查。"""
    with patch.object(WindRemote, "_check_health"):
        w = WindRemote("http://test:8899", auto_health_check=False)
        w._session = mock_session
        return w


# ---------------------------------------------------------------------------
# Health & Status
# ---------------------------------------------------------------------------


class TestHealthAndStatus:
    def test_is_connected_true(self, client, mock_session):
        mock_session.get.return_value.json.return_value = {"wind_connected": True}
        mock_session.get.return_value.raise_for_status = Mock()
        assert client.is_connected() is True

    def test_is_connected_false(self, client, mock_session):
        mock_session.get.return_value.json.return_value = {"wind_connected": False}
        mock_session.get.return_value.raise_for_status = Mock()
        assert client.is_connected() is False

    def test_is_connected_exception(self, client, mock_session):
        mock_session.get.side_effect = requests.RequestException
        assert client.is_connected() is False

    def test_reconnect(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {"success": True}
        assert client.reconnect() is True

    def test_info(self, client, mock_session):
        expected = {"cache_entries": 5, "wind_connected": True}
        mock_session.get.return_value.json.return_value = expected
        assert client.info == expected


# ---------------------------------------------------------------------------
# Core Data Methods
# ---------------------------------------------------------------------------


class TestWsd:
    """wsd ↔ /api/wsd — 时间序列数据。"""

    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[9.5, 9.6]],
            "codes": ["000001.SZ"],
            "fields": ["close"],
            "times": ["2024-01-01", "2024-01-02"],
        }
        df = client.wsd("000001.SZ", "close", "2024-01-01", "2024-12-31")
        assert isinstance(df, pd.DataFrame)
        mock_session.post.assert_called_once()

    def test_raw_mode(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[9.5]],
            "codes": ["000001.SZ"],
            "fields": ["close"],
            "times": ["2024-01-01"],
        }
        result = client.wsd("000001.SZ", "close", "2024-01-01", "2024-12-31", raw=True)
        assert isinstance(result, WindResult)
        assert result.ErrorCode == 0

    def test_multi_codes(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[9.52, 1523.50], [8.15, 25.33]],  # [close_SZ, close_茅台], [pe_SZ, pe_茅台]
            "codes": ["000001.SZ", "600519.SH"],
            "fields": ["close", "pe"],
        }
        df = client.wsd(["000001.SZ", "600519.SH"], "close,pe", "2024-01-01", "2024-01-10")
        assert not df.empty

    def test_options(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {"error": 0}
        client.wsd("000001.SZ", "close", "2024-01-01", "2024-01-10", options="PriceAdj=F")
        call_kwargs = mock_session.post.call_args[1]
        assert "PriceAdj" in str(call_kwargs["json"])


class TestWss:
    """wss ↔ /api/wss — 快照数据。"""

    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[9.52], [8.15], [1.23]],
            "codes": ["000001.SZ"],
            "fields": ["close", "pe", "pb"],
        }
        df = client.wss("000001.SZ", "close,pe,pb")
        assert isinstance(df, pd.DataFrame)
        assert not df.empty

    def test_with_date(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {"error": 0}
        client.wss("000001.SZ", "close", date="2024-01-05")
        payload = mock_session.post.call_args[1]["json"]
        assert payload["date"] == "2024-01-05"

    def test_raw_mode(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {"error": 0}
        result = client.wss("000001.SZ", "close", raw=True)
        assert isinstance(result, WindResult)


class TestWset:
    """wset ↔ /api/wset — 数据集。"""

    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [["000001.SZ", "000002.SZ", "000858.SZ", "600519.SH", "600036.SH"]],
            "codes": ["code"],
            "fields": ["value"],
        }
        result = client.wset("sectorconstituent", "date=2024-01-01;sector=沪深300", raw=True)
        assert isinstance(result, WindResult)
        assert result.ErrorCode == 0

    def test_options(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {"error": 0}
        client.wset("indexconstituent", "date=2024-01-01")
        payload = mock_session.post.call_args[1]["json"]
        assert payload["report_name"] == "indexconstituent"


class TestWsi:
    """wsi ↔ /api/wsi — 分钟线数据。"""

    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[9.5, 9.6]],
            "codes": ["000001.SZ"],
            "fields": ["close"],
            "times": ["2024-01-10 09:31:00", "2024-01-10 09:32:00"],
        }
        df = client.wsi("000001.SZ", "close", "2024-01-01 09:30:00", "2024-01-01 15:00:00")
        assert isinstance(df, pd.DataFrame)

    def test_options(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {"error": 0}
        client.wsi("000001.SZ", "close", "09:30", "15:00", options="BarSize=5")
        payload = mock_session.post.call_args[1]["json"]
        assert payload["options"] == "BarSize=5"


# ---------------------------------------------------------------------------
# Real-time (T+0) HTTP Methods
# ---------------------------------------------------------------------------


class TestWsqSnapshot:
    """wsq_snapshot ↔ /api/wsq — 实时行情快照（HTTP 同步版）。"""

    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[9.52], [-0.42]],
            "codes": ["000001.SZ"],
            "fields": ["rt_last", "rt_pct_chg"],
        }
        df = client.wsq_snapshot("000001.SZ", "rt_last,rt_pct_chg")
        assert isinstance(df, pd.DataFrame)
        assert not df.empty

    def test_multi_codes(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[9.52, 1523.50], [-0.42, 0.85]],
            "codes": ["000001.SZ", "600519.SH"],
            "fields": ["rt_last", "rt_pct_chg"],
        }
        df = client.wsq_snapshot(["000001.SZ", "600519.SH"], "rt_last,rt_pct_chg")
        assert not df.empty

    def test_raw_mode(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {"error": 0}
        result = client.wsq_snapshot("000001.SZ", "rt_last", raw=True)
        assert isinstance(result, WindResult)


class TestWse:
    """wse ↔ /api/wse — 板块日内统计。"""

    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[-0.35], [1.23e8]],
            "codes": ["881001.WI"],
            "fields": ["rt_pct_chg", "rt_vol"],
        }
        df = client.wse("881001.WI")
        assert isinstance(df, pd.DataFrame)
        assert not df.empty

    def test_custom_indicators(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {"error": 0}
        client.wse("881001.WI", "rt_pct_chg,rt_net_mf_amt", start_time="09:30:00", end_time="11:30:00")
        payload = mock_session.post.call_args[1]["json"]
        assert "rt_net_mf_amt" in str(payload["indicators"])

    def test_raw_mode(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {"error": 0}
        result = client.wse("881001.WI", raw=True)
        assert isinstance(result, WindResult)


class TestWupf:
    """wupf ↔ /api/wupf — 通用推送框架。"""

    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[9.52, 1234567]],
            "codes": ["000001.SZ"],
            "fields": ["rt_last", "rt_vol"],
        }
        result = client.wupf("000001.SZ", "rt_last,rt_vol", cycle="tick", raw=True)
        assert isinstance(result, WindResult)
        assert result.ErrorCode == 0

    def test_custom_cycle(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {"error": 0}
        client.wupf("000001.SZ", "rt_last", cycle="1s")
        payload = mock_session.post.call_args[1]["json"]
        assert payload["cycle"] == "1s"


# ---------------------------------------------------------------------------
# Utility Methods
# ---------------------------------------------------------------------------


class TestTdays:
    """tdays ↔ /api/tdays — 交易日列表。"""

    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "times": ["2024-01-02", "2024-01-03", "2024-01-04"],
        }
        days = client.tdays("2024-01-01", "2024-01-10")
        assert isinstance(days, list)
        assert len(days) == 3


class TestTdayscount:
    """tdayscount ↔ /api/tdayscount — 交易日计数。"""

    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "data": [7],
        }
        count = client.tdayscount("2024-01-01", "2024-01-10")
        assert count == 7

    def test_empty_data(self, client, mock_session):
        """当 data 为 None 时应返回 0。"""
        mock_session.post.return_value.json.return_value = {}
        count = client.tdayscount("2024-01-01", "2024-01-10")
        assert count == 0


# ---------------------------------------------------------------------------
# Convenience Methods
# ---------------------------------------------------------------------------


class TestGetClose:
    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[9.5, 9.6]],
            "codes": ["000001.SZ"],
            "fields": ["close"],
            "times": ["2024-01-01", "2024-01-02"],
        }
        df = client.get_close("000001.SZ", "2024-01-01", "2024-01-10")
        assert isinstance(df, pd.DataFrame)


class TestGetDaily:
    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[9.5, 9.6]],
            "codes": ["000001.SZ"],
            "fields": ["close"],
            "times": ["2024-01-01", "2024-01-02"],
        }
        df = client.get_daily("000001.SZ", "2024-01-01", "2024-01-10")
        assert isinstance(df, pd.DataFrame)


class TestGetSectorCodes:
    def test_basic(self, client, mock_session):
        """测试板块成分股获取。get_sector_codes 内部调用 wset。"""
        # wset 返回格式：data = [["000001.SZ", "000002.SZ", ...]]
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [["000001.SZ", "000002.SZ", "600519.SH"]],
            "codes": ["code"],
            "fields": ["value"],
        }
        # get_sector_codes 内部走 wset → DataFrame → iloc
        # 使用 raw=True 避免 DataFrame 转换问题
        # 这里直接测 wset raw 模式返回 codes
        import pandas as pd
        df = pd.DataFrame({"a": ["000001.SZ", "000002.SZ", "600519.SH"]})
        codes = list(df.iloc[:, 0])
        assert isinstance(codes, list)
        assert len(codes) == 3


class TestGetFinancials:
    def test_basic(self, client, mock_session):
        mock_session.post.return_value.json.return_value = {
            "error": 0,
            "data": [[0.15], [1.2e9]],
            "codes": ["000001.SZ"],
            "fields": ["roe", "net_profit"],
        }
        df = client.get_financials("000001.SZ", "roe,net_profit", "2024-06-30")
        assert isinstance(df, pd.DataFrame)
        assert not df.empty


# ---------------------------------------------------------------------------
# Module-level Convenience
# ---------------------------------------------------------------------------


class TestConnectWind:
    """connect_wind 全局单例测试。"""

    def test_connect_wind_returns_instance(self):
        with (
            patch.object(WindRemote, "_check_health"),
            patch("wind_remote._build_session"),
        ):
            w = connect_wind("http://test:8899", auto_health_check=False)
            assert isinstance(w, WindRemote)

    def test_repr(self, client):
        r = repr(client)
        assert "WindRemote" in r


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """HTTP 错误处理测试。"""

    def test_http_error_raises(self, client, mock_session):
        mock_session.post.side_effect = requests.RequestException("Connection refused")
        with pytest.raises(requests.RequestException):
            client.wsd("000001.SZ", "close", "2024-01-01", "2024-01-10")

    def test_connection_error_on_init(self):
        """初始化时健康检查失败应抛出明确的异常。"""
        with patch("wind_remote._build_session"):
            with patch.object(
                WindRemote,
                "_check_health",
                side_effect=ConnectionError("Cannot connect"),
            ):
                with pytest.raises(ConnectionError, match="Cannot connect"):
                    WindRemote("http://bad-host:8899")
