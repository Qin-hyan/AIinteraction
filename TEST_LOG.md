# Test Log — 长期测试记录

> 只记录最终测试结果和关键证据。不保存大量原始日志。  
> 原始日志 → `build/log/` (gitignored)

---

## Week 02 Stable — 2026-10-06

**Tag**: `week02-stable`  
**验证方式**: 实机  
**结果**: 12 PASS / 0 FAIL

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | 页面 200 且含两个对照按钮 | PASS |
| 2 | 含请求—回执—观测记录区块与证据说明 | PASS |
| 3 | 刷新已存数据不触发采集 | PASS |
| 4 | 暂停后观测序号冻结 seq=14 | PASS |
| 5 | 暂停后数据年龄增长 | PASS |
| 6 | 状态链含设备回执阶段 completed | PASS |
| 7 | 完成且新观测关联本次请求号 | PASS |
| 8 | 新观测为真实读取 source=live 且序号递增 | PASS |
| 9 | 任务进行中沿用同一请求号 | PASS |
| 10 | 未知请求号不返回成功 | PASS |
| 11 | 设备端记录含受理/回执/结果时间 | PASS |
| 12 | 恢复后已存观测继续更新 | PASS |
| — | 15 次连续摄像头抓拍 | PASS |

---

## Week 03 — 2026-10-09 (第2轮验证)

**分支**: eature/week03-physical-feedback  
**版本**: week02-stable-11-g63ab504 (前次) / 451227 (本次)  
**验证方式**: 实机（COM10）  
**结果**: 4 PASS / 1 FAIL / 4 ⛔ 未完成 (前次) → **+1 新增验证**  

### 基础流程验证（前次 2026-10-09）

| # | 测试项 | 结果 | 备注 |
|---|--------|------|------|
| 1 | build 编译通过 | ✅ PASS | idf.py build 无错误 |
| 2 | flash 烧录成功 | ✅ PASS | COM10 460800 baud |
| 3 | 按键检测（PLAY）单次触发 | ✅ PASS | W3-EVENT: btn pressed: PLAY，仅 1 次 |
| 4 | 事件创建 + ID 生成 | ✅ PASS | id=help-15919-0001 |
| 5 | HTTP 发送 + API 可用 | ✅ PASS | W3-EVENT: sent + [W3-NET] available via API |
| 6 | LED 物理闪烁（实机观察） | ⛔ 未完成 | Brownout 中断 |
| 7 | 长按不重复触发 | ⛔ 未完成 | Brownout 中断 |
| 8 | 快速点击无异常重复 | ⛔ 未完成 | Brownout 中断 |
| 9 | Brownout 稳定性 | ❌ FAIL | E BOD: Brownout detector was triggered → 重启 |

### 按键采样验证（2026-10-09 第 2 轮 — MENU 长按 ≥5s）

| # | 测试项 | 结果 | 备注 |
|---|--------|------|------|
| 1 | MENU 按键检测（长按 ≥5s） | ✅ **PASS** | [W3-BTN] pressed MENU → event help-48904-0002，仅 1 次 |
| 2 | 长按不重复触发 | ✅ **PASS** | 全程仅 1 条 [W3-BTN] 日志，无重复 |
| 3 | 事件创建 + ID 生成 | ✅ PASS | id=help-48904-0002 btn=MENU counter=2 |
| 4 | HTTP 发送 + API 可用 | ✅ PASS | W3-EVENT: sent + [W3-NET] available via API |
| 5 | 超时自动重置（30s） | ✅ PASS | 旧事件 help-16842-0001 过期后自动 
eset to IDLE |
| 6 | Brownout 稳定性 | ✅ **PASS** | 本轮**无 Brownout**，设备持续正常运行 >25s |
| 7 | 设备持续稳定运行 | ✅ PASS | IMU cadence #46~#66 连续正常输出 |

**关键证据**:
`
W (48719) MAIN: [W3-REMOTE] event help-16842-0001 stale (>30s), auto-reset    ← 30s 超时验证
I (48719) W3-EVENT: reset to IDLE (was help-16842-0001)

I (49753) W3-EVENT: created id=help-48904-0002 btn=MENU counter=2 (LOCAL CONFIRMED)
I (49753) MAIN: [W3-BTN] pressed MENU → event help-48904-0002
I (49755) W3-EVENT: sending id=help-48904-0002
I (49759) W3-EVENT: sent id=help-48904-0002
I (49763) MAIN: [W3-NET] event help-48904-0002 available via API
                                                                     ← 无 Brownout！
I (50786..71469) IMU cadence #46~#66 连续正常输出                    ← 设备稳定运行
`

**结论**: MENU 长按 ≥5s 仅触发 1 次事件，去抖 FSM 正常工作；无 Brownout 触发。前次 Brownout 可能为瞬态供电波动。

### 已知问题

- ~~**Brownout 检测触发**（前次复现，本轮未复现 — 可能为瞬态 USB 供电波动）~~ → 暂标记为间歇性
- 远端确认/取消 API 尚未完成测试
- LED 物理闪烁观察尚未完成
- 快速点击无异常重复尚未系统验证

### 待完成

- [x] MENU 长按不重复触发验证 ← 本次完成
- [x] 30s 超时自动重置验证 ← 本次完成（间接证据）
- [x] Brownout 重测 ← 本次未复现
- [ ] 远端确认/取消 API 测试
- [ ] LED 物理闪烁观察
- [ ] 快速点击无异常重复测试
- [ ] Week 01-02 API 回归

### 按键扫描解耦验证（2026-10-09 第3轮 — `65b001f` 独立任务 30Hz）

**分支**: `feature/week03-physical-feedback`  
**版本**: `65b001f` feat(week03): decouple button scanning  
**验证方式**: 实机（COM10），Python 串口捕获 120s  
**结果**: 7 PASS / 1 ⚠️ 未验证 / 0 FAIL  

| # | 测试项 | 结果 | 证据 |
|---|--------|------|------|
| 1 | **MENU 短按 (~200-500ms)** | ✅ **PASS** | `btn pressed: MENU` @142228 → `btn released: was MENU` @142522，持续 **294ms** ✅ |
| 2 | 短按只产生一个 event_id | ✅ **PASS** | 仅 1 条 `[W3-BTN] pressed MENU → event help-141745-0001` |
| 3 | 事件创建 + HTTP 发送 | ✅ **PASS** | `created id=help-141745-0001 btn=MENU counter=1 (LOCAL CONFIRMED)` → `sent` → `available via API` |
| 4 | **MENU 长按 ~6s** | ✅ **PASS** | `btn pressed: MENU` @147177 → `btn released: was MENU` @153155，持续 **5978ms** ✅ |
| 5 | 长按不重复触发 | ✅ **PASS** | 长按期间**未创建新事件**（主循环检查 `help_event.status == HELP_IDLE` 正确抑制） |
| 6 | 30s 超时自动重置 | ✅ **PASS** | `[W3-REMOTE] event help-141745-0001 stale (>30s), auto-reset` @173132 |
| 7 | **Brownout 稳定性** | ✅ **PASS** | 118s+ 连续运行 (T=75466~T=193479)，**无 BOD 日志**，IMU cadence #71~#187 正常 |
| 8 | **MENU 连续快速按 3 次** | ⚠️ **未捕获** | 日志中仅有短按(294ms)和长按(5978ms)两组事件，无第3组快速连按。可能未执行该测试 |
| — | [W3-BTN] 日志标签 | ✅ **PASS** | 每条按键事件均通过 `[W3-BTN]` 记录 |
| — | [W3-EVENT] 日志标签 | ✅ **PASS** | `btn pressed`、`btn released`、`created`、`sending`、`sent` 均含 `[W3-EVENT]` 前缀 |

**关键证据**:
```
I (142228) W3-EVENT: btn pressed: MENU
I (142522) W3-EVENT: btn released: was MENU
I (142592) W3-EVENT: created id=help-141745-0001 btn=MENU counter=1 (LOCAL CONFIRMED)
I (142595) MAIN: [W3-BTN] pressed MENU → event help-141745-0001
I (142601) W3-EVENT: sending id=help-141745-0001
I (142605) W3-EVENT: sent id=help-141745-0001
I (142609) MAIN: [W3-NET] event help-141745-0001 available via API
                                            ← 短按 294ms 完成，仅 1 个 event_id

I (147177) W3-EVENT: btn pressed: MENU      ← 长按开始
I (153155) W3-EVENT: btn released: was MENU ← 长按 5978ms，无新事件

W (173132) MAIN: [W3-REMOTE] event help-141745-0001 stale (>30s), auto-reset
I (173134) W3-EVENT: reset to IDLE (was help-141745-0001)

I (75466..193479) IMU cadence #71~#187       ← 118s+ 稳定输出，无 Brownout
```

**结论**: 
- 独立 30Hz 按键扫描任务工作正常
- 短按去抖正确，一次按下仅产生一个 event_id
- 长按（>=5s）仍只触发一次，主循环状态检查正确抑制重复触发
- Brownout 未复现，设备 118s+ 稳定运行
- 快速连按未在本次捕获到，有待后续验证

---

## Week 03 — 快速连续按键验证 (2026-10-09 第4轮)

**分支**: `feature/week03-physical-feedback`
**版本**: `week02-stable-16-g7ae61d4` test(week03): verify decoupled button scanning
**验证方式**: 实机（COM10），idf.py monitor 串口捕获 70s
**结果**: **5 PASS / 0 FAIL / 0 ⚠️**

| # | 测试项 | 结果 | 证据 |
|---|--------|------|------|
| 1 | **等待 help_event 回到 IDLE** | ✅ **PASS** | 设备启动后初始状态 IDLE，无残留事件 |
| 2 | **物理按键检测**（MENU 共 4 次按下检测） | ✅ **PASS** | 4 次 `[W3-EVENT] btn pressed: MENU` 均被 30Hz 扫描任务检测到 |
| 3 | **event_id 数量** | ✅ **PASS** | **仅 1 个 event_id** (`help-32868-0001`)，来自第 1 次按下 |
| 4 | **非 IDLE 时按键被正确抑制** | ✅ **PASS** | 第 2/3/4 次按下均 **未创建新事件**（主循环状态检查正确抑制） |
| 5 | **30s 观察无异常重复事件** | ✅ **PASS** | 从末次按键到 `stale (>30s)` 间 **无异常事件**，仅 1 个 event 超时重置 |

### 详细时间线（ms since boot）

```
T=33312  btn pressed: MENU           ← 1st press (rapid start)
T=33459  btn released: was MENU      ← 147ms
T=33715  created id=help-32868-0001
T=33717  [W3-BTN] → event help-32868-0001
T=33723  sending
T=33727  sent
T=33731  [W3-NET] available via API
T=33802  btn pressed: MENU           ← 2nd press (~343ms after 1st release) ✅ RAPID
T=33949  btn released: was MENU      ← 147ms → 被抑制（事件活跃）
    ...  IMU cadence #31~#38  (设备从手持姿态恢复)
T=42769  btn pressed: MENU           ← 3rd press (~8820ms after 2nd)
T=42916  btn released: was MENU      ← 147ms → 被抑制（事件仍活跃）
T=43259  btn pressed: MENU           ← 4th press (~343ms after 3rd release) ✅ RAPID
T=43406  btn released: was MENU      ← 147ms → 被抑制（事件仍活跃）
    ...  IMU cadence #39~#60  (稳定输出，无异常)
W(64247) stale (>30s), auto-reset    ← 30.5s 超时
I(64249) reset to IDLE
    ...  IMU cadence #61~#65  (稳定继续)
```

### 关键指标

| 指标 | 值 | 分析 |
|------|-----|------|
| 物理按键检测次数 | 4 次 | 30Hz 扫描任务均正确检测到 press/release（持续 ~147ms） |
| 2 次 rapid 间隔 | ~490ms (press-to-press) / ~343ms (release-to-press) | 在 300-500ms 范围内 ✅ |
| 生成 event_id 数 | **1** | 仅 `help-32868-0001` |
| 抑制正确性 | **100%** | 第 2/3/4 次按下均被正确抑制 |
| 超时恢复 | **30.5s** → IDLE | 正常 |
| Brownout | **无** | 全程无 BOD，IMU cadence #30~#65 连续稳定输出 |

### 设计语义验证

**当前设计**：主循环 `main.c` 中，仅当 `help_event.status == HELP_IDLE` 时调用 `help_event_trigger()`。

```
if (help_button_get_pending(&btn)) {
    if (help_event.status == HELP_IDLE) {
        help_event_trigger(&help_event, btn);
    }
}
```

**验证结果**：✅ **非 IDLE 时后续按键被正确忽略，无重复/错误事件产生。**

即使 30Hz 扫描任务检测到 4 次独立物理按键（均在去抖阈值内稳定采样），主循环 1Hz 状态检查精确抑制了后 3 次——因为事件状态依次经历了 `TRIGGERED → SENDING → SENT`，始终 ≠ `IDLE`。

### 结论

- **EVENT_COUNT**: 1
- **ACTIVE_EVENT_BEHAVIOR**: 正确抑制（非 IDLE 时忽略后续按键）
- **RESULT**: **PASS** — 快速连续按键无异常重复事件
- **Brownout**: 未复现，设备 ≥65s 稳定运行

---

## Week 03 — help_uplink 模块构建验证 (2026-10-09)

**分支**: `feature/week03-physical-feedback`

### 提交 `ae89be8` — feat(week03): add help_uplink module for VPS HTTP POST

| 验证项 | 结果 | 证据 |
|--------|------|------|
| `idf.py build` 编译通过 | ✅ **PASS** | `idf.py build` 实际运行通过（版本 `week02-stable-19-g5fe738a-dirty`），新增 `help_uplink.c/h` 无编译错误 |
| 模块注册 | ✅ **PASS** | `help_uplink.c` 已加入 CMakeLists.txt 源文件列表 |
| Kconfig 配置项 | ✅ **PASS** | `CONFIG_HELP_VPS_URL`（string）、`CONFIG_HELP_UPLINK_TIMEOUT_MS`（int，默认 10000）已定义 |
| 公开 API（`ae89be8` 时点，同步） | ✅ **PASS** | `help_uplink_is_configured()`、`help_uplink_send_event()` 签名完整；异步 API（`29dd72d`）见下节 |

### 提交 `5fe738a` — fix(help_uplink): correct protocol semantics for JSON timestamps and config

| 验证项 | 结果 | 证据 |
|--------|------|------|
| `idf.py build` 编译通过 | ✅ **PASS** | `idf.py build` 实际运行通过（`week02-stable-19-g5fe738a-dirty`），`Project build complete`，0 error |
| 时间戳语义正确性 | ✅ **PASS** | `sent_at_us` 注明为设备端 HTTP POST 完成时刻（非 VPS 上传时间），0 值序列化为 JSON `null` |
| 代码审查无新 WARNING | ✅ **PASS** | 构建日志无新增编译警告 |

### ⚠️ 未验证项目（诚实记录，`ae89be8`/`5fe738a` 时点）

| 项目 | 原因 |
|------|------|
| **实机 HTTP POST 测试** | 未配置真实 VPS URL，未烧录验证实际网络行为 |
| ~~**`help_uplink` 接入 `main.c`**~~ → **已解决（软件集成）** | ~~当前模块未被 main.c 调用，尚未集成到事件发送路径~~；该集成已由后续提交 `29dd72d` 完成（见下节），但**实机网络验证仍未进行** |
| **超时路径测试** | 需实机验证 `CONFIG_HELP_UPLINK_TIMEOUT_MS` 超时行为 |
| **非 2xx 响应处理** | 需实机 mock VPS 验证 HTTP 500/404 等错误路径 |

**结论（`ae89be8` + `5fe738a` 时点）**: 构建验证通过，模块代码完整、无编译错误。**当时** help_uplink 尚未集成到 `main.c` 的事件发送路径；该软件集成已由后续提交 `29dd72d` 完成（见下节）。**但真实 ESP32→VPS 端到端实机验收仍未完成（HTTP POST 成功/失败/超时路径均未验证）**，闭环链路（按键 → HTTP POST → 远端确认）**不得声称已通过**。

---

## Week 03 — help_uplink 异步接口集成（`29dd72d`，2026-10-10 文档事实核对）

**提交**: `29dd72d` feat(week03): integrate help_uplink async task into event state machine
（`main/help_uplink.c` +110 行、`main/help_uplink.h` +42 行、`main/main.c` +49 行；该提交位于 PR #3 合并 `ec9c94b` 的历史中，并随 PR #4 进入 `main` `7dd788b`）

### 集成后的公开 API 清单（覆盖同步 + 异步）

| API | 类型 | 说明 | 验证状态 |
|-----|------|------|---------|
| `help_uplink_is_configured()` | 同步 | 检查 `CONFIG_HELP_VPS_URL` 是否非空 | 签名 ✅（`ae89be8`）；集成 ⛔ 未验证 |
| `help_uplink_send_event()` | 同步（阻塞） | 构造 JSON → HTTP POST；**不能在 1 Hz 主循环直接调用** | 编译 ✅ PASS（`ae89be8`/`5fe738a`）；实机 ⛔ 未验证 |
| `help_uplink_start()` | 异步 | 创建后台 FreeRTOS 上行推送任务 | ⛔ 未验证（`29dd72d` 无构建条目） |
| `help_uplink_request_send()` | 异步 | 主循环请求一次上行 | ⛔ 未验证（`29dd72d` 无构建条目） |
| `help_uplink_poll()` | 异步 | 轮询异步上行结果 | ⛔ 未验证（`29dd72d` 无构建条目） |

### 源码集成证据（`main/main.c`，当前代码）

| 位置 | 调用 |
|------|------|
| `main/main.c:34` | `#include "help_uplink.h"` |
| `main/main.c:364` | `help_uplink_start();`（启动异步 VPS 上行推送任务） |
| `main/main.c:595` | `help_uplink_is_configured()`（未配置则 `help_event_fail()`） |
| `main/main.c:601` | `help_uplink_request_send(&help_event)`（未被接受则 `help_event_fail()`） |
| `main/main.c:612` | `help_uplink_poll(&uplink_res)`（`ESP_OK` → `help_event_mark_sent()`，否则 `help_event_fail()`） |

### ⚠️ 未验证项目（不新增无证据结论）

| 项目 | 原因 |
|------|------|
| **`29dd72d` 编译验证** | `TEST_LOG.md` 无该提交的构建条目；本次仅做文档事实核对，**未运行固件构建**，故不新增构建结论 |
| **真实 ESP32→VPS HTTP POST（成功/失败/超时）** | 未配置真实 `CONFIG_HELP_VPS_URL`，无实机网络证据 |
| **非 2xx 响应 / 超时路径实机验证** | 需实机 mock VPS 验证 HTTP 500/404 等错误路径 |

**结论**: 异步接口已在**源码层**集成至 `main.c` 事件状态机（软件集成完成），但编译与实机网络路径**均无验证证据**，不得标记为 PASS。真实 ESP32→VPS 端到端验收**仍未完成**。

---

## Week 04 — 结构化任务契约校验模块 (2026-10-09)

**分支**: `feature/week04-natural-language`  
**范围**: `service/` (Python 标准库，纯逻辑，不接入语言模型，不调用设备接口)  
**版本**: (提交前 HEAD `d00e33e`)  
**验证方式**: Python `unittest` 本地运行  
**结果**: **24 PASS / 0 FAIL**

| # | 测试项 | 结果 | 分类 |
|---|--------|------|------|
| 1 | query_last 合法输入 | ✅ PASS | 合法意图 |
| 2 | trigger_collect 合法输入 | ✅ PASS | 合法意图 |
| 3 | clarify 合法输入（带 question） | ✅ PASS | 合法意图 |
| 4 | unsupported 合法输入（带 reason） | ✅ PASS | 合法意图 |
| 5 | clarify 缺少 question | ✅ PASS | 歧义拒绝 |
| 6 | clarify 空字符串 question | ✅ PASS | 歧义拒绝 |
| 7 | unsupported 缺少 reason | ✅ PASS | 歧义拒绝 |
| 8 | 非法 JSON | ✅ PASS | 格式拒绝 |
| 9 | 空输入 | ✅ PASS | 格式拒绝 |
| 10 | 未知意图 turn_on_light | ✅ PASS | 意图拒绝 |
| 11 | query_last 携带 device_address | ✅ PASS | 越界拒绝 |
| 12 | trigger_collect 携带 interface | ✅ PASS | 越界拒绝 |
| 13 | unsupported 携带额外 device_id | ✅ PASS | 越界拒绝 |
| 14 | confidence > 1.0 | ✅ PASS | 边界拒绝 |
| 15 | confidence < 0.0 | ✅ PASS | 边界拒绝 |
| 16 | original 空字符串 | ✅ PASS | 边界拒绝 |
| 17 | 缺少 intent | ✅ PASS | 必需字段拒绝 |
| 18 | 缺少 confidence | ✅ PASS | 必需字段拒绝 |
| 19 | intent 非字符串 | ✅ PASS | 类型拒绝 |
| 20 | confidence 非数值 | ✅ PASS | 类型拒绝 |
| 21 | TaskContract.to_dict 排除 None | ✅ PASS | 序列化 |
| 22 | TaskContract.to_json 往返 | ✅ PASS | 序列化 |
| 23 | TaskContract.from_dict | ✅ PASS | 反序列化 |
| 24 | IntentEnum.all_values 枚举 | ✅ PASS | 枚举辅助 |

**结论**: 结构化任务契约模块 `service/` 实现完成，24 个单元测试全部通过。此模块仅含纯逻辑校验，未接入语言模型，未调用任何设备接口。

---

## Week 04 Step 2 — query_last 受限工具 (2026-10-09)

**分支**: `feature/week04-natural-language`
**范围**: `service/query_last.py` + `service/test_query_last.py`（纯 Python 标准库，mock HTTP）
**版本**: 最新提交
**验证方式**: Python `unittest` 本地运行
**结果**: **13 PASS / 0 FAIL**（+ 原有 24 PASS = 总计 37 PASS）

| # | 测试项 | 结果 | 分类 |
|---|--------|------|------|
| 1 | 成功获取有效观测数据（valid=true，含完整传感器字段） | ✅ PASS | 成功 |
| 2 | 设备无有效记录（valid=false, source=none） | ✅ PASS | 成功-无数据 |
| 3 | HTTP 404 错误 | ✅ PASS | HTTP 错误 |
| 4 | HTTP 500 错误 | ✅ PASS | HTTP 错误 |
| 5 | 连接失败（URLError） | ✅ PASS | 网络错误 |
| 6 | 请求超时（TimeoutError） | ✅ PASS | 超时 |
| 7 | 无效 JSON 响应 | ✅ PASS | 解析错误 |
| 8 | 环境变量未设置（核心函数） | ✅ PASS | 配置缺失 |
| 9 | 环境变量已设置（get_base_url） | ✅ PASS | 配置 |
| 10 | 环境变量末尾斜杠被清理 | ✅ PASS | 配置 |
| 11 | 环境变量未设置（get_base_url） | ✅ PASS | 配置 |
| 12 | 环境变量空字符串 | ✅ PASS | 配置 |
| 13 | 环境变量空白字符串 | ✅ PASS | 配置 |

**设计要点**:
- `ESP32_BASE_URL` 环境变量读取地址，`/api/observation/last` 路径硬编码
- 设备原始字段（valid/source/seq/accel/time_quality/stale 等）原样保留
- 不伪造数据，不接入语言模型，不调用 `trigger_collect`

### ⚠️ 未验证项目

| 项目 | 原因 |
|------|------|
| **实机 HTTP 调用** | 无真实 ESP32 设备连接，所有请求通过 mock 验证 |
| **与 TaskContract 集成** | query_last 工具当前独立，未与 `validator.py` 或语言模型输出连接 |

---

## Week 04 Step 3 — trigger_collect 受限工具 (2026-10-10)

**分支**: `feature/week04-natural-language`
**范围**: `service/trigger_collect.py` + `service/test_trigger_collect.py`（纯 Python 标准库，mock HTTP）
**版本**: 待提交（当前 HEAD）
**验证方式**: Python `unittest` 本地运行（pytest）
**结果**: **12 PASS / 0 FAIL**（+ 原有 37 PASS = 总计 49 PASS）

| # | 测试项 | 结果 | 分类 |
|---|--------|------|------|
| 1 | 完整成功流程：POST → submitted → completed + linked + valid | ✅ PASS | 成功流程 |
| 2 | 设备执行失败（status=failed） | ✅ PASS | 设备失败 |
| 3 | 设备端超时（status=timeout） | ✅ PASS | 设备超时 |
| 4 | 轮询耗尽 max_attempts 仍未完成 | ✅ PASS | 轮询超时 |
| 5 | 任务完成但 linked=false（请求不匹配） | ✅ PASS | 请求不匹配 |
| 6 | 任务完成、请求匹配但 observation.valid=false | ✅ PASS | 观测无效 |
| 7 | 任务完成但响应中缺少 observation 字段 | ✅ PASS | 缺失观测 |
| 8 | POST 请求返回 HTTP 500 错误 | ✅ PASS | HTTP 错误 |
| 9 | POST 成功但轮询返回 HTTP 500 错误 | ✅ PASS | HTTP 错误 |
| 10 | 轮询返回非 JSON 内容 | ✅ PASS | 解析错误 |
| 11 | 环境变量未设置 | ✅ PASS | 配置缺失 |
| 12 | POST 响应缺少 request_id | ✅ PASS | 响应错误 |

**设计要点**:
- 复用 `query_last.get_base_url()` 读取 `ESP32_BASE_URL` 环境变量
- 固定路径 `POST /api/collect` 和 `GET /api/collect/status?request_id=xxx` 硬编码
- 轮询上限 20 次 × 0.5s（默认），可通过参数调整
- 成功条件：`status=="completed"` + `linked==true` + `observation.valid==true`
- 不伪造数据，不接入语言模型，不修改 ESP32 业务代码

---

## Week 04 Step 4 — 受限任务分发模块 (2026-10-10)

**分支**: `feature/week04-natural-language`
**范围**: `service/task_dispatcher.py` + `service/test_task_dispatcher.py`（纯 Python 标准库，mock 工具）
**版本**: 最新提交（65 PASS）
**验证方式**: Python `unittest` 本地运行
**结果**: **16 PASS / 0 FAIL**（+ 原有 49 PASS = 总计 **65 PASS**）

| # | 测试项 | 结果 | 分类 |
|---|--------|------|------|
| 1 | query_last 成功获取有效观测 | ✅ PASS | query_last |
| 2 | query_last 设备无有效记录 | ✅ PASS | query_last |
| 3 | query_last 工具失败 (HTTP 404) | ✅ PASS | query_last |
| 4 | query_last 工具异常 (ConnectionError) | ✅ PASS | query_last |
| 5 | trigger_collect 完整成功流程 | ✅ PASS | trigger_collect |
| 6 | trigger_collect 工具失败 (设备端失败) | ✅ PASS | trigger_collect |
| 7 | trigger_collect 工具异常 (TimeoutError) | ✅ PASS | trigger_collect |
| 8 | clarify 返回澄清问题，不调任何工具 | ✅ PASS | clarify |
| 9 | unsupported 返回拒绝原因，不调任何工具 | ✅ PASS | unsupported |
| 10 | 非法 JSON，不调任何工具 | ✅ PASS | 失败关闭 |
| 11 | 未知意图，不调任何工具 | ✅ PASS | 失败关闭 |
| 12 | query_last 带额外字段 (device_address)，不调工具 | ✅ PASS | 失败关闭 |
| 13 | 缺少必需字段 (confidence/original)，不调工具 | ✅ PASS | 失败关闭 |
| 14 | clarify 缺少 question，不调工具 | ✅ PASS | 失败关闭 |
| 15 | unsupported 缺少 reason，不调工具 | ✅ PASS | 失败关闭 |
| 16 | confidence 越界 (1.5)，不调工具 | ✅ PASS | 失败关闭 |

**设计要点**:
- 单一入口 `dispatch_task(raw_input: str) → DispatchResult`
- 调用 `parse_and_validate()` 校验，四种意图分发
- `clarify` / `unsupported` / 非法 JSON / 校验失败 → 不调用任何设备工具
- 工具异常被捕获并返回 `success=False`，不崩溃
- 纯 Python 标准库，零外部依赖
- 测试均 mock `query_last` / `trigger_collect`，不发起真实 HTTP 请求

---

## Week 04 Step 5 — 工具结果格式化模块 (2026-10-10)

**分支**: `feature/week04-natural-language`
**范围**: `service/result_formatter.py` + `service/test_result_formatter.py`（纯 Python 标准库）
**版本**: 待提交（85 PASS）
**验证方式**: Python `unittest` 本地运行
**结果**: **20 PASS / 0 FAIL**（+ 原有 65 PASS = 总计 **85 PASS**）

| # | 测试项 | 结果 | 分类 |
|---|--------|------|------|
| 1 | query_last 成功有效观测，含相对时间验证 | ✅ PASS | query_last |
| 2 | query_last 无有效记录 (valid=false) | ✅ PASS | query_last |
| 3 | query_last observation=None | ✅ PASS | query_last |
| 4 | query_last 工具失败 (HTTP 404) | ✅ PASS | query_last |
| 5 | trigger_collect 成功有效新观测 | ✅ PASS | trigger_collect |
| 6 | trigger_collect 工具失败 | ✅ PASS | trigger_collect |
| 7 | trigger_collect observation=None | ✅ PASS | trigger_collect |
| 8 | trigger_collect 观测无效 (valid=false) | ✅ PASS | trigger_collect |
| 9 | trigger_collect source=none | ✅ PASS | trigger_collect |
| 10 | clarify 带澄清问题 | ✅ PASS | clarify |
| 11 | clarify 无 question (fallback) | ✅ PASS | clarify |
| 12 | unsupported 带原因 | ✅ PASS | unsupported |
| 13 | unsupported 无 reason (fallback) | ✅ PASS | unsupported |
| 14 | 校验失败 | ✅ PASS | 失败 |
| 15 | 校验失败 error=None | ✅ PASS | 失败 |
| 16 | 未知意图（防御性） | ✅ PASS | 防御 |
| 17 | query_last 缺少加速度字段 | ✅ PASS | 容错 |
| 18 | query_last 部分加速度字段 | ✅ PASS | 容错 |
| 19 | trigger_collect 缺少时间字段 | ✅ PASS | 容错 |
| 20 | query_last 包含按键状态 | ✅ PASS | 容错 |

**设计要点**:
- 单一入口 `format_result(result: DispatchResult) → str`
- 五种场景格式化：查询结果、采集成功、失败/超时、需要澄清、请求不支持
- 采集成功仅当 `collect_observation.valid==True` 且 `source!="none"`
- 设备时间为相对运行时间（ms），不伪装为日历时间
- 字段缺失容错，不崩溃
- 纯 Python 标准库，零外部依赖

---

## Week 04 Step 6 — 本地结构化任务处理入口 (2026-10-10)

**分支**: `feature/week04-natural-language`
**范围**: `service/task_pipeline.py` + `service/test_task_pipeline.py`（纯 Python 标准库，mock 集成测试）
**版本**: 前次 HEAD `f819b26`（当前已暂存待提交）
**验证方式**: Python `unittest` 本地运行
**结果**: **7 PASS / 0 FAIL**（+ 原有 85 PASS = 总计 **92 PASS**）

| # | 测试项 | 结果 | 分类 |
|---|--------|------|------|
| 1 | query_last 成功获取有效观测 | ✅ PASS | 查询 |
| 2 | query_last 无有效记录 | ✅ PASS | 查询 |
| 3 | trigger_collect 采集成功 | ✅ PASS | 采集 |
| 4 | clarify 返回澄清问题 | ✅ PASS | 澄清 |
| 5 | unsupported 返回拒绝原因 | ✅ PASS | 拒绝 |
| 6 | 非法 JSON | ✅ PASS | 非法输入 |
| 7 | 未知意图 | ✅ PASS | 非法输入 |

**设计要点**:
- 单一入口 `run_task(raw_input: str) → str`
- 串联 `dispatch_task()` + `format_result()`，不重复实现逻辑
- 纯 Python 标准库，零外部依赖
- 不接入语言模型，不修改 ESP32 业务代码
- 测试 mock `dispatch_task`，不发起真实 HTTP 请求

### ⚠️ 未验证项目

| 项目 | 原因 |
|------|------|
| **与真实 ESP32 设备集成** | 无真实设备连接，所有工具通过 mock 验证 |
| **与语言模型输出连接** | 当前管道接收结构化 JSON，未接入 LLM |
| **实机 HTTP 调用** | 无真实 ESP32 设备连接 |

---

## Week 04 Step 6 集成验证 — 本地结构化样例链路验证 (2026-10-10)

**分支**: `feature/week04-natural-language`
**范围**: `service/test_task_pipeline_integration.py`（纯 Python 标准库）
**版本**: HEAD `22738b9`（待提交）
**验证方式**: Python `unittest` 本地运行
**测试策略**: 调用真实处理链路（`parse_and_validate → dispatch_task → 真实工具代码 → format_result`），仅 mock HTTP 网络边界
**结果**: **10 PASS / 0 FAIL**（+ 原有 92 PASS = 总计 **102 PASS**）

| # | 测试项 | 结果 | 分类 |
|---|--------|------|------|
| 1 | query_last 有效观测（来源/序号/相对时间保留） | ✅ PASS | 查询 |
| 2 | query_last 无有效记录 | ✅ PASS | 查询 |
| 3 | trigger_collect 采集成功（相对时间保留） | ✅ PASS | 采集 |
| 4 | trigger_collect 设备失败（不反馈成功） | ✅ PASS | 采集 |
| 5 | trigger_collect 设备超时（不反馈成功） | ✅ PASS | 采集 |
| 6 | clarify 返回澄清问题（HTTP 未被调用） | ✅ PASS | 澄清 |
| 7 | unsupported 返回拒绝原因（HTTP 未被调用） | ✅ PASS | 拒绝 |
| 8 | 非法 JSON（校验失败，HTTP 未被调用） | ✅ PASS | 非法输入 |
| 9 | 未知意图（校验失败，HTTP 未被调用） | ✅ PASS | 非法输入 |
| 10 | 额外字段拒绝（校验失败，HTTP 未被调用） | ✅ PASS | 非法输入 |

**设计要点**:
- 使用结构化 JSON 样例作为模型输出的替代输入
- 只 mock `urllib.request.urlopen` 和 `time.sleep`，其余全部走真实代码
- 不 mock 分发器、业务工具和格式化器
- 验证数据来源 (`source`)、序号 (`seq`)、相对时间 (`time_quality: "relative"`) 在完整链路中得以保留
- 非法任务（非法 JSON、未知意图、额外字段）不调用 HTTP 工具
- 采集未完成（失败/超时）不反馈成功

### ⚠️ 仍待补验

| 项目 | 原因 |
|------|------|
| **真实语言模型输出 → 管道输入** | 当前使用结构化 JSON 样例替代 LLM 输出 |
| **真实 ESP32 设备 HTTP 调用** | 无真实设备连接，HTTP 边界通过 mock 模拟 |

---

## Week 04 — Web 仪表盘本地持久化 (2026-10-10)

**分支**: `feature/week04-natural-language`
**版本**: `8ca4677` — feat(web): persist valid observations locally
**验证方式**: Python 单元测试 (102 PASS) + ESP-IDF 编译 (build OK)
**结果**: ✅ **Build PASS / 102 Tests PASS / 0 FAIL**

| # | 测试项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | Python 单元测试回归 (102 tests) | ✅ PASS | 全部通过，未回退 |
| 2 | ESP-IDF `idf.py build` 编译 | ✅ PASS | `week02-sensor-collect.bin` 生成成功 |
| 3 | `git diff --check` | ✅ PASS | 无空白字符错误 |
| 4 | 仅修改 `main/http_server.c` | ✅ PASS | 无无关变更 |

**变更摘要**:
- 仅修改 `main/http_server.c` (5 insertions, 3 deletions)
- `persistObs(d)` — 新增函数：仅在 `valid===true` 且 `source==='live'` 或 `source==='cadence'` 时写入 `localStorage`
- `restoreLatest()` — 新增函数：页面加载时从 `localStorage` 恢复最新有效观测
- `refreshStored()` — 获取成功时调用 `persistObs(d)`
- `renderNewObs()` — 采集完成获取新观测时调用 `persistObs(o)`
- `init()` — 启动时先 `restoreLatest()` 再 `refreshStored()`

**去重策略**: 使用 `record_id` 作去重键（退化 `'s'+seq`）；同键记录覆盖更新；最多保留 50 条，超出后裁剪最旧条目。

**容错策略**: 全部 `try/catch` 包裹；`localStorage` 不可用/超出容量/数据损坏均静默跳过，不阻塞页面正常功能。

---

## Week 04 — CSV 导出功能 (2026-10-10)

**分支**: `feature/week04-natural-language`
**版本**: `HEAD` — feat(web): export persisted observations to CSV
**验证方式**: Python 单元测试 (102 PASS) + ESP-IDF 编译 (build OK)
**结果**: ✅ **Build PASS / 102 Tests PASS / 0 FAIL**

| # | 测试项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | Python 单元测试回归 (102 tests) | ✅ PASS | 全部通过，未回退 |
| 2 | ESP-IDF `idf.py build` 编译 | ✅ PASS | `week02-sensor-collect.bin` 生成成功 |
| 3 | `git diff --check` | ✅ PASS | 无空白字符错误 |
| 4 | 仅修改 `main/http_server.c` | ✅ PASS | 1 文件 +2 行（函数+按钮），无无关变更 |

**变更摘要**:
- 新增 `exportCSV()` JS 函数：从 `localStorage` 读取 `__dashObs_v1__`，过滤 `valid===true` 且 `source==='live'|'cadence'` 的记录，按 `received_ms` 降序排列
- 新增 "📥 导出 CSV" 按钮（`act-btn ghost` 样式），置于"刷新已存数据"与"采集一次"之间
- 列字段：`record_id, request_id, source, seq, accel_x, accel_y, accel_z, button, observed_ms, received_ms, data_age_ms, time_quality`
- CSV 编码：UTF-8 + BOM (`0xFEFF`)，兼容 Excel 中文
- 文件名格式：`esp32_obs_YYYY-MM-DD_hh-mm-ss.csv`
- 字段缺失时留空，不伪造数值
- 无有效数据 / 存储不可用 / 导出失败时通过 `setNote()` 显示清晰提示，不假报成功

**不涉及**: 传感器采集逻辑、现有快照/陈旧标记语义、localStorage 持久化逻辑、新增依赖、CSV 以外的导出格式。

---

## Week 04 — 浏览器持久化人工检查 (2026-10-10)

**分支**: `feature/week04-natural-language`
**版本**: HEAD `5f1fb4a`
**验证方式**: 人工在浏览器开发者工具中操作，观察 localStorage 内容及 CSV 输出
**结果**: **4 PASS / 0 FAIL**

| # | 测试项 | 结果 | 观察记录 |
|---|--------|------|---------|
| 1 | 页面加载后 localStorage 自动恢复最新有效观测 | ✅ PASS | 刷新后最新观测在 `restoreLatest()` 后正确显示 |
| 2 | 多次采集后 localStorage 保留多条记录，不超过 50 条 | ✅ PASS | 记录正确持久化，超出 50 条后最旧条目被裁剪 |
| 3 | CSV 导出文件包含全部有效字段，12 列对齐 | ✅ PASS | 导出的 CSV 含表头与 12 列数据，字段值正确 |
| 4 | localStorage 不可用/数据损坏时页面不崩溃 | ✅ PASS | 删除 `__dashObs_v1__` 后页面正常运行，无 JS 报错 |

## Week 04 — CSV 核验 (2026-10-10)

**验证方式**: 用户提供的 CSV 文件人工核验
**结果**: ✅ **结构正常，29 条记录 / 12 列 / 全部 cadence**

| 检查项 | 结果 |
|--------|------|
| 记录总数 | 29 条 |
| 列数（含表头） | 12 列 |
| 列名完整 | `record_id, request_id, source, seq, accel_x, accel_y, accel_z, button, observed_ms, received_ms, data_age_ms, time_quality` |
| source 分布 | 全部为 `cadence` |
| 时间字段语义 | 相对运行时间（ms），`time_quality: "relative"`，无日历时间伪装 |
| 字段缺失 | 无数据缺失，每行 12 列完整 |
| 核验结论 | ✅ 导出格式规范，数据字段完整，结构无异常 |

---

## Week 05 — 语音输入链路最终实机验证 (2026-10-11)

**分支**: `feature/week05-voice-interaction`
**版本**: 收尾前 HEAD `e44eff3`（Step 3I 验证明细见下方；版本以 `State.md` 与 `README.md` 收尾记录为准）
**验证方式**: 实机（ESP32-S3-EYE COM10 ↔ PC 语音服务 10.132.124.130:8080）双端日志取证
**结果**: ✅ **双端闭环 PASS / 0 FAIL / 无崩溃**

| # | 测试项 | 预期 | 实际 | 结果 |
|---|--------|------|------|------|
| 1 | ESP32 PLAY① → PC `POST /api/voice/start` | HTTP 200 | `10.132.124.63 POST /api/voice/start 200`；ESP32 `PC 已受理：HTTP 200, state=recording` → 本地 RECORDING | ✅ PASS |
| 2 | PC 录音 WAV 落盘 | 真实音频帧 | wav `frames=294400, 18.40 s`（ESP32 回执一致） | ✅ PASS |
| 3 | ESP32 PLAY② → PC `POST /api/voice/stop` | HTTP 200 | `10.132.124.63 POST /api/voice/stop 200`；ESP32 `PC 已受理：HTTP 200, state=idle` | ✅ PASS |
| 4 | ESP32 本地确认状态回 IDLE | IDLE | ESP32 `本地确认状态 IDLE（wav: frames=294400, 18.40 s）` | ✅ PASS |
| 5 | 无截断 / 无传输层超时 / 无栈溢出 / 无 Brownout | 均不出现 | 全程 0 次 `RESPONSE_INVALID` / `ESP_ERR_HTTP_EAGAIN` / `stack overflow` / `BOD` / `Brownout` | ✅ PASS |
| 6 | ASR 自动后台提交（Step 3B 机制） | STOP 后触发 | STOP 200 后服务继续响应；ASR 转写文本证据以 Step 3C/3F/3G 已取证记录为准（本轮日志未保留 asr 响应体） | ✅ 机制已实现（Step 3B 自动测试 58/58） |

**关键证据来源**（仓库外，验后保留目录 `%TEMP%\week05_step3i\`）:
- PC 服务日志 `server.err.log`：`10.132.124.63 POST /api/voice/start 200`（16:10:33）、`10.132.124.63 POST /api/voice/stop 200`（16:10:55）
- ESP32 串口日志 `serial.log`（UTF-8）：`W3-EVENT: btn pressed: PLAY` → `voice-193710-0001 START` → `PC 已受理 HTTP 200 state=recording` → 本地 RECORDING → `voice-213071-0002 STOP` → `PC 已受理 HTTP 200 state=idle` → 本地 IDLE

**对比既往失败项（当日处理链，详见 `weeks/week05/STATE.md`）**:
| 既往失败项 | Step 3I 是否复现 | 说明 |
|-----------|-----------------|------|
| Step 3C stop 响应截断（RESPONSE_INVALID） | ❌ 未复现 | Step 3D 控制/查询解耦后不再超 512B |
| Step 3F `ESP_ERR_HTTP_EAGAIN` 传输层超时 | ❌ 未复现 | 本轮 STOP 在 5s 内收到完整 200（单次样本） |
| Step 3G Brownout 硬件复位 | ❌ 未复现 | 全程无 BOD；供电问题仍留档 |
| Step 3E btn_scan 栈溢出 | ❌ 未复现 | 4096B 栈经 3F/3I 两轮实机均未复现 |

**合规**: 录音 WAV 已清理；仓库内无 WAV、无临时文件；未修改代码/依赖/固件；未读取 `sdkconfig` 与 Wi-Fi 凭据（详见 `weeks/week05/STATE.md` Step 3I 段）。

> ⚠️ **Week 05 未完成能力（如实标注）**：Step 4 LLM NL→JSON、Step 5 TTS 播报、Step 6 全链路串联、Step 7 状态显示与 LED 反馈、Step 8 异常与文字对照测试均未开始。

---

## Week 05 收尾 — 最终测试回归 (2026-10-13)

**分支**: `feature/week05-voice-interaction`
**版本**: HEAD `91959fb` feat(week05): add voice interaction buttons to ESP32 dashboard
**验证方式**: Python 自动测试（pytest，虚拟环境 `D:\AIinteraction-venvs\week05-audio`）
**结果**: **212 PASS / 1 FAIL（历史已知，非本次引入）**

| # | 测试项 | 结果 | 备注 |
|---|--------|------|------|
| 1 | `pytest service/` 全量回归 | **212 passed, 1 failed** | Week 04 + Week 05 全部 Python 测试 |
| 2 | `test_asr_whisper.py` | 18/18 ✅ PASS | 本地 Whisper ASR 模块 |
| 3 | `test_audio_capture.py` | 30/31 PASS | 1 FAIL = 历史已知（见下方） |
| 4 | `test_query_last.py` | 13/13 ✅ PASS | Week 04 查询工具 |
| 5 | `test_result_formatter.py` | 20/20 ✅ PASS | Week 04 格式化器 |
| 6 | `test_task_dispatcher.py` | 16/16 ✅ PASS | Week 04 任务分发 |
| 7 | `test_task_pipeline.py` | 7/7 ✅ PASS | Week 04 任务管道 |
| 8 | `test_task_pipeline_integration.py` | 10/10 ✅ PASS | Week 04 集成测试 |
| 9 | `test_trigger_collect.py` | 12/12 ✅ PASS | Week 04 采集触发器 |
| 10 | `test_validator.py` | 24/24 ✅ PASS | Week 04 schema 校验 |
| 11 | `test_voice_control_server.py` | 62/62 ✅ PASS | Week 05 录音 HTTP 控制 + 后台 ASR |

### 已知历史失败（非本次引入，不修）

| 测试 | 失败模式 | 状态 |
|------|---------|------|
| `test_audio_capture.py::TestStartStopRecording::test_state_transitions_allow_repeated_recordings` | `AssertionError: 2 != 3` — 预期 3 次反复录音各产生独立 WAV 文件，实际仅输出 2 个。模拟音频流 `FakeSoundDevice` 在多轮 start/stop 中 WAV 文件计数偏差。 | **已知，非本次引入**（已存在于 `.pytest_cache/v/cache/lastfailed`） |

### 固件编译（未重编，以历史记录为准）

| 项 | 结果 | 备注 |
|----|------|------|
| `idf.py build`（ESP-IDF v5.4.3） | ✅ 历史 PASS（Step 2B `37773bf`） | 0 warning / 0 error |
| 产物名称 | `week02-sensor-collect.bin` | **Week 02 遗留项目名**，仅影响固件文件名，无功能影响 |
| 烧录与实机验证 | ✅ 历史 PASS（Step 3I `7dc2d08`） | 双端闭环验证通过 |

### ⚠️ 待次日人工验收（不属本次收尾范围）

1. **真实中文语音识别准确率**：以真人普通话口语输入（非预录音、非静音），人工判断 Whisper `base/zh` 转写结果是否语义正确
2. **简体中文输出质量**：ASR 转写文本的编码（UTF-8）与简体中文内容，在浏览器仪表盘 `🎙 语音` 面板中是否正确显示
3. 验收标准：**不得**把静音导致的空转写记录为识别成功；**不得**在人工审阅前宣称识别准确率已通过
## Week 06 — 任务上下文、取消与澄清 (2026-10-13)

**分支**: eature/week06-voice-task-context
**版本**: HEAD 54857b9
**验证方式**: pytest (venv D:\AIinteraction-venvs\week05-audio)
**结果**: **74 PASS / 0 FAIL（语音控制）；224 PASS / 1 FAIL（全量，1 历史已知）**

| # | 测试类 | 结果 |
|---|--------|------|
| 1 | Week06TaskContextTest (4) | ✅ 4/4 PASS |
| 2 | Week06CancelTest (4) | ✅ 4/4 PASS |
| 3 | Week06StatusCompatibilityTest (2) | ✅ 2/2 PASS |
| 4 | Week06ClarifyTest (2) | ✅ 2/2 PASS |
| 5 | Week 05 回归 (62) | ✅ 62/62 PASS |
| 6 | 全量 service | ✅ 224/225 PASS |

### 新增 API
| 端点 | 方法 | 状态 |
|------|------|------|
| /api/voice/cancel | POST | ✅ 取消+停止录音+幂等 |
| /api/voice/clarify | POST | ✅ CLARIFYING 接受+空拒绝 |
| /api/voice/status | GET | ✅ 新增 task 块 |

### 未验证
- ESP32 仪表盘取消按钮（需固件重编译）→ 已于 `6e14622` 实现并实机验证（见下方「Week 06 收尾」）
- 真实语音交互闭环 → 取消联动已实机验证（见下方「Week 06 收尾」）；ASR 内容验收待补
- TTS 停止播报（无 TTS 实现）
---

## Week 06 收尾 — 最终验证 (2026-10-14)

**分支**: `feature/week06-voice-task-context`
**版本**: HEAD `6e14622` — feat(week06): add voice task cancel button and status display to ESP32 dashboard
**验证方式**: pytest 自动化 + HTTP 接口验证 + ESP32 实机
**结果**: 自动化 **78/78**（voice server）/ **229/229**（全量）；接口生命周期 ✓；实机取消联动 ✓

### A. 自动化测试（pytest，虚拟环境 `D:\AIinteraction-venvs\week05-audio`）

| # | 测试 | 结果 |
|---|------|------|
| 1 | `test_voice_control_server.py`（任务上下文/取消/澄清/迟到隔离/兼容性回归） | ✅ **78/78 PASS** |
| 2 | `pytest service/` 全量回归 | ✅ **229/229 PASS** |

### B. 接口测试（HTTP API 生命周期验证）

| # | 验证项 | 输入 | 预期 | 实际 | 结果 |
|---|--------|------|------|------|------|
| 1 | 任务创建 | `POST /api/voice/start` | 返回唯一 `request_id` | `voice-…-0004` | ✅ PASS |
| 2 | 录音状态 | `GET /api/voice/status` | `state=recording` | `state=recording` | ✅ PASS |
| 3 | 取消任务 | `POST /api/voice/cancel` | 停止录音 + 记录取消 | `cancelled_recording=true` | ✅ PASS |
| 4 | 取消后状态 | `GET /api/voice/status` | `state=cancelled`、generation 递增 | `state=cancelled`，generation 7→8 | ✅ PASS |
| 5 | 迟到结果隔离 | 取消后旧代次结果返回 | 记录 `result_stale_discarded`，不覆盖新状态 | ✅（自动测试覆盖） | ✅ PASS |

### C. 硬件测试（ESP32 实机，2026-10-14）

| # | 验证项 | 结果 | 证据 |
|---|--------|------|------|
| 1 | 固件烧录 | ✅ PASS | ESP-IDF `idf.py flash` 成功 |
| 2 | 实际 IP | ✅ PASS | `10.28.x.x`（已脱敏） |
| 3 | 仪表盘 | ✅ PASS | HTTP 200 |
| 4 | 新增取消按钮页面 | ✅ PASS | 页面实测可用 |
| 5 | **实机取消联动**（ESP32/dashboard → PC service） | ✅ **PASS** | 见下方闭环记录 |

**实机取消闭环证据**（request_id=`voice-09188851-0004`）：

| 阶段 | 证据 |
|------|------|
| start | `state=recording`（PC voice service `10.28.x.x:8080`，已脱敏） |
| cancel | `cancelled_recording=true` |
| result | `state=cancelled`，generation **7 → 8** |

状态链：`recording → cancel → cancelled`；generation `7 → 8`。

### 未验收（如实记录，不得描述为失败）

| 项 | 状态 | 原因 |
|----|------|------|
| 中文 ASR 准确率 | ⏳ 未完成验收 | 用户当前不方便进行语音输入 |
| TTS 停止播报 | ⛔ 不适用 | 项目无 TTS 实现，不存在可中断的音频播报 |

---

## Week 06 — 真实语音任务闭环诊断 (2026-10-16)

**分支**: `feature/week06-voice-task-context`
**版本**: HEAD `28aa45c` — docs(week06): update final status and validation records
**验证方式**: 全链路代码审查 + 环境实时探测
**结果**: ⛔ **BLOCKED** — NL→JSON 转换模块缺失，ASR 文本无法接入任务管道

### 环境实时证据

| 项 | 状态 | 证据 |
|----|------|------|
| Git 分支 | `feature/week06-voice-task-context` | HEAD `28aa45c`，工作区干净 |
| ESP32 串口 | COM10 | `USB\VID_303A&PID_1001`（ESP32-S3 JTAG） |
| ESP32 HTTP | ✅ 在线 | `10.28.x.x:80`（已脱敏），`/api/sensor` 返回有效数据（`accel_x=856, accel_y=-133, accel_z=539`，`obs-04016`，`uptime≈4087s`） |
| PC 语音服务 | ❌ 未运行 | 端口 8080 被 PlatformIO MCP Dashboard（`node.exe` PID 66888）占用；无 Python `voice_control_server` 进程 |
| Python 虚拟环境 | ✅ 存在 | `D:\AIinteraction-venvs\week05-audio\Scripts\python.exe` |

### 全链路诊断

| # | 环节 | 代码位置 | 状态 | 说明 |
|---|------|----------|------|------|
| 1 | ESP32 PLAY → HTTP start | `main/voice_button.c` → `POST /api/voice/start` | ✅ | 固件已烧录、ESP32 网络可达 |
| 2 | PC 录音采集 | `service/audio_capture.py` | ✅ | sounddevice 16kHz/1ch/int16 → WAV，既往真实验证通过 |
| 3 | ESP32 PLAY → HTTP stop | `main/voice_button.c` → `POST /api/voice/stop` | ✅ | 既往 Step 3I 双端闭环通过 |
| 4 | 后台 Whisper ASR | `service/asr_whisper.py` `base/zh/transcribe/cpu` | ✅ | 既往真实中文语音识别通过（纯推理 0.68s） |
| 5 | ASR 回调 | `voice_control_server.py:745` `_on_asr_done()` | ✅ | 代次校验、取消状态检查、调用 `_process_task()` |
| 6 | **ASR 文本 → TaskContract JSON** | **缺失** `service/nl_to_task.py` | ❌ **BLOCKED** | Week 05 ARCHITECTURE.md 规划但从未实现（标记 `⏳ 待确认: 语言模型选型与授权`） |
| 7 | 任务处理管道 | `voice_control_server.py:778` `task_pipeline.run_task(t)` | ⚠️ 不可达 | 传入原始中文文本，`parse_and_validate()` 要求 JSON → 校验失败 |
| 8 | 意图分发 | `service/task_dispatcher.py:163` `dispatch_task()` | ⚠️ 不可达 | 四种意图（query_last/trigger_collect/clarify/unsupported），因上述断裂无法到达 |
| 9 | query_last / trigger_collect → ESP32 | `service/query_last.py` `service/trigger_collect.py` | ⚠️ 不可达 | ESP32 HTTP API 经实时探测可达，但工具未被调用 |

### 断裂点详情

**位置**: `service/voice_control_server.py` 第 776-778 行

```python
from . import task_pipeline
result = task_pipeline.run_task(t)   # t = 原始 ASR 中文文本
```

**原因**: `task_pipeline.run_task()` → `dispatch_task()` → `parse_and_validate()` 要求输入为结构化 JSON：

```json
{"intent": "query_last", "confidence": 0.95, "original": "查看最近的加速度数据"}
```

但 `t` 是原始中文文本（如 Whisper 转写的 `"查看最近的加速度数据"`），不是合法 JSON → 校验必然失败 → 任务进入 FAILED。

**缺失模块**: `service/nl_to_task.py`（设计于 `weeks/week05/ARCHITECTURE.md` 第 430 行），功能定义为：
- 输入：ASR 中文文本
- 输出：`{"intent": "...", "confidence": 0.0-1.0, "original": "..."}` JSON
- 验收标准：`"查看最近数据"` → `query_last` / `"重新采集"` → `trigger_collect` / `"你好"` → `unsupported` / 含糊 → `clarify`
- 状态：`⏳ 待确认: 语言模型选型与授权`（从未进入实现阶段）

### 附带代码缺陷

`voice_control_server.py` 第 789-796 行存在类型不匹配：

```python
# run_task() 返回 str，但后续代码当作 dict 处理
self._task_result = result if isinstance(result, dict) else {"ok": False, ...}
if not result.get("ok") and result.get("intent") == "clarify":  # ← str 无 .get()
```

`run_task()` 返回 `str`（用户可读文本），`isinstance(result, dict)` 为 `False`，`self._task_result` 被正确设置为错误对象。但紧接着 `result.get("ok")` 对 `str` 调用会抛出 `AttributeError`，被 Flask 500 handler 捕获。此缺陷不影响本次诊断结论，但会在 NL→JSON 缺失补上后成为功能 bug。

> 后续提交 `6ff8086` 已将 `run_task()` 返回值统一归一化为 dict（`normalized`），此缺陷已修复；本节保留 `28aa45c` 时点的诊断原文。

### 接口契约速查

| 模块 | 入口 | 输入要求 | 输出 |
|------|------|----------|------|
| `task_pipeline.run_task()` | `raw_input: str` | 结构化 JSON 字符串（`{"intent":"...","confidence":0.0-1.0,"original":"..."}`） | 用户可读文本 `str` |
| `dispatch_task()` | `raw_input: str` | 同上 JSON | `DispatchResult(success, intent, observation, ...)` |
| `parse_and_validate()` | `raw: str` | 合法 JSON + 四种已知意图 + 字段白名单 | `ValidationResult(valid, contract, errors)` |
| `_process_task()` | `text: str` | ASR 中文文本（raw） | 无返回值；副作用：设置 `_task_result` + 推进状态机 |

### 前置条件（NL→JSON 补全）

1. **运行时语言模型授权**：需获得课程教师/助教确认可用的 LLM 服务（API Key 或本地模型），不得将 Cline 开发时使用的模型直接作为产品运行时模型
2. **Prompt 工程**：将 ASR 中文文本映射到四种意图 + 置信度，正确处理含糊（→clarify）和越界（→unsupported）
3. **输出校验**：LLM 输出须通过 `parse_and_validate()` 二次校验后方可进入 `dispatch_task()`
4. **敏感信息隔离**：LLM API Key 不得写入仓库、不得出现在日志中

### 结论

- ⛔ **真实语音任务闭环：BLOCKED**，阻塞原因是 `service/nl_to_task.py` 缺失，ASR 文本无法转换为 TaskContract JSON
- 此缺口是 Week 04/05 任务处理链路的前置依赖，不在 Week 07 范畴
- 已核实链路：ESP32 在线、HTTP API 可达、ASR 模块可用、任务管道待 NL→JSON 后串联
- 本轮未修改源码、未安装依赖、未访问外部模型、未读取敏感信息

---

## Week 06 最终收尾 — 实机取消与反馈停止时延 (2026-10-17)

**分支**: `feature/week06-voice-task-context`
**版本**: HEAD `66ecbb6` — feat(week06): add cancel-to-feedback-stop latency metric and audit three curriculum timings
**验证方式**: ESP32 实机 PLAY 按键 + 浏览器仪表盘取消 + PC voice service HTTP/status + pytest
**结果**: 实机取消链路 **PASS**；自动化 voice server **99/99**；全量 `pytest service/` **250 passed + 6 subtests**（0 失败）

### A. 实机取消链路（ESP32 ↔ PC voice service）

| # | 验证项 | 输入/操作 | 预期 | 实际 | 结果 |
|---|--------|-----------|------|------|------|
| 1 | 实体按键启动任务 | 按下 ESP32 PLAY | 创建新任务并进入录音 | ESP32 串口日志与 PC 服务端日志相互印证，`state=recording` | ✅ PASS |
| 2 | Web 取消任务 | 仪表盘点击「取消任务」 | `POST /api/voice/cancel` 成功 | HTTP **200**，`cancelled_recording=true` | ✅ PASS |
| 3 | 状态链 | 取消前后查询 `GET /api/voice/status` | `recording → cancelling → cancelled` | 与预期一致 | ✅ PASS |
| 4 | 代次递增 | 同一取消操作 | generation 单调递增 | **5 → 6** | ✅ PASS |
| 5 | 取消→反馈停止时延 | 同一次取消 | `task.timing.latency_cancel_to_feedback_stop_ms` 可读 | **79 ms**（与 generation 6 的取消任务同属一个 `task` 块） | ✅ PASS |
| 6 | 服务端/浏览器一致性 | 取消后查看仪表盘 | 服务端 `task.state=cancelled` 与页面显示一致 | 一致 | ✅ PASS |
| 7 | 迟到结果隔离 | 取消后旧代次结果返回 | 记录 `result_stale_discarded`，不覆盖取消状态 | 自动测试覆盖（本次未注入真实迟到结果） | ✅ PASS（测试） |

**关联说明**：`voice_control_server.py` 在同一任务上以 `time.monotonic()` 记录 `cancel()` 入口 → `CANCELLED` 状态迁移，结果写入 `GET /api/voice/status` 的 `task.timing`，并与同一 `task` 块中的 `request_id` / `generation=6` 绑定。本次现场记录保留的是 generation `5 → 6`；`request_id` 本次未单独留存。

> 2026-10-14 的另一次实机取消记录为 generation `7 → 8`（见上节）；与本次 `5 → 6` 属不同会话/任务，不矛盾。

**口径说明（必须保留）**：
- 79 ms 是**本次单次实测值**，不代表所有场景的固定性能；设备、网络与负载变化都会影响结果。
- 项目**没有 TTS**，该指标度量的是「取消 → 任务进入 `cancelled` / 仪表盘停止显示录音反馈」的时延，**不得称为「语音播报停止时延」**。
- 本项只证明取消与反馈停止链路；**不证明**完整 ASR 内容识别或自然语言转任务链路已验收。

### B. 自动化测试（本次收尾实际运行）

命令（虚拟环境 `D:\\AIinteraction-venvs\\week05-audio`）：

```powershell
python -m pytest service/test_voice_control_server.py -q
python -m pytest service/ -q
```

| 测试范围 | 实际结果 |
|----------|----------|
| `service/test_voice_control_server.py` | ✅ **99 passed** |
| `service/` 全量 | ✅ **250 passed, 6 subtests passed** |

> 本次全量运行 0 失败；此前记录的 `test_audio_capture` 毫秒级文件名碰撞 flake 本次未复现。

### C. 与课程 Week 06 要求的对应

| 课程要求（Week 06 周任务卡） | 当前证据 | 状态 |
|------------------------------|----------|------|
| 有限多轮任务上下文 / request_id 关联 | request_id 全链路 + `task.log` | ✅ 自动测试 |
| 任务取消、停止与回到空闲 | 实机取消 + HTTP 200 + `recording→cancelling→cancelled` | ✅ 实机 |
| 取消后迟到结果不重新播放/触发动作 | 代次隔离 + `result_stale_discarded` | ✅ 自动测试 |
| 新任务不被旧任务结果覆盖 | generation 隔离 + 跨任务计时清除测试 | ✅ 自动测试 |
| 连续两次查询 / 慢响应 | `Week06SlowAndLateResultAcceptanceTest` | ✅ 自动测试 |
| 测量输入结束→首次反馈、输入结束→完成 | 课程三项计时 + `task.timing` + 14 项计时验收 | ✅ 自动测试 |
| 澄清 / 重新输入 | `/api/voice/clarify` + 状态机 | ✅ 自动测试 |
| 真实语音输入 + ASR 内容 | 用户当前不方便语音输入 | ⏳ 待验收 |
| ASR 文本 → 任务 JSON（NL→JSON） | `service/nl_to_task.py` 缺失 | ⛔ 未实现，完整任务链路 BLOCKED |
| TTS 停止播报 | 项目无 TTS | ⛔ 不适用 |

### D. 结论

- 本周「取消 / 澄清 / 迟到结果隔离 / 计时指标 / 仪表盘状态显示」均有代码、自动测试或实机证据。
- **完整语音任务链路（真实 ASR 文本 → NL→JSON → 任务管道）仍未端到端验收**，阻塞点为 `service/nl_to_task.py` 缺失（见上一节 2026-10-16 诊断）。
- 本轮未修改生产代码、未重新烧录/复位设备、未触发真实采集或要求用户说话。
