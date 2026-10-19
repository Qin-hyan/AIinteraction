/**
 * @file image_quality.h
 * @brief Week 07: 画面可用性 / 遮挡识别评估
 *
 * 与 PC 端 service/image_quality.py 使用同一套阈值与判定顺序，
 * 用于 ESP32 端对摄像头 JPEG 图像做快速无状态评估。
 */

#ifndef IMAGE_QUALITY_H
#define IMAGE_QUALITY_H

#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief 画面可用性状态 */
typedef enum {
    IMG_STATUS_INVALID = 0,   /**< 输入无效/解码失败 */
    IMG_STATUS_OK,            /**< 画面可用 */
    IMG_STATUS_TOO_DARK,      /**< 过暗 */
    IMG_STATUS_TOO_BRIGHT,    /**< 过亮/曝光过度 */
    IMG_STATUS_LOW_CONTRAST,  /**< 低对比度（疑似镜头遮挡） */
    IMG_STATUS_LOW_DETAIL,    /**< 低细节（疑似失焦/模糊） */
} image_status_t;

/** @brief 画面质量评估结果 */
typedef struct {
    image_status_t status;    /**< 评估状态 */
    bool   usable;            /**< status == IMG_STATUS_OK */
    float  mean_luma;         /**< 灰度均值 0~255 */
    float  contrast;          /**< 灰度标准差 */
    float  detail;            /**< 相邻像素梯度能量均值（前向差分） */
    float  score;             /**< 0~1 相对可用性，仅作相对比较 */
} image_quality_t;

/**
 * @brief 评估一张 JPEG 图像的画面可用性
 *
 * @param[in]  jpeg_buf JPEG 数据缓冲区
 * @param[in]  jpeg_len JPEG 数据长度（字节）
 * @param[out] out      评估结果（由调用者分配）
 *
 * @return ESP_OK 成功（含返回 IMG_STATUS_INVALID 的评估结果）；
 *         ESP_ERR_INVALID_ARG 如果 out 为 NULL。
 */
esp_err_t image_quality_assess(const uint8_t *jpeg_buf, size_t jpeg_len,
                               image_quality_t *out);

/**
 * @brief 返回状态枚举的字符串名称
 */
const char *image_quality_status_name(image_status_t st);

#ifdef __cplusplus
}
#endif

#endif /* IMAGE_QUALITY_H */
