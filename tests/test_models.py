"""
Pydantic Request Model Validation Tests
========================================
验证每个 API 端点的输入参数模型是否正确处理边界条件。
"""

import pytest
from pydantic import ValidationError

# 将 server 模块添加到 sys.path，以便导入模型
import sys
from pathlib import Path

server_dir = Path(__file__).parent.parent / "wind_server"
sys.path.insert(0, str(server_dir))

from wind_api_server import (
    WsdParams,
    WssParams,
    WsetParams,
    WsiParams,
    WsqParams,
    WseParams,
    WupfParams,
)


class TestWsdParams:
    """时间序列数据请求参数测试。"""

    def test_basic(self):
        p = WsdParams(
            codes="000001.SZ,000002.SZ",
            fields="close,volume",
            begin_time="2024-01-01",
            end_time="2024-12-31",
        )
        assert p.codes == ["000001.SZ", "000002.SZ"]
        assert p.fields == ["close", "volume"]

    def test_list_input(self):
        p = WsdParams(
            codes=["000001.SZ"],
            fields=["close"],
            begin_time="2024-01-01",
            end_time="2024-12-31",
        )
        assert p.codes == ["000001.SZ"]

    def test_chinese_comma(self):
        p = WsdParams(
            codes="000001.SZ，000002.SZ",
            fields="close，volume",
            begin_time="2024-01-01",
            end_time="2024-12-31",
        )
        assert p.codes == ["000001.SZ", "000002.SZ"]
        assert p.fields == ["close", "volume"]

    def test_empty_code(self):
        """空代码应被过滤掉。"""
        p = WsdParams(
            codes="000001.SZ, ,,000002.SZ",
            fields="close",
            begin_time="2024-01-01",
            end_time="2024-12-31",
        )
        assert p.codes == ["000001.SZ", "000002.SZ"]

    def test_default_options(self):
        p = WsdParams(
            codes="000001.SZ",
            fields="close",
            begin_time="2024-01-01",
            end_time="2024-12-31",
        )
        assert p.options == ""


class TestWssParams:
    """快照数据请求参数测试。"""

    def test_basic(self):
        p = WssParams(
            codes="000001.SZ",
            fields="close,pe,pb",
            date="2024-01-05",
        )
        assert p.codes == ["000001.SZ"]
        assert p.fields == ["close", "pe", "pb"]

    def test_empty_date(self):
        """空日期表示最新。"""
        p = WssParams(
            codes="000001.SZ",
            fields="close",
            date="",
        )
        assert p.date == ""

    def test_options(self):
        p = WssParams(
            codes="000001.SZ",
            fields="close",
            options="tradeDate=2024-01-01;priceAdj=U",
        )
        assert p.options == "tradeDate=2024-01-01;priceAdj=U"


class TestWsetParams:
    """数据集请求参数测试。"""

    def test_basic(self):
        p = WsetParams(report_name="sectorconstituent", options="date=2024-01-01;sector=沪深300")
        assert p.report_name == "sectorconstituent"

    def test_default_options(self):
        p = WsetParams(report_name="indexconstituent")
        assert p.options == ""


class TestWsiParams:
    """分钟线数据请求参数测试。"""

    def test_basic(self):
        p = WsiParams(
            codes="000001.SZ",
            fields="close,volume",
            begin_time="2024-01-01 09:30:00",
            end_time="2024-01-01 15:00:00",
            options="BarSize=1",
        )
        assert p.codes == ["000001.SZ"]
        assert p.fields == ["close", "volume"]
        assert p.options == "BarSize=1"


class TestWsqParams:
    """实时行情请求参数测试。"""

    def test_basic(self):
        p = WsqParams(codes="000001.SZ,510050.SH", fields="rt_last,rt_vol,rt_pct_chg")
        assert len(p.codes) == 2
        assert len(p.fields) == 3

    def test_single_code(self):
        p = WsqParams(codes="000001.SZ", fields="rt_last")
        assert p.codes == ["000001.SZ"]
        assert p.fields == ["rt_last"]

    def test_list_input(self):
        p = WsqParams(codes=["000001.SZ"], fields=["rt_last", "rt_vol"])
        assert isinstance(p.codes, list)
        assert isinstance(p.fields, list)


class TestWseParams:
    """板块日内统计请求参数测试。"""

    def test_default_indicators(self):
        """WSE 的 indicators 有默认值。"""
        p = WseParams(codes="881001.WI,000300.SH")
        assert len(p.indicators) > 0
        assert "rt_pct_chg" in p.indicators
        assert "rt_net_mf_amt" in p.indicators

    def test_custom_indicators(self):
        p = WseParams(
            codes="881001.WI",
            indicators="rt_pct_chg,rt_vol",
            start_time="09:30:00",
            end_time="11:30:00",
        )
        assert p.indicators == ["rt_pct_chg", "rt_vol"]

    def test_empty_time(self):
        """空时间表示开盘到最新。"""
        p = WseParams(codes="881001.WI", indicators="rt_pct_chg", start_time="", end_time="")
        assert p.start_time == ""
        assert p.end_time == ""


class TestWupfParams:
    """通用推送框架请求参数测试。"""

    def test_default_fields_and_cycle(self):
        p = WupfParams(codes="000001.SZ")
        assert p.cycle == "tick"
        assert "rt_last" in p.fields

    def test_custom_cycle(self):
        p = WupfParams(codes="000001.SZ", fields="rt_last", cycle="1s")
        assert p.cycle == "1s"

    def test_invalid_cycle(self):
        """非法的 cycle 应该触发验证错误。"""
        with pytest.raises(ValidationError):
            WupfParams(codes="000001.SZ", fields="rt_last", cycle="10m")

    def test_all_valid_cycles(self):
        for cycle in ("tick", "1s", "3s", "5s", "30s", "1m", "5m"):
            p = WupfParams(codes="000001.SZ", fields="rt_last", cycle=cycle)
            assert p.cycle == cycle
