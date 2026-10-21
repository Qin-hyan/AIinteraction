# Project State — 18 周当前进度

> 每次 Agent 窗口开始时应首先读取此文件了解当前状态。

---

## 当前：Week 08 已收尾 — 视觉反馈闭环 + 中文化面板（2026-10-21 结项）

**分支**: `feature/week08-vision-feedback`（基线 `origin/main` `333a697`；功能/固件提交 `861ae64`，文档收尾提交见本分支最新提交（`git log -1`）；已推送 `origin`，**未合并 `main`**）
**阶段**: ✅ **W8-1 契约 / W8-2 本地 DBCNN 推理 / W8-3 反馈闭环 / W8-4 并入 Dashboard / W8-5 面板中文化均已实现**；W8-4 与 W8-5 均通过用户实机验收；⏳ 阈值校准、多真值样本、权重许可、稳定性等作为后续补验/研究项保留（见下）
**详见**: `weeks/week08/STATE.md` 第 7–10 节 / `TEST_LOG.md`「O」–「R」/ `DECISIONS.md` D011–D013 / `docs/vision-dbcnn.md`、`docs/vision-feedback.md`

**W8-1 已完成（有证据）**:
- `service/vision_result.py::build_vision_result()`：`request_id / label / score / model_version / decision / reject_reason / created_at` 契约；`decision ∈ {ok, low_score, needs_review, unknown}`（`needs_review` 仅由仅分数模型在阈值未配置时产生）
- 拒识语义：`no_result / parse_error / missing_label / unsupported_label / missing_score / invalid_score / image_invalid / score_below_threshold`；W8-2 增加 `threshold_not_configured`
- 标签词汇复用 `image_quality.IMAGE_STATUSES`；低分阈值必须由调用方显式提供（未校准，不设默认）
- `score` 为相对可用性原值，`score_kind=relative_usability`；不得称准确率
- 测试：`python -m pytest service/test_vision_result.py service/test_image_quality.py -q` → **62 passed**（2026-10-19）

**W8-2 已完成（有证据）**:
- `service/vision_dbcnn.py`：本地 DBCNN ONNX + `.onnx.data`，ONNX Runtime 1.22.1 CPU；预处理复用 RGB/224×224/ImageNet mean/std/NCHW float32；返回原始 0~1 分数、`dbcnn_onnx_v1`、权重 SHA-256、推理耗时
- 模型路径 `VISION_DBCNN_MODEL_PATH`（缺失/加载失败明确报错，**不退回规则**）；可选 `VISION_DBCNN_MODEL_VERSION`
- `service/vision_result.py::build_score_vision_result()`：`label=None`、`score_source` 显式；阈值未配置 → `needs_review` / `threshold_not_configured` / `review_required=true`；阈值仍无默认值
- `service/vision_analysis.py`：组合入口 + CLI（不新开端口）；DBCNN 与 Week 07 `rule_diagnostics` 来源分离；规则依赖缺失标记 `available=false`
- 真实输出：`ok.jpg` = `0.7561315298080444`，`occluded.jpg` = `0.25810927152633667`；规则诊断分别为 `ok` / `too_dark`；无阈值时均为 `needs_review`
- 测试：Anaconda 相关单元测试 **124 passed, 1 skipped**；隔离 venv 含真实 ONNX 冒烟 **125 passed**
- 依赖：`requirements-vision.txt` 增加 `onnxruntime==1.22.1`、`opencv-python-headless==5.0.0.93`；模型/外部权重留在 `.workbuddy/`（不提交）

**W8-3 已完成（有证据）**:
- `service/vision_feedback.py`：真实推理 + 仅追加记录（`records.jsonl`/`feedback.jsonl`）+ 设备代理 + 反馈校验；反馈与模型/规则来源分离，图像字节不落盘
- `service/vision_feedback_server.py`：独立本地服务（默认 `127.0.0.1:8090`，仅标准库）；页面分列展示 DBCNN 原始分数/版本、判定状态、Week 07 规则诊断、输入引用与阅读限制；人工反馈（可用/不可用/无法判断 + 原因）；重新采集复用设备 `POST /api/camera/capture`
- 阈值未配置时恒为 `needs_review`（不使用 0.5 默认值），页面显著提示分数未校准；反馈重复 409、非法输入 400、记录不存在 404、保存失败 500
- 真实进程冒烟：`ok.jpg` = `0.7561315298080444`、`occluded.jpg` = `0.25810927152633667`，规则 `ok` / `too_dark`，均为 `needs_review`；中文反馈 UTF-8 回读一致
- 测试：Anaconda 视觉相关 **204 passed, 2 skipped**；隔离 venv 含真实 ONNX **206 passed**
- 未改固件、未改 Week 07 定时抓拍策略、未新增依赖、未上传图片/未调用云端

**Week 08 后续补验/研究项（不构成当周核心要求未完成，但不得宣称已通过）**: 正式阈值校准与默认值（**不得自造**）、更多带质量真值的正常/低分/错误/未知样例、模型卡 0–100 与实测 0–1 差异解释、原始权重许可核实、连续帧/并发/长期运行稳定性验证（未执行）、T04 真机异常结构注入

**W8-4 已完成（有证据）**:
- 视觉反馈自然并入原有 ESP32 Dashboard（`main/http_server.c`「🔍 视觉反馈」导航 + 内嵌面板），地址栏不变、不新增第二个网页、无 iframe/跳转
- `service/vision_feedback_server.py` 转为 API-only（移除独立页面 `INDEX_HTML`），新增有边界 Origin 校验 + CORS + Private Network Access 处理（绝不 `*`），仍只监听 `127.0.0.1`
- 本地 API 地址经 Kconfig `CONFIG_VISION_API_URL`（默认 `http://127.0.0.1:8090`，回环、非设备/电脑局域网 IP）模板注入；设备照片/重采走同源相对路径
- 测试：Anaconda **224 passed, 2 skipped**；隔离 venv + 真实 ONNX **226 passed**；`idf.py build` **成功**（无 warning/error）；内嵌 JS `node --check` 通过
- **真实设备验收（2026-10-21，用户实机提供）**：固件 `27e1d42` 已烧录（Hash 验证通过）并启动，原 Dashboard 内嵌面板正常；设备照片 `cap-00001.jpg` 真实 DBCNN 推理 `0.34175166487693787` / `dbcnn_onnx_v1` / `needs_review` / 规则 `low_detail`；提交「不可用/遮挡」后 revision 1 保存且历史列显示 `unusable`（记录关联通过）；对照正常样例 `0.7561315298080444` / `ok` / 无反馈
- 详见 `weeks/week08/STATE.md` 第 7、8 节 / `TEST_LOG.md`「O」「P」/ `docs/vision-feedback.md`

**W8-5 已完成（有证据；已烧录并实机验收）**:
- 只改原有 Dashboard 视觉反馈面板（`main/http_server.c`），未新增网页、未改 API/枚举/模型/存储格式
- 状态中文化（值取自真实枚举，未知值原样回退）：`needs_review`→「待人工确认（未校准）」、`low_detail`→「画面细节不足」、规则 `ok`→「规则检查未发现明显问题」、verdict `usable/unusable/undetermined`→「可用/不可用/无法判断」
- 主区域改为「图像质量分析结果」，主要分数四位小数；「查看详细信息」折叠区保留完整原始分数、模型版本标识、图片来源、字节数、SHA-256、记录 ID
- 面板明示：质量分数不代表准确率或正确概率；阈值未校准不能自动判定图片是否可用
- 测试：新增 `service/test_dashboard_vision_ui.py` **14 passed**；视觉相关 7 文件 Anaconda **238 passed, 2 skipped**；隔离 venv + 真实 ONNX **240 passed**；内嵌 JS `node --check` + Node 行为校验通过；`idf.py build` 成功（`0x1142e0`，无 warning/error）
- **实机验收（2026-10-21）**：`861ae64` 固件烧录（三个镜像均 `Hash of data verified`），启动日志 `App version: week02-stable-103-g861ae64`；用户确认原 Dashboard 显示「图像质量分析结果」、主分数四位小数、完整原始分数在「查看详细信息」、判定「待人工确认（未校准）」、规则「画面细节不足」等中文，模型版本/图片来源/字节数/SHA-256/记录 ID 保留，未跳转第二个网址
- 详见 `weeks/week08/STATE.md` 第 9、10 节 / `TEST_LOG.md`「Q」「R」

**Week 08 课程要求核对与结项（2026-10-21）**: 官方 Week 08 任务卡要求（真实模型推理、结构化解析、低分/未知反馈、重采与人工纠正、输入—结果—反馈对应、中文可理解反馈）已实现并通过用户实机验收；T03 PASS，T04 在自动化/UI 层 PASS。交付物「正常、低分、错误和未知样例对照」仅部分完成（正常/较低分数有真实样例，错误/未知缺质量真值实拍对照），与阈值校准等一并保留为后续补验项。Week 08 可正式结束，**不得**把后续项表述为已完成。详见 `weeks/week08/STATE.md` 第 10 节 / `TEST_LOG.md`「R」。

---

## Week 07 — 按需获取一张环境图像（实机验收完成；失败态与失败记录待定；已合并 main）

**分支**: `feature/week07-vision-capture`（基线 `origin/main` `5aebea1`；已由 PR #8 合并至 `origin/main` `333a697`）
**阶段**: 🟢 **代码 + 构建 + 真实硬件验收完成**；⏳ 真实取帧失败 `failed` 未构造、无独立持久化失败记录
**详见**: `weeks/week07/STATE.md` / `TEST_LOG.md`「J」

**已完成（有证据）**:
- **PC 端画面可用性基线**（`b682cee`）：`service/image_quality.py` `assess_image()`，指标 `mean_luma/contrast/detail`，10 项测试全通过；全量 `pytest service/` **260 passed**（基线 250 + 新增 10）
- **端侧画面可用性评估**（`1abaa3d`）：`main/image_quality.c/h` 用 `esp_jpeg` 解码（RGB888+1/4 缩放，PSRAM），**阈值与 PC 端逐一对齐**
- **四态采集反馈**（`6d6853a`）：`capture_task_status_t`（`capturing/ok/unusable/failed`）与图像质量状态（`too_dark/too_bright/low_contrast/low_detail/invalid`）**语义分离**，映射实现于 `main.c`
- **API/UI**：`GET /api/camera/status`；gallery 暴露 `img_status/usable/mean_luma/contrast/detail`；Web UI 四态标签 + 画面不可用原因显示
- **最少保留策略**（2026-10-19）：默认上限 3（可配 1–9），超限释放最旧图像，`POST /api/camera/clear` 显式清除；`GET/POST /api/camera/retention`
- **只在触发后取图**（2026-10-19）：`POST /api/auto_capture` 启用请求返回 400 `course_policy_disabled`，主循环运行期守卫不执行自动抓拍
- **构建证据**：2026-10-19 `idf.py build` → `Project build complete`，0 warning / 0 error
- **实机验收**（2026-10-19，烧录 Hash verified）：保留上限 3 生效（photo index=3 → 404）、清空后 404、定时抓拍启用 400 `course_policy_disabled`
- **实机画面状态**：正常光照 `ok`（luma 117.64 / contrast 62.01 / detail 15.54）、遮挡 `unusable/too_dark`；遮挡后上一张 ok 图像保留原 request_id 与采集时间，未被改标

**未完成（如实标注）**:
- ⏳ **真实取帧失败 `failed`**：无故障注入手段，未构造
- ℹ️ **失败记录边界**：官网未要求持久化或独立接口（D010）；现有为 `failed` 状态 + `invalid` 图像状态 + 串口日志；真实 `failed` 样例待补验，不得以 `unusable` 代替
- ⚠️ 阈值仅单场景验证，未做多光照分布校准

**待用户决策**: Week 07 无既有 PR；是否创建 PR 及何时合并由用户决定（WorkBuddy 不自行合并）

---

## Week 06 — 让语音任务可以澄清与停止（核心开发完成，实机取消复测完成，ASR/NL→JSON 待验收）

**分支**: `feature/week06-voice-task-context`（HEAD `66ecbb6`，领先 `origin/feature/week06-voice-task-context` 3 提交；本次文档收尾提交后为 4 提交；2026-10-17 收尾）
**基线**: `f0653f0`（= Week 05 HEAD）
**远端**: `origin/main` `6761db6` 已包含 PR #6（合并 Week 06 至 `28aa45c`）；当前分支后续 3 个功能提交与本次文档提交**尚未合并 main**
**阶段**: 🟢 **核心开发完成 + 实机取消/反馈停止复测完成**；⏳ **中文 ASR 内容验收待补**；⛔ **完整 ASR→NL→JSON→任务链路未端到端验收**

**本周已完成（有证据）**:
- **任务上下文**：request_id `voice-{ms}-{counter:04d}` 贯穿 start → recording → stop → ASR → 管线 → 取消/完成
- **任务状态机**：IDLE→RECORDING→PROCESSING→COMPLETED / FAILED / CLARIFYING（→PROCESSING）/ CANCELLING→CANCELLED；取消可从 RECORDING / PROCESSING / CLARIFYING 触发
- **任务取消**：`POST /api/voice/cancel`（停止录音、幂等、已取消/完成/失败返回 409）
- **澄清/重新输入**：`POST /api/voice/clarify`（仅 CLARIFYING 接受、空输入拒绝、轮次递增）
- **迟到结果隔离**：generation 代次单调递增 + `_check_stale()`，旧代次结果记录 `result_stale_discarded` 不覆盖当前状态
- **ESP32 仪表盘**：取消按钮 + 任务状态显示（`6e14622`），固件已烧录、仪表盘 HTTP 200
- **生产计时**：`task.timing` 暴露 `latency_first_feedback_ms` / `latency_completion_ms` / `latency_termination_ms` / `latency_cancel_to_feedback_stop_ms`（`6ff8086`、`66ecbb6`）
- **自动测试（2026-10-17 本次运行）**：`test_voice_control_server.py` **99/99**；全量 `pytest service/` **250 passed + 6 subtests**（0 失败）
- **实机取消复测（2026-10-17）**：ESP32 PLAY 启动 → Web 取消 `POST /api/voice/cancel` HTTP 200 → 状态链 `recording → cancelling → cancelled`；generation `5 → 6`；`latency_cancel_to_feedback_stop_ms = 79 ms`；服务端取消状态与浏览器显示一致。**单次实测，不代表所有场景的固定性能；项目无 TTS，不得称为语音播报停止时延**
- **此前实机取消联动（2026-10-14）**：generation `7 → 8`（另一会话/任务，见 TEST_LOG）

**本周未完成（如实标注，收尾不扩开发范围）**:
- ⏳ **中文 ASR 准确率**：**未完成人工验收**（用户当前不方便进行语音输入；**不是失败，属待补验收**）
- ⛔ **完整语音任务链路**：`service/nl_to_task.py` 缺失，ASR 原始文本无法转为 TaskContract JSON，`task_pipeline.run_task()` 无法处理；详见 TEST_LOG 2026-10-16 诊断
- ⛔ **TTS 停止播报**：项目无 TTS 实现，不适用（不存在可中断的音频播报）
- ⏳ 语音澄清（CLARIFYING）真实语音交互闭环：待 ASR 人工验收后配合补验

---

## Week 05 汇总 — 接通按键说话与语音播报（代码集成已完成，收尾中）

**分支**: `feature/week05-voice-interaction`（本地分支 HEAD `91959fb`，领先 `origin/feature/week05-voice-interaction` 4 提交；2026-10-13 收尾）
**基线**: `7dd788b` — Merge pull request #4（= `main` = `origin/main`）
**阶段**: 🟢 **语音输入链路已实现并通过真实双端闭环验证**（2026-10-11 Step 3I：PLAY→PC 录音→自动后台 Whisper ASR→STOP 回执→ESP32 恢复 IDLE，START/STOP 均 HTTP 200、0 崩溃）。含：PC 音频采集、录音 HTTP 控制服务、本地 Whisper ASR 模块、ESP32 PLAY 按键控制、录音停止自动后台 ASR、**ESP32 仪表盘 🎙 语音交互面板**（2026-10-13 提交 `91959fb`）。已处理 Step 3C 截断（3D 解耦）、Step 3E 栈溢出（2048→4096，实机复核通过）；Step 3F 传输层超时与 Step 3G Brownout 均未在 Step 3I 复现（留档）。

> ⚠️ **Week 05 代码集成已完成；真实中文语音识别和简体中文输出待次日人工验收。** 不得把静音/背景噪声导致的空转录记录为识别成功，也不得宣称识别准确率已通过。

**本周已完成（有证据）**:
- Step 1 PC 音频采集：WAV 格式 + 真实麦克风有效语音 PASS
- Step 2A/2B/2C：PC 录音 HTTP 控制服务 + ESP32 PLAY 按键控制 + 实机联调通过
- Step 3A/3B/3C/3D/3E/3F/3G/3I：本地 Whisper ASR、自动后台 ASR、端到端验证、契约修复、栈修复与实机复核、最终双端闭环验证
- **ESP32 仪表盘 🎙 语音交互面板**：浏览器端语音按钮（开始/停止录音）、实时状态/帧数/ASR 文本显示，提交 `91959fb`（2026-10-13）
- **D008 决策草案**（`339c2ad`）：Week 05 语音链路 5 项决策点记录，待用户审阅
- **Step 4A/4B PC 端语音控制台集成**（`fedd33c`）→ 已 revert（`adf123f`），当前代码不含独立 PC 语音控制台页面

**本周未完成（如实标注，收尾不扩开发范围）**:
- Step 4 语言模型 NL→JSON（未开始）
- Step 5 TTS 播报（未开始）——课程目标「语音播报」输出侧未实现
- Step 6 全链路串联 / Step 7 状态显示与 LED / Step 8 异常处理与文字对照测试（未开始）

> ⚠️ 遗留待确认（不影响本周验收，留档后续）:
> - **edge-tts**：调用微软**在线** TTS 服务，**不需要 API Key**，但**需要网络**且合成文本会外发给外部服务；课程许可与隐私范围**尚未获得确认授权**（待向教师/助教确认）
> - **LLM API 凭据**：与 ASR 无关的**另一项独立配置**（仅在选用云服务时需要）；凭据不得写入仓库

---

## Week 04 汇总 — 自然语言查询与请求采集（已合并）

**分支**: `feature/week04-natural-language`（HEAD `0b4b323`，已合并至 `main`）
**阶段**: Step 1-6 全部完成 ✅（102 test PASS）+ **Web 仪表盘 localStorage 持久化 ✅** + **CSV 导出 ✅** + **浏览器持久化人工检查（4 项）✅** + **CSV 核验 ✅（29 条、12 列、全部 cadence）**
**合并**: PR #4 → `main` `7dd788b`（2026-10-10）
**仍待补验**: 真实 HTTP 调用（ESP32 设备）* 真实语言模型接入

---

## 全局进度

| 单元 | 周 | 主题 | 状态 |
|------|-----|------|------|
| 单元1 | Week 01 | 传感数据采集与 Web 展示 | ✅ 完成 |
| | Week 02 | 远程采集指令与执行反馈 | ✅ 完成 (tag: week02-stable) |
| | Week 03 | 实体按键与物理反馈闭环 | ✅ 已合并至 main (PR #3, `ec9c94b`) |
| 单元2 | Week 04 | 自然语言查询与请求采集 | ✅ 已合并至 main (PR #4, `7dd788b`) |
| | **Week 05** | **接通按键说话与语音播报** | 🟢 语音输入链路已完成实机双端闭环验证（Step 3I）；TTS 播报/LLM/全链路未完成（feature 分支） |
| | **Week 06** | **让语音任务可以澄清与停止** | 🟢 核心开发完成（分支 `feature/week06-voice-task-context`，HEAD `66ecbb6` + 文档收尾，voice server **99/99**、全量 **250 passed + 6 subtests**）；✅ 实机取消/反馈停止复测（generation 5→6，取消→反馈停止 **79 ms**）；✅ 此前实机取消联动（7→8）；⏳ 中文 ASR 内容验收待补；⛔ NL→JSON 缺失，完整语音任务链路待验收 |
| 单元3 | **Week 07** | **按需获取一张环境图像** | ✅ 已由 PR #8 合并至 `origin/main` `333a697`；按需抓拍、四态反馈、画面可用性、最少保留已实机验收；真实取帧失败 `failed` 样例待补 |
| | **Week 08** | **把视觉推理结果变成可理解反馈** | ✅ 已收尾（分支 `feature/week08-vision-feedback`；功能/固件提交 `861ae64`，文档收尾提交见 `git log -1`；已推送，**未合并 main**）；真实 DBCNN 推理 + 结构化拒识 + 重采/人工纠正 + Dashboard 中文化面板，W8-4/W8-5 已实机验收；阈值校准等列为后续补验 |
| | Week 09 | 用视觉事件触发一次主动询问 | ⏳ 未开始 |
| 单元4 | Week 10-12 | 多源上下文与状态推断 | ⏳ 未开始 |
| 单元5 | Week 13-15 | 边云协作与韧性通信 | ⏳ 未开始 |
| 单元6 | Week 16-18 | 体验检验、改进与结课交付 | ⏳ 未开始 |

---

## Week 03 历史记录 — 实体按键与物理反馈闭环（已合并至 main，PR #3 `ec9c94b`）

**目标**: 实体按键触发 → 去抖 → 生成唯一 event_id → 本地 LED 反馈 → 发送教学测试消息 → Web 接收 → 远端证据返回 → 更新物理反馈 → 支持取消/回应

**已完成**:
- `help_event.h` — 完整的接口定义（状态枚举、数据结构、按键 FSM、LED 模式、事件生命周期）
- `help_event.c` — 事件生命周期实现、按键去抖 FSM、LED 反馈驱动
- `main.c` — 主循环中集成按键去抖 + 事件触发 + 网络检测 + 超时保护 + LED 更新
- `http_server.h` — `sensor_ctx_t` 中增加 `help_event_t *help` 字段
- `http_server.c` — 三个 HTTP API 端点：`GET /api/help/status`、`POST /api/help/confirm`、`POST /api/help/cancel`
- `main/CMakeLists.txt` — 添加 `help_event.c`
- 👉 **Week 03 按键扫描解耦**：独立 FreeRTOS 任务 (30 Hz) + FreeRTOS Queue 通信，主循环不再负责按键采样
- 👉 **help_uplink 模块**：独立 HTTP POST 模块 (`help_uplink.c/.h`)，将 `help_event_t` 构造为 JSON 推送到外部 VPS 端点（`ae89be8`）
- 👉 **JSON 时间戳语义修正**：`sent_at_us` 标注为设备端 HTTP POST 完成时刻，0 值序列化为 `null`（`5fe738a`）
- 👉 **help_uplink 异步接口集成至 `main.c`**（`29dd72d`）：新增异步接口 `help_uplink_start()` / `help_uplink_request_send()` / `help_uplink_poll()`，由后台 FreeRTOS 任务执行 HTTP POST；`main.c` 初始化阶段调用 `help_uplink_start()`，事件状态机接入 `help_uplink_is_configured()` / `help_uplink_request_send()` / `help_uplink_poll()`（源码位置 `main.c:364` / `:595` / `:601` / `:612`）

**待完成**:
- ~~**`help_uplink` 接入 `main.c`**~~ → **已完成（软件集成）**：异步接口已集成至 `main.c` 事件状态机（`29dd72d`），**「模块已存在但未被调用」的旧描述已作废**
- **完整闭环链路测试**（按键 → 去抖 → event_id → LED 反馈 → HTTP 发送 → 远端确认/取消 → 物理反馈更新）
- **真实 ESP32→VPS 端到端实机验收（仍未完成）**：软件集成已完成（`29dd72d`），但需配置真实 VPS URL，实测 HTTP POST 成功/失败/超时路径并检查远端接收状态及响应；**在取得实机证据前不得声称端到端链路已通过**

### ⚠️ 实机测试记录 (2026-10-09)

| 测试项 | 结果 | 说明 |
|--------|------|------|
| idf.py build | ✅ PASS | week02-stable-15-g65b001f |
| 串口检测 COM10 | ✅ PASS | ESP32-S3-EYE (USB VID:PID=303A:1001) |
| idf.py flash | ✅ PASS | 烧录成功 |
| 设备启动 | ✅ PASS | IMU 数据正常输出（cadence 循环） |
| 按键单次按下检测 | ✅ PASS | 日志: `[W3-BTN] pressed MENU → event help-141745-0001` |
| 事件创建 | ✅ PASS | `W3-EVENT: created id=help-141745-0001 btn=MENU counter=1 (LOCAL CONFIRMED)` |
| HTTP 发送 | ✅ PASS | `W3-EVENT: sent id=help-141745-0001` |
| 网络 API 可用 | ✅ PASS | `[W3-NET] event help-141745-0001 available via API` |
| `[W3-FEEDBACK]` 日志 | ❌ 不存在 | LED 反馈走 `gpio_set_level()` 未打印单独日志，代码中无 `W3-FEEDBACK` 标签 |
| Brownout 稳定性 | ✅ **PASS** | 118s+ 连续运行，无 BOD 日志 |
| MENU 短按 ~294ms | ✅ **PASS** | btn pressed → released (294ms) → created, 仅1个 event_id |
| MENU 长按 ~6s 不重复 | ✅ **PASS** | held 5978ms，因前事件活跃正确抑制 |
| 30s 超时自动重置 | ✅ PASS | help-141745-0001 stale (>30s), auto-reset |
| MENU 连续快速按 3 次 | ⚠️ 未捕获 | 日志仅含短按+长按两组，无第3组 |
| LED 物理反馈观察 | ⛔ 未完成 | 未安排

---

## ⚠️ 已知问题

### Brownout 检测触发 — 间歇性 (2026-10-09)
- **现象**: 按键触发 → 事件创建 → HTTP 发送后，约 1 秒后 `E BOD: Brownout detector was triggered` → 设备重启
- **复现步骤**: 单次按下 PLAY 按键，事件流程正常完成后设备崩
- **可能原因**: 
  - 供电不足：WiFi HTTP POST + 摄像头 + IMU 同时运行导致瞬时电流超限
  - USB 线缆/供电能力不足
  - `CONFIG_BROWNOUT_DET_LVL` 设置过保守（当前未知）
- **建议排查**: 检查电源、USB 线缆；或在 menuconfig 中调整 Brownout 阈值
- **第2轮测试备注 (2026-10-09)**: MENU长按≥5s 操作下未复现 Brownout，设备稳定运行 25s+。
- **第3轮测试备注 (2026-10-09)**: 三次实机验证（短按294ms+长按6s）**均未复现 Brownout**，设备稳定运行 118s+。Brownout 概率较低。

### ✅ 快速连按验证通过
- **MENU 连续快速按 4 次**（含 2 次快速点击间隔 ~343ms release-to-press）→ 第4轮实机验证 (**8f8c511**) 已通过
- 结果：仅生成 **1 个 event_id**，非 IDLE 状态时后续 3 次按下**正确抑制**
- 参见 TEST_LOG.md "快速连续按键验证" 详细记录

### ✅ 已修复的编译问题
- **`main.c:370`** — `help_event` 先使用后声明：将 `help_event_t help_event` / `help_button_fsm_t btn_fsm` 声明移到 `sensor_ctx_t ctx` 初始化之前
- **`help_event.c`** — `help_status_name()` 函数体损坏修复：将孤立 case 标签移回 switch 内，删除末尾孤儿代码
- **`http_server.c`** — `HTTPD_500` → `HTTPD_500_INTERNAL_SERVER_ERROR`；注释块缺失 `/*` 修复；双重 `/*` 修复；添加 help URI 路由定义与注册（消除 unused-function warning）

### 文件修改状态
```
modified:   main/main.c             (变量声明顺序修复)
modified:   main/help_event.c       (函数体结构修复)
modified:   main/http_server.c      (编译错误修复 + URI 路由注册)
```
### ✅ 按键扫描解耦 (2026-10-09)
- **方案**: 独立 FreeRTOS 任务 `button_scan_task` (~30 Hz) 运行去抖 FSM，通过 `xQueueSend` 通知主循环
- **接口变更**: `help_event.h` 移除 `help_button_fsm_t` 类型/`help_button_fsm_init`/`help_button_fsm_update`，新增 `help_button_service_start`/`help_button_get_pending`
- **`main.c`**: 移除 `btn_fsm` 变量和主循环 ADC 读/FSM 更新，改为 `help_button_get_pending()` 非阻塞收队列
- **`help_event.c`**: FSM 改为 `static` 内部类型，新增 `button_scan_task`、Queue、两个公开 API
- **`idf.py build`**: ✅ PASS