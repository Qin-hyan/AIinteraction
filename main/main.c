/**
 * @file main.c
 * @brief 第1-2周：传感数据采集与 Web 展示 + 远程采集指令
 *
 * ESP32-S3-EYE v2.2 + SUB v1.1
 * - QMA6100P 三轴加速度计 (I2C: SDA=GPIO4, SCL=GPIO5, addr=0x12)
 * - ADC 按键检测 (GPIO1 / ADC1_CH0)
 * - Wi-Fi STA 连接
 * - HTTP 服务器 + Web 仪表盘 (500ms 刷新)
 * - Week 2: 手动采集指令 + request_id 追踪 + 任务状态反馈
 *
 * 初始化顺序: UART → PSRAM → I2C(QMA6100P) → ADC → LED → Wi-Fi → HTTP
 */

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "driver/i2c_master.h"
#include "driver/gpio.h"
#include "esp_mac.h"
#include <string.h>

#include "qma6100p.h"
#include "adc_button.h"
#include "wifi_app.h"
#include "http_server.h"

static const char *TAG = "MAIN";

/* ---- 硬件引脚定义 ---- */
#define I2C_SDA_GPIO        GPIO_NUM_4
#define I2C_SCL_GPIO        GPIO_NUM_5
#define QMA6100P_I2C_ADDR   0x12
#define CAM_PWDN_GPIO       GPIO_NUM_42  /* 摄像头/传感器电源使能 */

#define LED_GPIO            GPIO_NUM_38
#define MODULE_PWR_LED      GPIO_NUM_3

#define DEVICE_ID           "esp32s3-eye-wk01"

/* ================================================================
 * PSRAM 测试
 * ================================================================ */
static void psram_test(void)
{
    size_t psram_size = esp_psram_get_size();
    ESP_LOGI(TAG, "PSRAM size: %d bytes", (int)psram_size);
    if (psram_size > 0) {
        void *ptr = heap_caps_malloc(1024, MALLOC_CAP_SPIRAM);
        if (ptr) {
            memset(ptr, 0xA5, 1024);
            ESP_LOGI(TAG, "PSRAM test: PASS");
            free(ptr);
        } else {
            ESP_LOGW(TAG, "PSRAM alloc failed");
        }
    }
}

/* ================================================================
 * LED 测试 (GPIO38 RGB LED + GPIO3 模组电源 LED)
 * ================================================================ */
static void led_test(void)
{
    gpio_config_t led_cfg = {
        .pin_bit_mask = BIT64(LED_GPIO),
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&led_cfg);
    gpio_set_level(LED_GPIO, 1);

    gpio_config_t pwr_cfg = {
        .pin_bit_mask = BIT64(MODULE_PWR_LED),
        .mode         = GPIO_MODE_OUTPUT_OD,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&pwr_cfg);
    gpio_set_level(MODULE_PWR_LED, 1);

    for (int i = 0; i < 3; i++) {
        gpio_set_level(LED_GPIO, 0);
        gpio_set_level(MODULE_PWR_LED, 0);
        vTaskDelay(pdMS_TO_TICKS(150));
        gpio_set_level(LED_GPIO, 1);
        gpio_set_level(MODULE_PWR_LED, 1);
        vTaskDelay(pdMS_TO_TICKS(150));
    }
    ESP_LOGI(TAG, "LED test: PASS");
}

/* ================================================================
 * I2C 总线初始化
 * ================================================================ */
static esp_err_t i2c_bus_init(i2c_master_bus_handle_t *bus_handle)
{
    i2c_master_bus_config_t bus_cfg = {
        .clk_source    = I2C_CLK_SRC_DEFAULT,
        .i2c_port      = I2C_NUM_0,
        .scl_io_num    = I2C_SCL_GPIO,
        .sda_io_num    = I2C_SDA_GPIO,
        .flags.enable_internal_pullup = true,
    };
    return i2c_new_master_bus(&bus_cfg, bus_handle);
}

/* ================================================================
 * 主入口
 * ================================================================ */
void app_main(void)
{
    ESP_LOGI(TAG, "Week 1-2: Sensor Data + Remote Collect");
    ESP_LOGI(TAG, "ESP32-S3-EYE v2.2 / SUB v1.1");

    /* NVS */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    /* 按顺序初始化: PSRAM → LED → I2C → QMA6100P → ADC → Wi-Fi → HTTP */
    psram_test();
    led_test();

    /* 使能摄像头电源域（QMA6100P 共享供电） */
    gpio_set_direction(CAM_PWDN_GPIO, GPIO_MODE_OUTPUT);
    gpio_set_level(CAM_PWDN_GPIO, 0);  /* 拉低使能 */
    ESP_LOGI(TAG, "Camera power enabled (GPIO42=0)");

    /* I2C 总线 */
    i2c_master_bus_handle_t i2c_bus = NULL;
    ESP_ERROR_CHECK(i2c_bus_init(&i2c_bus));

    /* QMA6100P */
    qma6100p_handle_t imu = NULL;
    ret = qma6100p_create(i2c_bus, QMA6100P_I2C_ADDR, &imu);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "QMA6100P not found at 0x12, trying 0x13...");
        ret = qma6100p_create(i2c_bus, 0x13, &imu);
    }
    if (ret == ESP_OK && imu) {
        uint8_t devid;
        ret = qma6100p_get_deviceid(imu, &devid);
        ESP_LOGI(TAG, "QMA6100P CHIP_ID=0x%02X", devid);
        if (ret != ESP_OK || devid != 0x90) {
            ESP_LOGE(TAG, "QMA6100P wrong chip (ret=0x%X devid=0x%02X)", ret, devid);
            imu = NULL;
        }
        if (imu) {
            uint8_t diag_pre[4];
            qma6100p_read_reg(imu, 0x10, &diag_pre[0], 1);
            qma6100p_read_reg(imu, 0x11, &diag_pre[1], 1);
            qma6100p_read_reg(imu, 0x0F, &diag_pre[2], 1);
            qma6100p_read_reg(imu, 0x00, &diag_pre[3], 1);
            ESP_LOGI(TAG, "[DIAG] PRE-INIT: 00=%02X 0F=%02X 10=%02X 11=%02X(MODE=%s)",
                     diag_pre[3], diag_pre[2], diag_pre[0], diag_pre[1],
                     (diag_pre[1] & 0x80) ? "ACTIVE" : "STANDBY");

            esp_err_t wake_ret = qma6100p_wake_up(imu);
            if (wake_ret != ESP_OK) {
                ESP_LOGE(TAG, "INIT failed: 0x%X", wake_ret);
                imu = NULL;
            } else {
                vTaskDelay(pdMS_TO_TICKS(10));

                uint8_t diag_post[4];
                qma6100p_read_reg(imu, 0x00, &diag_post[0], 1);
                qma6100p_read_reg(imu, 0x0F, &diag_post[1], 1);
                qma6100p_read_reg(imu, 0x10, &diag_post[2], 1);
                qma6100p_read_reg(imu, 0x11, &diag_post[3], 1);
                ESP_LOGI(TAG, "[DIAG] POST-INIT: 00=%02X 0F=%02X 10=%02X 11=%02X(MODE=%s)",
                         diag_post[0], diag_post[1], diag_post[2], diag_post[3],
                         (diag_post[3] & 0x80) ? "ACTIVE" : "STANDBY");

                if (!(diag_post[3] & 0x80)) {
                    ESP_LOGE(TAG, "FATAL: MODE=STANDBY after init!");
                    imu = NULL;
                } else {
                    ESP_LOGI(TAG, "QMA6100P ready — MODE = ACTIVE ✓");
                }
            }
        }
    } else {
        ESP_LOGW(TAG, "QMA6100P not found");
    }

    /* ADC 按键 */
    adc_button_handle_t btn_handle = NULL;
    ret = adc_button_init(&btn_handle);
    if (ret != ESP_OK) ESP_LOGW(TAG, "ADC btn init failed");

    /* Wi-Fi */
    ESP_LOGI(TAG, "Connecting Wi-Fi...");
    ret = wifi_app_init();
    if (ret != ESP_OK) ESP_LOGE(TAG, "Wi-Fi FAILED! Check SSID/password.");

    /* HTTP 服务器 — Week 2: 加入采集任务上下文 */
    collect_task_t collect_task = {0};
    sensor_ctx_t ctx = {
        .imu = imu, .btn = btn_handle, .device_id = DEVICE_ID,
        .task = &collect_task,
    };
    http_server_start(&ctx);

    ESP_LOGI(TAG, "READY! Open http://%s/ in browser (Week 2: +collect)", wifi_app_get_ip());

    /* 主循环 — Week 2: 含手动采集任务处理 */
    qma6100p_acce_value_t accel;
    int diag_cnt = 0;
    while (1) {
        /* ---- Week 2: 处理手动采集任务 ---- */
        if (collect_task.status == COLLECT_SUBMITTED) {
            /* 设备收到指令，标记为 RECEIVED */
            ESP_LOGI(TAG, "[TASK] Received: %s", collect_task.request_id);
            collect_task.status = COLLECT_RECEIVED;
            vTaskDelay(pdMS_TO_TICKS(50));
        }

        if (collect_task.status == COLLECT_RECEIVED) {
            /* 执行一次新采集 */
            if (imu && qma6100p_get_acce(imu, &accel) == ESP_OK) {
                collect_task.accel_x = (int)(accel.acce_x * 1000);
                collect_task.accel_y = (int)(accel.acce_y * 1000);
                collect_task.accel_z = (int)(accel.acce_z * 1000);
            } else {
                collect_task.accel_x = 0;
                collect_task.accel_y = 0;
                collect_task.accel_z = 0;
            }
            if (btn_handle) {
                adc_button_t btn = adc_button_read(btn_handle);
                strncpy(collect_task.button, adc_button_name(btn),
                        sizeof(collect_task.button) - 1);
            } else {
                strncpy(collect_task.button, "disabled",
                        sizeof(collect_task.button) - 1);
            }
            collect_task.completed_at_us = esp_timer_get_time();

            if (imu) {
                collect_task.status = COLLECT_COMPLETED;
                ESP_LOGI(TAG, "[TASK] Completed: %s X=%d Y=%d Z=%d",
                         collect_task.request_id,
                         collect_task.accel_x,
                         collect_task.accel_y,
                         collect_task.accel_z);
            } else {
                collect_task.status = COLLECT_FAILED;
                ESP_LOGI(TAG, "[TASK] Failed: %s (no IMU)",
                         collect_task.request_id);
            }
        }

        /* ---- DIAG: read status registers (reduced frequency) ---- */
        if (imu && diag_cnt % 10 == 0) {
            uint8_t sts[6];
            qma6100p_read_reg(imu, 0x09, &sts[0], 1);
            qma6100p_read_reg(imu, 0x0A, &sts[1], 1);
            qma6100p_read_reg(imu, 0x0E, &sts[2], 1);
            qma6100p_read_reg(imu, 0x10, &sts[3], 1);
            qma6100p_read_reg(imu, 0x11, &sts[4], 1);
            qma6100p_read_reg(imu, 0x0F, &sts[5], 1);
            ESP_LOGI(TAG, "[DIAG #%d] STS: 10=%02X 11=%02X(bit7=%d) 0F=%02X",
                     diag_cnt, sts[3], sts[4], (sts[4] >> 7) & 1, sts[5]);
        }

        if (imu && qma6100p_get_acce(imu, &accel) == ESP_OK) {
            ESP_LOGI(TAG, "IMU: X=%6d Y=%6d Z=%6d mg",
                     (int)(accel.acce_x * 1000),
                     (int)(accel.acce_y * 1000),
                     (int)(accel.acce_z * 1000));
        }
        if (btn_handle) {
            adc_button_t btn = adc_button_read(btn_handle);
            if (btn != BTN_NONE)
                ESP_LOGI(TAG, "BTN: %s", adc_button_name(btn));
        }

        diag_cnt++;
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}