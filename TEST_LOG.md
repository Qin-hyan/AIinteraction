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

## Week 03 — 2026-10-09 (部分验证)

**分支**: `feature/week03-physical-feedback`  
**版本**: `week02-stable-11-g63ab504`  
**验证方式**: 实机（COM10）  
**结果**: 4 PASS / 1 FAIL / 4 ⛔ 未完成  

### 基础流程验证

| # | 测试项 | 结果 | 备注 |
|---|--------|------|------|
| 1 | build 编译通过 | ✅ PASS | `idf.py build` 无错误 |
| 2 | flash 烧录成功 | ✅ PASS | COM10 460800 baud |
| 3 | 按键检测（PLAY）单次触发 | ✅ PASS | `W3-EVENT: btn pressed: PLAY`，仅 1 次 |
| 4 | 事件创建 + ID 生成 | ✅ PASS | `id=help-15919-0001` |
| 5 | HTTP 发送 + API 可用 | ✅ PASS | `W3-EVENT: sent` + `[W3-NET] available via API` |
| 6 | LED 物理闪烁（实机观察） | ⛔ 未完成 | Brownout 中断 |
| 7 | 长按不重复触发 | ⛔ 未完成 | Brownout 中断 |
| 8 | 快速点击无异常重复 | ⛔ 未完成 | Brownout 中断 |
| 9 | Brownout 稳定性 | ❌ **FAIL** | `E BOD: Brownout detector was triggered` → 重启 |

### 关键证据

```
I (16763) W3-EVENT: btn pressed: PLAY
I (16763) W3-EVENT: created id=help-15919-0001 btn=PLAY counter=1 (LOCAL CONFIRMED)
I (16763) MAIN: [W3-BTN] pressed PLAY → event help-15919-0001
I (16768) W3-EVENT: sending id=help-15919-0001
I (16772) W3-EVENT: sent id=help-15919-0001
I (16776) MAIN: [W3-NET] event help-15919-0001 available via API
E BOD: Brownout detector was triggered                ← ⚠️
```

### 已知问题

- **Brownout 检测触发**: 按键事件流程完成后约 1s 设备掉电重启。需排查供电 / USB 线缆 / `CONFIG_BROWNOUT_DET_LVL` 配置。

### 待完成

- [ ] 解决 Brownout 问题后重新验证完整链路
- [ ] 远端确认/取消 API 测试
- [ ] 超时自动重置（30s）
- [ ] Week 01-02 API 回归
