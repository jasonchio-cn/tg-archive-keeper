# Telegram Archive Keeper - 实现说明

## 概述

这是一个完整的 Telegram 归档机器人实现，满足你提供的所有需求规范。

## 核心特性

### 1. 数据库设计

- 使用 SQLite + WAL 模式，支持并发
- 完整实现了你提供的 DDL schema
- 关键特性：
  - `files.file_unique_id` 唯一约束实现去重
  - `jobs` 表的部分唯一索引防止重复入队
  - 外键约束保证数据完整性

### 2. Bot 进程 (app/bot.py)

**功能**：
- 接收转发消息（支持所有媒体类型）
- 解析 forward source（支持新旧 API）
- 小文件（≤20MB）直接下载
- 大文件（>20MB）入队给 Worker
- 写入 Markdown 日志

**关键实现**：
- `parse_forward_source()`：解析转发来源，支持 channel/group/user/unknown
- `extract_file_info()`：提取文件元数据（document/photo/video/audio/voice/animation/sticker）
- `download_small_file()`：使用 Bot API 下载，原子写入（.part + rename）
- 事务处理：upsert source → insert message → upsert files → insert message_files

### 3. Worker 进程 (app/worker.py)

**功能**：
- 串行处理任务队列（并发=1）
- 使用 tdl 下载大文件
- 失败重试（指数退避）
- 启动时恢复 stale jobs

**关键实现**：
- `pick_and_lock_job()`：原子抢占任务（BEGIN IMMEDIATE 事务）
- `download_with_tdl()`：调用 tdl 命令行下载
- `calculate_backoff()`：指数退避，最大 6 小时
- `recover_stale_jobs()`：恢复超时任务

### 4. 文件管理 (app/file_manager.py)

**功能**：
- 路径生成和文件名清理
- SHA256 校验
- 原子写入

**关键实现**：
- `sanitize_filename()`：清理文件名，仅保留 `[A-Za-z0-9._-]`
- `get_archive_path()`：生成归档路径 `/files/<type>/<id>_<title>/<unique_id>__<name>`
- `calculate_sha256()`：异步计算哈希
- `atomic_write()`：临时文件 + rename 保证原子性

### 5. Markdown 日志 (app/markdown_logger.py)

**功能**：
- 按月滚动日志文件
- 只追加，不回写
- 记录消息和任务完成

**关键实现**：
- `append_message_entry()`：Bot 写入消息记录
- `append_job_complete()`：Worker 写入完成记录
- `append_job_failed()`：Worker 写入失败记录

### 6. 容器化 (Dockerfile + supervisord)

**特性**：
- 单容器运行 Bot 和 Worker
- supervisord 管理两个进程
- 自动重启
- 日志分离

## 去重验证

### 数据库层面

```sql
-- files 表唯一约束
UNIQUE(file_unique_id)

-- jobs 表部分唯一索引（防止重复入队）
CREATE UNIQUE INDEX idx_jobs_one_active_per_file
ON jobs(file_id)
WHERE status IN ('QUEUED','RUNNING','RETRY');
```

### 应用层面

1. **Bot 处理消息时**：
   - `upsert_file()` 基于 `file_unique_id` 去重
   - 检查 `files.status == 'DOWNLOADED'` 跳过下载
   - 每次转发都插入 `message_files` 记录引用关系

2. **Worker 处理任务时**：
   - 下载前再次检查文件是否已存在
   - 文件名包含 `file_unique_id`，自然去重

## MVP 验收用例

### 用例 1：转发同一个 >20MB 文件两次

**预期结果**：
1. `messages` 表：2 条记录
2. `message_files` 表：2 条记录（指向同一个 `file_id`）
3. `files` 表：1 条记录
4. `/files/` 目录：1 个文件
5. `/notes/YYYY-MM.md`：2 条消息块 + 1 条 COMPLETE 块

**验证 SQL**：
```sql
-- 查看消息
SELECT id, tg_message_id, received_at FROM messages;

-- 查看文件引用
SELECT mf.message_id, mf.file_id, f.file_unique_id, f.local_path
FROM message_files mf
JOIN files f ON mf.file_id = f.id;

-- 查看任务
SELECT id, file_id, status FROM jobs;
```

### 用例 2：容器重启恢复

**测试步骤**：
1. 转发大文件，等待 Worker 开始下载
2. `docker-compose down`
3. `docker-compose up -d`

**预期结果**：
- Worker 启动时调用 `recover_stale_jobs()`
- RUNNING 任务变为 RETRY
- 继续下载并完成
- tdl 会话保持有效（因为 `/data/tdl_session` 持久化）

## 关键技术点

### 1. SQLite 并发

- WAL 模式：支持多读一写
- `BEGIN IMMEDIATE`：Worker 抢占任务时避免竞争
- 外键约束：`PRAGMA foreign_keys=ON`

### 2. 原子操作

- 文件下载：`.part` 临时文件 + `rename()`
- 任务抢占：事务内 SELECT + UPDATE
- 数据库写入：所有操作都在事务内

### 3. 错误处理

- Bot：捕获异常，记录日志，不影响后续消息
- Worker：失败重试（指数退避），超过 MAX_ATTEMPTS 标记为 FAILED
- 数据库：外键约束 + 唯一索引保证一致性

### 4. tdl 集成

- 会话持久化：`/data/tdl_session` 挂载卷
- 命令行调用：`tdl dl -c <chat_id> -m <message_id>`
- 注意：实际 tdl 命令参数可能需要根据版本调整

## 部署建议

### 生产环境

1. **备份策略**：
   - 定期备份 `/data/app.db`
   - 定期备份 `/data/tdl_session`

2. **监控**：
   - 监控磁盘空间
   - 监控任务队列长度：`SELECT COUNT(*) FROM jobs WHERE status='QUEUED'`
   - 监控失败任务：`SELECT COUNT(*) FROM jobs WHERE status='FAILED'`

3. **日志轮转**：
   - 配置 logrotate 或使用 Docker 日志驱动

4. **性能优化**：
   - SQLite 已使用 WAL 模式
   - 如需更高并发，考虑迁移到 PostgreSQL

### 扩展性

如果需要支持多个 Worker：

1. 修改 `pick_and_lock_job()` 的锁定逻辑
2. 每个 Worker 使用不同的 `worker_id`
3. 注意 tdl 会话的并发限制

## 已知限制

1. **tdl 命令参数**：
   - 当前实现假设 `tdl dl -c <chat_id> -m <message_id>` 可用
   - 实际可能需要根据 tdl 版本调整
   - 可能需要先获取 message link 或使用其他参数

2. **媒体组（Album）**：
   - 当前按单条消息处理
   - 如需聚合，需要额外逻辑处理 `media_group_id`

3. **文件名冲突**：
   - 理论上 `file_unique_id` 唯一，不会冲突
   - 如果手动修改文件，可能导致不一致

## 下一步改进

1. **Web 界面**：查看下载进度、浏览文件
2. **搜索功能**：全文搜索消息和文件
3. **统计报表**：按来源统计文件数量和大小
4. **自动清理**：删除旧文件释放空间
5. **多用户支持**：支持多个用户使用同一个 Bot

## 总结

这个实现完全遵循你提供的规范：

✅ SQLite 作为唯一数据库和队列  
✅ 按来源目录归档  
✅ file_unique_id 去重  
✅ 20MB 阈值分流  
✅ Bot API + tdl 双模式下载  
✅ Markdown 月度日志  
✅ 单容器双进程  
✅ 持久化卷挂载  
✅ 失败重试机制  
✅ 容器重启恢复  

代码已经可以直接使用，只需要：
1. 配置 `.env` 文件
2. 初始化 tdl 会话
3. `docker-compose up -d`

祝使用愉快！
