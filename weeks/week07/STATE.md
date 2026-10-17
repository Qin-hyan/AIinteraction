# Week 07 State — 按需获取一张环境图像

**分支**: `feature/week07-vision-capture`
**基线**: `origin/main` = `5aebea1`（Merge PR #7，Week 06 已合并）
**日期**: 2026-10-17
**阶段**: 🟡 **代码实现与编译验证完成**；⏳ **实机烧录验证未完成**

> ⚠️ 本页所有"已完成"均指**代码已实现并通过编译/单元测试**。
> **尚未烧录至 ESP32-S3-EYE，未做真实镜头遮挡、真实采集失败等实机验证。**
> 未取得实机证据前，不得声称课程当堂验证项已通过。

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

---

## 4. 验证证据

| 验证层级 | 项目 | 结果 |
|---|---|---|
| 单元测试 | `pytest service/test_image_quality.py -v` | ✅ **10 passed** |
| 单元测试 | `pytest service/ -q` 全量 | ✅ **260 passed**（基线 250 + 新增 10） |
| 固件构建 | `idf.py build` | ✅ 成功，无新增警告 |
| 链接级 | 固件符号 | ✅ `image_quality_assess@420131c4`、`capture_task_status_name@42011628`、`camera_status_handler@4201166c` |
| 端点注册 | `httpd_register_uri_handler` | ✅ `main/http_server.c:1397` |
| **真实硬件** | 烧录 / 实机四态 / 真实遮挡 | ⛔ **未完成** |

---

## 5. 未完成与遗留

- ⛔ **实机烧录验证未完成**：需烧录固件后验证四态在真实设备上的表现
- ⛔ **真实镜头遮挡样例未提供**：课程要求"带采集时间的输入样例"与"采集失败记录"，需实机产出
- ⚠️ **"最少保留"策略未显式实现**：当前仅依赖 9 槽位环形覆盖，未实现可配置的保留上限与显式清除
- ⚠️ **阈值未经真实场景校准**：两套实现（PC/端侧）阈值均基于合成图像设定，真实光照下需现场微调
- ⚠️ **自动抓拍与四态共享字段**：`auto_capture` 成功时也会更新 `cap_task_status`，但其默认关闭
- ℹ️ **PC 端与端侧指标绝对值会有差异**：端侧解码为 1/4 缩放，交叉验证应比对**状态一致性**而非指标绝对值

---

## 6. 需要用户决策的事项

1. **定时自动抓拍（`auto_capture`）是否保留**：当前默认关闭，符合课程"只在请求或教学事件触发后取图"原则；是否需要在 Week 07 语境下明确禁用或加注隐私说明？
2. **"最少保留"的具体含义确认**：理解为"最小化图像留存（隐私）"还是"至少保留最近 N 张不被覆盖"？两者实现方向相反。
3. **实机验证授权**：烧录固件需占用 COM10 并对设备复位，是否授权执行？
