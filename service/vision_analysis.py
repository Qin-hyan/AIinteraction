"""
vision_analysis.py — Week 08 W8-2: 本地视觉分析组合入口（DBCNN + 规则诊断）

职责：
  1. 调用 `DbcnnScorer` 得到真实模型质量分数与模型版本；
  2. 通过 `build_score_vision_result()` 生成契约结果；
  3. 可选调用 Week 07 `image_quality.assess_image()` 提供**规则诊断**，
     并以 `rule_diagnostics.source` 显式标明它不是 DBCNN 输出；
  4. 阈值未配置时返回 `needs_review` / `threshold_not_configured`。

边界：
  - 不启动新的 HTTP 服务或端口；供现有 Python 视觉流程/CLI 调用。
  - 模型不可用或推理失败时直接抛出 `DbcnnError`，**不**退回规则判断。
  - 不把规则标签写入 DBCNN 结果的 `label` 字段（该字段恒为 None）。
  - DBCNN 分数是通用质量分数，不是遮挡类别或准确率。

配置：
  - `VISION_DBCNN_MODEL_PATH`：模型路径（必需，见 vision_dbcnn）。
  - `VISION_DBCNN_MODEL_VERSION`：模型/权重快照标识（可选）。
  - `VISION_DBCNN_SCORE_THRESHOLD`：分数门槛（可选，必须位于 (0, 1]）。
    未配置时不产生 `ok` / `low_score`，只返回 `needs_review`。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Any, Callable, Optional

from . import image_quality
from .vision_dbcnn import DbcnnError, DbcnnScore, DbcnnScorer
from .vision_result import SCORE_KIND, build_score_vision_result

# ──────────────────────────────────────────────
# 配置与常量
# ──────────────────────────────────────────────

ENV_SCORE_THRESHOLD = "VISION_DBCNN_SCORE_THRESHOLD"
"""DBCNN 质量分数门槛的环境变量名；未设置表示尚未完成阈值判定。"""

RULE_SOURCE = "week07_image_quality_rules"
"""Week 07 规则诊断的来源标识，必须与 DBCNN 模型输出区分。"""


def get_configured_threshold() -> Optional[float]:
    """读取环境变量中的分数门槛；未配置时返回 None。

    Raises:
        ValueError: 环境变量不是数值或不在 (0, 1] 内。
    """
    raw = os.environ.get(ENV_SCORE_THRESHOLD)
    if raw is None or not raw.strip():
        return None

    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"环境变量 {ENV_SCORE_THRESHOLD} 必须是数值，实际 {raw!r}"
        ) from exc

    if not math.isfinite(value) or not (0.0 < value <= 1.0):
        raise ValueError(
            f"环境变量 {ENV_SCORE_THRESHOLD} 必须落在 (0, 1] 内，实际 {value}"
        )
    return value


# ──────────────────────────────────────────────
# 规则诊断
# ──────────────────────────────────────────────


def _rule_thresholds() -> dict[str, float]:
    """返回 Week 07 规则常量，明确这些门槛不属于 DBCNN 分数。"""
    return {
        "mean_luma_dark": image_quality.LUMA_DARK,
        "mean_luma_bright": image_quality.LUMA_BRIGHT,
        "contrast_low": image_quality.CONTRAST_LOW,
        "detail_low": image_quality.DETAIL_LOW,
    }


def _rule_diagnostics(
    jpeg_bytes: bytes,
    assessor: Callable[[bytes], dict[str, Any]],
) -> dict[str, Any]:
    """执行规则评估并标注来源；规则标签不等于模型分类。"""
    assessment = assessor(jpeg_bytes)
    return {
        "source": RULE_SOURCE,
        "kind": "rule_based_diagnostics",
        "available": True,
        "label": assessment.get("status"),
        "usable": bool(assessment.get("usable")),
        "score": assessment.get("score"),
        "score_kind": SCORE_KIND,
        "metrics": assessment.get("metrics") or {},
        "bytes_len": assessment.get("bytes_len"),
        "thresholds": _rule_thresholds(),
        "thresholds_source": "week07_rule_constants",
        "note": (
            "Week 07 规则诊断；不是 DBCNN 分类输出，"
            "其 label/score 不可当作模型结果或准确率。"
        ),
    }


def _rule_diagnostics_unavailable(jpeg_bytes: bytes) -> dict[str, Any]:
    """依赖缺失时明确标记规则诊断未执行，不冒充图像无效。"""
    return {
        "source": RULE_SOURCE,
        "kind": "rule_based_diagnostics",
        "available": False,
        "label": None,
        "usable": None,
        "score": None,
        "score_kind": SCORE_KIND,
        "metrics": {},
        "bytes_len": len(jpeg_bytes),
        "thresholds": _rule_thresholds(),
        "thresholds_source": "week07_rule_constants",
        "note": (
            "Week 07 规则诊断未执行：缺少 numpy/Pillow；"
            "不得把该状态当作图像无效或规则判定结果。"
        ),
    }


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────


def analyze_jpeg(
    *,
    request_id: str,
    jpeg_bytes: bytes,
    scorer: Optional[DbcnnScorer] = None,
    score_threshold: Optional[float] = None,
    include_rule_diagnostics: bool = True,
    rule_assessor: Optional[Callable[[bytes], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """对一张 JPEG 执行 DBCNN 推理并组合结构化结果。

    Args:
        request_id: 输入/采集请求的关联键。
        jpeg_bytes: JPEG 字节。
        scorer: 可注入的 DBCNN 评分器；None 时按环境变量创建。
        score_threshold: 显式门槛；None 时读取环境变量，仍未配置则返回
            `needs_review`。**不设默认生产阈值**。
        include_rule_diagnostics: 是否附带 Week 07 规则诊断。
        rule_assessor: 可注入的规则评估函数（测试用）；None 时使用
            `image_quality.assess_image`。

    Returns:
        结构化结果：DBCNN 原始分数/版本/来源、阈值判定状态，以及可选的
        `rule_diagnostics`（来源标记为 Week 07 规则，不是模型输出）。

    Raises:
        DbcnnError: 模型不可用或推理失败；不会回退为规则结果。
        ValueError: 阈值/关联键等调用方配置非法。
        TypeError: `jpeg_bytes` 类型错误。
    """
    if not isinstance(jpeg_bytes, (bytes, bytearray)):
        raise TypeError("jpeg_bytes 必须是 bytes 或 bytearray")
    payload = bytes(jpeg_bytes)

    threshold = score_threshold
    if threshold is None:
        threshold = get_configured_threshold()

    if scorer is None:
        scorer = DbcnnScorer()

    # 先完成真实模型推理；失败必须直接暴露，不能用规则诊断掩盖。
    dbcnn: DbcnnScore = scorer.score_jpeg(payload)

    result = build_score_vision_result(
        request_id=request_id,
        score=dbcnn.score,
        model_version=dbcnn.model_version,
        score_threshold=threshold,
        score_source=dbcnn.score_source,
    )
    result["model_sha256"] = dbcnn.model_sha256
    result["model_data_sha256"] = dbcnn.model_data_sha256
    result["inference_ms"] = dbcnn.inference_ms
    result["input_bytes"] = len(payload)

    if not include_rule_diagnostics:
        result["rule_diagnostics"] = None
    elif rule_assessor is not None:
        result["rule_diagnostics"] = _rule_diagnostics(payload, rule_assessor)
    elif image_quality.is_available():
        result["rule_diagnostics"] = _rule_diagnostics(
            payload, image_quality.assess_image
        )
    else:
        result["rule_diagnostics"] = _rule_diagnostics_unavailable(payload)

    return result


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


def _read_input(image_path: Optional[str], use_stdin: bool) -> bytes:
    if use_stdin:
        return sys.stdin.buffer.read()
    if not image_path:
        raise ValueError("需要提供图片路径，或使用 --stdin 从标准输入读取")
    with open(image_path, "rb") as handle:
        return handle.read()


def main(argv: Optional[list[str]] = None) -> int:
    """本地 CLI：读取一张 JPEG，输出结构化视觉结果 JSON。"""
    parser = argparse.ArgumentParser(
        description="对本地 JPEG 执行 DBCNN 质量推理（ONNX Runtime CPU）"
    )
    parser.add_argument("image", nargs="?", help="JPEG 图片路径；使用 --stdin 时可省略")
    parser.add_argument("--stdin", action="store_true", help="从标准输入读取 JPEG")
    parser.add_argument(
        "--request-id",
        default=None,
        help="输入关联键；默认生成 vision-{毫秒时间戳}",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="显式质量分数门槛 (0,1]；省略则读取环境变量，未配置则不判定",
    )
    parser.add_argument(
        "--no-rules",
        action="store_true",
        help="不附带 Week 07 规则诊断",
    )
    args = parser.parse_args(argv)

    request_id = args.request_id or f"vision-{int(time.time() * 1000)}"

    try:
        payload = _read_input(args.image, args.stdin)
        result = analyze_jpeg(
            request_id=request_id,
            jpeg_bytes=payload,
            score_threshold=args.threshold,
            include_rule_diagnostics=not args.no_rules,
        )
    except (DbcnnError, ValueError, OSError) as exc:
        error_payload = {
            "request_id": request_id,
            "error": type(exc).__name__,
            "message": str(exc),
        }
        print(json.dumps(error_payload, ensure_ascii=False, indent=2, allow_nan=False))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI 入口
    raise SystemExit(main())


__all__ = [
    "analyze_jpeg",
    "get_configured_threshold",
    "main",
    "ENV_SCORE_THRESHOLD",
    "RULE_SOURCE",
]
