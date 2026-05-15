"""
WindResult 核心数据类测试
==========================
验证 WindResult 的构造、DataFrame 转换、边界情况。
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

client_dir = Path(__file__).parent.parent / "wind_client"
sys.path.insert(0, str(client_dir))

from wind_remote import WindResult

TIMES = ["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10"]


class TestWindResultConstruction:
    """WindResult 构造测试。"""

    def test_empty(self):
        result = WindResult()
        assert result.ErrorCode == 0
        assert result.Data == []
        assert result.Codes == []
        assert result.Fields == []
        assert result.Times == []

    def test_with_data(self):
        result = WindResult(
            error=0,
            data=[[9.48, 9.52, 9.55, 9.47, 9.50, 9.53]],
            codes=["000001.SZ"],
            fields=["close"],
            times=TIMES,
        )
        assert result.ErrorCode == 0
        assert len(result.Data) == 1
        assert len(result.Data[0]) == 6
        assert result.Codes == ["000001.SZ"]

    def test_error_code(self):
        result = WindResult(error=-1)
        assert result.ErrorCode == -1
        assert result.to_dataframe().empty

    def test_repr(self):
        result = WindResult(error=0, data=[[1]], codes=["A"], fields=["f1"])
        r = repr(result)
        assert "WindResult" in r
        assert "ErrorCode" in r


class TestWindResultToDataFrame:
    """DataFrame 转换测试。"""

    def test_wsd_single_field(self):
        """wsd 单指标：index=Times, columns=Codes。"""
        result = WindResult(
            error=0,
            data=[[9.48, 9.52, 9.55, 9.47, 9.50, 9.53]],
            codes=["000001.SZ"],
            fields=["close"],
            times=TIMES,
        )
        df = result.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert list(df.columns) == ["000001.SZ"]
        assert len(df) == 6
        assert df.index.name == "date"

    def test_wsd_multi_field(self):
        """wsd 多指标：MultiIndex columns (Fields, Codes)。"""
        result = WindResult(
            error=0,
            data=[
                [9.48, 9.52, 9.55, 9.47, 9.50, 9.53],
                [12345, 23456, 34567, 23456, 12345, 23456],
            ],
            codes=["000001.SZ"],
            fields=["close", "volume"],
            times=TIMES,
        )
        df = result.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert isinstance(df.columns, pd.MultiIndex)

    def test_wss_snapshot(self):
        """wss 快照：index=Fields, columns=Codes。"""
        result = WindResult(
            error=0,
            data=[[9.52, 1523.50], [8.15, 25.33], [1.23, 6.78]],
            codes=["000001.SZ", "600519.SH"],
            fields=["close", "pe", "pb"],
        )
        df = result.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert list(df.columns) == ["000001.SZ", "600519.SH"]

    def test_wsq_snapshot(self):
        """wsq 快照：与 wss 同结构。"""
        result = WindResult(
            error=0,
            data=[[9.52, 1523.50], [-0.42, 0.85]],
            codes=["000001.SZ", "600519.SH"],
            fields=["rt_last", "rt_pct_chg"],
        )
        df = result.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert list(df.columns) == ["000001.SZ", "600519.SH"]

    def test_empty_data(self):
        """空数据的 DataFrame 应是空表。"""
        result = WindResult(data=[], codes=["000001.SZ"], fields=["close"])
        df = result.to_dataframe()
        assert df.empty

    def test_no_times_single_row(self):
        """没有 Times 但有数据时的降级行为。"""
        result = WindResult(data=[[1, 2]], codes=["A", "B"], fields=["f1"])
        df = result.to_dataframe()
        assert not df.empty
