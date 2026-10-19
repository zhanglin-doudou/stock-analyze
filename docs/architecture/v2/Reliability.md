# 服务级高可用与可靠性设计（Reliability · v2）

> 版本 **v2.0** ｜ 2026-10-19 ｜ **取代** [v1/Reliability.md](../v1/Reliability.md)（v1 单机版，只读基线；本文不继承"单用户/单进程/SQLite/双击启动"任何前提）
> 上游：[PRD-v2.md](../../prd/PRD-v2.md) §4.2 部署形态、§4.4 安全模型、FR-L1/L6/L7、FR-R2、NFR-6/10/13/14/15
> 形态（严格沿用）：nginx ×1（TLS/静态 SPA/`/api` 同源反代）、api ×N（无状态，≥2）、worker ×M（PG 队列）、postgres 16 ×1（`ledger`/`review`/`platform` + RLS + WAL 归档）、migrate 一次性、volumes `pgdata`/`data`/`logs`；无 Redis/Kafka/K8s/多活/多可用区。
> 编号：`SLO-x` 目标、`FM-xx` FMEA、`DR-xx` 演练、`G-Rx` 门禁、`SOP-x` 处置。`⚠`=假设值，M0 实测替换；其余数字均给算式。
> **结论**：服务级高可用 =「**有副本可服务 × 口径算得对 × 新鲜度说得清**」的乘积；单机首版的诚实答案是"**主机可以宕，但 30 分钟内重建、数据一条不丢、每个降级都看得见**"。

## 0. 为什么不能小修 v1

| 维度 | v1 单机 | v2 云上多租户 | 连带改变 |
|---|---|---|---|
| 故障单位 | 进程 / 一台电脑 | 容器、主机、卷、DB、**租户** | 逐项故障域与单点（§2） |
| 不可用后果 | 一人打不开工具 | **100 租户同时不可用** | 99.9% + 错误预算（§1） |
| 数据丢失 | 可从原件重导 | 100 人交易史，**不可再生** | WAL 异地 + 每日全量 + PITR（§4） |
| 恢复 | 用户双击自愈 | 无人可点按钮 | SOP-1..10 + 季度演练（§11/§12） |
| 发布 | 关机替换 | 滚动、N-1 兼容、5xx <0.1% | SIGTERM 序列 + expand-contract（§3） |
| 状态 | 进程内可持有 | 会话/锁/队列/限流**必须外置** | 无状态检查表（§3.1） |
| 失败放大 | 单请求失败 | 重试 × 队列 × 连接池级联 | 重试预算 + bulkhead（§8） |
| 安全故障 | 不存在 | **越权 = P0** | SLO-8 + 越权检测（§10.5） |

### 0.1 可用性三段分解（本文核心判据）
```
A_服务 = A_副本 × A_口径 × A_新鲜 = 0.9995 × 0.9999 × 0.9996 ≈ 0.9990
```
| 因子 | 含义 | 失效表现 | 预算（43.2min/月） | 主责 |
|---|---|---|---|---|
| `A_副本` 99.95% | 30s 内至少一个 api/worker 可服务 | 5xx、连接拒绝、502 | 21.6min（50%） | §2/§3 |
| `A_口径` 99.99% | 数字与 `definition_version` 一致，不出错数 | 错数 = 完全不可用 | 8.6min（20%） | SLO-7/8、§7 |
| `A_新鲜` 99.96% | 降级必带 `data_as_of`/`stale`/`blocked` | 静默陈旧 = 假数据 | 13.0min（30%） | SLO-5/7、§7 |

> 0.05%+0.01%+0.04% = 0.10% = 30×24×60×0.1% = 43.2min/月。单机形态下 `A_副本` 全靠"主机可重建"，所以 §2 的消除路径比 §4 参数调优更重要。

## 1. SLO 体系
| SLO | 指标 | 目标 | 度量口径 | 数据来源 | 违反后果 | 错误预算 |
|---|---|---|---|---|---|---|
| **SLO-1** 可用性 | 月度成功请求比 | ≥99.9% | 1min 桶；失败=5xx+连接失败+服务端 >5s 超时+SSE 异常断流；**不豁免计划维护**；排除客户端 4xx（429 误伤单列） | nginx access log + API 中间件 | 预算耗尽冻结功能发布；连续 2 月违约 → 强制第二台主机 + LB | **43.2min/月** |
| **SLO-2** 延迟 | 读 p95 ≤300ms；写 p95 ≤500ms（不含 `202` 异步完成时间） | 每窗口 | 服务端直方图按**路由模板**聚合（排除 `/healthz` `/readyz` `/metrics`）；月末直方图合并求 p95，**不对窗口 p95 取平均**（会低估） | `http_request_duration_seconds` | 连续 3 天超标 → 性能任务升 P0；单端点 >2× 基线 10min → 回滚 | 5% 可超阈（p95 定义） |
| **SLO-3** 错误率与放大 | 5xx ≤0.1%（1h 滚动）；429 误伤率 ≤0.5%；job 失败率 ≤0.5% | 同上 | 429 误伤=被限流但令牌桶本应放行的比例（影子 dry-run 计数）；job 失败含 `failed` 且非用户取消 | Prometheus + `platform.job` | 查重试放大（§8.3）；误伤 >2% → 限流参数回退 | 0.1%/h |
| **SLO-4** 积压时效 | 最老 `pending` p95 ≤5min（常态）/≤30min（月末峰值）；排队 p95 ≤60s；月度 job 成功率 ≥99.5% | 每 5min 采样 | `max(now()-created_at) FILTER (WHERE status='pending')` 存点；月末峰值窗口单列 | `platform.job` 采样器 | >30min → 自动背压（§5.5）；成功率 <99.5% → 逐类归因 | 0.5% job |
| **SLO-5** 备份新鲜度 | 归档延迟 p99 ≤60s 且**最大 ≤300s**；全量 age ≤26h；校验通过率 100% | RPO 硬界 5min | 归档延迟=对象 last_modified−段写满时刻；全量 age=now−`manifest.created_at` | 归档清单 + `platform.backup_run` | 连续 **2 日**失败/校验不过 = P0：冻结发布 + 强制演练 | **0** |
| **SLO-6** 演练通过率 | 季度 100% 通过；实测 RTO ≤30min、RPO ≤5min | 每季度 ≥1 次 | 按 §4.7 五步；RTO 为墙钟（决策→`/readyz` 200→抽检通过） | `platform.drill_run` | 未通过 → 下版本禁止发布 | **0**（每季 1 次） |
| **SLO-7** 降级标注率 | 100% 降级响应带机器可读标注；静默降级 = 0 | 逐请求断言 | 必须含 `degraded[]{code,scope}`+`data_as_of`+`stale/blocked`；月抽 100 响应与 §7 逐格比对 | 响应契约 + `degraded_responses_total` | 一格不符 = P0 口径缺陷（FR-L6 同级） | **0** |
| **SLO-8** 越权零容忍 | 跨租户成功读写 = 0；RLS 拦截后仍成功 = 0；admin 读明细 = 0 | 绝对零 | 每端点负向用例 + RLS 拒绝日志 + 审计；任何 1 次成功即事件 | 测试报告 + `authz_denials_total` + 审计 | P0：冻结发布、轮换 `JWT_SECRET` 与全部 refresh/PAT、72h 出根因 | **0** |

| 对象 | RPO | RTO | 机制 | 诚实边界 |
|---|---|---|---|---|
| 已提交流水 `ledger.*` | 崩溃 **0**；主机丢失 ≤**120s** | ≤30min | `synchronous_commit=on`；`archive_timeout=60s`+上传 ≤60s = 120s < 300s（余量 2.5×） | 云盘谎报 flush；无跨 AZ |
| 派生指标 `review.*` | **0**（`inputs_hash` 校验重算 diff=0） | 30min 内可读旧快照；全量重建 ≤4h ⚠ | 恢复后按需重算，被访问租户优先入队（100×60s/3 worker ≈33min，峰值上限 4h） | 期间看旧值（已标注，不算违约） |
| 原始账单 `data` 卷 | ≤24h（卷快照） | 不阻塞服务 | 每日快照 + 用户本地原件可重传 | 快照与库不同刻，重放按批次号对齐 |
| 报告/导出产物 | 不适用（可重生成） | ≤10min/份 | `tmp+rename` 原子写、DB 记录后写 | 无 |
| 异地备份 | 恢复点 age ≤1h（PITR 任意秒，WAL 14 天） | ≤30min | 每日 basebackup + 连续 WAL | 对象存储凭据/欠费 |

> RTO 刻意区分"服务可用"与"派生数据就位"：**30min 内能登录、能读、能看到带标注的旧值**；全量重算后台完成。否则 RTO 只能靠虚假承诺达标。

| 剩余预算（SLO-1） | 发布策略 |
|---|---|
| >50% | 正常功能发布 |
| 10%~50% | 只允许修复类 + 契约兼容发布，发布后 1h 看 SLO 面板 |
| <10% / 耗尽 | 冻结功能发布（hotfix 双人审批 + 复盘）；只做可靠性并列出本月消耗来源 |

> SLO-8 任一违反 = 立即冻结 + 安全评审，不参与预算计算（零容忍类不能"花"）。

## 2. 故障域与单点清单
```
Internet ──TLS──▶ [云主机 · 单可用区] nginx:443 ─▶ SPA(dist 卷) + /api/*
                  ├─ api ×3 ─┬─▶ postgres:5432 ─▶ pgdata 卷(库+WAL)
                  ├─ worker ×3 ┘        └─▶ data / logs 卷
                  └─ migrate(一次性)        └─▶ 外部行情源 / LLM（可失败）
```
| # | 单点 | 爆炸半径 | 影响 | 判定 | 理由与消除路径 |
|---|---|---|---|---|---|
| SP-1 | 云主机 | 全部容器 + 卷 | 全站中断直到重建 | **接受（限时）** | 100 用户盘后为主；云主机 ⚠99.95%（22min/月，占预算 50%）。触发消除：付费 SLA/用户 >100/单月中断 >2 次 → M2.5 第二台主机 + LB |
| SP-2 | nginx 容器 | 全部入站 | 502/超时（api 健康也不可达） | **缓解** | `nginx -t` 前置、配置漂移检测、`restart: unless-stopped`、SPA 可回切；消除 = 前置云 LB（M2.5） |
| SP-3 | api 副本集 | 单容器无影响；主机全断 | 滚动期间请求失败 | **缓解（≠消除）** | 3 副本只解决"单容器死"，**不解决主机死**——副本数不是 HA，跨故障域才是 |
| SP-4 | worker 进程 | 单死降吞吐；全死异步停摆 | 导入/重算/报告/导出排队 | **缓解** | M=3，滚动时仍 2 个；积压靠背压 + `--scale` |
| SP-5 | postgres 单实例 | 全部数据 | 全站不可用 | **接受（限时）** | 首版最大单点；缓解=快速重建（§4.7）+ WAL 异地；消除=M2 流复制热备（骨架已预留 `wal_level=replica`） |
| SP-6 | `pgdata` 卷 | 库 + WAL 本地副本 | 重建后回滚到最近备份 | **缓解** | 每日 basebackup 异地 + WAL 连续归档（RPO ≤120s）；不做 RAID（云盘已有副本） |
| SP-7 | `data` 卷 | 归档/报告/导出 | 产物丢失、导出不可用 | **缓解** | 产物可重生成、原件在用户手里；消除=对象存储（M3，OQ-2 已预留） |
| SP-8 | `logs` 卷 / Docker 引擎 | 日志 / 全部容器 | 可观测缺口 / 全站中断 | **接受** | `docker logs` 兜底 + 轮转；引擎死 = `systemctl restart docker`（SOP-1） |
| SP-9 | 域名 DNS + TLS 证书 | 入站 | 无法访问 / 全员 HTTPS 失败 | **缓解** | TTL 300s、双商冗余；自动续期 + 21/14/7 天三级告警 + 提前 reload（SOP-5） |
| SP-10 | 云可用区 | 主机+卷+网络 | 全站中断 | **接受（明确不做多 AZ）** | 需托管 PG/对象存储，与单机 Compose 骨架冲突（§13） |
| SP-11 | 外部行情源 | 指标/归因/报告 | 降级 | **缓解** | 适配器 + 缓存 + 熔断 + 能力协商（§7/§8） |
| SP-12 | LLM | 对话 | 部分可用 | **缓解** | 模板兜底 + 金额后端填充（FR-D7） |
| SP-13 | NTP / 时钟 / 备份目标 | 调度、JWT、审计、RPO | 重复触发、误判、RPO 违约 | **缓解** | UTC 存储 + `monotonic` 算时长 + 漂移告警（DR-07）；双凭据/双桶路径 + 本地短期缓冲 |

| 首版诚实问题 | 回答 |
|---|---|
| 主机宕了？ | **全站中断**，RTO ≤30min：重建 5min + basebackup 8GB/20MB/s≈7min + WAL 回放 ≤3min + 启动校验 5min + 公告 5min ≈25min |
| 为什么可接受？ | 100 用户盘后使用；数据不丢（WAL 异地）→ 中断不升级为数据事故；有演练 SOP 与 SLO-6 门禁；30min < 43.2min 预算 |
| 何时必须消除？ | 付费 SLA、用户 >100、单月主机中断 >2 次、或重建实测 >30min（SLO-6 违约）。顺序：先 PG 热备（RTO→≤10min）再跨主机 api |

## 3. 无状态化与发布
### 3.1 无状态检查表（每条有自动化断言）
| 状态项 | 允许位置 | 禁止 | 验证 |
|---|---|---|---|
| 会话/refresh/撤销 | `platform.session`、`refresh_token`、`token_version` | 内存 dict、本地文件、sticky session | 双副本交替请求同一会话 200 |
| 任务队列 | `platform.job`（§5） | `asyncio.Queue`、本地队列文件 | 杀 worker 后任务被接管 |
| 跨请求锁/单飞 | `pg_advisory_xact_lock`（事务级）或 `platform.lock` 行锁 | 进程内 `asyncio.Lock`、文件锁 | 双 api 并发重算 → 仅 1 个 running（FR-A3） |
| 幂等键 | `platform.idempotency`（唯一 `(user_id,key)`） | 内存缓存 | 并发同 key → 同 `job_id` |
| 限流计数 | `platform.rate_limit`（原子 `UPDATE ... RETURNING`） | 进程内计数（多副本不准） | 3 副本下第 N+1 次 429 |
| L1 缓存 | 进程内 dict+TTL，**只缓存可重建数据** | 缓存用户授权结论 | 冷启动行为一致 |
| 上传临时文件 | `data/tmp`（同卷 → `rename` 原子） | `/tmp`、容器可写层 | 容器重建无孤儿引用 |
| 定时任务 | 单实例 `pg_try_advisory_lock` + 幂等 | 容器内 cron / 本地调度 | 3 worker 同时到点只执行 1 次 |
| 静态 SPA | `dist-<sha>` + `current` 软链原子切换 | 就地覆盖 `dist` | 回滚=软链切回上一 sha |
| 日志/指标 | stdout → `logs` 卷；`/metrics` 各副本自报 | 进程内聚合单点暴露 | 任一副本重启不丢历史 |

> 多租户额外一条：**授权每请求从 PG/JWT 重算**，"首次校验后缓存 5 分钟"会把 RLS 第二道防线变成摆设（SLO-8）。

### 3.2 SIGTERM 优雅停机（FR-L1：≤30s）
> 顺序：**readiness 先摘除 → drain（停新 + 在途）→ 释放 job 租约 → 退出**，总预算 30s；顺序不可调换。

| 阶段 | t(s) | 动作 | 预算 | 失败兜底 |
|---|---|---|---|---|
| ① 摘除 | 0 | `/readyz` 立即 503（`draining=true`，DB 仍连）；nginx `max_fails=1 fail_timeout=5s` 被动摘除 | ≤5 | 首个失败请求由 `proxy_next_upstream` 转投其他副本（仅幂等方法） |
| ② 停新 | 0 | 关 listen backlog；新写返回 `503 + Retry-After: 2` | 0 | 客户端重试（§8） |
| ③ 在途 | 0~20 | uvicorn `--timeout-graceful-shutdown 25`；SSE 发 `event: reconnect` 断流（客户端带 `Last-Event-ID`） | ≤20 | 超 25s 强断，事务回滚 |
| ④ 释放租约 | 20~25 | worker 停 claim；未完成 job → `pending, available_at=now()`，**attempt 不增**（非失败） | ≤5 | 超 25s → SIGKILL，租约回收（attempt+1，§5.1） |
| ⑤ 退出 | ≤30 | 关池、flush 日志、exit 0 | — | compose `stop_grace_period: 40s` |

### 3.3 滚动升级与兼容规则
| 步 | 动作 | 兼容要求 | 失败判定 |
|---|---|---|---|
| 0 | 发布前备份 + 迁移 dry-run（生产快照副本） | 迁移可重入 | 备份 age >26h → 先补备份 |
| 1 | `migrate`（**只做 expand**，DDL 锁 <2s，总时长 <300s） | 旧 api/worker 可运行 | 失败 → 中止发布，旧版继续（无副作用） |
| 2 | worker 滚动（`--scale 6` 起新再缩 3，旧实例 drain） | worker N-1 能处理新 `payload_version` | job 失败率 >2% 5min → 停 |
| 3 | api 滚动（`--scale 6` → 缩 3） | API 向后兼容 N-1（只增不改） | 5xx >0.5% 3min → 回滚 |
| 4 | SPA 发布（`dist-<sha>` + 软链） | 新 SPA 兼容 N-1 API（SDK 由契约生成） | 白屏/5xx → 软链回切 |
| 5 | **contract 延迟到下一版本** | 旧代码已不引用旧结构 | — |
> nginx 关键细节：Compose 扩容时容器 IP 变，nginx 启动时解析 `api` 会**永远打旧 IP**。必须 `resolver 127.0.0.11 valid=5s; set $api http://api:8000; proxy_pass $api;` + upstream keepalive，否则"扩了副本流量没过去"，SLO-1/3 会莫名抖动。

| 类别 | 规则 | 反例 |
|---|---|---|
| REST API | 只增字段/枚举/端点；删除或改语义 = `/api/v2` | 给 `status` 加 `dead` 破坏前端 N-1 → 本文用 `failed + dead_letter_at`（§5.3） |
| DB schema | expand-contract；旧列/旧索引保留 ≥1 版本 | 同版本"加列+删列" |
| job payload | 带 `payload_version`；worker 双读 N-1/N | 改字段名 → 在途 job 反序列化失败 |
| 认证/SPA | JWT claim 只增、refresh 表只加列；旧 dist 可回切 | 改 `sub` 语义 → 全员掉线 |

### 3.4 expand-contract 三步迁移（示例：`ledger.trade` 加 `fee_v2` 并切读）
| 阶段 | 可执行动作 | 代码状态 | 可回滚 | 门禁 |
|---|---|---|---|---|
| **expand**（N） | `ALTER TABLE ledger.trade ADD COLUMN fee_v2 numeric(18,2) NULL;`（无默认 → 不重写表）；`CREATE INDEX CONCURRENTLY` | 双写；读仍用 `fee` | ✅ 回滚代码 | DDL 锁 <2s、迁移 <300s |
| **backfill**（N） | 按 `(user_id, trade_date)` 分片，每事务 ≤5000 行、限速 500 行/s，`WHERE fee_v2 IS NULL` 可重入 | 双写+双读 `COALESCE(fee_v2, fee)` | ✅ | 每 10min 比对 `SUM(fee)` vs `SUM(fee_v2)`，diff>0 即停 |
| **switch**（N+1） | `CHECK (fee_v2 IS NOT NULL) NOT VALID` 后 `VALIDATE` | 只读 `fee_v2`；flag 灰度 10%→100%，观察 24h | ✅ flag 回旧读 | 抽样 30 回合 pnl ≤0.01 元 |
| **contract**（N+2） | `DROP COLUMN fee`、删旧索引 | 不再引用 | ❌ 不回滚（靠备份） | 上一版本稳定 ≥1 周且无旧实例 |
**回滚触发**（任一命中即停并回退代码，expand 不回滚）：迁移锁 >2s / 迁移 >300s / backfill diff>0 / 发布后 5xx >0.5% 3min / p95 >2× 基线 10min / job 失败率 >2% / `/readyz` schema 不匹配 / SLO-8 异常。**contract 一旦执行禁止回滚旧版本**——这就是"延迟一个版本"的真正原因。

## 4. PostgreSQL 可靠性
### 4.1 规格与容量
| 项 | 取值 | 算式 / 理由 |
|---|---|---|
| 主机 | 4 vCPU / 8GB / 200GB SSD ⚠ | 100 用户 × 5 万行；瓶颈是慢 SQL 不是 IOPS（§9.2） |
| 库设计容量 | **8GB** | heap+index ≈3.9GB ×1.5（膨胀/新索引/临时），§9.1 |
| 卷 | `pgdata` 50GB / `data` 30GB / `logs` 10GB | 库 8+WAL 峰值 4+余量；归档 7.2GB/3 年+报告+导出峰值 |

### 4.2 参数建议表
| 参数 | 取值 | 理由 | 风险 / 约束 |
|---|---|---|---|
| `max_connections` | **100** | api 3×(8+2)=30 + worker 3×4=12 + 运维/备份/监控 6 = **48**，×1.3≈63，取 100 容纳扩副本与应急 | 每连接 ~8MB → 0.8GB；**调大不是扩容，是掩盖连接泄漏** |
| `shared_buffers` / `effective_cache_size` | **2GB**（25%×8GB）/ 5GB | 热点集（近 1 年月度访问+索引根）常驻；planner 按 62% RAM 估算 | 过大挤占容器与 OS cache |
| `work_mem` | **16MB** | 每排序/哈希节点；20 并发×3 节点×16MB ≈1GB 上界 ⚠ | 高估 OOM，低估落盘（看 `log_temp_files`） |
| `wal_level` | `replica` | 归档 + 预留流复制热备（SP-5） | 不可 `minimal`（归档失效） |
| `archive_mode` / `archive_command` | `on` / `rclone` 或 `wal-g` 到对象存储（加密） | RPO ≤5min 的载体 | 命令必须非 0 退出；积压撑爆 pgdata（告警 §10.2） |
| `archive_timeout` | **60s** | 空闲库也强制切段：最坏=60s 切段+60s 上传=**120s** <300s | WAL 段小、请求多（~1440/天），存储成本可忽略 |
| `wal_compression` / `max_wal_size` / `min_wal_size` | `on` / 4GB / 1GB | 归档体积降 2~3× ⚠；批量导入减少 checkpoint 抖动 | 少量 CPU；崩溃恢复时间随 max_wal_size 增长（<30s ⚠，DR-02 实测） |
| `checkpoint_timeout` / `completion_target` | 15min / 0.9 | 平滑 checkpoint IO，避免 p95 尖刺 | 恢复上界 ≈15min ⚠ |
| `synchronous_commit` | `on` | 本机崩溃 RPO=0（每次 commit 一次 fsync ≈1~2ms） | 热备后改 `remote_write` 可更低延迟保 RPO=0 |
| `full_page_writes` / `fsync` | `on` / `on` | 防页撕裂与假提交，**不可关** | 写放大，必须接受 |
| `random_page_cost` / `effective_io_concurrency` | 1.1 / 200 | SSD 随机读接近顺序读 | HDD 需回调（本项目不做） |
| `statement_timeout` | API 读 5s / 写 15s / worker 30min（按角色） | 防慢 SQL 拖垮连接池；worker 长任务单独放宽 | 5s 会杀合法大聚合（指标重算走 worker 不走 API） |
| `lock_timeout` / `idle_in_transaction_session_timeout` / `deadlock_timeout` / `log_lock_waits` | 3s / 60s / 1s / `on` | 锁等待快速失败；杀"忘提交"连接；死锁可观测 | 与 §4.4 处置联动；日志量小 |
| `log_min_duration_statement` / `log_temp_files` / `pg_stat_statements` / `track_io_timing` | 200ms / 64MB / 预加载 / `on` | 慢查询与落盘排序留痕；归一化 SQL 成本、区分 CPU 与 IO 慢 | 受 §10.1 采样控制；重启生效 |
| `row_security` / `FORCE RLS` | `on` / 所有业务表 | RLS 第二道防线（§4.8） | 表 owner 也不能绕过；只有 `migrate` 角色 `BYPASSRLS` |
| autovacuum（`platform.job` 覆盖） / `maintenance_work_mem` | `scale_factor=0.02, cost_delay=2ms` / 256MB | job 高频 update 膨胀最快；索引重建加速 | 过度 vacuum 抢 IO，仅 job 表加频 |
| `timezone` / `default_transaction_isolation` | `UTC` / `read committed` | 时间统一 UTC（附录 B）；报告用 `REPEATABLE READ` 快照 | 无需串行化 |
| `max_wal_senders` / `wal_keep_size` / `hot_standby` | 3 / 1GB / `on` | 为 M2 热备预留，不开备机零成本 | — |

### 4.3 连接池与 `max_connections` 算式
| 来源 | 算式 | 峰值 |
|---|---|---|
| api | 3 副本 ×（`pool_size` 8 + `max_overflow` 2） | 30 |
| worker + migrate/备份/监控/临时 | 3×4 + 6 | 18 |
| 合计 → `max_connections` | 48；`ceil(48×1.3/10)×10 = 70` → 取 **100** | 48 |
| `pool_timeout` / `recycle` / `pre_ping` / `statement_cache` | **1s** / 300s / `on` / 0（若上 pgbouncer） | 排队 >1s 已不可接受（读 p95 300ms）→ 快速失败 `503 DB_POOL_EXHAUSTED`+`Retry-After: 2` |
| 何时上 pgbouncer | api 副本 >6 或连接使用率 >80% 持续 5min | 48/100 远未到；上了可降 `max_connections` 到 40，但**会话级 advisory lock 失效** → 锁改事务级/行锁（首版不上） |

> 连接池在多租户下的真实价值不是"更快"，而是**过载时把雪崩变成可预测的 503**：没有 `pool_timeout=1s`，一次慢 SQL 占满 30 个连接，100 个租户一起超时（FM-03）。

### 4.4 慢查询 / 锁等待 / 长事务
| 监控项 | 阈值 | 自动动作 | 人工动作 |
|---|---|---|---|
| `pg_stat_statements` 单语句 p95 | >500ms（API）/ >10s（worker） | 记 `slow_query` | 下版本加索引/改写；SOP-4 定位 |
| 锁等待（`lock_timeout` 触发） | >10/min | 记录阻塞树 | 按阻塞树 kill 源头 |
| 长事务 / `idle in transaction` | >60s / >60s | 告警 / 数据库自动终止 | `pg_terminate_backend`；backfill 改分片（§3.4）；idle 出现即代码 bug |
| 表膨胀 / dead tuples | >20% 且 >10 万 | 触发 `VACUUM (ANALYZE)` | 检查 autovacuum 是否被长事务阻塞 |
| `pg_wal` / 归档积压 | >10GB 或 >100 段 | 告警（SLO-5） | 修归档命令、清 slot |

> 阻塞树查询（SOP-4 第一步）：`SELECT pid, state, now()-xact_start AS age, pg_blocking_pids(pid) AS blockers, left(query,120) FROM pg_stat_activity WHERE wait_event_type='Lock' ORDER BY age DESC;`。处置原则：**先降级再根因**——kill 长事务前确认是否持写锁（导入/迁移），能等 30s 就等（kill 掉的导入只是留下一个可重试 job，成本低于猜错）；API 有 `statement_timeout` 兜底，慢 SQL 不会变成"全站挂"。

### 4.5 WAL 归档与 PITR
| 项 | 做法 | 校验 |
|---|---|---|
| 归档目标 | 对象存储独立桶/前缀，服务端加密 + object lock 防误删 | 每小时 `LIST`：最近 3 段可下载且 sha256 与 `pg_stat_archiver` 一致 |
| 归档失败 | 非 0 退出 → PG 重试并保留 WAL；积压 >100 段告警 | SLO-5 直接触发 |
| PITR 窗口 / 跨版本 | 14 天 WAL + 每日全量；任意秒恢复点；跨 major 只走逻辑备份 + `pg_upgrade` 演练 | 恢复后 `pg_controldata` 时间线与 `pg_waldump` 一致；门禁 G-R2 |
| 恢复 | `restore_command` 拉取 → `recovery_target_time` → `recovery_target_action=promote` | 见 §4.7 |

### 4.6 备份策略与校验（5 项全过才算成功）
| 类型 | 频率 | 工具 | 保留 | 用途 |
|---|---|---|---|---|
| 物理全量 | 每日 02:00 | `pg_basebackup -Ft -z -X stream --checkpoint=fast --manifest-checksums=SHA256 --max-rate=50M` | 7 天 | PITR 基线、整机重建 |
| WAL 连续 | 60s | `archive_command` | 14 天 | RPO ≤5min、任意秒恢复 |
| 逻辑 + 卷快照 | 每周日 / 每日 | `pg_dump -Fc --no-owner`（按 schema）/ 云盘快照（`data` 卷） | 4 周 / 7 天 | 跨版本迁移、单租户抢救 / 原始账单与产物 |

| # | 校验项 | 通过判据 | 失败动作 |
|---|---|---|---|
| 1 | 结构 | `pg_verifybackup` 退出码 0（依赖 manifest 校验和） | 重做 1 次；再失败 P0（SLO-5） |
| 2 | 指纹 | 每文件 sha256 与 manifest 一致，manifest sha256 记入 `platform.backup_run` | 同上 |
| 3 | 行数 | `ledger.trade`、`review.round`、`round_fill`、`nav_day` 与源一致（容差 0） | 同上 |
| 4 | 哨兵聚合 | `count/sum(amount)`、`count(round)/sum(pnl)`、`max(updated_at)` 一致；抽 30 回合 pnl ≤0.01 元、收益率 ≤1bp（PRD G1） | 同上 |
| 5 | 版本与安全 | `schema_version` 匹配；RLS policy 数与 `pg_policies` 一致；`row_security=on` | 标版本差异 → 恢复后强制全量重算 |

> 坏件**不删**，改名 `*.bad-<ts>` 保留诊断。恢复后必须验证 RLS 仍生效（任意租户读他人 → 0 行），否则恢复出一份"越权可用"的库。

### 4.7 恢复演练 SOP 与判据（SLO-6）
| 步 | 动作 | 通过判据 |
|---|---|---|
| 1 | 决策 + 公告（演练/真实同流程）→ 重建容器栈（空卷起 postgres） | 5min 内完成；`pg_isready` |
| 2 | basebackup + `restore_command` 回放到目标时刻 | 8GB/20MB/s≈**410s**，WAL 回放 ≤3min |
| 3 | 启动 api/worker/migrate | `/readyz` 200、`schema_version` 匹配、api 3/3 ready |
| 4 | 冒烟 + 校验 | 5 项全过；租户读自己 200/读他人 404；30 回合 pnl diff ≤0.01 元；实测 RTO ≤30min、RPO ≤5min |
> **不能单租户 PITR**：误删某租户 = 恢复到临时实例 → 逻辑导出该 `user_id` → 导入生产 → 重算。写进 SOP-7，因为"注销即物理删除"（PRD §4.4）让误删不可逆。

### 4.8 RLS 与角色
`app` 角色 CRUD + **FORCE RLS**、无 `BYPASSRLS`、非 owner（api/worker 池）；`migrate` 为 owner 且 `BYPASSRLS`（仅迁移容器）；`backup` 用 `pg_read_all_data`+复制权限；`monitor` 用 `pg_monitor`。每事务 `SET LOCAL app.current_user_id = :uid`（pgbouncer transaction 模式兼容），policy 用 `current_setting('app.current_user_id', true)::uuid`，未设置返回 NULL → **0 行（fail-closed）**。代价是每查询多一个谓词（⚠<0.2ms）；`EXPLAIN` 必须显示 `user_id` 索引参与过滤，否则"慢查询"会伪装成 RLS 问题。

## 5. 任务队列可靠性（`platform.job`）
```sql
WITH c AS (SELECT id FROM platform.job WHERE status='pending' AND available_at<=now()
           ORDER BY priority, available_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
UPDATE platform.job j SET status='running', attempt=attempt+1, heartbeat_at=now(),
       started_at=now(), worker_id=$1 FROM c WHERE j.id=c.id RETURNING j.*;
-- 部分索引: CREATE INDEX CONCURRENTLY job_claim_idx ON platform.job (available_at,id) WHERE status='pending';
-- 单飞: CREATE UNIQUE INDEX job_singleflight ON platform.job (kind,user_id,dedupe_key) WHERE status IN ('pending','running');
```
| 机制 | 取舍 |
|---|---|
| `SKIP LOCKED` | 优点：worker 不互相阻塞，吞吐线性可扩。代价：**不保证 FIFO**（用 `priority`+年龄提升兜底）、**非 exactly-once**（认领后崩溃会重跑 → 业务必须幂等）。100 用户下无热点行（认领即改 `running`，索引只扫 pending） |
| 部分索引 / 清理 | 扫描限制在待办集合（通常 <100 行），避免全表扫；`succeeded` 保 30 天、`failed` 90 天，`DELETE ... LIMIT 10000` 分批避免长事务 |

### 5.1 租约、心跳与回收
| 参数 | 取值 | 算式 / 理由 |
|---|---|---|
| `lease` / `heartbeat_interval` | 60s / 20s | 心跳 20s × 3 次容错；长任务（报告/重算）每阶段必心跳 |
| 回收 | `running` 且 `heartbeat_at < now()-60s` → `pending`，`available_at=now()+backoff`，`error='LEASE_EXPIRED'` | 3 次心跳丢失 = worker 已死；崩溃计一次失败（认领时已 attempt+1），防崩溃循环 |
| 优雅释放（SIGTERM） | → `pending`，attempt **不增** | 主动让出不受罚 |
| `max_attempts` | 5（导入/重算）、3（报告/导出） | 见 §5.2 总等待 |

> 心跳为什么不放 Redis：第二个状态源会让"worker 以为自己在跑、队列以为它死了"成为常态；PG 单写点+60s 租约的代价只是回收延迟 ≤60s。

### 5.2 重试退避（算式）
`delay(n) = min(30s × 2^(n−1), 1h) × (1 + U(−0.3, +0.3))`；n=1..5 退避 21~39s / 42~78s / 84~156s / 168~312s / 336~624s（封顶 1h），累计最坏 ≤1.6h 后进 dead-letter。抖动 ±30% 的作用：100 租户月末同时失败时，避免整批 worker 齐步重试打满 PG（重试放大，§8）。

### 5.3 毒任务隔离（不新增 status，避免破坏 API 枚举）
| 项 | 设计 |
|---|---|
| 判定 | `attempt >= max_attempts`，或同 `error_code` 连续 3 次，或 payload 校验失败（**立即判死**，不浪费重试） |
| 落库 | 保持 `status='failed'` + `dead_letter_at=now()` + `error_code` + `progress` 快照；**不加 `dead` 枚举**（PRD 固定 5 态，增枚举破坏前端 N-1，§3.3） |
| 可见 / 处置 | `GET /jobs/{id}` 返回 `dead_letter=true`+`what_to_do`（NFR-12）；admin 列表可筛；修输入后 `POST /admin/jobs/{id}/requeue`（attempt 重置、写审计）；同 kind 1h 内 ≥3 个毒任务 → 自动暂停该 kind 新任务并告警 |
| 指标 | `job_dead_letter_total{kind,error_code}` >0 → P1；同 kind 1h ≥5 → P0 |

### 5.4 幂等前提（没有幂等就没有重试）
| 任务 | 幂等键 | 重复执行后果 |
|---|---|---|
| 导入 commit | `(user_id, dedupe_key)` bag 去重（A27）+ `Idempotency-Key` 映射同一 job | 第二次 inserted=0（FR-L3） |
| 回合/指标重算 | `(user_id, period, definition_version, inputs_hash)`；窗口内 `DELETE+INSERT` 同事务 | diff=0（NFR-5） |
| 报告 / 导出 | `(user_id, period, spec_hash)` 复用 `report_id` / `(user_id, scope_hash)`+文件名含 hash | 同一文件，不重复计费/不产生两份 |
| 单飞 | 部分唯一索引（见上） | 并发提交返回同一 `job_id`（FR-A3） |

### 5.5 背压（拒绝 vs 排队）
| 水位 | 判定 | 行为 | 用户可见 |
|---|---|---|---|
| 正常 / 压力 | 最老 pending <5min / 5~10min | 接受 / 接受 + `X-Queue-Estimate: 480s` | "预计 8 分钟"（场景 B 可脚本判断） |
| 高压 | >10min 或 pending >2000 | **新异步写 429 + `Retry-After: 60`**，读不受影响 | "系统繁忙，60s 后重试" |
| 熔断 | >30min 或 worker 健康数 =0 | 所有异步写 503 `QUEUE_BACKPRESSURE` | 拒绝受理优于受理后永不完成 |
> 拒绝优先于排队：异步任务的"未完成"对用户是黑箱；超过 30min 已违反 SLO-4，继续收单只会让恢复更难。

### 5.6 worker 崩溃 / 滚动恢复
| 场景 | 恢复动作 | 用户可见 |
|---|---|---|
| `SIGKILL` / 全部 worker 挂 | 租约 60s 回收 → pending（attempt+1）→ 其他 worker 接管；`restart: unless-stopped` + 背压 + `--scale` | 进度略回退，最终成功；最长 60s 无消费 |
| 滚动升级 / 长任务卡单步 | 先起新再缩旧，旧实例 ≤25s 完成或释放；心跳仍在 → 不回收（正确），阶段超时 >10min 判失败 | 无中断；可取消（FR-R2） |
| 队列表膨胀 | 部分索引 + 定期清理 + job 表加频 autovacuum | 无 |

## 6. FMEA（20 项，逐条对应 PRD 风险清单）
| ID | 故障模式 | 检测 | 影响 | 缓解 | 恢复 | 残留风险 |
|---|---|---|---|---|---|---|
| FM-01 | api 副本全挂 | `/readyz` 全 503、502 率 >0.1% | 全站不可用 | 3 副本 + 自动重启 + 内存上限 | compose 重启 ≤30s；失败 → SOP-1 | 主机级时副本救不了（SP-1） |
| FM-02 | PG 主库宕 | `pg_isready` 失败、`/readyz db:down` | 全站不可用 | 参数保守、磁盘水位、自动重启 | SOP-1 重启→崩溃恢复 ≤30s；仍失败→SOP-7 | 单实例无自动 failover（§13） |
| FM-03 | 连接池耗尽 | `pool_wait` p95 >1s、`503 DB_POOL_EXHAUSTED` | 延迟飙升、写失败 | `pool_timeout=1s`、`statement_timeout`、慢 SQL 门禁 | 杀慢查询/长事务 → 压测验证（SOP-4） | 慢 SQL+突发叠加仍拒部分请求 |
| FM-04 | 迁移失败/中断 | migrate 非 0；`/readyz` schema 不匹配 | 新版本无法上线（旧版仍跑） | expand-only、`lock_timeout=3s`、dry-run、备份前置 | 中止发布；expand 列保留无害；contract 前可回滚 | contract 后不可回滚 |
| FM-05 | `pgdata` 写满 | 水位 <15%、`ENOSPC` | 写失败、WAL 无法归档（RPO 违约） | 容量预算、WAL 上限、日志轮转 | SOP-4 清已归档 WAL/旧日志→扩卷→归档补齐 | 连 `pg_wal` 都不可写时只能扩卷 |
| FM-06 | 卷丢失 | 容器起不来、`pg_controldata` 不可读 | 回滚到最近备份（RPO ≤120s） | 发布脚本禁用 `down -v`；每日快照 | SOP-7 重建 + PITR | 快照与库不同刻，需归档对齐 |
| FM-07 | worker 全挂 | `worker_healthy=0`、最老 pending 增长 | 异步停摆（读可用） | 3 副本 + healthcheck + 自动重启 | 拉起→租约回收→自动消化 | 毒任务集体崩需先隔离 |
| FM-08 | 队列积压 | 最老 pending >10min | 导入/报告延迟 | 背压、月末 `--scale 8`、单飞 | SOP-3 扩 worker→隔离毒任务 | 100 租户同时全量重算仍需限流 |
| FM-09 | OOM | `OOMKilled` 计数、RSS 斜率 | 单副本重启；批量时全挂 | 容器内存上限、请求体 20MB、流式解析、并发上限 | 自动重启；查泄漏（FM-13） | 大文件+大报表并发仍可能 OOM |
| FM-10 | 慢 SQL / 锁等待 | `lock_wait`、阻塞树 | 延迟雪崩、池耗尽 | 索引评审、`lock_timeout`、并发建索引 | SOP-4 杀阻塞源→加索引 | 统计信息过期导致计划突变 |
| FM-11 | nginx 配置错误 | `nginx -t` 失败、502/504 飙升 | 全站或 api 不可达 | 发布前 `-t`+模板校验+reload 灰度 | SOP-6 回滚配置→reload | 语法对但语义错（upstream 无 resolve） |
| FM-12 | TLS 证书过期 | `cert_expiry_days <21` | 全员 HTTPS 硬阻断 | 自动续期+三级告警+提前 reload | SOP-5 续期→reload | 续期因 DNS/端口校验静默失败 |
| FM-13 | 内存泄漏 | RSS 斜率 >5%/天、重启频率 | 周期性 OOM、延迟劣化 | 72h soak、内存上限、定期滚动重启 | 滚动重启；定位版本回滚 | Python 堆碎片难完全避免 |
| FM-14 | 时钟漂移/回拨 | NTP 偏移、`now()-monotonic` 差 | 调度重复/漏触发、JWT 误判 | UTC 存储、`monotonic` 算时长 | SOP-9+重算受影响窗口 | 人工改时间需审计 |
| FM-15 | 发布中断（滚动一半） | 新旧混合、5xx 抬升、部分 ready 失败 | 延迟/错误抖动、半版本 | 逐步扩容-缩容、N-1 兼容、一次只动一类 | SOP-6 回滚 tag；expand 保留 | job payload 双读覆盖不足 |
| FM-16 | secret 轮换 | 认证错误率、`401` 骤增 | 全员掉线或连接失败 | 双密钥窗口（旧钥保留 ≥2× access 有效期） | SOP-8 分批轮换→观察→撤旧 | 窗口过短导致在途 refresh 失败 |
| FM-17 | 行情源故障 | 熔断 OPEN、`market_fetch_errors` | 指标/归因/报告降级（导入不受影响） | 缓存优先、超时重试、灾备源、能力协商 | SOP-10+§7 标注 stale | 源"200 但脏数据"（校验层兜） |
| FM-18 | LLM 故障/超限 | 熔断、`llm_errors`、成本告警 | 对话部分不可用；金额类仍对 | 模板兜底、金额后端填充、30s 超时、配额 | 自动切模板（无人工步骤） | 措辞质量下降（数字不编造） |
| FM-19 | 越权事件 | `authz_denials{layer=rls}` >0、负向用例失败 | 数据泄漏（P0） | 仓储 `user_id`+RLS+404 语义+每端点负向用例 | SOP-10：冻结、轮换密钥、取证、通知 | 逻辑漏洞（批量回显）需人工审计 |
| FM-20 | 备份不可用/损坏 | SLO-5 校验失败、`backup_age` | RPO/RTO 违约（恢复时才发现最危险） | 备份后立即 5 项校验+保留上一份+异地 | SOP-7 用上一份；皆坏→归档重放 | 对象存储凭据/欠费导致"以为有备份" |

## 7. 降级矩阵
> 三条硬规则：① **资产类硬阻断**（净值/回撤/夏普/超额 → `blocked`，无 `value` 字段）；② **回合级仍输出但标"含 N 笔可疑"**；③ **任何降级必须可见**（`degraded[]`+`data_as_of`+人话）。**导出正确性优先于可用性：可以不给导出，不能给错的导出。** 图例：`可用`/`部分(x)`=输出但带标注/`阻断(y)`=只给原因。

| 依赖故障 | 认证 | 导入 | 查询 | 净值 | 指标 | 归因 | 报告 | 导出 | 对话 | 管理 |
|---|---|---|---|---|---|---|---|---|---|---|
| **行情源故障**（缓存 ≤T-1） | 可用 | 可用 | 可用 | 部分(stale T-1) | 部分(浮盈/基准标注) | 部分(仅费用因子+残差) | 部分(脚注 data_as_of) | 部分(不导 stale 值；缺失段不带资产字段) | 部分(只答缓存内) | 可用 |
| **LLM 故障/超限** | 可用 | 可用 | 可用 | 可用 | 可用 | 可用 | 可用 | 可用 | 部分(模板化；金额正确) | 可用 |
| **PG 只读** | 部分(已登录 access 15min 内可用；登录/refresh/注册阻断) | 阻断(需写) | 可用(读) | 部分(读已有+data_as_of；新计算不产生) | 部分(同上) | 部分(同上) | 阻断(需写产物记录；已生成可下载) | 阻断(需写 job/产物) | 部分(只读取数；会话写阻断) | 阻断(运维需写) |
| **队列停摆** | 可用 | 阻断(503 QUEUE_BACKPRESSURE，拒收不排队) | 可用 | 部分(上次结果+stale) | 部分(上次结果+stale) | 部分(上次结果+stale) | 阻断 | 阻断 | 可用(模板+缓存取数) | 部分(积压可见；备份/重算不可) |
| **备份盘/对象存储故障** | 可用 | 可用 | 可用 | 可用 | 可用 | 可用 | 可用 | 可用(本地卷) | 可用 | 部分(备份失败告警；SLO-5 违约) |
| **外部限流**（行情/LLM 429） | 可用 | 可用 | 可用 | 部分(stale+说明) | 部分(降频+标注) | 部分(仅可用因子) | 部分(延后或不生成数字) | 部分(标注 data_as_of) | 部分(LLM 走模板) | 可用 |
| **`data` 卷不可写/满** | 可用 | 阻断(原件无法归档→不可重放，拒绝 commit) | 可用 | 部分(读已有) | 部分(读已有) | 部分(读已有) | 阻断(产物无处落盘) | 阻断 | 可用 | 部分(告警) |
| **对账断链**（FR-L6） | 可用 | 可用 | 可用 | 阻断(断点→下一自洽锚点 NAV_UNRELIABLE) | 部分(资产类阻断；行为类部分) | 部分(占比可出；金额标可疑) | 阻断(仅"数据质量说明单") | 部分(可导原始流水；资产类字段不导) | 部分(资产类答"数据可疑暂不输出") | 可用 |
**一致性自查**：① FR-L6 资产类硬阻断 → 对账断链行净值/月报为 `阻断` 且无 `value`；② 回合级标可疑 → 指标/归因/对话用 `部分(...可疑)`；③ 导出正确性优先 → 行情缺失与断链行导出为 `部分/阻断`，**不填充 stale 数值**；④ PG 只读时 access token 仍可用（JWT 无状态）但 refresh 需写——否则 15min 后全员掉线且无法重登，因此认证列只能是 `部分`。

## 8. 超时 / 重试 / 熔断 / 限流预算
| 依赖 | 连接/总超时 | 重试 | 退避 | 熔断 | 并发隔离 | 限流 | 幂等前提 |
|---|---|---|---|---|---|---|---|
| 行情源 | 3s / 10s | 2 次（超时/5xx/429） | 1s/4s ±30% | 60s 内失败 ≥5 且失败率 >50%（样本 ≥10）→ OPEN 30s，逐次翻倍至 300s | 每副本并发 8、独立线程池 | 令牌桶 5 rps（⚠ 源上限 60/min） | GET 幂等；批量"全市场某日"降请求数 |
| LLM | 5s / 30s（首 token 8s） | **0 次**（非幂等，重试重复计费） | — | 同上 → OPEN 60s | 每副本并发 4 | 用户级 token 配额+全局并发 | 前端重发，服务端不重试 |
| 备份目标 | 10s / 60s | 3 次跨 24h | 60s/300s/900s | 不熔断（低频） | 单线程 | — | 对象 key 含时间戳，覆盖安全 |
| 入站读 | 5s（`statement_timeout`） | 客户端 2 次，0.5s/1s | — | — | 连接池 10/副本 | 用户+Token 令牌桶（PG 原子计数） | GET 幂等 |
| 入站写 | 15s | **不自动重试**（须 `Idempotency-Key`） | — | — | 同上+长任务 ≤1/用户 | 同上 | 必须幂等键 |
| SSE/对话 | 首字节 8s、单事件 30s | 断线重连（`Last-Event-ID`） | — | — | 同上 | 同上 | 消息幂等键 |

**重试放大算式**：`R = Π(1 + r_i)`。错误做法：nginx 1 × api 3 × job 3 = `(2)(4)(4) = 32×`。本文约束**只允许一层重试**：出站行情 2 次（3×）、job 级 5 次但长退避（§5.2）、入站 POST 0 次（靠幂等键让客户端重试）→ 业务路径最大放大 **3×**。

| 级联防护 | 做法 |
|---|---|
| bulkhead | 行情/LLM/DB 各自独立并发池；LLM 慢不占 api 的 DB 连接 |
| 队列分池 | `WORKER_CLASS=critical`（import/rebuild/report）与 `bulk`（export/backfill）；bulk 仅在 critical 空闲时消费，防报告风暴饿死导入 |
| 熔断状态 | 每副本内存态即可（探测成本低、无一致性要求；代价是 N=3 份探测，可接受）；OPEN 期直接走缓存不发请求 |
| 抖动 | 所有退避 ±30%，防 100 租户齐步重试打爆 PG/行情源 |

## 9. 容量与压测
### 9.1 存储算式（100 用户 / 500 万行）
| 对象 | 行数算式 | 单行估算 | 小计 |
|---|---|---|---|
| `ledger.trade` | 100 × 5 万 = **5,000,000** | heap 200B + 索引 160B = 360B | **1.8GB** |
| 行情缓存（跨租户去重） | 2,000 标的 × 2,440 交易日 = **4.88M** | 120B + 80B 索引 | **1.0GB** |
| `round`/`round_fill`/`nav_day`/`metric_result` | 0.63M / 1.3M / 244k / 324k | 300B / 80B / 150B / 300B | 0.55GB |
| `import_batch`+隔离区 / `job`+`audit`+`session`+`idempotency`（1 年） | 4.3 万+0.05M / ~1M | 300B / 400B | 0.45GB |
| **合计 → 设计** | 3.9GB × 1.5（膨胀/新索引/临时） | | **≈5.9GB → 8GB** |
| 磁盘 | pgdata 50 + data 30 + logs 10 = 90GB × 2 余量 | | **采购 200GB** |
> 500 万行在 PG 里是**小库**：真正风险不是空间，而是月末 100 租户同时触发一次没走索引的聚合（DR-05）。

### 9.2 连接 / IOPS / 内存 / CPU 算式
| 项 | 算式 | 结论 |
|---|---|---|
| QPS | 100 用户 × 日均 40 请求 × 80% 集中在 3h → **0.30 rps**；峰值 ×10 → ~3 rps | 压测目标 **50 rps**（5× 余量） |
| DB 查询 / IOPS | 50 rps × 5 查询/请求 = 250 qps；250 × 30% 未命中 × 1.2 页 ≈ 90，聚合峰值 ×5 | 稳态 ~150、峰值 ~600 → 3000 IOPS 盘 **5× 余量**，**瓶颈不是 IOPS** |
| 写 / 内存 | 导入 5,000 行/批；月末 500k 行/3 天 ≈ 2 行/s；shared_buffers 2 + 连接 0.8 + worker 1.5 + api 1.2 + OS 1 ≈ 6.5GB | 无压力；8GB 余 1.5GB ⚠ |
| CPU | 全量重算 60s × 100 租户 = 6,000 CPU·s，月末 1h → 1.7 核 | 需 2~3 worker 并行 |

### 9.3 队列吞吐与 worker 数（M/M/c 近似）
```
月末 1h：λ = 100/3600 = 0.028 job/s；混合 1/μ ≈ 31s（导入20/重算60/报告15/导出30）
M=3 → ρ = 0.028/(3×0.032) = 0.29 → Wq ≈ 15s          ✅ p95 ≤60s
M=2 → ρ = 0.44 → Wq ≈ 40s                            ⚠ 仅够冗余
极端 100 任务挤在 10min：λ = 0.167 → M=3 时 ρ = 1.7 > 1  ❌
  → 月末 20:00–22:00 定时 --scale worker=8（ρ=0.65，Wq≈45s）
```
> 基线 **M=3**：满足 NFR-13"≥1"且能扛 1 死/滚动；月末峰值靠 `--scale`，不为 2 小时峰值常年养 8 个 worker（成本 vs SLO-4 的取舍，写在明处）。

### 9.4 压测场景与判据
| # | 场景 | 负载 | 通过判据 | 失败动作 |
|---|---|---|---|---|
| PT-1 | 看板并发读 | 50 VU × 10min | 读 p95 ≤300ms、5xx <0.1%、连接占用 <70% | 查慢 SQL/索引 |
| PT-2 | 批量指标 | 10 并发 × 30 指标 × 12 月 | p95 ≤500ms、无 `statement_timeout` | 加物化/缓存 |
| PT-3 | 导入 5,000 行 | 20 并发 commit | job ≤40s ⚠、无锁等待 >1s、重复导入 inserted=0 | 调事务粒度 |
| PT-4 | 全量重算 | 5 万行 × 5 并发 | 单租户 ≤60s（NFR-10）、diff=0、无 OOM | 分批窗口化 |
| PT-5 | 报告生成 | 50 并发 | p95 ≤45s、sha256 稳定、无孤儿文件 | 限流/排队 |
| PT-6 | 发布叠加压测 | PT-1 + 滚动升级 api | **5xx <0.1%**（NFR-13）、无 502 尖刺 | 修 SIGTERM/nginx resolve |
| PT-7 | Soak 72h | 稳态+每小时导入 | RSS 斜率≈0、无连接泄漏、备份/WAL 正常 | 定位泄漏版本 |

### 9.5 扩缩容决策
| 信号 | 阈值 | 动作 | 回退 |
|---|---|---|---|
| api CPU / 读 p95 | >60% 10min / >200ms 10min | `--scale api=6`（先查慢 SQL） | <40% 30min / p95 <150ms |
| 池等待 | p95 >50ms | 先修慢查询；仍高按 §4.3 重算连接后扩 | 等待 <10ms |
| 队列最老 pending / worker CPU | >5min 10min / >70% 15min | 扩 worker（月末定时 8） | <1min / <40% 30min |
| 库体积 / DB CPU / 慢查询 p95 | >60% 卷 / >70% 15min / >500ms（API 类） | 扩卷 / 先查 `pg_stat_statements` 再加规格 / 索引·改写·物化，**不扩副本**（扩了也慢） | 回基线 |

## 10. 可观测性与告警
### 10.1 日志字段与脱敏
| 字段 | 必填 | 示例 | 说明 |
|---|---|---|---|
| `ts`/`level`/`event` | ✅ | `2026-10-19T12:00:00Z`/`info`/`job.succeeded` | `event` 稳定 snake_case 便于计数 |
| `request_id`/`trace_id` | HTTP | ULID / 16 hex | 贯穿 nginx→api→worker（header 透传） |
| `user_id` / `job_id`/`attempt` | 业务/任务 | UUID / UUID,2 | 多租户定位（**不写邮箱**）；与 `platform.job` 对齐 |
| `route`/`method`/`status`/`duration_ms` | HTTP | `/api/v1/metrics`/GET/200/87 | 路由用**模板**（`/rounds/{id}`），否则基数爆炸 |
| `db_ms`/`db_queries`/`pool_wait_ms` | HTTP/任务 | 42/5/3 | 区分"应用慢"与"DB 慢" |
| `degraded[]`/`data_as_of` | 降级 | `[{code:"MARKET_STALE"}]`/`2026-10-18` | SLO-7 的证据链 |
| `error_code`/`outcome` | 失败 | `DB_POOL_EXHAUSTED`/`failed` | 仅枚举，不写自由文本 |

| 敏感项 | 处理 | 例外 |
|---|---|---|
| 金额 | 只写 `amount_present=true` 或量级档，**不写原值** | 用户导出（明确动作，非日志） |
| 股东账号 / 邮箱 / 密码 / PAT | 末 4 位 `****1469`；`a***@example.com`；密码·PAT·refresh **永不记录**（PAT 只记前缀 8 位+hash 前 8 位） | 审计用 `actor_id`（UUID） |
| SQL 参数 / 出站 LLM | `pg_stat_statements` 归一化，应用不打印参数；LLM 只记 `payload_hash`+token 数 | 诊断模式 15min 自动关并审计 |

### 10.2 指标与告警（阈值 + 动作）
| 指标 | 阈值 | 级别 | 动作 |
|---|---|---|---|
| 5xx 比率 / 延迟 p95 | >0.1%(1h)/>1%(5min)；读 >300ms/写 >500ms | P0/P1 | 看 `request_id`→回滚或修 §7；慢 SQL（§4.4） |
| `db_pool_wait` / 连接使用率 | >50ms / >80% 5min | P1 | 修慢查询/扩副本；查泄漏，评估 pgbouncer |
| 长事务 / 锁等待 p99 | >60s / >1s | P1 | SOP-4 阻塞树 |
| `wal_archive_lag` / 磁盘水位 | >60s(p99)/>300s(max)；<15%/<5% | P0/P1 | SLO-5、SOP-5 修归档；SOP-4 磁盘 |
| `job_oldest_pending` / 失败率 / dead_letter | >300s/>1800s；>2%(15min)；>0 或 1h ≥5 | P1/P0 | 扩 worker/背压；查失败码；隔离毒任务（§5.3） |
| `worker_healthy` / 容器重启 | 低于期望数；1h ≥3 | P0/P1 | SOP-3；查 OOM/崩溃循环 |
| 熔断 OPEN / 行情错误 / LLM 错误·成本 | >10min / >10% / >10%·超预算 80% | P1/P2 | 确认 §7 降级+告警文案；切模板/降配额 |
| 登录失败 / `authz_denials{rls}` | >10/min/IP 或 ≥5 账号；**>0** | P2/`P0` | 限流+SOP-10；越权事件（SLO-8） |
| 降级响应 / 备份 / 证书 / 审计 | 与矩阵不符；>26h 或连续 2 次 false；<21·<7 天；>0 | P0/P1 | SLO-7 缺陷；SOP-7；SOP-5；FM-23（业务应已回滚） |

### 10.3 健康端点语义（FR-L1）
| 端点 | 语义 | 依赖 | 返回 | 用途 |
|---|---|---|---|---|
| `/healthz` | 进程存活 | 无 | 200 `{"status":"ok"}`，**永不 503** | Docker healthcheck |
| `/readyz` | 可接流量 | DB 可连、`schema_version` 匹配、`draining=false` | 200 `{db:"ok",schema_version}`；DB 异常 503 `DB_UNAVAILABLE`；draining 503 | nginx 摘除、发布门禁 |
| `/metrics` | Prometheus 文本 | 无 | 200；**不暴露公网**（内网/认证） | 采集 |
| `/version` | 构建版本 | 无 | `{version,git_sha,built_at,schema_version}` | 发布校验、与 `openapi.json` 一致（FR-A2） |
> `/healthz` 不查 DB 是刻意的：DB 挂时若它也 503，容器会被反复重启，把"DB 故障"升级成"DB+api 重启风暴"。

### 10.4 Trace 采样
默认请求 20% 头部采样、保留 7 天（100 用户规模足够，采样率可热更新）；错误与 >1s 慢请求 **100%**（`traceparent` sampled 位+服务端强制）、保留 30 天；管理端点与发布窗口 100%；出站行情/LLM 跟随父 span（脱敏 URL、状态、耗时、重试次数）。采集器可选：默认 trace 写 `logs/trace` 并聚合错误，接外部 APM 才上 OTel Collector——不为一个后端服务加第二个常驻组件。

### 10.5 越权与异常登录检测
| 规则 | 判定 | 级别 | 动作 |
|---|---|---|---|
| R1 跨租户 ID 探测 | 同一 user 5min 内他人资源 404 ≥20 次（按类型） | P1 | 限流+安全审计 |
| R2 RLS 拦截 | `authz_denials{layer="rls"}` >0 | **P0** | 第一道防线有漏：冻结发布、SOP-10 |
| R3 refresh 复用 | 同 token family 旧 refresh 再用 | P1 | 撤销全家族+强制重登 |
| R4 登录爆破 | 同邮箱/IP 5min 失败 ≥10 或 ≥5 账号 | P2 | `429`+指数封禁（15min 起） |
| R5 admin 越界 | admin 调交易端点 403 ≥1 | P1 | 审计+查前端/脚本 |
| R6/R7 导出与进程异常 | 单用户 24h 导出 >5 次或 >500MB；同账号 1h ≥3 个 /24 段或 UA 突变 | P2 | 提醒+审计（外带风险）；二次验证/通知 |
| R8/R9 PAT 与审计 | 新 IP 首用且 1h 写 >100 次；`audit_log` 时间空洞 >5min | P2/P0 | 审计+可选自动撤销；视同审计失效（合规） |

## 11. Runbook（SOP-1..SOP-10）
> 无人值守（§13），第 1 步必须是"自动化能做的"，第 2 步才人工；命令全在 `ops/`，禁止临场拼长命令。

| SOP | 症状 | 影响 | 三步处置 | 验证恢复 | 升级条件 |
|---|---|---|---|---|---|
| **SOP-1** DB 不可用 | `/readyz db:down`、连接错误、5xx 100% | 全站不可用 | ① 看容器状态与 `docker logs`，重启一次；② 查磁盘/WAL（SOP-4）、`pg_controldata` 是否损坏；③ 损坏→SOP-7 | `/readyz` 200；`SELECT 1`；5xx 回落；抽样读一租户 | >15min 或数据损坏→通知+SOP-7 |
| **SOP-2** 迁移卡住 | migrate >5min、DDL 等待 | 新版本不上线（旧版服务） | ① 查阻塞树是否被长事务挡；② 等 ≤60s 或终止阻塞源（非 migrate）；③ `lock_timeout` 触发→中止发布 | 旧版 `/readyz` 正常；migration head 未前进；无残留锁 | 已部分执行且回滚失败→备份+人工修复脚本 |
| **SOP-3** 队列积压 | 最老 pending >10min | 导入/报告延迟（SLO-4） | ① `--scale worker=8`；② 查 dead_letter 与失败码，隔离毒任务；③ 评估背压（§5.5） | pending 年龄 <300s 且下降；成功率回升 | 积压 >30min 或 worker 起不来→公告+降级批量任务 |
| **SOP-4** 磁盘满 | 水位 <5%、`ENOSPC` | 写失败、WAL 无法归档 | ① `ops/cleanup.sh` 删过期导出/日志/**已归档** WAL；② 扩卷；③ 验证归档补齐 | >15%；`wal_archive_lag` 回落；写测试成功 | 无法扩容/清理不足→只读模式+公告 |
| **SOP-5** 证书过期 | `cert_expiry_days <7` | 全员不可用 | ① `certbot renew --force-renewal`；② `nginx -t && nginx -s reload`；③ 验链/OCSP | `curl -I` 200；续期 >30 天 | 续期失败（DNS/端口）→手动上传+修 ACME |
| **SOP-6** 发布回滚 | 5xx >0.5% 3min / p95 >2× / job 失败 >2% | 部分或全部受影响 | ① 停发布保留现场；② `ops/rollback.sh` 回滚镜像 tag；③ SPA 软链回切 | 5xx <0.1%；p95 回基线；`/version` 旧版；队列消化 | 回滚后仍异常→数据/迁移问题→SOP-2/§3.4 |
| **SOP-7** PITR 恢复 | 损坏/误删/误迁移（FM-06） | 数据回滚目标时刻 | ① 冻结写入+卷快照；② 空实例恢复 basebackup+`recovery_target_time`；③ 5 项校验+RLS 冒烟后切流 | 5 项全过；RTO ≤30min；RPO ≤5min；越权 404 | 校验失败或 RTO 超 30min→上一份备份+通知 |
| **SOP-8** 备份校验失败 | `backup_verify_ok=false`（SLO-5） | 恢复点可靠性下降（暂不影响服务） | ① 立即重做并校验；② 失败回退上一份已验证备份并标坏件；③ 查对象存储凭据/配额/网络 | 一次校验通过；`backup_age <26h` | 连续 2 次失败→P0 冻结发布 |
| **SOP-9** OOM/崩溃循环 | `OOMKilled`、1h 重启 ≥3 | 副本或全站抖动 | ① `docker stats` 查 RSS/请求体；② 限并发/请求体+滚动重启；③ 定位泄漏版本回滚 | 1h 无重启；RSS 平稳；p95 正常 | 新版本泄漏→回滚开 issue；旧版也泄漏→限流+定时重启 |
| **SOP-10** 越权/安全告警 | RLS 拒绝 >0、R2/R5、异常登录 | 数据泄漏风险（P0） | ① 冻结发布+关相关端点；② 取证（审计+日志+request_id）；③ 轮换 `JWT_SECRET`、撤销全部 refresh/PAT | 越权用例全绿；审计连续；无新拒绝 | 确认泄漏→合规流程+通知+72h 复盘 |

## 12. 故障演练与验收（注入与判据归本文，测试实现归 Testing）
| ID | 注入方式 | 判定标准（全满足才通过） | 阶段 |
|---|---|---|---|
| DR-01 | `docker kill -s KILL api`（逐个 3 副本） | 5xx <0.1%（1min 采样）；nginx 自动转投；30s 内 3/3 ready | M0 |
| DR-02 | `kill -9` PG postmaster | 崩溃恢复 ≤30s ⚠；已提交行数不丢（RPO=0）；无页撕裂；`pg_controldata` 正常 | M0 |
| DR-03 | `iptables` 断行情/LLM 出站 | §7 对应格逐格断言；含 `degraded/data_as_of`；恢复后补齐且 diff=0 | M1 |
| DR-04 | 写满 `pgdata`/`data` 卷（稀疏文件） | §7 对应格行为；无静默丢数据；清理自愈；文案三段式 | M1 |
| DR-05 | 注入慢 SQL+表锁（`pg_sleep`/`LOCK TABLE`） | `statement_timeout` 生效；池不耗尽；阻塞树可定位；5xx <0.1% | M1 |
| DR-06 | 暂停 worker+注入 1000 job | 背压（10min/2000）行为正确；扩 worker 后 ≤10min 清空；无重复执行 | M1 |
| DR-07 | `date -s` 回拨 5min / NTP 跳变 | 任务不重复不丢失（幂等+租约）；JWT 误判 0；审计时序可解释 | M1 |
| DR-08 | 发布中途 kill（migrate 后、api 半量） | 旧版本可服务；expand 无副作用；回滚后 5xx <0.1%；无 schema 失配 | M2 |
| DR-09 | 解挂 `data` 卷 / 改名目录 | §7 对应格（报告/导出阻断、读可用）；重挂后自愈 | M1 |
| DR-10 | 过期证书+错误 nginx 配置 | `nginx -t` 拦截（非 0）；SOP-5/6 可执行；恢复后 200 | M0 |
| DR-11 | 越权脚本（ID 猜测/批量混入/admin 调用） | 越权成功 0；404 语义；RLS 拒绝有日志；证据留存 | 每版本 |
| DR-12 | 时钟+磁盘+慢 SQL 叠加（混沌） | 至少一项可服务；降级可见；RTO 不退化 | M3 |

| 门禁 | 要求 | 里程碑 |
|---|---|---|
| **G-R1** | DR-01/02/10：kill api 5xx <0.1%、PG 崩溃恢复 ≤30s、证书与 nginx 配置门禁生效 | M0 |
| **G-R2** | DR-07 恢复演练（§4.7 五步）实测 RTO ≤30min、RPO ≤5min、5 项校验全过；每季度复跑 | M0 起每季 |
| **G-R3** | §7 矩阵 8 行 × 10 列 = **80 格逐格断言**；SLO-4/5/7 度量埋点上线 | M1 |
| **G-R4** | DR-06+毒任务+幂等：背压/回收/dead-letter/单飞/重复提交有自动化证据 | M1 |
| **G-R5** | PT-1~PT-6 达标+DR-08 可回滚+expand-contract 演练通过 | M2/M3 |
> 分工：本文定义 `DR-xx` 的**注入方式与判定标准**（可执行验收语义）；pytest 标记、fixture、CI 编排、浏览器 e2e 归 [Testing.md](Testing.md)（v2 待写）。两边靠 `DR-xx` 编号互引，避免两份演练清单不一致。

## 13. 明确不做
| 不做 | 理由（当前形态下） | 未来触发条件 |
|---|---|---|
| **Kubernetes** | 3 api+3 worker+1 PG 单机栈，Compose 已覆盖 90% 编排收益；K8s 引入控制平面、ingress、存储类三个新故障域 | 实例 >20、多主机 >2、团队 >3 人需要声明式发布 |
| **多活 / 跨地域** | 100 用户、单地域、无实时交易；多活需全局一致性/双写/路由层，成本 ≫ 收益 | 付费 SLA、跨区用户 >20%、监管数据驻留要求 |
| **PG 自动故障转移（首版）** | 没有第二台主机；同主机备机=同一故障域，自动 promote 只是自我安慰 | M2 上第二台主机+异步流复制后，先脚本化 promote（不自动） |
| **跨可用区 / 多 AZ** | 需云 LB+托管 PG/对象存储，偏离"Compose 一键拉起"（PRD §4.2 硬约束） | 用户 >100 或付费；先多机（M2.5）再评估多 AZ |
| **7×24 值守** | 个人投资者用量集中在盘后；100 用户规模养不起也不需要轮班 on-call | 付费合同/机构用户，或月 P0 >2 次 |
| **对象存储 / pgbouncer（首版）** | 本地卷+快照够用（少一个依赖少一类故障）；48/100 连接无收益，且 pgbouncer 会破坏会话级 advisory lock | `data` 卷 >30GB 或多机共享（M3）；连接使用率 >80%（§4.3） |
> **诚实边界**：只剩四类残留风险——① **单主机/单 AZ 故障**仍有 30min 级中断（靠重建 SOP 与季度演练兜底，不假装无缝）；② **主机故障时 RPO 实测可能 >120s**（云盘 flush 语义+归档上传抖动，需 DR-02 实测替换）；③ **contract 迁移后不可回滚**（靠"延迟一个版本"+备份）；④ **越权只能靠测试与 RLS 双防线**，逻辑层漏洞（批量回显）没有自动化的额外保险。其余故障都有"检测→降级→恢复→验证"闭环；"第五层保险"不写进本文。
