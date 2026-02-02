# 快速启动指南（本地开发）

## 前置要求

- Python 3.11+
- uv (Python 包管理器)
- Telegram Bot Token (从 @BotFather 获取)

## 1. 安装依赖

```bash
# 使用 uv 创建虚拟环境并安装依赖
uv sync

# 或者手动安装
uv pip install python-telegram-bot aiosqlite aiofiles python-dotenv
```

## 2. 配置环境变量

复制 `.env.example` 为 `.env`:

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的配置：

```env
# 必填：从 @BotFather 获取
BOT_TOKEN=your_real_bot_token_here

# 必填：你的 Telegram User ID
USER_ID=your_user_id

# 可选：文件大小阈值（默认 20MB）
FILE_SIZE_THRESHOLD=20971520

# 可选：本地路径
DB_PATH=./data/app.db
LOG_PATH=./data/logs
FILES_PATH=./files
NOTES_PATH=./notes
```

## 3. 运行测试（可选）

验证代码是否正常工作：

```bash
# 基础功能测试
uv run python test_basic.py

# 任务队列和去重测试
uv run python test_queue.py
```

## 4. 启动 Bot

在一个终端窗口运行：

```bash
uv run python -m app.bot
```

你应该看到：

```
INFO - Starting Telegram Archive Bot
INFO - Database initialized at data\app.db
INFO - Bot is running...
```

## 5. 启动 Worker（可选）

如果需要下载大文件（>20MB），在另一个终端窗口运行：

```bash
uv run python -m app.worker
```

**注意**: Worker 需要 tdl 客户端和有效的会话。如果只测试小文件，可以跳过这一步。

## 6. 测试 Bot

1. 在 Telegram 中找到你的 Bot
2. 转发任意消息给 Bot（文本、图片、文档等）
3. 查看日志输出
4. 检查生成的文件：
   - `./data/app.db` - 数据库
   - `./files/` - 下载的文件
   - `./notes/` - Markdown 日志

## 目录结构

运行后会生成以下目录：

```
telegram-archive-keeper/
├── .venv/              # 虚拟环境
├── data/
│   ├── app.db          # SQLite 数据库
│   └── logs/           # 日志文件
│       ├── bot.log
│       └── worker.log
├── files/              # 下载的文件
│   └── <source_type>/
│       └── <source_id>_<title>/
│           └── <file_unique_id>__<filename>
└── notes/              # Markdown 日志
    └── YYYY-MM.md
```

## 常见问题

### Q: Bot 启动失败，提示 "BOT_TOKEN is required"

A: 确保 `.env` 文件存在且包含有效的 `BOT_TOKEN`。

### Q: 如何获取 Bot Token?

A: 
1. 在 Telegram 中搜索 @BotFather
2. 发送 `/newbot` 创建新 Bot
3. 按提示设置名称
4. 复制返回的 Token

### Q: 如何获取我的 User ID?

A:
1. 在 Telegram 中搜索 @userinfobot
2. 发送 `/start`
3. 复制返回的 ID

### Q: Worker 提示找不到 tdl

A: 
- 小文件（≤20MB）不需要 tdl，Bot 会直接下载
- 大文件需要安装 tdl：https://github.com/iyear/tdl
- 或者在 Docker 中运行（已包含 tdl）

### Q: 如何查看数据库内容?

A:
```bash
# 使用 SQLite 命令行
sqlite3 data/app.db

# 查看表
.tables

# 查看消息
SELECT * FROM messages;

# 查看文件
SELECT * FROM files;

# 退出
.quit
```

### Q: 如何清理测试数据?

A:
```bash
# 删除数据库
rm data/app.db

# 删除文件
rm -rf files/*

# 删除日志
rm -rf notes/*
```

## 开发模式

### 修改代码后重启

Bot 和 Worker 不会自动重载，修改代码后需要手动重启：

```bash
# Ctrl+C 停止进程
# 然后重新运行
uv run python -m app.bot
```

### 查看实时日志

```bash
# Bot 日志
tail -f data/logs/bot.log

# Worker 日志
tail -f data/logs/worker.log
```

### 调试模式

修改 `app/bot.py` 或 `app/worker.py` 中的日志级别：

```python
logging.basicConfig(
    level=logging.DEBUG,  # 改为 DEBUG
    ...
)
```

## 生产部署

生产环境建议使用 Docker：

```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

详见 `README.md` 中的完整部署说明。

## 下一步

- 阅读 `README.md` 了解完整功能
- 阅读 `IMPLEMENTATION.md` 了解技术细节
- 阅读 `TEST_REPORT.md` 了解测试结果
- 查看 `PROJECT_SUMMARY.md` 了解项目概览
