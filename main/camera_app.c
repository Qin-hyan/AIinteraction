/**
 * @file camera_app.c
 * @brief ESP32-S3-EYE 摄像头驱动 (OV2640)
 *
 * ESP32-S3-EYE v2.2 摄像头引脚:
 *   XCLK:  GPIO15
 *   PCLK:  GPIO13
 *   VSYNC: GPIO6
 *   HREF:  GPIO7
 *   SDA:   GPIO4  (shared I2C)
 *   SCL:   GPIO5  (shared I2C)
 *   D0-D7: GPIO11,9,8,10,12,18,17,16
 *   PWDN:  GPIO42 (shared, already defined in main.c)
 *   RESET: GPIO48
 *   SIOD:  GPIO4
 *   SIOC:  GPIO5
 */
#include "camera_app.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>

static const char *TAG = "CAM";

/* ESP32-S3-EYE v2.2 引脚定义 */
#define CAM_PIN_PWDN   GPIO_NUM_42
#define CAM_PIN_RESET  GPIO_NUM_48
#define CAM_PIN_XCLK   GPIO_NUM_15
#define CAM_PIN_SIOD   GPIO_NUM_4
#define CAM_PIN_SIOC   GPIO_NUM_5
#define CAM_PIN_D7     GPIO_NUM_16
#define CAM_PIN_D6     GPIO_NUM_17
#define CAM_PIN_D5     GPIO_NUM_18
#define CAM_PIN_D4     GPIO_NUM_12
#define CAM_PIN_D3     GPIO_NUM_10
#define CAM_PIN_D2     GPIO_NUM_8
#define CAM_PIN_D1     GPIO_NUM_9
#define CAM_PIN_D0     GPIO_NUM_11
#define CAM_PIN_VSYNC  GPIO_NUM_6
#define CAM_PIN_HREF   GPIO_NUM_7
#define CAM_PIN_PCLK   GPIO_NUM_13

/* 图像参数 */
#define CAM_XCLK_FREQ_MHZ  20
#define CAM_FRAME_SIZE     FRAMESIZE_SVGA  /* 800x600 - 平衡质量与内存 */
#define CAM_JPEG_QUALITY   10  /* 0-63, lower = better */
#define CAM_FB_COUNT       2   /* 帧缓冲数量 */

esp_err_t camera_app_init(camera_handle_t *handle)
{
    ESP_LOGI(TAG, "Initializing camera (OV2640 on ESP32-S3-EYE)");

    camera_config_t cfg = {
        .pin_pwdn       = CAM_PIN_PWDN,
        .pin_reset      = CAM_PIN_RESET,
        .pin_xclk       = CAM_PIN_XCLK,
        .pin_sccb_sda   = -1,  /* 不创建新 I2C 总线，复用主程序已初始化的 I2C_NUM_0 */
        .pin_sccb_scl   = -1,  /* 与 QMA6100P 共享 GPIO4/GPIO5 I2C 总线 */
        .pin_d7         = CAM_PIN_D7,
        .pin_d6         = CAM_PIN_D6,
        .pin_d5         = CAM_PIN_D5,
        .pin_d4         = CAM_PIN_D4,
        .pin_d3         = CAM_PIN_D3,
        .pin_d2         = CAM_PIN_D2,
        .pin_d1         = CAM_PIN_D1,
        .pin_d0         = CAM_PIN_D0,
        .pin_vsync      = CAM_PIN_VSYNC,
        .pin_href       = CAM_PIN_HREF,
        .pin_pclk       = CAM_PIN_PCLK,
        .xclk_freq_hz   = CAM_XCLK_FREQ_MHZ * 1000000,
        .ledc_timer     = LEDC_TIMER_0,
        .ledc_channel   = LEDC_CHANNEL_0,
        .pixel_format   = PIXFORMAT_JPEG,
        .frame_size     = CAM_FRAME_SIZE,
        .jpeg_quality   = CAM_JPEG_QUALITY,
        .fb_count       = CAM_FB_COUNT,
        .grab_mode      = CAMERA_GRAB_WHEN_EMPTY,
        .sccb_i2c_port  = 0,  /* 复用主程序已初始化的 I2C_NUM_0 */
    };

    esp_err_t ret = esp_camera_init(&cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Camera init failed: 0x%X", ret);
        return ret;
    }

    /* 检测传感器 */
    sensor_t *sensor = esp_camera_sensor_get();
    if (sensor) {
        ESP_LOGI(TAG, "Sensor PID: 0x%02X, VER: 0x%02X, MIDL: 0x%02X, MIDH: 0x%02X",
                 sensor->id.PID, sensor->id.VER, sensor->id.MIDL, sensor->id.MIDH);
        /* 根据实际传感器调整参数 */
        sensor->set_framesize(sensor, CAM_FRAME_SIZE);
        sensor->set_quality(sensor, CAM_JPEG_QUALITY);
        /* 白天自动白平衡等 */
        sensor->set_whitebal(sensor, 1);
        sensor->set_awb_gain(sensor, 1);
        sensor->set_wb_mode(sensor, 0);
        sensor->set_exposure_ctrl(sensor, 1);
        sensor->set_aec2(sensor, 1);
        sensor->set_ae_level(sensor, 0);
        sensor->set_aec_value(sensor, 300);
        sensor->set_gain_ctrl(sensor, 1);
        sensor->set_agc_gain(sensor, 0);
        sensor->set_gainceiling(sensor, (gainceiling_t)0);
        sensor->set_bpc(sensor, 0);
        sensor->set_wpc(sensor, 1);
        sensor->set_raw_gma(sensor, 1);
        sensor->set_lenc(sensor, 1);
        sensor->set_hmirror(sensor, 0);
        sensor->set_vflip(sensor, 0);
        sensor->set_dcw(sensor, 0);
        sensor->set_colorbar(sensor, 0);
    }

    ESP_LOGI(TAG, "Camera initialized OK");
    *handle = (camera_handle_t)sensor;
    return ESP_OK;
}

esp_err_t camera_app_capture(camera_handle_t handle, camera_frame_t *frame)
{
    if (!frame) return ESP_ERR_INVALID_ARG;

    /* 给摄像头传感器留出稳定时间 */
    vTaskDelay(pdMS_TO_TICKS(60));

    int64_t t0 = esp_timer_get_time();

    /* 获取一帧 */
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        ESP_LOGE(TAG, "Camera capture failed (fb_get returned NULL)");
        /* 尝试一次重试 */
        vTaskDelay(pdMS_TO_TICKS(100));
        fb = esp_camera_fb_get();
        if (!fb) {
            ESP_LOGE(TAG, "Camera retry also failed");
            return ESP_FAIL;
        }
        ESP_LOGW(TAG, "Camera retry succeeded");
    }

    int64_t t1 = esp_timer_get_time();
    ESP_LOGI(TAG, "Capture OK: %zu bytes, fmt=%d, %dx%d, latency=%lld us",
             fb->len, fb->format, fb->width, fb->height,
             (long long)(t1 - t0));

    /* 拷贝帧数据 (fb会在fb_return后失效) */
    frame->buf = (uint8_t *)malloc(fb->len);
    if (!frame->buf) {
        esp_camera_fb_return(fb);
        return ESP_ERR_NO_MEM;
    }
    memcpy(frame->buf, fb->buf, fb->len);
    frame->len = fb->len;
    frame->width = fb->width;
    frame->height = fb->height;
    frame->timestamp_us = t1;

    esp_camera_fb_return(fb);
    return ESP_OK;
}

void camera_app_release_frame(camera_handle_t handle, camera_frame_t *frame)
{
    if (frame && frame->buf) {
        free(frame->buf);
        frame->buf = NULL;
        frame->len = 0;
    }
}

void camera_app_deinit(camera_handle_t handle)
{
    esp_camera_deinit();
    ESP_LOGI(TAG, "Camera deinitialized");
}