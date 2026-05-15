"""
Wind Bridge Test Suite
======================
Comprehensive test suite covering all WindPy interface wrappers.

Run with:
    pytest tests/ -v
    pytest tests/ -v --cov=wind_client --cov=wind_server
"""

from typing import Any, Dict, List, NamedTuple, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Mock WindPy Result Object
# ---------------------------------------------------------------------------

class MockWindResult:
    """
    模拟 WindPy 返回对象。
    WindResult(ErrorCode=0, Data=[[...]], Codes=[...], Fields=[...], Times=[...])
    """

    def __init__(
        self,
        error: int = 0,
        data: Optional[List] = None,
        codes: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
        times: Optional[List] = None,
    ):
        self.ErrorCode = error
        self.Data = data or []
        self.Codes = codes or []
        self.Fields = fields or []
        self.Times = times or []


# ---------------------------------------------------------------------------
# Shared Test Data — 模拟真实 Wind 行情数据
# ---------------------------------------------------------------------------

TEST_CODES_SINGLE = ["000001.SZ"]
TEST_CODES_MULTI = ["000001.SZ", "600519.SH"]

WSQ_FIELDS = ["rt_last", "rt_vol", "rt_amt", "rt_pct_chg", "rt_pe"]
WSD_FIELDS = ["close", "volume", "open", "high", "low"]

# wsq - 实时行情快照数据
WSQ_SNAPSHOT_DATA = [
    [9.52, 1234567, 87654321, -0.42, 8.15],        # 000001.SZ
    [1523.50, 2345678, 345678901, 0.85, 25.33],     # 600519.SH
]

# wsd - 时间序列数据（5个交易日）
WSD_DATA_SINGLE = [
    [9.48, 9.52, 9.55, 9.47, 9.50, 9.53],  # close values for 6 days
]
WSD_TIMES = ["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10"]

# wss - 快照数据（WindPy 格式：每个字段=一行，每个代码=一列）
WSS_DATA = [
    [9.52, 1523.50],     # close for 000001.SZ, 600519.SH
    [8.15, 25.33],       # pe for 000001.SZ, 600519.SH
    [1.23, 6.78],        # pb for 000001.SZ, 600519.SH
]

# wsi - 分钟线数据
WSI_DATA = [
    [9.50, 9.51, 9.52, 9.49, 12345],      # 5分钟K线 for 000001.SZ
    [9.52, 9.53, 9.54, 9.51, 23456],
]
WSI_TIMES = ["2024-01-10 09:31:00", "2024-01-10 09:32:00"]

# wset - 板块成分股
WSET_DATA = [["000001.SZ", "000002.SZ", "000858.SZ", "600519.SH", "600036.SH"]]

# wse - 板块日内统计
WSE_INDICATORS = ["rt_pct_chg", "rt_vol", "rt_amt", "rt_net_mf_amt", "rt_rise", "rt_fall"]
WSE_DATA = [
    [-0.35, 1.23e8, 8.76e9, -1.23e8, 12, 8],     # 881001.WI 银行板块
    [0.52, 2.34e8, 3.45e10, 5.67e8, 25, 3],       # 000300.SH 沪深300
]

# wupf - 推送数据
WUPF_DATA = [
    [9.52, 1234567, 87654321, -0.42],      # 000001.SZ
]

# tdays 数据
TDAYS_DATA = [
    ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
     "2024-01-08", "2024-01-09", "2024-01-10"],
]

# ---------------------------------------------------------------------------
# Factory Helpers
# ---------------------------------------------------------------------------

def make_wsd_result():
    """创建模拟 wsd 返回结果。"""
    return MockWindResult(
        error=0,
        data=WSD_DATA_SINGLE,
        codes=TEST_CODES_SINGLE,
        fields=WSD_FIELDS[:1],  # close only
        times=WSD_TIMES,
    )


def make_wsd_multi_result():
    """创建多指标 wsd 返回结果。"""
    data = [
        [9.48, 9.52, 9.55, 9.47, 9.50, 9.53],                    # close
        [1234567, 2345678, 3456789, 2345678, 1234567, 2345678],   # volume
    ]
    return MockWindResult(
        error=0,
        data=data,
        codes=TEST_CODES_SINGLE,
        fields=WSD_FIELDS[:2],  # close, volume
        times=WSD_TIMES,
    )


def make_wss_result():
    """创建模拟 wss 返回结果。"""
    return MockWindResult(
        error=0,
        data=WSS_DATA,
        codes=TEST_CODES_MULTI,
        fields=WSD_FIELDS[:3],  # close, pe, pb
    )


def make_wsq_result():
    """创建模拟 wsq 返回结果。"""
    return MockWindResult(
        error=0,
        data=WSQ_SNAPSHOT_DATA,
        codes=TEST_CODES_MULTI,
        fields=WSQ_FIELDS,
    )


def make_wse_result():
    """创建模拟 wse 返回结果。"""
    return MockWindResult(
        error=0,
        data=WSE_DATA,
        codes=["881001.WI", "000300.SH"],
        fields=WSE_INDICATORS,
    )


def make_wupf_result():
    """创建模拟 wupf 返回结果。"""
    return MockWindResult(
        error=0,
        data=WUPF_DATA,
        codes=TEST_CODES_SINGLE,
        fields=["rt_last", "rt_vol", "rt_amt", "rt_pct_chg"],
    )
