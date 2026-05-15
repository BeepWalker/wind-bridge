"""
Wind Remote Client — Ubuntu 端 SDK
===================================
生产可用的 Wind 数据访问客户端，通过 HTTP 网关连接到 Windows 上的 Wind 终端。

设计目标：
- 与原生 windpy 体验一致的 API
- 自动重试 + 指数退避
- 连接池复用
- 完整的类型标注
- 支持 Pandas DataFrame

快速开始：
    from wind_remote import WindRemote
    w = WindRemote("http://192.168.1.100:8899")

    # 基本用法 - 完全兼容 windpy 风格
    df = w.wsd("000001.SZ,000002.SZ", "close,volume", "2024-01-01", "2024-12-31")
    df = w.wss("000001.SZ", "close,pe", date="2024-06-15")

环境变量配置（推荐）：
    export WIND_API_URL="http://192.168.1.100:8899"
    export WIND_API_KEY=""  # 可选
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union, Callable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import websockets

__version__ = "1.0.0"
__all__ = ["WindRemote", "WindResult", "connect_wind"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("wind_remote")


def _get_logger():
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(os.getenv("WIND_LOG_LEVEL", "INFO"))
    return logger


# ---------------------------------------------------------------------------
# WindResult - 模拟 windpy 的返回对象
# ---------------------------------------------------------------------------

class WindResult:
    """
    模拟 WindPy 返回对象，提供 .ErrorCode, .Data, .Codes, .Fields, .Times 属性。
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

    def to_dataframe(self) -> pd.DataFrame:
        """
        将 Wind 返回结果转换为 DataFrame。

        wsd 结果：index=Times, columns=Codes（单指标时）
                 index=Times, columns=MultiIndex(Felds, Codes)（多指标时）
        wss 结果：index=Felds, columns=Codes
        """
        if not self.Data:
            return pd.DataFrame()

        if self.Times and len(self.Data) == 1:
            # wsd 单指标：Data[0] 是嵌套列表
            df = pd.DataFrame(self.Data[0], index=self.Times, columns=self.Codes)
            df.index = pd.to_datetime(df.index)
            df.index.name = "date"
            return df

        if self.Times and len(self.Data) > 1:
            # wsd 多指标：构建 MultiIndex columns
            arrays = []
            for i, field in enumerate(self.Fields):
                df_field = pd.DataFrame(self.Data[i], index=self.Times, columns=self.Codes)
                arrays.append(df_field)
            df = pd.concat(arrays, axis=1, keys=self.Fields)
            df.index = pd.to_datetime(df.index)
            df.index.name = "date"
            return df

        if not self.Times:
            # wss 快照：Data[i] 是一个列表
            df = pd.DataFrame(self.Data, index=self.Fields, columns=self.Codes)
            return df

        return pd.DataFrame(self.Data)

    def __repr__(self):
        return f"WindResult(ErrorCode={self.ErrorCode}, shape={len(self.Data)}x{len(self.Codes) if self.Codes else 0})"


# ---------------------------------------------------------------------------
# HTTP Session with Retry
# ---------------------------------------------------------------------------

def _build_session(
    max_retries: int = 3,
    backoff_factor: float = 1.0,
    timeout: Tuple[float, float] = (5.0, 60.0),  # (connect, read)
    pool_connections: int = 10,
    pool_maxsize: int = 20,
    api_key: Optional[str] = None,
) -> requests.Session:
    """
    构建带重试机制的 requests Session。

    Args:
        max_retries: 最大重试次数
        backoff_factor: 退避因子（实际退避 = backoff_factor * (2 ** (retry-1))）
        timeout: (连接超时, 读取超时)
        pool_connections: 连接池大小
        pool_maxsize: 连接池最大连接数
        api_key: Bearer Token
    """
    session = requests.Session()

    # 重试策略
    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # 默认超时
    session.timeout = timeout

    # 认证
    if api_key:
        session.headers.update({"Authorization": f"Bearer {api_key}"})

    session.headers.update({"User-Agent": f"wind-remote/{__version__}"})

    return session


# ---------------------------------------------------------------------------
# WindRemote - 主客户端类
# ---------------------------------------------------------------------------

class WindRemote:
    """
    Wind 数据远程访问客户端。

    用法:
        w = WindRemote("http://192.168.1.100:8899")

        # 时间序列
        df = w.wsd("000001.SZ", "close,volume", "2024-01-01", "2024-12-31")

        # 快照
        df = w.wss("000001.SZ,000002.SZ", "close,pe", date="2024-06-15")

        # 数据集
        codes = w.wset("sectorconstituent", "date=2024-01-01;sector=沪深300")

        # 分钟K线
        df = w.wsi("000001.SZ", "close", "2024-01-01 09:30:00", "2024-01-01 15:00:00")

        # 返回 WindResult 对象（含 ErrorCode）
        result = w.wsd_raw("000001.SZ", "close", "2024-01-01", "2024-12-31")

        # 实时行情 (T+0)
        async def on_data(data):
            print(data)
        w = WindRemote("http://192.168.1.100:8899")
        await w.wsq("510050.SH", "rt_last,rt_vol", callback=on_data)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        timeout: Tuple[float, float] = (5.0, 120.0),
        auto_health_check: bool = True,
    ):
        """
        Args:
            base_url: Wind API 服务器地址，例如 "http://192.168.1.100:8899"
                      默认从环境变量 WIND_API_URL 读取
            api_key: Bearer Token，默认从环境变量 WIND_API_KEY 读取
            max_retries: HTTP 请求最大重试次数
            backoff_factor: 退避因子
            timeout: (连接超时, 读取超时)，默认120秒适合大查询
            auto_health_check: 初始化时是否自动健康检查
        """
        self.base_url = (base_url or os.getenv("WIND_API_URL", "http://localhost:8899")).rstrip("/")
        api_key = api_key or os.getenv("WIND_API_KEY") or None

        self._session = _build_session(
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            timeout=timeout,
            api_key=api_key,
        )
        self._log = _get_logger()

        if auto_health_check:
            self._check_health()

    # ------------------------------------------------------------------
    # Health & Status
    # ------------------------------------------------------------------

    def _check_health(self) -> Dict:
        """健康检查。成功返回 dict，失败抛出异常。"""
        try:
            resp = self._session.get(f"{self.base_url}/api/health", timeout=(3, 5))
            resp.raise_for_status()
            data = resp.json()
            self._log.info(
                "Wind Bridge 连接成功 | wind_connected=%(wind_connected)s",
                data,
            )
            if not data.get("wind_connected"):
                self._log.warning("⚠️ Wind 终端未连接，数据请求将失败！")
            return data
        except requests.RequestException as e:
            self._log.error(f"无法连接到 Wind Bridge 服务器: {self.base_url} — {e}")
            raise ConnectionError(
                f"Wind Bridge ({self.base_url}) 无法连接。请确认：\n"
                f"  1. Windows 机器已开机且运行 wind_api_server.py\n"
                f"  2. 网络互通（ping {self.base_url.split('://')[1].split(':')[0]}）\n"
                f"  3. 防火墙已放行端口\n"
                f"  错误详情: {e}"
            )

    def is_connected(self) -> bool:
        """检查 Wind 终端是否已连接。"""
        try:
            resp = self._session.get(f"{self.base_url}/api/health", timeout=(3, 5))
            return resp.json().get("wind_connected", False)
        except Exception:
            return False

    def reconnect(self) -> bool:
        """触发 Wind 重连。"""
        resp = self._session.post(f"{self.base_url}/api/reconnect", timeout=30)
        return resp.json().get("success", False)

    @property
    def info(self) -> Dict:
        """获取服务器基本信息。"""
        resp = self._session.get(f"{self.base_url}/api/stats")
        return resp.json()

    # ------------------------------------------------------------------
    # 核心数据方法
    # ------------------------------------------------------------------

    def wsd(
        self,
        codes: Union[str, List[str]],
        fields: Union[str, List[str]],
        begin_time: str,
        end_time: str,
        options: str = "",
        raw: bool = False,
    ) -> Union[pd.DataFrame, WindResult]:
        """
        获取 Wind 时间序列数据。

        Args:
            codes: 证券代码，如 "000001.SZ" 或 ["000001.SZ", "000002.SZ"]
            fields: 指标，如 "close" 或 ["close", "volume"]
            begin_time: 开始日期 "2024-01-01"
            end_time: 结束日期 "2024-12-31"
            options: 附加参数，如 "PriceAdj=F"
            raw: True 返回 WindResult 对象，False 返回 DataFrame

        Returns:
            DataFrame (raw=False) 或 WindResult (raw=True)
        """
        payload = {
            "codes": _to_list(codes),
            "fields": _to_list(fields),
            "begin_time": begin_time,
            "end_time": end_time,
            "options": options,
        }
        result = self._post("/api/wsd", payload)
        if raw:
            return result
        return result.to_dataframe()

    def wss(
        self,
        codes: Union[str, List[str]],
        fields: Union[str, List[str]],
        date: str = "",
        options: str = "",
        raw: bool = False,
    ) -> Union[pd.DataFrame, WindResult]:
        """
        获取 Wind 快照数据。

        Args:
            codes: 证券代码
            fields: 指标，如 "close,pe,pb"
            date: 快照日期，"" 表示最新
            options: 附加参数，如 "tradeDate=2024-01-01;priceAdj=U"
            raw: True 返回 WindResult 对象
        """
        payload = {
            "codes": _to_list(codes),
            "fields": _to_list(fields),
            "date": date,
            "options": options,
        }
        result = self._post("/api/wss", payload)
        if raw:
            return result
        return result.to_dataframe()

    def wset(
        self,
        report_name: str,
        options: str = "",
        raw: bool = False,
    ) -> Union[pd.DataFrame, WindResult]:
        """
        获取 Wind 数据集。

        Args:
            report_name: 报表名称，如 "sectorconstituent", "indexconstituent"
            options: 筛选项，如 "date=2024-01-01;sector=沪深300"
            raw: True 返回 WindResult 对象
        """
        payload = {"report_name": report_name, "options": options}
        result = self._post("/api/wset", payload)
        if raw:
            return result
        return result.to_dataframe()

    def wsi(
        self,
        codes: Union[str, List[str]],
        fields: Union[str, List[str]],
        begin_time: str,
        end_time: str,
        options: str = "",
        raw: bool = False,
    ) -> Union[pd.DataFrame, WindResult]:
        """
        获取 Wind 分钟线数据。

        Args:
            codes: 证券代码
            fields: 指标
            begin_time: 开始时间 "2024-01-01 09:30:00"
            end_time: 结束时间 "2024-01-01 15:00:00"
            options: 附加参数，如 "BarSize=1"
            raw: True 返回 WindResult 对象
        """
        payload = {
            "codes": _to_list(codes),
            "fields": _to_list(fields),
            "begin_time": begin_time,
            "end_time": end_time,
            "options": options,
        }
        result = self._post("/api/wsi", payload)
        if raw:
            return result
        return result.to_dataframe()

    def tdays(self, begin_time: str, end_time: str) -> List[str]:
        """获取交易日列表。"""
        resp = self._session.post(
            f"{self.base_url}/api/tdays",
            json={"begin_time": begin_time, "end_time": end_time},
        ).json()
        return resp.get("times", [])

    def tdayscount(self, begin_time: str, end_time: str) -> int:
        """获取区间内交易日数量。"""
        resp = self._session.post(
            f"{self.base_url}/api/tdayscount",
            json={"begin_time": begin_time, "end_time": end_time},
        ).json()
        return resp.get("data", [0])[0] if resp.get("data") else 0

    # ------------------------------------------------------------------
    # 实时行情 (T+0) - WebSocket
    # ------------------------------------------------------------------

    async def wsq(
        self,
        codes: Union[str, List[str]],
        fields: Union[str, List[str]],
        callback: Callable[[Dict], None],
        ping_interval: float = 20.0,
    ):
        """
        订阅Wind实时行情 (T+0) 数据流。

        Args:
            codes: 证券代码，如 "510050.SH" 或 ["510050.SH", "510300.SH"]
            fields: 指标，如 "rt_last,rt_vol" 或 ["rt_last", "rt_vol"]
            callback: 回调函数，用于处理接收到的实时数据。函数签名应为 func(data: Dict)
            ping_interval: 向服务器发送ping的间隔（秒），用于保持连接。

        Example:
            async def on_data(data):
                if data.get("error") == 0:
                    df = pd.DataFrame(data["data"], index=data["fields"], columns=data["codes"])
                    print(df)
                else:
                    print(f"Error: {data.get('error')}")

            w = WindRemote("ws://192.168.1.100:8899")
            await w.wsq("510050.SH", "rt_last,rt_vol", callback=on_data)
        """
        # 构建WebSocket URL
        ws_url = self.base_url.replace("http", "ws") + "/ws/realtime"
        # 构建订阅参数
        payload = {
            "codes": _to_list(codes),
            "fields": _to_list(fields),
        }

        async with websockets.connect(ws_url) as websocket:
            # 发送订阅消息
            await websocket.send(json.dumps(payload))
            # 等待订阅成功确认
            response = await websocket.recv()
            self._log.info(f"实时行情订阅: {response}")

            # 启动后台ping任务以保持连接
            async def keep_alive():
                while True:
                    try:
                        await asyncio.sleep(ping_interval)
                        await websocket.ping()
                        self._log.debug("Ping sent to keep connection alive")
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        self._log.warning(f"Ping failed: {e}")
                        break

            keep_alive_task = asyncio.create_task(keep_alive())

            try:
                # 持续接收数据
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        # 调用用户回调函数
                        callback(data)
                    except Exception as e:
                        self._log.error(f"处理实时数据时发生错误: {e}")
            except websockets.exceptions.ConnectionClosed as e:
                self._log.info(f"实时行情连接已关闭: {e}")
            finally:
                # 取消ping任务
                keep_alive_task.cancel()
                try:
                    await keep_alive_task
                except asyncio.CancelledError:
                    pass

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def get_close(
        self,
        codes: Union[str, List[str]],
        begin_time: str,
        end_time: str,
    ) -> pd.DataFrame:
        """便捷：获取收盘价。"""
        return self.wsd(codes, "close", begin_time, end_time)

    def get_daily(
        self,
        codes: Union[str, List[str]],
        begin_time: str,
        end_time: str,
    ) -> pd.DataFrame:
        """便捷：获取日频行情（开/高/低/收/量/额）。"""
        return self.wsd(codes, "open,high,low,close,volume,amt", begin_time, end_time)

    def get_sector_codes(self, sector: str, date: str = "") -> List[str]:
        """便捷：获取板块成分股代码。sector 如 "沪深300", "中证500"."""
        today = date or datetime.now().strftime("%Y-%m-%d")
        df = self.wset("sectorconstituent", f"date={today};sector={sector}")
        if df.empty:
            return []
        return list(df.iloc[:, 0]) if df.shape[1] == 1 else list(df.columns)

    def get_financials(
        self,
        codes: Union[str, List[str]],
        fields: Union[str, List[str]],
        report_date: str,
    ) -> pd.DataFrame:
        """便捷：获取财务报表数据。fields 如 "roe,net_profit,revenue"."""
        return self.wss(codes, fields, f"reportDate={report_date};rptType=1")

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _post(self, endpoint: str, payload: Dict) -> WindResult:
        """内部 POST 请求，返回 WindResult。"""
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self._session.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return WindResult(
                error=data.get("error", -1),
                data=data.get("data"),
                codes=data.get("codes"),
                fields=data.get("fields"),
                times=data.get("times"),
            )
        except requests.RequestException as e:
            self._log.error(f"请求失败 [{endpoint}]: {e}")
            raise

    def __repr__(self):
        status = "✓" if self.is_connected() else "✗"
        return f"WindRemote({self.base_url}, connected={status})"

    def close(self):
        """关闭 HTTP 会话。"""
        self._session.close()


# ---------------------------------------------------------------------------
# Module-level Helpers
# ---------------------------------------------------------------------------

def _to_list(value: Union[str, List[str]]) -> List[str]:
    """将字符串或列表统一转为列表。"""
    if isinstance(value, str):
        # 支持中英文逗号
        return [v.strip() for v in value.replace("，", ",").split(",") if v.strip()]
    return list(value)


# ---------------------------------------------------------------------------
# Module-level Convenience (全局单例)
# ---------------------------------------------------------------------------

_global_instance: Optional[WindRemote] = None

def connect_wind(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    **kwargs,
) -> WindRemote:
    """
    连接 Wind 远程服务，返回全局单例。

    >>> import wind_remote
    >>> w = wind_remote.connect_wind("http://192.168.1.100:8899")
    >>> df = w.wsd("000001.SZ", "close", "2024-01-01", "2024-12-31")
    """
    global _global_instance
    if _global_instance is None or base_url:
        _global_instance = WindRemote(base_url=base_url, api_key=api_key, **kwargs)
    return _global_instance


# ---------------------------------------------------------------------------
# Quick Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Wind Remote Client Test")
    parser.add_argument("--url", default=os.getenv("WIND_API_URL", "http://localhost:8899"), help="Wind Bridge URL")
    args = parser.parse_args()

    print(f"Testing connection to {args.url}...")
    w = WindRemote(args.url)

    print(f"  Status: {'✓ Connected' if w.is_connected() else '✗ Wind terminal not connected'}")
    print(f"  Server: {w.info}")

    # 快速测试
    print("\n--- WSD Test ---")
    try:
        df = w.wsd("000001.SZ", "close", "2024-01-01", "2024-01-10")
        print(df)
    except Exception as e:
        print(f"  Error: {e}")

    print("\n--- WSS Test ---")
    try:
        df = w.wss("000001.SZ", "close,pe,pb")
        print(df)
    except Exception as e:
        print(f"  Error: {e}")

    print("\nDone!")
