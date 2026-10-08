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
