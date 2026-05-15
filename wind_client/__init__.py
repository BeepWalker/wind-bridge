"""
Wind Remote Client — Ubuntu 端 Wind 数据访问 SDK
================================================

使用示例:
    from wind_remote import WindRemote, connect_wind

    # 方式1：创建实例
    w = WindRemote("http://192.168.1.100:8899")
    df = w.wsd("000001.SZ", "close", "2024-01-01", "2024-12-31")

    # 方式2：全局单例
    w = connect_wind("http://192.168.1.100:8899")
    df = w.get_daily("000001.SZ", "2024-01-01", "2024-12-31")

    # 方式3：环境变量
    # export WIND_API_URL="http://192.168.1.100:8899"
    w = connect_wind()
"""

from wind_remote import WindRemote, WindResult, connect_wind

__version__ = "1.0.0"
__all__ = ["WindRemote", "WindResult", "connect_wind"]
