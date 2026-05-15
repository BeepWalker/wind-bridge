# Wind Bridge 🌉

> **生产级 Wind 数据 HTTP 网关 — 让 Ubuntu 开发环境无缝访问 Windows 上的 Wind 终端**

```
     Ubuntu (开发机)                        Windows (Wind终端)
    ╭──────────────╮                    ╭──────────────────────╮
    │  Jupyter     │──── HTTP/JSON ────▶│  FastAPI Server      │
    │  Airflow     │    REST API        │   ↓                  │
    │  Python SDK  │                    │  WindPy → Wind终端   │
    │  Backtrader  │                    │   ↓                  │
    │  ...         │◀─── DataFrame ────│  数据返回             │
    ╰──────────────╯                    ╰──────────────────────╯
```

---

## 🚀 快速开始（5分钟）

### 1. Windows 端（Wind终端所在机器）

```powershell
# 安装依赖
cd wind_server
pip install -r requirements.txt

# 确保 WindPy 可用
python -c "from WindPy import w; w.start(); print(w.isconnected())"

# 启动服务
python wind_api_server.py

# 验证
curl http://localhost:8899/api/health
# → {"status":"ok","wind_connected":true,...}
```

**设为 Windows 服务（开机自启）**：以管理员身份运行 `install_service.bat`

### 2. Ubuntu 端（开发/生产服务器）

```bash
# 安装客户端
cd wind_client
pip install -e .

# 测试连接
WIND_API_URL=http://192.168.1.100:8899 python wind_remote.py
```

### 3. 写代码

```python
from wind_remote import WindRemote

w = WindRemote("http://192.168.1.100:8899")

# 日线数据
close = w.wsd("000001.SZ", "close,volume", "2024-01-01", "2024-12-31")
print(close.head())

# 快照数据
snap = w.wss("000001.SZ,000002.SZ,600000.SH", "close,pe,pb,roe")
print(snap)

# 板块成分股
codes = w.get_sector_codes("沪深300")
print(f"沪深300成分股: {len(codes)}只")

# 财务报表
fin = w.get_financials("000001.SZ", "roe,net_profit,revenue", "2024Q3")
print(fin)
```

---

## 📦 项目结构

```
wind-bridge/
├── wind_server/                  # Windows 端
│   ├── wind_api_server.py        # FastAPI 核心服务
│   ├── config.yaml               # 配置文件
│   ├── requirements.txt          # Python 依赖
│   └── install_service.bat       # Windows 服务注册脚本
├── wind_client/                  # Ubuntu 端 Python SDK
│   ├── __init__.py               # 包入口
│   ├── wind_remote.py            # SDK 主模块
│   └── requirements.txt          # 客户端依赖
├── config/
│   └── prometheus.yml            # 监控配置
├── docker-compose.dev.yml        # Ubuntu 开发环境（Mock + Jupyter）
├── Makefile                      # 常用命令快捷入口
└── README.md                     # 本文档
```

---

## 🔧 完整部署指南

### 前置条件

| 组件 | 要求 |
|------|------|
| **Windows** | Win10/11, Python ≥3.9, Wind终端已安装并登录 |
| **Ubuntu** | Python ≥3.9, pip |
| **网络** | 两台机器在同一局域网，Windows防火墙放行8899端口 |

### Windows 防火墙配置

```powershell
# 以管理员身份运行 PowerShell
New-NetFirewallRule -DisplayName "Wind Bridge API" -Direction Inbound -LocalPort 8899 -Protocol TCP -Action Allow
```

### 生产部署建议

1. **Windows 设为固定IP**（路由器 DHCP 绑定，或手动设置）
2. **使用 nssm 注册为 Windows 服务**（开机自启 + 崩溃自动重启）
3. **配置 API Key**（如果暴露到非内网环境）
4. **启用 Prometheus 监控**（可选）

### API Key 鉴权配置

```yaml
# wind_server/config.yaml
server:
  api_key: "your-secret-key-here"
```

```bash
# Ubuntu 端
export WIND_API_KEY="your-secret-key-here"
```

---

## 📊 API 参考

| 端点 | 方法 | 说明 | WindPy 对应 |
|------|------|------|------------|
| `/api/health` | GET | 健康检查 + Wind状态 | - |
| `/api/stats` | GET | 服务器统计 | - |
| `/api/wsd` | POST | 时间序列数据 | `w.wsd()` |
| `/api/wss` | POST | 快照数据 | `w.wss()` |
| `/api/wset` | POST | 数据集 | `w.wset()` |
| `/api/wsi` | POST | 分钟K线 | `w.wsi()` |
| `/api/tdays` | POST | 交易日列表 | `w.tdays()` |
| `/api/tdayscount` | POST | 交易日数量 | `w.tdayscount()` |
| `/api/reconnect` | POST | 触发重连 | - |
| `/api/cache/clear` | GET | 清除缓存 | - |
| `/docs` | GET | Swagger UI | - |
| `/metrics` | GET | Prometheus 指标 | - |

### 请求示例

```bash
# WSD：获取平安银行 2024 全年收盘价和成交量
curl -X POST http://192.168.1.100:8899/api/wsd \
  -H "Content-Type: application/json" \
  -d '{
    "codes": ["000001.SZ"],
    "fields": ["close", "volume"],
    "begin_time": "2024-01-01",
    "end_time": "2024-12-31"
  }'

# WSS：获取多只股票最新快照
curl -X POST http://192.168.1.100:8899/api/wss \
  -H "Content-Type: application/json" \
  -d '{
    "codes": ["000001.SZ", "000002.SZ", "600000.SH"],
    "fields": ["close", "pe", "pb", "roe", "total_shares"],
    "date": "",
    "options": "tradeDate=2024-12-20"
  }'

# WSET：获取沪深300成分股
curl -X POST http://192.168.1.100:8899/api/wset \
  -H "Content-Type: application/json" \
  -d '{
    "report_name": "sectorconstituent",
    "options": "date=2024-12-20;sector=沪深300"
  }'
```

---

## 🐳 Docker 开发环境（Ubuntu）

如果暂时没有 Windows 机器在线，可以在 Ubuntu 上启动 Mock 服务器进行代码开发：

```bash
# 启动 Mock Wind Server + Jupyter
make dev-up

# 访问 Jupyter
open http://localhost:8888

# 在 Jupyter 中使用
import sys
sys.path.append("/home/jovyan/wind_client")
from wind_remote import WindRemote
w = WindRemote("http://wind-mock:8899")
```

---

## 📈 监控

### Prometheus + Grafana

```bash
# 启动 Prometheus（需要先安装）
prometheus --config.file=config/prometheus.yml

# 导入 Grafana Dashboard（JSON 模板见 docs/grafana_dashboard.json）
```

### 关键指标

| 指标 | 含义 |
|------|------|
| `http_requests_total` | 总请求数（按端点/状态码） |
| `http_request_duration_seconds` | 请求延迟分布 |
| `wind_connected` | Wind 终端连接状态 |
| `cache_size` | 缓存条目数 |

---

## ⚠️ 常见问题

### Q1: `WindPy import failed`
**A:** WindPy 不在 PyPI 上，需要从 Wind 终端安装。检查：
```python
import sys
print(sys.path)
# 应包含 Wind 终端 Python 路径，如 C:\Wind\Wind.NET.Client\WindNET\bin\
```

### Q2: Ubuntu 连接超时
**A:** 三步排查：
```bash
# 1. 检查网络可达
ping 192.168.1.100

# 2. 检查端口是否开放
nc -zv 192.168.1.100 8899

# 3. 检查 Windows 防火墙
# 在 Windows PowerShell 管理员中运行：
Get-NetFirewallRule -DisplayName "*Wind*"
```

### Q3: Wind Terminal 自动断开
**A:** 在 `config.yaml` 中配置自动重连：
```yaml
wind:
  reconnect_attempts: 10
  reconnect_interval: 30
  health_check_interval: 300  # 每5分钟检查一次
```

### Q4: 请求频率超限（429错误）
**A:** Wind 终端有频率限制：
```yaml
# config.yaml 调整限流
rate_limit:
  requests_per_second: 3    # 降低到3次/秒
  burst_size: 5
```

### Q5: 数据量大时请求慢
**A:** 
1. 启用缓存：`wind.data_cache_seconds: 600`
2. 按股票分批请求（单次不超过100只）
3. 考虑在 Ubuntu 侧做二级缓存

---

## 🏗️ 架构设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 通信协议 | HTTP/JSON | 简单、调试方便、跨平台零成本 |
| Python框架 | FastAPI | 高性能异步、自动文档、生产级 |
| 限流策略 | Token Bucket | 平滑限流，允许突发 |
| 缓存策略 | 内存 LRU | 简单有效，数据有明确时效性 |
| 服务化管理 | nssm | Windows 服务化最佳实践 |
| 监控方案 | Prometheus | 行业标准，Grafana友好 |

---

## 📜 License

MIT

---

**Made with ❤️ by Socrates × Elon Musk Senior**
*"从第一性原理出发，让 Wind 数据无处不在"*
