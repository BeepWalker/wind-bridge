# Wind Bridge - Makefile
# =======================

.PHONY: help install-server install-client start-server dev-up dev-down test monitor clean

WIND_URL ?= http://192.168.1.100:8899  # 改为你的Windows IP

help:  ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------
# 安装
# ------------------------------------------------------------------

install-server:  ## Windows上安装服务端依赖
	pip install -r wind_server/requirements.txt

install-client:  ## Ubuntu上安装客户端依赖
	pip install -e wind_client/

# ------------------------------------------------------------------
# 服务管理（Windows端）
# ------------------------------------------------------------------

start-server:  ## 启动Wind API服务（开发模式）
	cd wind_server && python wind_api_server.py --reload

start-server-prod:  ## 启动Wind API服务（生产模式）
	cd wind_server && uvicorn wind_api_server:app --host 0.0.0.0 --port 8899 --workers 2

# ------------------------------------------------------------------
# Docker Compose（Ubuntu开发环境）
# ------------------------------------------------------------------

dev-up:  ## 启动开发环境（Mock + Jupyter）
	docker-compose -f docker-compose.dev.yml up -d

dev-down:  ## 停止开发环境
	docker-compose -f docker-compose.dev.yml down

dev-logs:  ## 查看开发环境日志
	docker-compose -f docker-compose.dev.yml logs -f

# ------------------------------------------------------------------
# 测试
# ------------------------------------------------------------------

test-health:  ## 测试健康检查
	@curl -s $(WIND_URL)/api/health | python -m json.tool

test-wsd:  ## 测试WSD接口
	@curl -s -X POST $(WIND_URL)/api/wsd \
		-H "Content-Type: application/json" \
		-d '{"codes":["000001.SZ"],"fields":["close"],"begin_time":"2024-01-01","end_time":"2024-01-10"}' \
		| python -m json.tool | head -20

test-wss:  ## 测试WSS接口
	@curl -s -X POST $(WIND_URL)/api/wss \
		-H "Content-Type: application/json" \
		-d '{"codes":["000001.SZ"],"fields":["close,pe"],"date":"2024-01-05"}' \
		| python -m json.tool | head -20

test-client:  ## 测试Python客户端
	@cd wind_client && WIND_API_URL=$(WIND_URL) python wind_remote.py --url $(WIND_URL)

# ------------------------------------------------------------------
# 监控
# ------------------------------------------------------------------

monitor:  ## 查看Prometheus指标
	@curl -s $(WIND_URL)/metrics | head -50

stats:  ## 查看服务器统计
	@curl -s $(WIND_URL)/api/stats | python -m json.tool

# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------

codes:  ## 获取沪深300成分股（需要Wind在线）
	@curl -s -X POST $(WIND_URL)/api/wset \
		-H "Content-Type: application/json" \
		-d '{"report_name":"sectorconstituent","options":"date=2024-01-01;sector=沪深300"}' \
		| python -c "import sys,json; d=json.load(sys.stdin); print('\n'.join(d.get('data',[[]])[0][:20]))"

clear-cache:  ## 清除Wind服务端缓存
	@curl -s $(WIND_URL)/api/cache/clear | python -m json.tool

reconnect:  ## 触发Wind重连
	@curl -s -X POST $(WIND_URL)/api/reconnect | python -m json.tool
