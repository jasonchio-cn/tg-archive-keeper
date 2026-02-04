# Telegram Archive Keeper

Telegram 归档管理器，自动下载、去重、分类存储转发的消息和文件。

## 功能特性

- **智能文件管理**：按来源（频道/群组/用户）自动分类存储
- **自动去重**：基于 `file_unique_id` 避免重复下载
- **双模式下载**：
  - 小文件（≤20MB）：Bot API 直接下载
  - 大文件（>20MB）：使用 tdl 客户端下载
- **任务队列**：SQLite 实现的可靠任务队列，支持失败重试
- **Markdown 日志**：按月归档所有操作记录

## 架构

```
┌─────────────────┐     ┌─────────────────┐
│   bot 服务       │     │  worker 服务     │
│  (监听消息)      │────▶│  (tdl 下载)      │
│                 │ DB  │                 │
└─────────────────┘     └─────────────────┘
         │                      │
         └──────────┬───────────┘
                    ▼
              ┌──────────┐
              │  /data   │
              │ (SQLite) │
              └──────────┘
```

## 快速开始

### 1. 前置要求

- Docker 和 Docker Compose
- Telegram Bot Token（从 [@BotFather](https://t.me/BotFather) 获取）
- Telegram 用户账号（用于 tdl 下载大文件）

### 2. 配置

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
BOT_TOKEN=your_bot_token_here
USER_ID=your_user_id_here
```

### 3. 初始化 tdl 会话

```bash
mkdir -p data/tdl_session

docker run -it --rm -v $(pwd)/data/tdl_session:/root/.tdl \
  ghcr.io/iyear/tdl:latest login
```

### 4. 启动服务

```bash
docker-compose up -d
```

查看日志：

```bash
docker-compose logs -f bot
docker-compose logs -f worker
```

### 5. 使用

1. 在 Telegram 中找到你的 Bot
2. 转发任意消息给 Bot
3. Bot 自动下载并归档

## 目录结构

```
data/
├── app.db              # SQLite 数据库
├── tdl_session/        # tdl 会话
├── logs/               # 日志
├── files/              # 下载的文件
│   └── <source_type>/<source_id>_<title>/
└── notes/              # Markdown 日志
    └── YYYY-MM.md
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `BOT_TOKEN` | Telegram Bot Token | 必填 |
| `USER_ID` | Telegram User ID | 必填 |
| `FILE_SIZE_THRESHOLD` | 文件大小阈值（字节） | 20971520 |
| `MAX_ATTEMPTS` | 最大重试次数 | 8 |
| `STALE_JOB_MINUTES` | 任务超时时间（分钟） | 30 |

## 故障排查

### Bot 无法启动

```bash
docker-compose logs bot
```

### Worker 下载失败

```bash
# 查看日志
docker-compose logs worker

# 重新登录 tdl
docker run -it --rm -v $(pwd)/data/tdl_session:/root/.tdl \
  ghcr.io/iyear/tdl:latest login
```

### 数据库查询

```bash
docker-compose exec bot sh -c "sqlite3 /data/app.db 'SELECT COUNT(*) FROM jobs WHERE status=\"QUEUED\"'"
```

## 开发

```bash
pip install -r requirements.txt

# 运行 Bot
python -m app.bot

# 运行 Worker（另一个终端）
python -m app.worker
```

## License

MIT
