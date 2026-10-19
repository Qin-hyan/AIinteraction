# DBCNN ONNX 质量分数适配器（Week 08 W8-2）

> 本文记录本地 DBCNN 模型接入的配置、来源、许可注意事项、真实输出与限制。
> 所有结论以实际代码和本地真实推理证据为准；未完成的反馈闭环/阈值校准不得写成已完成。

---

## 1. 作用与边界

**作用**

- 用本地 DBCNN ONNX 模型（外部权重 `.onnx.data`）在 PC 端 CPU 上对一张 JPEG 做一次无参考质量评估；
- 返回模型**原始质量分数**、模型/权重标识和推理耗时；
- 由 `service/vision_analysis.py` 组合为结构化结果，并可选附带 Week 07 规则诊断。

**不是**

- 不是遮挡类别分类器：DBCNN 只输出一个通用质量分数，**不输出标签**；
- 不是准确率/置信度：`score` 是 0~1 相对值，且本轮未校准；
- 不做阈值判定：适配器不设默认门槛；未配置阈值时结果为 `needs_review`；
- 不调用云端推理 API、不上传图片、不启动新的 HTTP 端口。

**调用关系**

```
service/vision_analysis.py   ← 组合入口 / CLI（无新端口）
        ├── service/vision_dbcnn.py     ← ONNX Runtime CPU 推理适配器
        ├── service/vision_result.py    ← 结构化契约与 threshold/decision
        └── service/image_quality.py    ← Week 07 规则诊断（可选，来源单独标注）
```

---

## 2. 配置

沿用项目现有的环境变量配置习惯（参考 `service/query_last.py`）。

| 环境变量 | 必需 | 说明 |
|---|---|---|
| `VISION_DBCNN_MODEL_PATH` | 是 | `dbcnn_model.onnx` 的本地路径；未设置或文件不存在时**明确报错**，不会退回规则判断 |
| `VISION_DBCNN_MODEL_VERSION` | 否 | 结果中的 `model_version`，默认 `dbcnn_onnx_v1`；这是**本适配器/权重快照标识**，不是上游模型语义版本 |
| `VISION_DBCNN_SCORE_THRESHOLD` | 否 | 质量分数门槛，必须位于 `(0, 1]`；**未设置 = 尚未完成阈值判定**，不得用隐含默认值替代 |

模型需要 `.onnx` 与同目录的外部权重 `.onnx.data`；ONNX Runtime 会自动加载外部数据，缺少时会加载失败并报 `DbcnnModelError`。

PowerShell 示例（`<repo>` 替换为本地仓库根目录）：

```powershell
$env:VISION_DBCNN_MODEL_PATH = "<repo>\.workbuddy\artifacts\dbcnn-iqa-test\weights\dbcnn_model.onnx"
$env:VISION_DBCNN_MODEL_VERSION = "dbcnn_onnx_v1"
# 只有完成阈值校准后才设置；未校准时保持未设置
Remove-Item Env:VISION_DBCNN_SCORE_THRESHOLD -ErrorAction SilentlyContinue
```

---

## 3. 安装依赖

使用仓库外的隔离虚拟环境（不要装进系统全局 Python）：

```powershell
python -m venv <venv>
<venv>\Scripts\python.exe -m pip install -r requirements-vision.txt
```

`requirements-vision.txt` 中与本适配器相关的依赖：

- `onnxruntime==1.22.1`（仅 `CPUExecutionProvider`）
- `opencv-python-headless==5.0.0.93`
- `Pillow==10.2.0`（Week 07 规则诊断）
- `numpy==2.4.4`（Week 07 既有 pin；本次隔离 venv 实测为 2.4.6）

未安装 PyTorch、Docker、Roboflow SDK；未调用任何云端推理接口。

---

## 4. 运行

### 4.1 命令行（推荐用于本地冒烟）

```powershell
<venv>\Scripts\python.exe -m service.vision_analysis <image.jpg> --request-id req-demo-0001
```

可选参数：

- `--threshold <value>`：显式门槛（仅用于已验证的校准值；**不是生产默认值**）；
- `--stdin`：从标准输入读取 JPEG；
- `--no-rules`：不附带 Week 07 规则诊断。

### 4.2 Python 调用

```python
from service.vision_analysis import analyze_jpeg
from service.vision_dbcnn import DbcnnScorer

scorer = DbcnnScorer()  # 读取 VISION_DBCNN_MODEL_PATH
result = analyze_jpeg(
    request_id="req-demo-0001",
    jpeg_bytes=jpeg_bytes,
    scorer=scorer,
)
```

模型不可用、权重加载失败或推理失败会抛出 `DbcnnError` 子类；**不会**静默返回规则结果。

---

## 5. 输出字段与来源

| 字段 | 来源/语义 |
|---|---|
| `request_id` | 输入与结果的关联键 |
| `label` | **恒为 `null`**：DBCNN 不产生类别标签 |
| `label_source` | **恒为 `null`** |
| `score` | DBCNN 原始输出（0~1）；不乘 100、不裁剪、不四舍五入 |
| `score_source` | `dbcnn_onnx` |
| `score_kind` | `relative_usability`（仅相对比较，不是准确率） |
| `model_version` | `dbcnn_onnx_v1`（可配置的适配器/权重快照标识） |
| `decision` | `ok` / `low_score` / `needs_review` / `unknown` |
| `reject_reason` | 未采纳时的原因，如 `threshold_not_configured` |
| `review_required` | 结果是否仍需人工复核（`decision != ok`） |
| `score_threshold` | 实际使用的门槛；未配置时为 `null` |
| `threshold_status` | `configured` / `not_configured` |
| `model_sha256` | 本地 `.onnx` 图文件 SHA-256 |
| `model_data_sha256` | 本地 `.onnx.data` 外部权重 SHA-256 |
| `inference_ms` | 前向推理耗时（毫秒，不含解码/预处理） |
| `rule_diagnostics` | Week 07 规则诊断；**不是 DBCNN 输出** |

`rule_diagnostics` 的结构：

- `source = week07_image_quality_rules`
- `kind = rule_based_diagnostics`
- `available`：依赖（numpy/Pillow）是否可用；不可用时为 `false` 且 `label=null`，**不得解释为图像无效**
- `label`：规则状态（`ok` / `too_dark` / `too_bright` / `low_contrast` / `low_detail` / `invalid`）
- `thresholds` / `thresholds_source=week07_rule_constants`：规则常量，**不是 DBCNN 分数阈值**

---

## 6. 阈值策略

- 未配置 `VISION_DBCNN_SCORE_THRESHOLD`：返回 `decision=needs_review`、`reject_reason=threshold_not_configured`、`review_required=true`；
- 配置后：`score >= threshold` → `ok`；`score < threshold` → `low_score`（闭区间，等于门槛视为达标）；
- 阈值非法（非数值、0、负数、>1、NaN/Inf）→ 明确抛出 `ValueError`，不静默忽略；
- **本轮未做阈值校准**，因此没有生产默认值。测试/演示中出现的 `0.5` 只是契约行为示例，不是校准结论。

---

## 7. 真实推理证据（2026-10-19，本地隔离 venv）

环境：Python 3.11.7、onnxruntime 1.22.1（CPU）、opencv-python-headless 5.0.0.93、numpy 2.4.6、Pillow 10.2.0。

| 样例 | DBCNN 原始分数 | 规则诊断 | `decision` |
|---|---:|---|---|
| `.workbuddy/week07_acceptance/ok.jpg` | `0.7561315298080444` | `ok`（mean_luma 118.57 / contrast 63.41 / detail 8.32） | `needs_review` |
| `.workbuddy/week07_acceptance/occluded.jpg` | `0.25810927152633667` | `too_dark`（mean_luma 5.99 / contrast 3.71 / detail 0.37） | `needs_review` |

- 两个分数与 `.workbuddy/artifacts/dbcnn-iqa-test/report.md` 的已验证值一致；
- 本次运行 `inference_ms` 约 36–38 ms（随机器负载变化，不构成性能基准）；
- 显式设置测试阈值 `0.5` 时：`ok.jpg` → `ok`，`occluded.jpg` → `low_score`；
- 未设置阈值时两者均为 `needs_review`，符合「未校准不判定」的要求。

**能证明**：本地权重可加载、CPU 前向可运行、服务能返回原始分数/版本/判定状态，且规则诊断与模型输出来源分离。

**不能证明**：准确率、泛化能力、遮挡识别能力；只有两张样例且无质量真值。低分不能直接解释为镜头遮挡。

---

## 8. 模型来源与文件

- 来源：`https://huggingface.co/86Cao/IQA-ONNX-Models`
- 文件：`dbcnn_model.onnx` + `dbcnn_model.onnx.data`
- 已验证 SHA-256：
  - `dbcnn_model.onnx`：`2a679a6264bfc188e623e9a937ad214dd761f7deb9469442a37107560557bc3f`
  - `dbcnn_model.onnx.data`：`ed3e6df9109aa30323bcd2006a098023aa57fbcb98bfe1d2767227fc71c5382f`
- 模型与外部权重**不纳入 Git**，保存在本地忽略目录 `.workbuddy/`；源码不依赖提交模型二进制。

### 许可注意事项

- Hugging Face 仓库 README 的 YAML 元数据标注 `license: apache-2.0`；
- README 原文同时写明：`Apache 2.0. Original model weights are subject to their respective licenses.`；
- 仓库中没有独立 `LICENSE` 文件（此前核查 `raw/main/LICENSE` 返回 HTTP 404）；
- **因此不能把仓库标签当作原始 DBCNN 权重的许可确认**；本文不作法律结论。正式分发/商用前需由用户另行核实原始权重许可。

### 已知元数据不一致

- 模型卡标称 DBCNN 分数范围为 0–100；
- 本地实测输出为 0–1（`0.7561` / `0.2581`）；
- 未做 ×100 或截断，也未把 0–1 强行套用 0–100 的阈值；原因尚未确认。

---

## 9. 已知限制

1. 尚无校准后的低分阈值；未配置时只能返回 `needs_review`；
2. 没有低分/未知反馈、重新采集、人工纠正 UI（W8-3 未开始）；
3. 只有两张本地样例，不能评估准确率或泛化；
4. 预处理使用 `cv2.INTER_CUBIC` 作为模型卡 PIL BICUBIC 的近似；未与 PIL BICUBIC 做像素级对齐验证；
5. 未接入 HTTP 端点/设备端；未做并发、连续帧或长时间稳定性验证；
6. 模型输出 0–1 与模型卡 0–100 的差异未解释；
7. 原始权重许可未核实（见第 8 节）。

---

## 10. 测试

```powershell
# 相关单元测试（无 ONNX Runtime 时自动跳过真实冒烟）
python -m pytest service/test_vision_result.py service/test_vision_dbcnn.py service/test_vision_analysis.py service/test_image_quality.py -q

# 真实集成冒烟（隔离 venv + 本地权重）
$env:VISION_DBCNN_RUN_INTEGRATION = "1"
$env:VISION_DBCNN_MODEL_PATH = "<repo>\.workbuddy\artifacts\dbcnn-iqa-test\weights\dbcnn_model.onnx"
<venv>\Scripts\python.exe -m pytest service/test_vision_dbcnn.py service/test_vision_analysis.py service/test_vision_result.py service/test_image_quality.py -q
```

结果与完整证据见 `TEST_LOG.md`「M」节与 `weeks/week08/STATE.md`。
