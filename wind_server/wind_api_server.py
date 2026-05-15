import asyncio
import json
import logging
import os
import sys
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from functools import lru_cache, wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml
from fastapi import FastAPI, HTTPException, Request, Security, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Configuration Loader
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    """加载配置文件，缺失时使用内置默认值。"""
    builtin = {
        "server": {"host": "0.0.0.0", "port": 8899, "workers": 2, "api_key": ""},
        "wind": {
            "reconnect_attempts": 5,
            "reconnect_interval": 10,
            "health_check_interval": 600,
            "data_cache_seconds": 300,
        },
        "rate_limit": {"enabled": True, "requests_per_second": 5, "burst_size": 10},
        "logging": {
            "level": "INFO",
            "file": "wind_server.log",
            "max_bytes": 10 * 1024 * 1024,
            "backup_count": 5,
        },
        "monitoring": {"prometheus": True},
        "websocket": {"enabled": True, "max_connections": 100, "ping_interval": 20, "data_update_interval": 1},
    }
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            user_config = yaml.safe_load(fh) or {}
        # 深度合并（仅一层）
        for section, values in user_config.items():
            if section in builtin and isinstance(values, dict):
                builtin[section].update(values)
            else:
                builtin[section] = values
    return builtin


CONFIG = load_config()

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> logging.Logger:
    log_cfg = CONFIG["logging"]
    logger = logging.getLogger("wind_bridge")
    logger.setLevel(getattr(logging, log_cfg["level"].upper(), logging.INFO))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE))
    logger.addHandler(console)

    # 文件 handler（滚动）
    log_file = Path(log_cfg["file"])
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=log_cfg["max_bytes"],
        backupCount=log_cfg["backup_count"],
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE))
    logger.addHandler(file_handler)

    return logger


logger = setup_logging()

# ---------------------------------------------------------------------------
# WindPy Connection Manager
# ---------------------------------------------------------------------------

class WindConnection:
    """
    Wind终端连接管理器。
    提供自动重连、健康检查、登录状态查询。
    """

    def __init__(self):
        self._w = None
        self._last_connected: Optional[datetime] = None
        self._reconnect_attempts = CONFIG["wind"]["reconnect_attempts"]
        self._reconnect_interval = CONFIG["wind"]["reconnect_interval"]

    @property
    def w(self):
        """延迟导入 WindPy，避免无 Wind 环境导入失败。"""
        if self._w is None:
            try:
                from WindPy import w as wind_obj

                self._w = wind_obj
            except ImportError:
                logger.error("WindPy 未安装！请在Windows上 pip install WindPy")
                raise RuntimeError("WindPy import failed — 请确认Wind终端已安装且windpy可用")
            except Exception as e:
                logger.error(f"WindPy 初始化失败: {e}")
                raise
        return self._w

    def connect(self) -> bool:
        """连接 Wind 终端。返回是否成功。"""
        for attempt in range(1, self._reconnect_attempts + 1):
            try:
                logger.info(f"正在连接 Wind 终端 (第{attempt}次)...")
                result = self.w.start()
                if hasattr(result, "ErrorCode") and result.ErrorCode == 0:
                    self._last_connected = datetime.now()
                    logger.info("✅ Wind 终端连接成功")
                    return True
                else:
                    logger.warning(f"Wind 连接返回非零错误码: {getattr(result, 'ErrorCode', 'Unknown')}")
            except Exception as e:
                logger.error(f"Wind 连接异常 (第{attempt}次): {e}")
            if attempt < self._reconnect_attempts:
                time.sleep(self._reconnect_interval)
        logger.critical("❌ Wind 终端连接失败，已达最大重试次数")
        return False

    def ensure_connected(self) -> bool:
        """确保连接状态。自动重连。"""
        try:
            if self.w.isconnected():
                return True
        except Exception:
            pass
        logger.warning("Wind 终端已断开，尝试重连...")
        return self.connect()

    def is_connected(self) -> bool:
        try:
            return self.w.isconnected()
        except Exception:
            return False

    @property
    def last_connected(self) -> Optional[datetime]:
        return self._last_connected


# 全局单例
wind_conn = WindConnection()

# ---------------------------------------------------------------------------
# Rate Limiter (Token Bucket)
# ---------------------------------------------------------------------------

class TokenBucket:
    """令牌桶限流器。"""

    def __init__(self, rate: float, burst: int):
        self.rate = rate  # tokens/sec
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


_rate_limit_cfg = CONFIG["rate_limit"]
_bucket = TokenBucket(
    rate=_rate_limit_cfg["requests_per_second"],
    burst=_rate_limit_cfg["burst_size"],
)

def rate_limit(request: Request):
    """FastAPI 依赖：若超过限流则返回 429。"""
    if not _rate_limit_cfg["enabled"]:
        return
    if not _bucket.consume():
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后重试 (Wind 数据源频率限制)",
        )


# ---------------------------------------------------------------------------
# Auth (Optional)
# ---------------------------------------------------------------------------

API_KEY = CONFIG["server"].get("api_key", "")
security = HTTPBearer(auto_error=False)

def verify_auth(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)):
    """如果配置了 api_key 则强制验证。"""
    if not API_KEY:
        return
    if credentials is None or credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# Caching (Simple in-memory)
# ---------------------------------------------------------------------------

_cache: Dict[str, Tuple[Any, float]] = {}
CACHE_TTL = CONFIG["wind"]["data_cache_seconds"]

def _cache_key(func_name: str, **kwargs) -> str:
    return f"{func_name}:{sorted(kwargs.items())}"

def cached_query(ttl: int = CACHE_TTL):
    """装饰器：对查询结果进行内存缓存。"""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if ttl <= 0:
                return await func(*args, **kwargs)
            key = _cache_key(func.__name__, **kwargs)
            now = time.time()
            if key in _cache and now - _cache[key][1] < ttl:
                logger.debug(f"缓存命中: {key}")
                return _cache[key][0]
            result = await func(*args, **kwargs)
            _cache[key] = (result, now)
            # 简单清理过期条目
            expired = [k for k, v in _cache.items() if now - v[1] > ttl]
            for k in expired:
                del _cache[k]
            return result

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Pydantic Request Models
# ---------------------------------------------------------------------------

def _validate_codes(v: Union[str, List[str]]) -> List[str]:
    """统一 code 格式，支持逗号分隔字符串或列表。"""
    if isinstance(v, str):
        return [c.strip() for c in v.replace("，", ",").split(",") if c.strip()]
    return v

class WsdParams(BaseModel):
    """时间序列数据请求参数。"""

    codes: Union[str, List[str]]
    fields: Union[str, List[str]]
    begin_time: str
    end_time: str
    options: str = ""

    @field_validator("codes", mode="before")
    @classmethod
    def normalize_codes(cls, v):
        return _validate_codes(v)

    @field_validator("fields", mode="before")
    @classmethod
    def normalize_fields(cls, v):
        if isinstance(v, str):
            return [f.strip() for f in v.replace("，", ",").split(",") if f.strip()]
        return v

class WssParams(BaseModel):
    """快照数据请求参数。"""

    codes: Union[str, List[str]]
    fields: Union[str, List[str]]
    date: str = Field(default="")
    options: str = ""

    @field_validator("codes", mode="before")
    @classmethod
    def normalize_codes(cls, v):
        return _validate_codes(v)

    @field_validator("fields", mode="before")
    @classmethod
    def normalize_fields(cls, v):
        if isinstance(v, str):
            return [f.strip() for f in v.replace("，", ",").split(",") if f.strip()]
        return v

class WsetParams(BaseModel):
    """数据集请求参数。"""

    report_name: str
    options: str = ""

class WsiParams(BaseModel):
    """分钟线数据请求参数。"""

    codes: Union[str, List[str]]
    fields: Union[str, List[str]]
    begin_time: str
    end_time: str
    options: str = ""

    @field_validator("codes", mode="before")
    @classmethod
    def normalize_codes(cls, v):
        return _validate_codes(v)

    @field_validator("fields", mode="before")
    @classmethod
    def normalize_fields(cls, v):
        if isinstance(v, str):
            return [f.strip() for f in v.replace("，", ",").split(",") if f.strip()]
        return v

class WsqParams(BaseModel):
    """实时行情 (T+0) 请求参数。"""

    codes: Union[str, List[str]]
    fields: Union[str, List[str]]

    @field_validator("codes", mode="before")
    @classmethod
    def normalize_codes(cls, v):
        return _validate_codes(v)

    @field_validator("fields", mode="before")
    @classmethod
    def normalize_fields(cls, v):
        if isinstance(v, str):
            return [f.strip() for f in v.replace("，", ",").split(",") if f.strip()]
        return v


class WseParams(BaseModel):
    """板块/指数日内统计 (T+0) 请求参数。"""

    codes: Union[str, List[str]]
    indicators: Union[str, List[str]] = Field(
        default="rt_pct_chg,rt_vol,rt_amt,rt_net_mf_amt,rt_lg_mf_amt,rt_mid_mf_amt,rt_sm_mf_amt",
        description="统计指标，默认含涨跌幅/成交量/成交额/各档资金流向",
    )
    start_time: str = Field(default="", description="起始时间，空=从开盘开始，如 '09:30:00'")
    end_time: str = Field(default="", description="截止时间，空=最新，如 '15:00:00'")
    options: str = ""

    @field_validator("codes", mode="before")
    @classmethod
    def normalize_codes(cls, v):
        return _validate_codes(v)

    @field_validator("indicators", mode="before")
    @classmethod
    def normalize_indicators(cls, v):
        if isinstance(v, str):
            return [i.strip() for i in v.replace("，", ",").split(",") if i.strip()]
        return v


class WupfParams(BaseModel):
    """Wind 通用推送框架 (wupf) 订阅请求参数。"""

    codes: Union[str, List[str]]
    fields: Union[str, List[str]] = Field(default="rt_last,rt_vol,rt_amt,rt_pct_chg")
    cycle: str = Field(default="tick", pattern="^(tick|1s|3s|5s|30s|1m|5m)$")
    options: str = ""

    @field_validator("codes", mode="before")
    @classmethod
    def normalize_codes(cls, v):
        return _validate_codes(v)

    @field_validator("fields", mode="before")
    @classmethod
    def normalize_fields(cls, v):
        if isinstance(v, str):
            return [f.strip() for f in v.replace("，", ",").split(",") if f.strip()]
        return v

# ---------------------------------------------------------------------------
# WebSocket Manager for Real-time Data
# ---------------------------------------------------------------------------

class WebSocketManager:
    """
    WebSocket 连接管理器，用于处理实时行情推送。
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.wsq_params: Optional[WsqParams] = None  # 存储当前订阅的参数
        self.data_task: Optional[asyncio.Task] = None  # 数据推送任务

    async def connect(self, websocket: WebSocket):
        """接受新的WebSocket连接。"""
        await websocket.accept()
        if len(self.active_connections) >= CONFIG["websocket"]["max_connections"]:
            await websocket.send_text(json.dumps({"error": "max_connections_exceeded", "message": "连接数已达上限"}))
            await websocket.close()
            return
        self.active_connections.append(websocket)
        logger.info(f"WebSocket 连接建立，当前连接数: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """断开WebSocket连接。"""
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket 连接断开，剩余连接数: {len(self.active_connections)}")
        # 如果没有连接了，取消数据推送任务
        if not self.active_connections and self.data_task:
            self.data_task.cancel()
            self.data_task = None
            self.wsq_params = None

    async def send_personal_message(self, message: str, websocket: WebSocket):
        """向指定连接发送消息。"""
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        """向所有活动连接广播消息。"""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except WebSocketDisconnect:
                disconnected.append(connection)
        # 清理已断开的连接
        for connection in disconnected:
            self.disconnect(connection)

    async def start_data_stream(self, params: WsqParams):
        """
        启动实时数据流。
        """
        # 如果已有任务在运行，先取消
        if self.data_task:
            self.data_task.cancel()
        # 存储新的订阅参数
        self.wsq_params = params
        # 启动后台任务
        self.data_task = asyncio.create_task(self._data_stream_loop())

    async def _data_stream_loop(self):
        """
        后台循环，定期从Wind获取数据并推送。
        """
        update_interval = CONFIG["websocket"]["data_update_interval"]
        while True:
            try:
                if not self.wsq_params or not wind_conn.ensure_connected():
                    await asyncio.sleep(update_interval)
                    continue

                # 调用WindPy的wsq获取实时数据
                result = wind_conn.w.wsq(
                    self.wsq_params.codes,
                    ','.join(self.wsq_params.fields)
                )

                if result.ErrorCode == 0:
                    # 格式化数据
                    data = {
                        "error": result.ErrorCode,
                        "data": result.Data,
                        "codes": result.Codes,
                        "fields": result.Fields,
                        "server_time": datetime.now().isoformat(),
                    }
                    # 广播给所有客户端
                    await self.broadcast(json.dumps(data))
                else:
                    logger.error(f"wsq 调用失败: {result.ErrorCode}")
                    # 发送错误信息
                    error_data = {"error": result.ErrorCode, "message": "Wind wsq error"}
                    await self.broadcast(json.dumps(error_data))

                # 等待下一次更新
                await asyncio.sleep(update_interval)
            except asyncio.CancelledError:
                logger.info("实时数据流已取消")
                break
            except Exception as e:
                logger.error(f"实时数据流异常: {e}")
                # 发送错误信息
                error_data = {"error": -1, "message": str(e)}
                await self.broadcast(json.dumps(error_data))
                await asyncio.sleep(update_interval)


# 全局WebSocket管理器
websocket_manager = WebSocketManager()

# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时连接 Wind，关闭时记录日志。"""
    logger.info("=" * 60)
    logger.info("Wind Bridge Server 启动中...")
    logger.info(f"配置: {CONFIG_PATH.resolve()}")
    logger.info(f"监听: {CONFIG['server']['host']}:{CONFIG['server']['port']}")
    logger.info(f"限流: {'启用' if CONFIG['rate_limit']['enabled'] else '关闭'} "
                f"({CONFIG['rate_limit']['requests_per_second']} req/s)")
    logger.info(f"缓存: {CONFIG['wind']['data_cache_seconds']}秒")
    logger.info(f"鉴权: {'启用' if API_KEY else '关闭 (内网模式)'}")
    if CONFIG["websocket"]["enabled"]:
        logger.info(f"WebSocket 实时行情: 启用 (推送间隔: {CONFIG['websocket']['data_update_interval']}s)")

    # 连接 Wind
    connected = wind_conn.connect()
    if not connected:
        logger.critical("⛔ Wind 终端连接失败，服务将继续运行但所有数据请求将报错")

    yield  # 服务运行中

    logger.info("Wind Bridge Server 正在关闭...")


def _build_app() -> FastAPI:
    """构建 FastAPI 应用。"""
    app = FastAPI(
        title="Wind Bridge API",
        description="生产级 Wind 数据 HTTP 网关",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Prometheus (可选)
    if CONFIG["monitoring"]["prometheus"]:
        try:
            from prometheus_fastapi_instrumentator import Instrumentator

            Instrumentator().instrument(app).expose(app, endpoint="/metrics")
            logger.info("Prometheus metrics 已启用: /metrics")
        except ImportError:
            logger.warning("prometheus_fastapi_instrumentator 未安装，/metrics 不可用")

    # ------------------------------------------------------------------
    # 全局异常处理
    # ------------------------------------------------------------------

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(f"未捕获异常: {exc}\n{traceback.format_exc()}")
        return JSONResponse(
            status_code=500,
            content={"error": True, "message": str(exc), "type": type(exc).__name__},
        )

    # ------------------------------------------------------------------
    # Health & Info
    # ------------------------------------------------------------------

    @app.get("/api/health", dependencies=[])
    async def health():
        """健康检查 + Wind 状态。"""
        connected = wind_conn.is_connected()
        return {
            "status": "ok" if connected else "degraded",
            "wind_connected": connected,
            "last_connected": (
                wind_conn.last_connected.isoformat()
                if wind_conn.last_connected
                else None
            ),
            "server_time": datetime.now().isoformat(),
            "version": "1.0.0",
            "websocket_enabled": CONFIG["websocket"]["enabled"],
        }

    @app.get("/api/stats", dependencies=[])
    async def stats():
        """服务器统计。"""
        return {
            "cache_entries": len(_cache),
            "cache_ttl": CACHE_TTL,
            "rate_limit": _rate_limit_cfg,
            "config_path": str(CONFIG_PATH.resolve()),
            "websocket_connections": len(websocket_manager.active_connections),
            "websocket_params": websocket_manager.wsq_params.model_dump() if websocket_manager.wsq_params else None,
        }

    @app.post("/api/reconnect", dependencies=[])
    async def reconnect():
        """手动触发 Wind 重连。"""
        success = wind_conn.connect()
        return {"success": success, "message": "重连成功" if success else "重连失败"}

    @app.get("/api/cache/clear", dependencies=[])
    async def clear_cache():
        """清除内存缓存。"""
        count = len(_cache)
        _cache.clear()
        return {"cleared": count}

    # ------------------------------------------------------------------
    # Core Data Endpoints
    # ------------------------------------------------------------------

    @app.post("/api/wsd")
    async def api_wsd(params: WsdParams, _rate=Security(rate_limit), _auth=Security(verify_auth)):
        """
        Wind 时间序列数据 (wsd).

        codes: 证券代码列表，如 "000001.SZ,000002.SZ"
        fields: 指标列表，如 "close,volume"
        begin_time: 开始日期，如 "2024-01-01"
        end_time: 结束日期，如 "2024-12-31"
        """
        if not wind_conn.ensure_connected():
            raise HTTPException(status_code=503, detail="Wind 终端未连接，请检查Windows上的Wind终端")

        logger.info(f"WSD: codes={len(params.codes)}, fields={params.fields}, "
                    f"period={params.begin_time}~{params.end_time}")

        # 对超长请求做保护
        if len(params.codes) > 5000:
            raise HTTPException(status_code=400, detail="单次请求证券代码数量不能超过5000")

        try:
            result = wind_conn.w.wsd(
                params.codes, params.fields,
                params.begin_time, params.end_time,
                params.options,
            )
            return _format_wind_result(result)
        except Exception as e:
            logger.error(f"WSD 查询异常: {e}")
            raise HTTPException(status_code=500, detail=f"Wind wsd 调用失败: {e}")

    @app.post("/api/wss")
    async def api_wss(params: WssParams, _rate=Security(rate_limit), _auth=Security(verify_auth)):
        """
        Wind 快照数据 (wss).

        codes: 证券代码列表
        fields: 指标列表
        date: 快照日期 (空字符串 = 最新), 如 "2024-01-01"
        options: 附加参数, 如 "tradeDate=2024-01-01;priceAdj=U"
        """
        if not wind_conn.ensure_connected():
            raise HTTPException(status_code=503, detail="Wind 终端未连接")

        logger.info(f"WSS: codes={len(params.codes)}, fields={params.fields}, date={params.date or 'latest'}")

        if len(params.codes) > 5000:
            raise HTTPException(status_code=400, detail="单次请求证券代码数量不能超过5000")

        try:
            result = wind_conn.w.wss(
                params.codes, params.fields,
                params.date, params.options,
            )
            return _format_wind_result(result)
        except Exception as e:
            logger.error(f"WSS 查询异常: {e}")
            raise HTTPException(status_code=500, detail=f"Wind wss 调用失败: {e}")

    @app.post("/api/wset")
    async def api_wset(params: WsetParams, _rate=Security(rate_limit), _auth=Security(verify_auth)):
        """
        Wind 数据集 (wset).

        report_name: 报表名称，如 "sectorconstituent"
        options: 筛选项，如 "date=2024-01-01;sector=沪深300"
        """
        if not wind_conn.ensure_connected():
            raise HTTPException(status_code=503, detail="Wind 终端未连接")

        logger.info(f"WSET: report={params.report_name}, options={params.options[:100]}...")

        try:
            result = wind_conn.w.wset(params.report_name, params.options)
            return _format_wind_result(result)
        except Exception as e:
            logger.error(f"WSET 查询异常: {e}")
            raise HTTPException(status_code=500, detail=f"Wind wset 调用失败: {e}")

    @app.post("/api/wsi")
    async def api_wsi(params: WsiParams, _rate=Security(rate_limit), _auth=Security(verify_auth)):
        """
        Wind 分钟线数据 (wsi).

        codes: 证券代码列表
        fields: 指标列表
        begin_time: 开始时间，如 "2024-01-01 09:30:00"
        end_time: 结束时间，如 "2024-01-01 15:00:00"
        """
        if not wind_conn.ensure_connected():
            raise HTTPException(status_code=503, detail="Wind 终端未连接")

        logger.info(f"WSI: codes={len(params.codes)}, fields={params.fields}, "
                    f"period={params.begin_time}~{params.end_time}")

        if len(params.codes) > 500:
            raise HTTPException(status_code=400, detail="WSI 单次请求证券代码数量不能超过500")

        try:
            result = wind_conn.w.wsi(
                params.codes, params.fields,
                params.begin_time, params.end_time,
                params.options,
            )
            return _format_wind_result(result)
        except Exception as e:
            logger.error(f"WSI 查询异常: {e}")
            raise HTTPException(status_code=500, detail=f"Wind wsi 调用失败: {e}")

    # ------------------------------------------------------------------
    # Real-time (T+0) HTTP Endpoints
    # ------------------------------------------------------------------

    @app.post("/api/wsq")
    async def api_wsq(params: WsqParams, _rate=Security(rate_limit), _auth=Security(verify_auth)):
        """
        Wind 实时行情快照 (wsq) — HTTP 一键查询，无需 WebSocket。

        codes: 证券代码，如 "000001.SZ,510050.SH"
        fields: 实时指标，如 "rt_last,rt_vol,rt_amt,rt_pct_chg,rt_pe"
        """
        if not wind_conn.ensure_connected():
            raise HTTPException(status_code=503, detail="Wind 终端未连接")

        logger.info(f"WSQ snapshot: codes={len(params.codes)}, fields={params.fields}")

        if len(params.codes) > 500:
            raise HTTPException(status_code=400, detail="WSQ 单次请求证券代码数量不能超过500")

        try:
            result = wind_conn.w.wsq(
                ",".join(params.codes),
                ",".join(params.fields),
            )
            return _format_wind_result(result)
        except Exception as e:
            logger.error(f"WSQ 查询异常: {e}")
            raise HTTPException(status_code=500, detail=f"Wind wsq 调用失败: {e}")

    @app.post("/api/wse")
    async def api_wse(params: WseParams, _rate=Security(rate_limit), _auth=Security(verify_auth)):
        """
        Wind 板块/指数日内统计 (wse) — 实时板块资金流向、涨跌统计。

        codes: 板块/指数代码，如 "881001.WI,000300.SH"
        indicators: 统计指标，如 "rt_pct_chg,rt_vol,rt_amt,rt_net_mf_amt"
        start_time: 起始时间，空=从开盘开始
        end_time: 截止时间，空=最新
        """
        if not wind_conn.ensure_connected():
            raise HTTPException(status_code=503, detail="Wind 终端未连接")

        logger.info(f"WSE: codes={len(params.codes)}, indicators={params.indicators}")

        if len(params.codes) > 50:
            raise HTTPException(status_code=400, detail="WSE 单次请求板块数量不能超过50")

        try:
            result = wind_conn.w.wse(
                ",".join(params.codes),
                ",".join(params.indicators),
                params.start_time,
                params.end_time,
                params.options,
            )
            return _format_wind_result(result)
        except Exception as e:
            logger.error(f"WSE 查询异常: {e}")
            raise HTTPException(status_code=500, detail=f"Wind wse 调用失败: {e}")

    @app.post("/api/wupf")
    async def api_wupf(params: WupfParams, _rate=Security(rate_limit), _auth=Security(verify_auth)):
        """
        Wind 通用推送框架 (wupf) — 实时数据推送订阅。

        codes: 证券代码
        fields: 指标列表
        cycle: 推送周期 — tick, 1s, 3s, 5s, 30s, 1m, 5m
        options: 附加参数
        """
        if not wind_conn.ensure_connected():
            raise HTTPException(status_code=503, detail="Wind 终端未连接")

        logger.info(f"WUPF: codes={len(params.codes)}, fields={params.fields}, cycle={params.cycle}")

        if len(params.codes) > 200:
            raise HTTPException(status_code=400, detail="WUPF 单次请求证券代码数量不能超过200")

        try:
            result = wind_conn.w.wupf(
                ",".join(params.codes),
                ",".join(params.fields),
                params.cycle,
                params.options,
            )
            return _format_wind_result(result)
        except Exception as e:
            logger.error(f"WUPF 查询异常: {e}")
            raise HTTPException(status_code=500, detail=f"Wind wupf 调用失败: {e}")

    @app.post("/api/tdays")
    async def api_tdays(params: dict, _rate=Security(rate_limit), _auth=Security(verify_auth)):
        """获取交易日列表。"""
        if not wind_conn.ensure_connected():
            raise HTTPException(status_code=503, detail="Wind 终端未连接")

        begin = params.get("begin_time", "")
        end = params.get("end_time", "")
        logger.info(f"TDAYS: {begin} ~ {end}")

        try:
            result = wind_conn.w.tdays(begin, end, "")
            return {"error": result.ErrorCode, "data": result.Data, "times": result.Times}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/tdayscount")
    async def api_tdayscount(params: dict, _rate=Security(rate_limit), _auth=Security(verify_auth)):
        """获取区间内交易日数量。"""
        if not wind_conn.ensure_connected():
            raise HTTPException(status_code=503, detail="Wind 终端未连接")

        begin = params.get("begin_time", "")
        end = params.get("end_time", "")
        try:
            result = wind_conn.w.tdayscount(begin, end, "")
            return {"error": result.ErrorCode, "data": result.Data}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------------
    # WebSocket Endpoint for Real-time Data (T+0)
    # ------------------------------------------------------------------

    @app.websocket("/ws/realtime")
    async def websocket_realtime(websocket: WebSocket):
        """
        WebSocket 端点，用于建立实时行情连接。
        客户端连接后，需发送一个JSON消息来订阅数据，例如：
        {"codes": "510050.SH", "fields": "rt_last,rt_vol"}
        """
        if not CONFIG["websocket"]["enabled"]:
            await websocket.close(code=1008, reason="WebSocket 未启用")
            return

        await websocket_manager.connect(websocket)
        try:
            # 等待客户端发送订阅消息
            while True:
                data = await websocket.receive_text()
                try:
                    # 解析客户端发送的订阅参数
                    json_data = json.loads(data)
                    params = WsqParams(**json_data)
                    # 启动数据流
                    await websocket_manager.start_data_stream(params)
                    # 发送确认
                    await websocket_manager.send_personal_message(
                        json.dumps({"message": "订阅成功，实时数据推送已启动"}),
                        websocket
                    )
                    break  # 成功订阅后跳出循环，等待推送
                except Exception as e:
                    error_msg = {"error": "invalid_subscription", "message": f"订阅参数无效: {e}"}
                    await websocket_manager.send_personal_message(json.dumps(error_msg), websocket)

            # 保持连接，等待推送或断开
            while True:
                # 接收ping或断开
                try:
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=CONFIG["websocket"]["ping_interval"])
                    # 如果收到消息，可以是新的订阅或ping
                    if data.strip().lower() == "ping":
                        await websocket_manager.send_personal_message(json.dumps({"pong": True}), websocket)
                    else:
                        # 尝试处理为新的订阅
                        try:
                            json_data = json.loads(data)
                            params = WsqParams(**json_data)
                            await websocket_manager.start_data_stream(params)
                            await websocket_manager.send_personal_message(
                                json.dumps({"message": "订阅已更新"}),
                                websocket
                            )
                        except Exception as e:
                            error_msg = {"error": "invalid_subscription", "message": f"更新订阅失败: {e}"}
                            await websocket_manager.send_personal_message(json.dumps(error_msg), websocket)
                except asyncio.TimeoutError:
                    # 超时，发送ping
                    try:
                        await websocket.send_text(json.dumps({"ping": True}))
                    except Exception:
                        # 发送失败，可能连接已断
                        break
        except WebSocketDisconnect:
            pass
        finally:
            websocket_manager.disconnect(websocket)

    return app


def _format_wind_result(result) -> dict:
    """将 WindPy 返回对象格式化为 JSON 友好的字典。"""
    return {
        "error": getattr(result, "ErrorCode", -1),
        "data": getattr(result, "Data", None),
        "codes": getattr(result, "Codes", None),
        "fields": getattr(result, "Fields", None),
        "times": getattr(result, "Times", None),
    }


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = _build_app()

# ---------------------------------------------------------------------------
# CLI Entry
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Wind Bridge API Server")
    parser.add_argument("--host", default=CONFIG["server"]["host"], help="监听地址")
    parser.add_argument("--port", type=int, default=CONFIG["server"]["port"], help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    parser.add_argument("--workers", type=int, default=None, help="工作进程数")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "wind_api_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers or CONFIG["server"]["workers"],
        log_level=CONFIG["logging"]["level"].lower(),
    )


if __name__ == "__main__":
    main()
