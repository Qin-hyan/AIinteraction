# 语音服务健康检查与命令链路（Week 04–06 补强）

**日期**：2026-10-22
**分支**：`feature/voice-health-command`（基于 Week 09 分支）

---

## 1. 这次修复的两个真实问题

1. **语音面板连不上**：`CONFIG_VOICE_PC_URL` 里保存的是旧 PC 局域网 IP；PC 的 DHCP 地址从 `10.28.x.x` 变为 `10.244.x.x` 后，浏览器面板仍在请求旧地址。
   - 修复：新增 `CONFIG_VOICE_BROWSER_URL`（默认 `http://127.0.0.1:8080`），只供浏览器面板使用；
   - ESP32 实体 PLAY 键仍使用 `CONFIG_VOICE_PC_URL`（设备 → PC 局域网）；
   - 语音服务 CORS 改为有边界回显回环/私网 Origin，并支持 PNA，浏览器从设备页面请求 `127.0.0.1` 可通过预检。
2. **语音命令执行失败**：`GET /api/voice/status` 能拿到 ASR 文本，但 `_process_task()` 把原始中文文本直接交给 `task_pipeline.run_task()`，后者要求 JSON，校验必然失败。
   - 修复：新增 `service/nl_to_task.py`，把 ASR 文本确定性映射为 TaskContract JSON；
   - `task_pipeline.run_task()` 返回字符串；语音服务用已分类 intent 恢复成功/澄清/拒绝，不再一律判 `invalid_result`。
   - 发现 `trigger_collect` 在设备完成后回到 `status=idle`、但 `linked=true` 且观测有效时误报轮询超时；已按 `linked + valid observation` 识别完成。

---

## 2. 语音服务健康检查

`GET /api/voice/health`（PC 本地 8080）返回：

```json
{
  "ok": true,
  "service": "voice_control",
  "state": "idle",
  "asr": {"status":"idle","model":"base","language":"zh","task":"transcribe","device":"cpu","last_error":null},
  "audio": {"samplerate":16000,"channels":1,"dtype":"int16","device":null},
  "pipeline": {"nl_to_task":true,"task_pipeline":true,"tts":false}
}
```

Dashboard 语音面板新增「🩺 检查服务」：

- 显示本地服务已连接/未连接；
- 显示 ASR 模型/语言；
- 显示采样率/声道；
- 明确显示 `NL→JSON 已接入`、`TTS 未实现`；
- 失败时提示启动器命令，不伪装成功。

---

## 3. 命令映射（确定性，不是大模型）

| 输入示例 | 意图 | 行为 |
|---|---|---|
| 查看上次数据 / 最近的观测值 / 查看上次采集的数据 | `query_last` | 读取设备 `/api/observation/last`，保留来源/序号/相对时间 |
| 重新采集一次 / 采集一次最新数据 / 采集 | `trigger_collect` | POST `/api/collect` 并轮询，只有关联新观测才算成功 |
| 取消 / 停止任务 | `clarify` | 不执行设备命令，提示使用界面「取消任务」按钮 |
| 打开灯 / 播放音乐 | `unsupported` | 拒绝，说明只支持查询/采集白名单 |
| 无法识别 | `clarify` | 追问，不猜测 |

> 这不是 Week 04 原文所说的“接入授权语言服务/大模型语义解析”，而是本地确定性映射；真实 LLM 接入仍未完成，报告中不得混淆。

---

## 4. ASR 模型

- 默认 `openai-whisper base`，固定 `zh`，CPU，16 kHz 单声道。
- 新增 CLI：`--asr-model base|small|...`、`--asr-language zh`；健康接口会显示实际模型。
- 本地已缓存 `base.pt`（约 145 MB）与 `small.pt`（约 484 MB）。
- **单句实测（2026-10-22，用户真人语音，非基准）**：
  - `base` 把“重新采集一次”识别成“同心踩击一次”，任务进入 clarify；
  - `small` 正确识别“重新采集一次”，任务 completed，真实采集 seq #61 / `req-63048-0002`；
  - 用户反馈 `small` 也需要较标准发音，第一遍未识别对、第二遍才成功；不能据此宣称整体识别率。
- 当前本地启动器配置使用 `small`（`service/launcher.config.json`，gitignored）；仓库默认仍为 `base`，避免强制下载。

---

## 5. 验证边界

**能证明**：

- 健康接口与面板检查可用；
- 用注入 ASR 文本（`查看上次数据`）经 `nl_to_task → task_pipeline → query_last` 拿到真实设备观测；
- 用注入 ASR 文本（`重新采集一次`）经 `trigger_collect` 完成真实设备采集；
- `trigger_collect` 对 `idle + linked + valid` 不再误报超时；
- 浏览器从设备页面请求 `127.0.0.1:8080` 的 CORS/PNA 预检通过；
- **真人语音**：`查看上次数据` 完成真实查询（seq #1070）；`重新采集一次`（small）完成真实采集（seq #61 / `req-63048-0002`）。

**不能证明**：

- 单人单句之外的识别质量；base 与 small 的系统化 CER 对比；
- 安静/嘈杂环境、不同口音、不同距离下的稳定性；
- TTS 播报（项目无实现）；
- 大模型语义理解（当前是确定性关键词映射）；
- 服务重启后登录自启动触发（沿用 Week 08 未验证项）。
