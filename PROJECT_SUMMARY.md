# Telegram Archive Keeper - 项目文件清单

## 核心应用代码

### app/
- `__init__.py` - Python 包初始化文件
- `config.py` - 配置管理，加载环境变量
- `database.py` - SQLite 数据库操作（DDL + 所有 CRUD 函数）
- `file_manager.py` - 文件管理工具（路径生成、清理、校验、SHA256）
- `markdown_logger.py` - Markdown 日志写入（按月滚动）
- `bot.py` - Bot 进程主程序（接收消息、小文件下载、入队）
- `worker.py` - Worker 进程主程序（tdl 下载、任务队列处理）

## 容器化配置

### Docker
- `Dockerfile` - 容器镜像定义（Python + tdl + supervisord）
- `docker-compose.yml` - 容器编排配置
- `docker/supervisord.conf` - Supervisor 进程管理配置

### CI/CD

- `.github/workflows/dockerhub.yml` - GitHub Actions: 构建并推送 DockerHub 镜像

## 配置文件

- `.env.example` - 环境变量模板
- `.gitignore` - Git 忽略规则
- `requirements.txt` - Python 依赖

## 文档

- `README.md` - 用户使用文档（快速开始、配置、故障排查）
- `IMPLEMENTATION.md` - 技术实现说明（架构、验收、扩展）

## 项目统计

- **Python 文件**: 7 个
- **总代码行数**: ~1500 行
- **数据库表**: 5 个（sources, messages, files, message_files, jobs）
- **支持的媒体类型**: 7 种（document, photo, video, audio, voice, animation, sticker）

## 关键特性实现

✅ SQLite 作为唯一数据库和队列
✅ 按来源目录归档（/data/files/<type>/<id>_<title>/）
✅ file_unique_id 去重机制
✅ 20MB 阈值分流（Bot API vs tdl）
✅ Markdown 月度日志（/data/notes/YYYY-MM.md）
✅ 单容器双进程（supervisord）
✅ 持久化卷挂载（/data）
✅ 失败重试机制（指数退避）
✅ 容器重启恢复（stale job recovery）
✅ 原子操作（.part + rename）
✅ SHA256 校验

## 下一步

1. 复制 `.env.example` 为 `.env` 并配置
2. 初始化 tdl 会话
3. 运行 `docker-compose up -d`
4. 转发消息给 Bot 测试

## 技术栈

- **语言**: Python 3.11
- **Bot 框架**: python-telegram-bot 20.7
- **数据库**: SQLite (aiosqlite)
- **下载工具**: tdl (Telegram Downloader)
- **进程管理**: supervisord
- **容器化**: Docker + Docker Compose
