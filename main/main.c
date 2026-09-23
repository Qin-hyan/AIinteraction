/**
 * @file main.c
 * @brief 第1周：传感数据采集与 Web 展示系统
 *
 * ESP32-S3-EYE v2.2 + SUB v1.1
 * - QMA6100P 三轴加速度计 (I2C: SDA=GPIO4, SCL=GPIO5, addr=0x12)
 * - ADC 按键检测 (GPIO1 / ADC1_CH0)
 * - Wi-Fi STA 连接
 * - HTTP 服务器 + Web 仪表盘 (500ms 刷新)
 *
 * 初始化顺序: UART → PSRAM → I2C(QMA6100P) → ADC → LED → Wi-Fi → HTTP
 */

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_spiram.h"
#include "nvs_flash.h"
#include "driver/i2c_master.h"
#include "driver/gpio.h"
#include "esp_mac.h"

#include "qma6100p.h"
#include "adc_button.h"
#include "wifi_app.h"
#include "http_server.h"

static const char *TAG = "MAIN";

/* ---- 硬件引脚定义 ---- */
#define I2C_SDA_GPIO        GPIO_NUM_4
#define I2C_SCL_GPIO        GPIO_NUM_5
#define QMA6100P_I2C_ADDR   0x12

#define LED_GPIO            GPIO_NUM_38  /* RGB LED (SUB V1.1) */
#define MODULE_PWR_LED      GPIO_NUM_3   /* 模组电源指示灯 (开漏!) */

#define DEVICE_ID           "esp32s3-eye-wk01"

/* ================================================================
 * PSRAM 测试
 * ================================================================ */
static void psram_test(void)
{
    size_t psram_size = esp_spiram_get_size();
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
    /* RGB LED - 低电平点亮 */
    gpio_config_t led_cfg = {
        .pin_bit_mask = BIT64(LED_GPIO),
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
/* ================================================================
 * I2C 总线初始化 (共享: QMA6100P + 摄像头)
 * ================================================================ */
static esp_err_t i2c_bus_init(i2c_master_bus_handle_t *bus_handle)
{
    i2c_master_bus_config_t bus_cfg = {
        .clk_source    = I2C_CLK_SRC_DEFAULT,
        .i2c_port      = I2C_NUM_0,
        .scl_io_num    = I2C_SCL_GPIO,
        .sda_io_num    = I2C_SDA_GPIO,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    return i2c_new_master_bus(&bus_cfg, bus_handle);
}

/* ================================================================
 * 主入口
 * ================================================================ */
void app_main(void)
{
    ESP_LOGI(TAG, "Week 1: Sensor Data + Web Dashboard");
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

    /* I2C 总线 */
    i2c_master_bus_handle_t i2c_bus = NULL;
    ESP_ERROR_CHECK(i2c_bus_init(&i2c_bus));

    /* QMA6100P (先试 0x12, 再试 0x13) */
    qma6100p_handle_t *imu = NULL;
    ret = qma6100p_init(i2c_bus, QMA6100P_I2C_ADDR, &imu);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Retry QMA6100P at 0x13...");
        ret = qma6100p_init(i2c_bus, 0x13, &imu);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "QMA6100P not found - continuing");
        }
    }

    /* ADC 按键 */
    adc_button_handle_t btn_handle = NULL;
    ret = adc_button_init(&btn_handle);
    if (ret != ESP_OK) ESP_LOGW(TAG, "ADC btn init failed");

    /* Wi-Fi */
    ESP_LOGI(TAG, "Connecting Wi-Fi...");
    ret = wifi_app_init();
    if (ret != ESP_OK) ESP_LOGE(TAG, "Wi-Fi FAILED! Check SSID/password.");

    /* HTTP 服务器 */
    sensor_ctx_t ctx = {
        .imu = imu, .btn = btn_handle, .device_id = DEVICE_ID,
    };
    http_server_start(&ctx);

    ESP_LOGI(TAG, "READY! Open http://%s/ in browser", wifi_app_get_ip());

    /* 主循环 - 串口打印传感器数据 */
    qma6100p_data_t imu_data;
    while (1) {
        if (imu && qma6100p_read_accel(imu, &imu_data) == ESP_OK) {
            ESP_LOGI(TAG, "IMU: X=%6d Y=%6d Z=%6d mg",
                     imu_data.x, imu_data.y, imu_data.z);
        }
        if (btn_handle) {
            adc_button_t btn = adc_button_read(btn_handle);
            if (btn != BTN_NONE)
                ESP_LOGI(TAG, "BTN: %s", adc_button_name(btn));
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
    };
    gpio_config(&led_cfg);
    gpio_set_level(LED_GPIO, 1); /* 初始熄灭 */

    /* 模组电源指示灯 - 必须开漏模式! */
    gpio_config_t pwr_cfg = {
        .pin_bit_mask = BIT64(MODULE_PWR_LED),
        .mode         = GPIO_MODE_OUTPUT_OD,  /* OPEN-DRAIN */
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&pwr_cfg);
    gpio_set_level(MODULE_PWR_LED, 1); /* 开漏: 1=高阻(灭) */

    /* 双 LED 闪烁 3 次 */
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