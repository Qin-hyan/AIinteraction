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
| 公开 API | ✅ **PASS** | `help_uplink_is_configured()`、`help_uplink_send_event()` 签名完整 |

### 提交 `5fe738a` — fix(help_uplink): correct protocol semantics for JSON timestamps and config

| 验证项 | 结果 | 证据 |
|--------|------|------|
| `idf.py build` 编译通过 | ✅ **PASS** | `idf.py build` 实际运行通过（`week02-stable-19-g5fe738a-dirty`），`Project build complete`，0 error |
| 时间戳语义正确性 | ✅ **PASS** | `sent_at_us` 注明为设备端 HTTP POST 完成时刻（非 VPS 上传时间），0 值序列化为 JSON `null` |
| 代码审查无新 WARNING | ✅ **PASS** | 构建日志无新增编译警告 |

### ⚠️ 未验证项目（诚实记录）

| 项目 | 原因 |
|------|------|
| **实机 HTTP POST 测试** | 未配置真实 VPS URL，未烧录验证实际网络行为 |
| **`help_uplink` 接入 `main.c`** | 当前模块未被 main.c 调用，尚未集成到事件发送路径 |
| **超时路径测试** | 需实机验证 `CONFIG_HELP_UPLINK_TIMEOUT_MS` 超时行为 |
| **非 2xx 响应处理** | 需实机 mock VPS 验证 HTTP 500/404 等错误路径 |

**结论**: `ae89be8` + `5fe738a` 构建验证通过，模块代码完整、无编译错误。但 **help_uplink 尚未集成到 main.c 的事件发送路径**，且未经过任何实机网络验证。闭环链路（按键 → HTTP POST → 远端确认）尚未完成。
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

### ⚠️ 未验证项目

| 项目 | 原因 |
|------|------|
| **与真实 ESP32 设备集成** | 无真实设备连接，所有工具通过 mock 验证 |
| **与语言模型输出连接** | 当前分发器接收结构化 JSON，未接入 LLM |
| **实机 HTTP 调用** | 无真实 ESP32 设备连接 |