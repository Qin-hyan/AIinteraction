# Week 07 State — 按需获取一张环境图像

**分支**: `feature/week07-vision-capture`
**基线**: `origin/main` = `5aebea1`（Merge PR #7，Week 06 已合并）
**日期**: 2026-10-19
**阶段**: 🟢 **代码 + 构建 + 真实硬件验收完成**；其中「真实取帧失败」状态未构造，如实保留为待验证项

> ✅ 2026-10-17 已获烧录授权并完成首轮实机验证（详见 TEST_LOG.md「E. 端到端采集验证」）。
> 已实机确认：新固件运行、按需采集受理、**capturing 中间态**、真实取帧 800x600 JPEG、
> 端侧 esp_jpeg 评估（200x150，约 780ms）、四态终态（暗环境正确判 `unusable/too_dark`）、
> 请求-图像-采集时间关联、串口连接期间状态稳定保持 105 秒+。
> ✅ 2026-10-19 完成实机验收（TEST_LOG「J」）：最少保留（limit=3，第 4 张起释放）、
> 显式清空后照片接口 404、定时抓拍启用返回 400 `course_policy_disabled`、
> 正常光照 `ok`（luma 117.6 / contrast 62.0 / detail 15.5）与遮挡 `too_dark` 样例；
> 且遮挡后上一张 `ok` 图像仍保留原 `request_id` 与采集时间，未被改标为本次成功。
> ✅ 2026-10-18 「脱机重启」根因已定位更正：**非固件缺陷**，实为主机默认方式打开 COM10
> 时 DTR/RTS 触发原生 USB 硬件复位（DECISIONS.md D009 / TEST_LOG.md「H」）。
> 设备无人值守运行稳定；监视须用 `tools/serial_monitor_safe.py`；课堂演示期间不开串口。
> 覃总决策（2026-10-18）：「最少保留」与「只在触发后取图」均**按课程要求**实现。
> ✅ 2026-10-19 已实现最少保留（默认上限 3，可配 1–9，超限释放 + 显式清除），并完成烧录后实机验收。

---

## 1. 课程官方要求（2026-10-17 核实自课程主页）

- **学习目标**：用户请求查看环境时，系统如何说明采集是否成功，采到的是哪一时刻的画面？
- **前 2 学时**：在既有任务与存储服务中增加按需图像采集；讲解授权、图像输入与有效性；复用摄像头驱动或实际图像接口；**关联请求、图像记录和采集时间**。
- **后 2 学时**：实现**采集中、采集成功、镜头遮挡/画面不可用、采集失败**四种反馈；**只在请求或教学事件触发后取图，设置最少保留**。
- **当堂验证**：**上次成功图像不得被标为本次采集成功**；**采集失败保持明确状态**。
- **交付物**：按需环境快照功能；带采集时间的输入样例；采集权限与失败记录；明确个人项目中图像采集的触发条件和停止条件。
- **范围控制**：共同保底视觉任务为「**画面可用性/遮挡识别**」，场景粗分类为拓展；**不得**推导精确位置或人身风险；**未经校准的模型分数不得写成现实正确率**。

---

## 2. 需求差距分析（接管时审计结论）

| 课程要求 | 接管前状态 | 缺口 |
|----------|-----------|------|
| 按需图像采集（请求触发） | ✅ `POST /api/camera/capture` 已存在 | 已具备 |
| 关联请求、图像记录、采集时间 | ✅ `request_id` + `captured_ms` 已由 gallery 返回 | 已具备 |
| 只在请求/教学事件触发后取图 | ✅ `auto_cap.enabled` 默认 `false` | 已具备 |
| 四种采集反馈 | ❌ 仅有 Week 02 传感器状态机，图像只是副作用 | **主要缺口** |
| 画面可用性/遮挡识别（保底任务） | ❌ **完全无实现**（`valid` 仅表示槽位已填充） | **核心缺口** |
| 最少保留策略 | ⚠️ 9 槽位环形覆盖，无明示策略 | 部分缺口 |
| PC 端视觉模块 | ❌ 无 | 已补齐 |

---

## 3. 已完成（有证据）

### 3.1 PC 端画面可用性/遮挡识别基线（提交 `b682cee`）

- `service/image_quality.py`：`assess_image(jpeg_bytes)` 单一入口
  - 输出 `usable / status / score / metrics / bytes_len`
  - 指标：`mean_luma`（灰度均值）、`contrast`（标准差）、`detail`（前向差分梯度能量）
  - 判定顺序：`invalid → too_dark → too_bright → low_contrast → low_detail → ok`
  - 阈值具名常量：`LUMA_DARK=25` / `LUMA_BRIGHT=230` / `CONTRAST_LOW=8` / `DETAIL_LOW=8`
- `service/test_image_quality.py`：10 项单元测试，**10 passed**
- `requirements-vision.txt`：Pillow 10.2.0 / numpy 2.4.4

### 3.2 ESP32 端画面可用性评估（提交 `1abaa3d`）

- `main/image_quality.h/.c`：`image_quality_assess()`
  - `esp_jpeg` 解码（RGB888 + 1/4 缩放），缓冲区优先 PSRAM
  - **阈值与判定顺序与 PC 端逐一对齐**，支持两端交叉验证
  - 所有错误路径（OOM / 解码失败 / 尺寸不符）均释放缓冲并返回 `INVALID`，不崩溃
- `main/http_server.h`：`capture_slot_t` 增加 `img_status/usable/mean_luma/contrast/detail`
- `main/main.c`：`save_capture_slot()` 评估本次 JPEG 并写入槽位

### 3.3 四态采集反馈（提交 `6d6853a`）

**状态语义分离**（解决"采集失败"与"图像解码失败"语义混用）：

| 采集任务状态 | 触发条件 | 含义 |
|---|---|---|
| `capturing` | 采集请求被受理 | 采集中 |
| `ok` | 取帧成功 且 `img_status == OK` | 采集成功，画面可用 |
| `unusable` | 取帧成功 且 `img_status != OK` | 采集成功，画面不可用（遮挡/暗/亮/模糊） |
| `failed` | 取帧失败 / 摄像头不可用 | 采集失败 |

实现位置：
- `main/main.c:506` 受理 → `capturing`
- `main/main.c:263-264` 依据 `img_status` → `ok` / `unusable`
- `main/main.c:531`（取帧失败）、`548`（`cam_handle==NULL`）→ `failed`

### 3.4 API 与界面

- `POST /api/camera/capture`：返回 `cap_task_status`
- `GET /api/camera/gallery`：每条目增加 `img_status / usable / mean_luma / contrast / detail`
- **新增** `GET /api/camera/status`：返回四态 + `last_request_id` + `updated_ms` + `last_img_status` + 质量指标 + `slots/total`
- 内嵌 Web UI：四态标签 + 「画面不可用具体原因」显示区 + 画廊显示 `img_status` 与采集时间

### 3.5 最少保留策略（2026-10-19 实现）

**课程依据**：后 2 学时「只在请求或教学事件触发后取图，设置最少保留」。覃总已决策按课程要求实现图像留存最小化。

- `collect_task_t.cap_retain_limit`：保留上限，默认 `CAPTURE_RETAIN_DEFAULT = 3`，有效范围 `1..CAPTURE_SLOTS_MAX(9)`
- `capture_retention_apply()`（`main/main.c`）：抓拍保存后，从环形缓冲最旧端释放超出上限的 JPEG 缓冲；累计释放数记入 `cap_evicted_count`
- 新增 API：
  - `GET /api/camera/retention` → `policy/limit/max_slots/retained/total/evicted`
  - `POST /api/camera/retention` `{"limit":N}` → 仅接受 1..9，越界返回 400；立即应用
  - `POST /api/camera/clear` → 显式释放全部已存图像，返回 `cleared/retained`
- `GET /api/camera/gallery` 与 `/api/camera/status` 增加 `retain_limit/retained/evicted`
- 质量指标改为任务级保存（`last_cap_mean_luma/contrast/detail`），清除图像后仍保留最近一次评估结果
- Web UI：画廊显示「上限/当前保留/累计释放」，新增「🗑 清空图像」按钮
- `httpd` 路由上限 `max_uri_handlers` 16 → 24（原 14 个基础路由 + 3 个 help 路由已接近上限，新增 3 个路由必须扩容）
- 并发边界（如实标注）：图像释放与 `/api/camera/photo` 读取沿用现有无锁槽位访问模式，未新增互斥；未做并发压力测试，设备端行为亦未实机验证

---

## 4. 验证证据

| 验证层级 | 项目 | 结果 |
|---|---|---|
| 单元测试 | `pytest service/test_image_quality.py -v` | ✅ **10 passed** |
| 单元测试 | `pytest service/ -q` 全量 | ✅ **260 passed**（基线 250 + 新增 10） |
| 固件构建 | `idf.py build` | ✅ 成功，无新增警告 |
| 固件构建 | `idf.py build`（2026-10-19，含采集策略 T28/T29） | ✅ `Project build complete`，无 warning/error |
| 静态检查 | 路由注册数 / 保留环形释放逻辑审阅 | ✅ 通过（`max_uri_handlers` 已扩容至 24） |
| **真实硬件** | 烧录 / 按需采集 / 四态反馈 / 暗环境 unusable | ✅ **2026-10-17 已验证**（TEST_LOG「E」） |
| **真实硬件** | 保留上限覆盖 / 显式清除 / 自动抓拍拒绝 | ✅ **2026-10-19 已验证**（limit=3、cleared=3、自动抓拍 400） |
| **真实硬件** | 正常光照 ok 态 / 遮挡态 / 当堂验证项 | ✅ **2026-10-19 已验证**（`ok` / `too_dark`；上一张 ok 未被改标） |
| **真实硬件** | 真实取帧失败（`failed`）状态 | ⏳ 未构造（无故障注入手段） |

---

## 5. 未完成与遗留

- ✅ **正常光照与遮挡实机样例已产出**（2026-10-19）：正常场景 `ok`（luma 117.64 / contrast 62.01 / detail 15.54），遮挡 `unusable/too_dark`（luma 5.31 / detail 0.81）
- ✅ **当堂验证①已闭环**：遮挡采集后，上一张 `ok` 图像仍保留 `req-201358-0006` 与 `captured_ms=201997`，照片接口 200 且字节数不变；新采集单独标记 `req-291085-0007 / too_dark`
- ⏳ **当堂验证②部分**：「采集失败保持明确状态」中的 `unusable` 已实机验证；真实取帧失败 `failed` 未构造（无故障注入手段）
- ℹ️ **失败记录边界（原文核对）**：官网未要求持久化或独立接口；现有为 `cap_task_status=failed` + `last_img_status=invalid` + 串口日志。**真实 `failed` 样例仍待补验，不得以 `unusable` 代替**
- ✅ ~~脱机自动重启~~ → **已定位更正**（2026-10-18，D009）：非固件缺陷，为主机默认打开 COM 口的硬件复位；监视用 `tools/serial_monitor_safe.py`，演示期间不开串口
- ✅ **"最少保留"已实现并实机验证**（2026-10-19）：保留上限默认 3（可配 1–9）、超限自动释放、`POST /api/camera/clear` 显式清除；实测 retained 1→2→3→3，第 4 张后 photo index=3 返回 404
- ⚠️ **阈值仅单场景验证**：固定阈值在 1 个正常场景与 1 个遮挡场景方向正确，但未做多光照分布校准，不得宣称准确率
- ✅ **定时自动抓拍已禁用**（T29）：`POST /api/auto_capture` 启用请求返回 400 `course_policy_disabled`，主循环运行期守卫也不执行自动抓拍
- ℹ️ **POST 受理响应中 `cap_task_status` 为受理瞬时值（idle）**：capturing 由主循环设置且存活 <1s，HTTP 轮询难以捕获，但串口日志证实中间态真实存在
- ℹ️ **PC 端与端侧指标绝对值会有差异**：端侧解码为 1/4 缩放，交叉验证应比对**状态一致性**而非指标绝对值

---

## 6. 需要用户决策的事项

1. ~~实机验证授权~~ → **已授权并执行**（2026-10-17）
2. ~~脱机重启缺陷是否本周修复~~ → **已处理**（2026-10-18）：实验证明非固件缺陷（D009），无需固件修复；监视规范已建立
3. ~~定时自动抓拍（`auto_capture`）是否保留~~ → **覃总决策：按课程要求明确禁用**（2026-10-18，已实现 T29）
4. ~~"最少保留"的具体含义~~ → **覃总决策：按课程要求**（2026-10-18）：实现图像留存最小化——可配置保留上限 + 超限覆盖 + 显式清除（T28）
5. ~~光照/遮挡配合~~ → **已完成**（2026-10-19）：正常光照 `ok`、遮挡 `too_dark`，当堂验证①通过
6. ~~烧录授权~~ → **已授权并完成**（2026-10-19）：烧录 Hash verified，实机验收见 TEST_LOG「J」
7. ~~是否补做「采集失败记录」~~ → **已核对原文，不新增功能**（2026-10-19，D010）：原文未要求持久化、独立接口或账户级鉴权；已补触发/停止条件文档；真实 `failed` 样例待补验

---

## 8. 课程原文核对：采集权限与失败记录（2026-10-19）

- **原文**（Week 07 提交与迁移）：「按需环境快照功能；带采集时间的输入样例；采集权限与失败记录。 明确个人项目中图像采集的触发条件和停止条件。」
- **判定**：原文未规定失败记录的存储格式、独立接口或持久化，也未要求账户级鉴权 → 不新增功能（DECISIONS D010）
- **对应实现**：仅显式 `POST /api/camera/capture` 触发（定时抓拍 400）；gallery 记录 `request_id/captured_ms`；`failed` 与 `unusable` 分离；触发/停止条件见 README「Week 07」
- **缺口**：真实取帧失败样例未构造，PR 审查时须标注为待补验

---

## 7. 2026-10-19 实机验收摘要（详见 TEST_LOG「J」）

| 项目 | 实测证据 |
|---|---|
| 固件身份 | 1,118,400 字节构建产物，烧录 `Hash of data verified`；`/api/camera/retention` 返回 200（旧固件无此端点） |
| 保留 | limit=3；4 次采集后 retained=3、evicted=2；photo 0/1/2=200、3=404 |
| 清除 | cleared=3、retained=0；photo=404；gallery photos=0 |
| 自动抓拍 | HTTP 400 `{"error":"course_policy_disabled"}`；`enabled=false` |
| 正常光照 | `req-201358-0006`：ok，luma 117.64 / contrast 62.01 / detail 15.54，captured_ms=201997，50195 B |
| 遮挡 | `req-291085-0007`：unusable/too_dark，luma 5.31 / contrast 3.65 / detail 0.81，captured_ms=291857 |
| 当堂验证① | `ok` 条目在原 request_id 与采集时间下仍可读取（200，50195 B），未被本次遮挡结果覆盖 |
| 未验证 | 真实取帧失败 `failed`；失败记录持久化 |
