# PC 服务启动器（Week 08 收尾修复）

**日期**：2026-10-22
**入口**：`service/service_launcher.py`
**脚本**：`scripts/start_services.ps1`、`scripts/start_services.cmd`、`scripts/install_autostart.ps1`、`scripts/uninstall_autostart.ps1`
**默认监听**：`127.0.0.1:8079`（仅回环）

---

## 1. 要解决的问题

语音服务（`service/voice_control_server.py`，8080）和视觉反馈服务（`service/vision_feedback_server.py`，8090）原本都要分别打开终端手动启动。用户打开 ESP32 仪表盘时，页面无法自行拉起电脑进程，于是每次都要"先开两个窗口，再开网页"。

浏览器出于安全限制**不能**直接启动本机进程，因此不采用页面直接执行命令的方案，而是增加一个本地启动器：

1. 启动器随 Windows 登录自动运行（可选，一次性安装）；
2. Dashboard 打开时调用启动器的幂等 `ensure` 接口；
3. 启动器先做健康检查，未运行才启动；已运行（包括外部终端启动）保持不动；
4. 页面显示两个服务的真实状态、端口、PID 与日志路径。

这样"打开网页"就会触发服务就绪，而不是在页面里伪造"已启动"。

---

## 2. 运行结构

```
ESP32 Dashboard (http://<device>/)
  │  POST http://127.0.0.1:8079/api/services/ensure   (JSON, 幂等)
  ▼
service_launcher.py  (127.0.0.1:8079)
  ├─ 健康检查 GET /api/voice/status   → 127.0.0.1:8080
  └─ 健康检查 GET /api/vision/health  → 127.0.0.1:8090
        │ 仅在未运行时 Popen 固定命令
        ├─ python -m service.voice_control_server  --host <cfg> --port 8080
        └─ python -m service.vision_feedback_server --host 127.0.0.1 --port 8090
```

- 启动器只负责进程生命周期，**不改**语音/视觉服务的 API、模型、存储格式。
- 子进程日志写入 `.workbuddy/logs/voice-8080.log` / `vision-8090.log`（gitignored）。
- 视觉服务默认仍是 `127.0.0.1:8090`；语音服务是否监听 `0.0.0.0` 由本地配置决定。

---

## 3. HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 本地控制台页面（状态、日志路径、启动/重启按钮） |
| GET | `/api/launcher/health` | 启动器自身健康检查 |
| GET | `/api/services` | 两个服务的状态数组 |
| GET | `/api/services/<name>` | 单个服务状态（`voice` / `vision`） |
| POST | `/api/services/ensure` | 幂等启动；body 可传 `{"services":["voice"]}`，默认全部 |
| POST | `/api/services/<name>/start` | 启动指定服务 |
| POST | `/api/services/<name>/stop` | 停止**由本启动器启动**的服务 |
| POST | `/api/services/<name>/restart` | 重启**由本启动器启动**的服务 |

**状态值**（`state`）：

| 值 | 含义 |
|---|---|
| `running` | 健康检查通过，且由本启动器启动 |
| `running_external` | 健康检查通过，但由其他终端启动；启动器不会停止它 |
| `starting` | 进程已启动，等待健康检查 |
| `stopped` | 未运行 |
| `failed` | 进程退出或启动超时（`detail` 含日志尾部） |
| `port_conflict` | 端口被其他进程占用且健康检查不通过；不启动、不误杀 |
| `missing_python` | 配置的 Python 解释器不存在 |
| `unhealthy` | 端口被占用但健康检查失败（状态查询时） |

`POST /api/services/ensure` 在两个服务都就绪时返回 200，否则返回 503；响应体始终包含逐个服务的真实状态。

---

## 4. 配置

复制示例并按本机修改：

```powershell
Copy-Item service\launcher.config.example.json service\launcher.config.json
```

```json
{
  "host": "127.0.0.1",
  "port": 8079,
  "services": {
    "voice": {
      "python": "<语音虚拟环境>\\Scripts\\python.exe",
      "host": "0.0.0.0",
      "port": 8080,
      "args": []
    },
    "vision": {
      "python": "<视觉虚拟环境>\\Scripts\\python.exe",
      "host": "127.0.0.1",
      "port": 8090,
      "args": ["--model-path", "<本地>\\dbcnn_model.onnx"]
    }
  }
}
```

- `service/launcher.config.json` 已被 `.gitignore` 忽略，**不得提交**本机绝对路径。
- 顶层 `host` / `port` 是启动器自己的监听地址（`--host` / `--port` 显式参数优先）；修改 `port` 后需同步固件 `CONFIG_PC_LAUNCHER_URL`。
- 不配置时使用安全默认值：两个服务都用当前 Python、语音 `127.0.0.1`、视觉 `127.0.0.1`，启动器 `127.0.0.1:8079`。
- 启动器默认在启动时预热两个服务（`--no-ensure-on-start` 可关闭）；登录自启动会连带启动语音/视觉，这是"打开页面即可用"的实现方式。
- 语音若要让 ESP32 实体 PLAY 键或局域网浏览器访问，需 `"host": "0.0.0.0"`；这会暴露到局域网且服务无认证，只在可信网络使用。
- 视觉模型路径也可由 `VISION_DBCNN_MODEL_PATH` 环境变量提供；设备地址由 `VISION_DEVICE_BASE_URL` 显式提供，启动器不猜 IP。

---

## 5. 安全边界

1. **启动器只能绑定回环地址**（`127.0.0.1` / `localhost`），`--host 0.0.0.0` 会被拒绝；否则局域网内任何人都能直接调用启动/停止 API。
2. POST 必须是 `application/json`；浏览器 `Origin` 只允许回环/私网/链路本地地址，绝不返回 `Access-Control-Allow-Origin: *`。
3. 支持 Private Network Access 预检：仅当请求带 `Access-Control-Request-Private-Network: true` 时返回 `Access-Control-Allow-Private-Network: true`。
4. HTTP 请求不能指定命令、模块或任意参数；命令只来自本地配置的固定服务规格。
5. 请求体上限 8 KiB；未知服务名返回 404；外部启动的服务拒绝 stop/restart（409）。
6. 日志只写本地 `.workbuddy/logs/`；不读取、不记录凭据。
7. 跨源响应只返回日志**文件名**，不返回绝对路径或日志尾部；完整详情只在本机回环控制台可见。
8. 允许私网来源是**为了 ESP32 Dashboard 页面本身**：`ensure/start` 对私网来源开放，`stop/restart` 只接受本机回环来源。因此同一局域网内被攻陷/恶意的网页最多能让本机服务启动，不能停止/重启、执行任意命令或读取凭据；本机任意进程（无 Origin）可调用 stop/restart。启动器无认证，不要在不可信网络中运行。

---

## 6. 登录自启动

```powershell
# 安装：写入当前用户 Startup 文件夹，无需管理员
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_autostart.ps1

# 卸载
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall_autostart.ps1
```

安装内容是一个 Unicode `.lnk` 快捷方式，调用 `scripts/start_services.ps1`；不创建 Windows 服务、计划任务或注册表项。使用快捷方式而不是批处理文本，是为了让仓库路径含中文等非 ASCII 字符时仍然有效。启动器已在运行时脚本直接退出（幂等）。

---

## 7. 验证与边界

**已验证（2026-10-22）**：

- `service/test_service_launcher.py`：31 项，覆盖幂等启动、外部服务、停止/重启、端口冲突、解释器缺失/启动失败详情、启动超时清理、停止后端口复核、监听/探测地址一致、功能受限识别、HTTP/CORS/PNA、私网来源 stop/restart 拒绝、跨源状态脱敏、配置校验；
- 真实进程：启动器成功启动语音（8080）和视觉（8090），健康检查通过；`taskkill /T` 能停止虚拟环境父/子进程并释放端口；
- 真实设备 Dashboard（`http://<device-ip>/`）+ Chrome Headless：页面加载后 `POST /api/services/ensure` 返回 200，运行环境面板显示"服务就绪"；
- Dashboard 导航顺序与页面卡片一致，点击/滚动高亮在桌面与 390px 视口下通过。

**不能证明**：

- 未在"用户注销→重新登录"后自动验证 Startup 条目实际触发（安装与文件存在已验证；登录触发需下一次登录观察）；
- 不影响语音识别质量、视觉模型准确率或阈值校准；
- 不替代真实设备上的语音/视觉功能验收。
