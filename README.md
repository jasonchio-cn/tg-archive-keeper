# Telegram Archive Keeper

Telegram 归档管理器，自动下载、去重、分类存储转发的消息和文件。

## 功能特性

- **智能文件管理**：按来源（频道/群组/用户）自动分类存储
- **自动去重**：基于 `file_unique_id` 避免重复下载
- **双模式下载**：
  - 小文件：Bot API 失败后自动切换 tdl 下载
  - 大文件：直接使用 tdl 下载
- **后台任务队列**：并发下载，默认最多 4 个同时下载
- **Markdown 日志**：按月归档所有操作记录
- **WebDAV 支持**：可选上传到 WebDAV 服务器（如 Nextcloud、AList 等）
- **失败统计**：记录下载失败类型和错误详情

## 架构

```
┌─────────────────────────────┐
│       tg-archive-keeper       │
│                                     │
│  ┌─────────────┐  ┌──────────────┐  │
│  │ 消息处理器   │  │ 下载任务队列  │  │
│  │ (同步)      │─▶│ (异步后台)   │  │
│  └─────────────┘  └──────────────┘  │
│         │                │          │
│         ▼                ▼          │
│      SQLite          Bot API/tdl    │
│         │                │          │
│         └────┬───────┘          │
│                  ▼                  │
│            WebDAV (可选)            │
└─────────────────────────────────────┘
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

# Download settings
MAX_CONCURRENT_DOWNLOADS=4  # 默认同时最多4个下载任务

# Storage mode: local, webdav, or local,webdav (both)
STORAGE_MODE=local

# WebDAV (当 STORAGE_MODE 包含 webdav 时必填)
WEBDAV_URL=https://your-webdav-server.com/dav/telegram
WEBDAV_USERNAME=your_username
WEBDAV_PASSWORD=your_password
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
docker-compose logs -f
```

### 5. 使用

1. 在 Telegram 中找到你的 Bot
2. 转发任意消息给 Bot
3. Bot 自动下载并归档

## 目录结构

```
data/
├── task_db/
│   └── app.db          # SQLite 数据库
├── tdl_session/        # tdl 会话
├── logs/               # 日志
├── files/              # 下载的文件
│   └── <source_type>/<source_id>_<title>/
└── notes/              # Markdown 日志
    ├── YYYY-MM.md        # 消息和下载记录
    └── YYYY-MM-失败统计.md  # 下载失败统计
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `BOT_TOKEN` | Telegram Bot Token | 必填 |
| `MAX_CONCURRENT_DOWNLOADS` | 最大并发下载数 | 4 |
| `STORAGE_MODE` | 存储模式：`local`/`webdav`/`local,webdav` | local |
| `WEBDAV_URL` | WebDAV 服务器地址 | 空 |
| `WEBDAV_USERNAME` | WebDAV 用户名 | 空 |
| `WEBDAV_PASSWORD` | WebDAV 密码 | 空 |

## 下载策略

- **所有文件都通过任务队列处理**，不区分大小
- **下载尝试顺序**：先 Bot API，失败后使用 tdl
- **并发控制**：默认最多 4 个同时下载
- **失败处理**：不重试，记录到 `download_failures` 表和 markdown

## 故障排查

### Bot 无法启动

```bash
docker-compose logs
```

### 下载失败统计

查看每月下载失败统计：

```bash
docker-compose exec tg-archive-keeper cat /data/notes/2026-02-失败统计.md
```

查看数据库中的失败记录：

```bash
docker-compose exec tg-archive-keeper sqlite3 /data/task_db/app.db "SELECT * FROM download_failures ORDER BY created_at DESC LIMIT 20"
```

### tdl 下载失败

```bash
# 查看日志
docker-compose logs | grep "tdl failed"

# 重新登录 tdl
docker run -it --rm -v $(pwd)/data/tdl_session:/root/.tdl \
  ghcr.io/iyear/tdl:latest login
```

## 开发

```bash
pip install -r requirements.txt

# 运行
python -m app.bot
```

## License

MIT
