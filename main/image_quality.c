/**
 * @file image_quality.c
 * @brief Week 07: ESP32 端画面可用性 / 遮挡识别实现
 *
 * 使用 esp_jpeg 组件解码，输出缩放到 1/4 的 RGB888 图像，
 * 计算 mean_luma / contrast / detail，按 PC 端阈值判定状态。
 *
 * 约束：
 *   - 只做画面可用性 / 遮挡识别，不推断精确位置或人身风险。
 *   - score 仅作相对比较，不得解释为现实正确率。
 *   - 输入为空/解码失败统一返回 IMG_STATUS_INVALID，不得崩溃。
 */

#include "image_quality.h"
#include "jpeg_decoder.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include <math.h>
#include <string.h>

static const char *TAG = "IMGQ";

/* 与 PC 端 service/image_quality.py 对齐的阈值 */
#define LUMA_DARK      25.0f
#define LUMA_BRIGHT    230.0f
#define CONTRAST_LOW   8.0f
#define DETAIL_LOW     8.0f

#define JPEG_SOI_0     0xFF
#define JPEG_SOI_1     0xD8

/* 解码输出配置：RGB888 + 1/4 缩放，兼顾精度与内存 */
#define IQ_OUT_FORMAT  JPEG_IMAGE_FORMAT_RGB888
#define IQ_OUT_SCALE   JPEG_IMAGE_SCALE_1_4

const char *image_quality_status_name(image_status_t st)
{
    switch (st) {
    case IMG_STATUS_OK:           return "ok";
    case IMG_STATUS_TOO_DARK:     return "too_dark";
    case IMG_STATUS_TOO_BRIGHT:   return "too_bright";
    case IMG_STATUS_LOW_CONTRAST: return "low_contrast";
    case IMG_STATUS_LOW_DETAIL:   return "low_detail";
    case IMG_STATUS_INVALID:      return "invalid";
    default:                      return "unknown";
    }
}

static float clampf(float v, float min, float max)
{
    if (v < min) return min;
    if (v > max) return max;
    return v;
}

esp_err_t image_quality_assess(const uint8_t *jpeg_buf, size_t jpeg_len,
                               image_quality_t *out)
{
    if (!out) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out, 0, sizeof(*out));
    out->status = IMG_STATUS_INVALID;

    if (!jpeg_buf || jpeg_len < 2 ||
        jpeg_buf[0] != JPEG_SOI_0 || jpeg_buf[1] != JPEG_SOI_1) {
        ESP_LOGD(TAG, "invalid input: buf=%p len=%u", (void *)jpeg_buf,
                 (unsigned)jpeg_len);
        return ESP_OK;
    }

    /* 1) 获取解码输出尺寸，便于分配合适缓冲区 */
    esp_jpeg_image_cfg_t cfg = {0};
    cfg.indata = (uint8_t *)jpeg_buf;  /* 解码器只读，但头文件未声明 const */
    cfg.indata_size = jpeg_len;
    cfg.out_format = IQ_OUT_FORMAT;
    cfg.out_scale = IQ_OUT_SCALE;
    cfg.flags.swap_color_bytes = 0;
    cfg.advanced.working_buffer = NULL;  /* 由 esp_jpeg_decode 内部分配工作区 */

    esp_jpeg_image_output_t info = {0};
    esp_err_t ret = esp_jpeg_get_image_info(&cfg, &info);
    if (ret != ESP_OK || info.output_len == 0) {
        ESP_LOGD(TAG, "jpeg get info failed: 0x%X output_len=%u",
                 ret, (unsigned)info.output_len);
        return ESP_OK;
    }

    /* 2) 分配解码输出缓冲区（优先 PSRAM） */
    uint8_t *outbuf = (uint8_t *)heap_caps_malloc(info.output_len,
                                                  MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
    if (!outbuf) {
        ESP_LOGW(TAG, "OOM allocating decode buffer (%u bytes)",
                 (unsigned)info.output_len);
        return ESP_OK;
    }
    cfg.outbuf = outbuf;
    cfg.outbuf_size = info.output_len;

    /* 3) 解码 JPEG */
    esp_jpeg_image_output_t decoded = {0};
    ret = esp_jpeg_decode(&cfg, &decoded);
    if (ret != ESP_OK || decoded.width == 0 || decoded.height == 0) {
        ESP_LOGW(TAG, "jpeg decode failed: 0x%X %ux%u", ret,
                 (unsigned)decoded.width, (unsigned)decoded.height);
        heap_caps_free(outbuf);
        return ESP_OK;
    }

    const uint32_t pixels = (uint32_t)decoded.width * decoded.height;
    if (pixels * 3 > info.output_len) {
        /* 防御：输出长度与分辨率不一致 */
        ESP_LOGW(TAG, "decoded size mismatch: %ux%u len=%u",
                 (unsigned)decoded.width, (unsigned)decoded.height,
                 (unsigned)info.output_len);
        heap_caps_free(outbuf);
        return ESP_OK;
    }

    /* 4) 申请亮度缓存，一次遍历提取 Y */
    uint8_t *luma = (uint8_t *)heap_caps_malloc(pixels,
                                                MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
    if (!luma) {
        ESP_LOGW(TAG, "OOM allocating luma buffer (%u bytes)", (unsigned)pixels);
        heap_caps_free(outbuf);
        return ESP_OK;
    }

    const uint8_t *p = outbuf;
    double sum = 0.0;
    for (uint32_t i = 0; i < pixels; i++) {
        uint8_t r = *p++;
        uint8_t g = *p++;
        uint8_t b = *p++;
        uint8_t y = (uint8_t)(0.299f * r + 0.587f * g + 0.114f * b + 0.5f);
        luma[i] = y;
        sum += y;
    }
    const double mean = sum / pixels;

    /* 5) 对比度：标准差 */
    double sq_diff_sum = 0.0;
    for (uint32_t i = 0; i < pixels; i++) {
        double diff = (double)luma[i] - mean;
        sq_diff_sum += diff * diff;
    }
    const double contrast = sqrt(sq_diff_sum / pixels);

    /* 6) 细节：前向差分梯度能量均值 */
    double detail_sum = 0.0;
    uint32_t detail_count = 0;
    if (decoded.width > 1 && decoded.height > 1) {
        for (uint16_t y = 0; y < decoded.height - 1; y++) {
            for (uint16_t x = 0; x < decoded.width - 1; x++) {
                uint32_t idx = (uint32_t)y * decoded.width + x;
                double cur   = (double)luma[idx];
                double right = (double)luma[idx + 1];
                double down  = (double)luma[idx + decoded.width];
                double dx = right - cur;
                double dy = down - cur;
                detail_sum += sqrt(dx * dx + dy * dy);
                detail_count++;
            }
        }
    }
    const double detail = (detail_count > 0) ? (detail_sum / detail_count) : 0.0;

    /* 7) 判定顺序：invalid → too_dark → too_bright → low_contrast → low_detail → ok */
    image_status_t status = IMG_STATUS_OK;
    if (mean < LUMA_DARK) {
        status = IMG_STATUS_TOO_DARK;
    } else if (mean > LUMA_BRIGHT) {
        status = IMG_STATUS_TOO_BRIGHT;
    } else if (contrast < CONTRAST_LOW) {
        status = IMG_STATUS_LOW_CONTRAST;
    } else if (detail < DETAIL_LOW) {
        status = IMG_STATUS_LOW_DETAIL;
    }

    /* 8) 相对可用性分数（与 PC 端 _score_from_metrics 对齐） */
    float dark_penalty = (mean < LUMA_DARK)
                         ? ((float)(LUMA_DARK - mean) / LUMA_DARK)
                         : 0.0f;
    float bright_penalty = (mean > LUMA_BRIGHT)
                           ? ((float)(mean - LUMA_BRIGHT) / (255.0f - LUMA_BRIGHT))
                           : 0.0f;
    float luma_score = 1.0f - (dark_penalty > bright_penalty ? dark_penalty : bright_penalty);
    float contrast_score = (contrast / CONTRAST_LOW) < 1.0f
                           ? ((float)contrast / CONTRAST_LOW)
                           : 1.0f;
    float detail_score = (detail / DETAIL_LOW) < 1.0f
                         ? ((float)detail / DETAIL_LOW)
                         : 1.0f;
    float score = luma_score * contrast_score * detail_score;
    score = clampf(score, 0.0f, 1.0f);

    out->status = status;
    out->usable = (status == IMG_STATUS_OK);
    out->mean_luma = (float)mean;
    out->contrast = (float)contrast;
    out->detail = (float)detail;
    out->score = score;

    ESP_LOGI(TAG,
             "assess status=%s usable=%d %ux%u mean=%.2f contrast=%.2f detail=%.2f score=%.3f",
             image_quality_status_name(status), (int)out->usable,
             (unsigned)decoded.width, (unsigned)decoded.height,
             out->mean_luma, out->contrast, out->detail, out->score);

    heap_caps_free(luma);
    heap_caps_free(outbuf);
    return ESP_OK;
}
