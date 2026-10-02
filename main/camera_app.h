/**
 * @file camera_app.h
 * @brief ESP32-S3-EYE 摄像头驱动 (OV2640/OV3660)
 */
#ifndef CAMERA_APP_H
#define CAMERA_APP_H

#include "esp_err.h"
#include "esp_camera.h"
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void *camera_handle_t;

/** @brief 摄像头帧信息 */
typedef struct {
    uint8_t  *buf;          /**< JPEG 缓冲区 */
    size_t    len;          /**< JPEG 数据长度 */
    int64_t   timestamp_us; /**< 拍摄时间 (µs since boot) */
    int       width;        /**< 图像宽度 */
    int       height;       /**< 图像高度 */
} camera_frame_t;

/** @brief 初始化摄像头 (OV2640/OV3660 on ESP32-S3-EYE) */
esp_err_t camera_app_init(camera_handle_t *handle);

/** @brief 捕获一帧 JPEG 图像 */
esp_err_t camera_app_capture(camera_handle_t handle, camera_frame_t *frame);

/** @brief 释放帧缓冲区 */
void camera_app_release_frame(camera_handle_t handle, camera_frame_t *frame);

/** @brief 反初始化摄像头 */
void camera_app_deinit(camera_handle_t handle);

#ifdef __cplusplus
}
#endif

#endif /* CAMERA_APP_H */