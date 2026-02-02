# Telegram Archive Keeper

一个功能完整的 Telegram 归档管理器，支持自动下载、去重、分类存储转发的消息和文件。

## 功能特性

- **智能文件管理**：按来源（频道/群组/用户）自动分类存储
- **自动去重**：基于 `file_unique_id` 避免重复下载
- **双模式下载**：
  - 小文件（≤20MB）：Bot API 直接下载
  - 大文件（>20MB）：使用 tdl 客户端下载
- **任务队列**：SQLite 实现的可靠任务队列，支持失败重试
- **Markdown 日志**：按月归档所有操作记录
- **容器化部署**：单容器运行 Bot 和 Worker 两个进程

## 架构设计

### 目录结构

```
/data/
  ├── app.db              # SQLite 数据库
  ├── tdl_session/        # tdl 用户会话（必须持久化）
  ├── logs/               # 日志文件
  ├── files/              # 下载的文件
  │   └── <source_type>/
  │       └── <source_id>_<title>/
  │           └── <file_unique_id>__<filename>
  └── notes/              # Markdown 归档日志
      └── YYYY-MM.md
```

### 数据库表结构

- `sources`：转发来源（频道/群组/用户）
- `messages`：接收的消息记录
- `files`：唯一文件实体（按 file_unique_id 去重）
- `message_files`：消息与文件的引用关系
- `jobs`：下载任务队列（仅大文件）

### 去重机制

1. **数据库层面**：`files.file_unique_id` 唯一约束
2. **任务队列**：部分唯一索引防止同一文件重复入队
3. **文件系统**：文件名包含 `file_unique_id`，自然去重

## 快速开始

### 1. 前置要求

- Docker 和 Docker Compose
- Telegram Bot Token（从 [@BotFather](https://t.me/BotFather) 获取）
- Telegram 用户账号（用于 tdl 下载大文件）

### 2. 配置

复制环境变量模板：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```bash
# Telegram Bot Token (必填)
BOT_TOKEN=your_bot_token_here

# 你的 Telegram User ID (必填，用于 tdl)
USER_ID=your_user_id_here

# 文件大小阈值（字节，默认 20MB）
FILE_SIZE_THRESHOLD=20971520

# Worker 设置
MAX_ATTEMPTS=8
STALE_JOB_MINUTES=30
```

### 3. 初始化 tdl 会话

首次运行需要登录 tdl：

```bash
# 创建数据目录
mkdir -p data/tdl_session

# 运行 tdl 登录（在容器外或临时容器内）
docker run -it --rm -v $(pwd)/data/tdl_session:/data/tdl_session \
  ghcr.io/iyear/tdl:latest login
```

按提示输入手机号和验证码完成登录。

### 4. 启动服务

```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 5. 使用

1. 在 Telegram 中找到你的 Bot
2. 转发任意消息（文本、图片、视频、文档等）给 Bot
3. Bot 会自动：
   - 解析来源信息
   - 下载文件（小文件立即下载，大文件入队）
   - 按来源分类存储
   - 写入 Markdown 日志

## 验收测试

### 测试 1：大文件去重

1. 转发一个 >20MB 的文件给 Bot
2. 再次转发同一个文件
3. 验证：
    - `data/app.db` 中 `messages` 表有 2 条记录
    - `message_files` 表有 2 条引用记录
    - `files` 表只有 1 条记录
    - `/data/files/` 目录下只有 1 个实际文件
    - `/data/notes/YYYY-MM.md` 中有 2 条消息记录，都指向同一个文件

### 测试 2：容器重启恢复

1. 转发一个大文件，等待 Worker 开始下载
2. 停止容器：`docker-compose down`
3. 重启容器：`docker-compose up -d`
4. 验证：
   - Worker 自动恢复 RUNNING 状态的任务
   - 继续下载并完成
   - tdl 会话保持有效

## 目录说明

### 项目结构

```
telegram-archive-keeper/
├── app/
│   ├── __init__.py
│   ├── config.py           # 配置管理
│   ├── database.py         # 数据库操作
│   ├── file_manager.py     # 文件管理工具
│   ├── markdown_logger.py  # Markdown 日志
│   ├── bot.py              # Bot 进程
│   └── worker.py           # Worker 进程
├── docker/
│   └── supervisord.conf    # Supervisor 配置
├── .github/workflows/
│   └── dockerhub.yml       # GitHub Actions: 构建并推送 DockerHub 镜像
├── .env.example            # 环境变量模板
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

### 运行时目录

```
data/                       # 持久化数据（需挂载）
├── app.db                  # SQLite 数据库
├── tdl_session/            # tdl 会话文件
└── logs/                   # 日志文件
    ├── supervisord.log
    ├── bot.log
    ├── bot.err.log
    ├── worker.log
    └── worker.err.log

data/files/                 # 下载的文件（持久化）
└── <source_type>/
    └── <source_id>_<title>/
        └── <file_unique_id>__<filename>

data/notes/                 # Markdown 日志（持久化）
└── YYYY-MM.md
```

## 配置说明

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `BOT_TOKEN` | Telegram Bot Token | 必填 |
| `USER_ID` | Telegram User ID | 必填 |
| `FILE_SIZE_THRESHOLD` | 文件大小阈值（字节） | 20971520 (20MB) |
| `MAX_ATTEMPTS` | 最大重试次数 | 8 |
| `STALE_JOB_MINUTES` | 任务超时时间（分钟） | 30 |
| `DB_PATH` | 数据库路径 | /data/app.db |
| `TDL_SESSION_PATH` | tdl 会话路径 | /data/tdl_session |
| `LOG_PATH` | 日志路径 | /data/logs |
| `FILES_PATH` | 文件存储路径 | /data/files |
| `NOTES_PATH` | Markdown 日志路径 | /data/notes |

### 文件命名规则

- **目录**：`/data/files/<source_type>/<source_id>_<sanitized_title>/`
  - `source_type`：channel | group | supergroup | user | unknown
  - `source_id`：Telegram chat ID
  - `sanitized_title`：仅保留 `[A-Za-z0-9._-]`，最大 64 字符

- **文件**：`<file_unique_id>__<sanitized_original_name>`
  - 便于去重和人工定位

## 故障排查

### Bot 无法启动

1. 检查 `BOT_TOKEN` 是否正确
2. 查看日志：`docker-compose logs bot`

### Worker 下载失败

1. 检查 tdl 会话是否有效：
   ```bash
   docker-compose exec telegram-archive-keeper tdl version
   ```

2. 重新登录 tdl：
   ```bash
   docker-compose down
   docker-compose run --rm telegram-archive-keeper tdl login
   docker-compose up -d
   ```

3. 查看 Worker 日志：
   ```bash
   docker-compose logs worker
   ```

### 数据库锁定

SQLite 使用 WAL 模式，支持并发读写。如果遇到锁定问题：

1. 检查是否有其他进程访问数据库
2. 重启容器：`docker-compose restart`

### 磁盘空间不足

定期清理：

```bash
# 查看文件占用
 du -sh data/files/*

# 删除旧文件（根据需要）
 find data/files/ -type f -mtime +365 -delete
```

## 开发说明

### 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export BOT_TOKEN=your_token
export USER_ID=your_id

# 运行 Bot
python -m app.bot

# 运行 Worker（另一个终端）
python -m app.worker
```

### 数据库操作

```bash
# 进入容器
docker-compose exec telegram-archive-keeper bash

# 查看数据库
sqlite3 /data/app.db

# 常用查询
SELECT COUNT(*) FROM messages;
SELECT COUNT(*) FROM files;
SELECT COUNT(*) FROM jobs WHERE status='QUEUED';
```

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

## 致谢

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [tdl](https://github.com/iyear/tdl)
