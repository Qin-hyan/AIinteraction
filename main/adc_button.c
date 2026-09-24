/**
 * @file adc_button.c
 * @brief ADC 按键检测驱动实现
 */

#include "adc_button.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdlib.h>

static const char *TAG = "ADC_BTN";

/* ADC 参数 */
#define ADC_UNIT          ADC_UNIT_1
#define ADC_CHANNEL       ADC_CHANNEL_0   /* GPIO1 */
#define ADC_ATTEN         ADC_ATTEN_DB_12  /* 0–3.3V range */
#define ADC_BITWIDTH      ADC_BITWIDTH_12  /* 0–4095 */

/* 按键电压阈值 (mV)，在 3.3V 基准下 */
/* 实际 12-bit 读数 ≈ voltage_mV * 4096 / 3300 */
#define BTN_MENU_MV       2410
#define BTN_PLAY_MV       1980
#define BTN_UP_MV         820
#define BTN_DOWN_MV       380

/* 容差范围 mV (允许的电压波动) */
#define BTN_TOLERANCE_MV   150

/* 无按键按下阈值 (接近 VDD=3300mV) */
#define BTN_RELEASE_MV     3000

typedef struct {
    adc_oneshot_unit_handle_t adc_handle;
    adc_cali_handle_t         cali_handle;
    bool                      calibrated;
} adc_button_ctx_t;

/* ---- 读取 ADC 电压 (mV) ---- */
static int adc_read_mv(adc_button_ctx_t *ctx)
{
    int raw = 0;
    adc_oneshot_read(ctx->adc_handle, ADC_CHANNEL, &raw);

    int voltage = 0;
    if (ctx->calibrated) {
        adc_cali_raw_to_voltage(ctx->cali_handle, raw, &voltage);
    } else {
        /* 无校准: 粗略转换 */
        voltage = raw * 3300 / 4096;
    }
    return voltage;
}

/* ---- 电压 → 按键 ---- */
static adc_button_t voltage_to_button(int mv)
{
    if (mv > BTN_RELEASE_MV) return BTN_NONE;

    if (abs(mv - BTN_MENU_MV) < BTN_TOLERANCE_MV)   return BTN_MENU;
    if (abs(mv - BTN_PLAY_MV) < BTN_TOLERANCE_MV)   return BTN_PLAY;
    if (abs(mv - BTN_UP_MV)   < BTN_TOLERANCE_MV)   return BTN_UP;
    if (abs(mv - BTN_DOWN_MV) < BTN_TOLERANCE_MV)   return BTN_DOWN;

    return BTN_UNKNOWN;
}

/* ================================================================
 * 公开 API
 * ================================================================ */

esp_err_t adc_button_init(adc_button_handle_t *handle)
{
    adc_button_ctx_t *ctx = calloc(1, sizeof(adc_button_ctx_t));
    if (!ctx) return ESP_ERR_NO_MEM;

    /* One-shot ADC 初始化 */
    adc_oneshot_unit_init_cfg_t init_cfg = {
        .unit_id = ADC_UNIT,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    esp_err_t ret = adc_oneshot_new_unit(&init_cfg, &ctx->adc_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ADC oneshot init failed");
        free(ctx);
        return ret;
    }

    /* 配置通道 */
    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten    = ADC_ATTEN,
        .bitwidth = ADC_BITWIDTH,
    };
    ret = adc_oneshot_config_channel(ctx->adc_handle, ADC_CHANNEL,
                                      &chan_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ADC channel config failed");
        adc_oneshot_del_unit(ctx->adc_handle);
        free(ctx);
        return ret;
    }

    /* 尝试 ADC 校准 (eFuse VREF) */
    adc_cali_curve_fitting_config_t cali_cfg = {
        .unit_id  = ADC_UNIT,
        .atten    = ADC_ATTEN,
        .bitwidth = ADC_BITWIDTH,
    };
    ret = adc_cali_create_scheme_curve_fitting(&cali_cfg, &ctx->cali_handle);
    ctx->calibrated = (ret == ESP_OK);
    if (ctx->calibrated) {
        ESP_LOGI(TAG, "ADC calibration enabled");
    } else {
        ESP_LOGW(TAG, "ADC calibration not available, using raw");
    }

    ESP_LOGI(TAG, "ADC button ready (GPIO1/ADC1_CH0)");
    *handle = ctx;
    return ESP_OK;
}

adc_button_t adc_button_read(adc_button_handle_t handle)
{
    adc_button_ctx_t *ctx = (adc_button_ctx_t *)handle;
    if (!ctx) return BTN_NONE;

    /* 多次采样取均值去抖 */
    int sum = 0;
    for (int i = 0; i < 8; i++) {
        sum += adc_read_mv(ctx);
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    int avg_mv = sum / 8;

    adc_button_t btn = voltage_to_button(avg_mv);
    ESP_LOGV(TAG, "ADC=%dmV → %s", avg_mv, adc_button_name(btn));
    return btn;
}

const char *adc_button_name(adc_button_t btn)
{
    switch (btn) {
        case BTN_NONE:    return "NONE";
        case BTN_MENU:    return "MENU";
        case BTN_PLAY:    return "PLAY";
        case BTN_DOWN:    return "DOWN";
        case BTN_UP:      return "UP+";
        case BTN_UNKNOWN: return "UNKNOWN";
        default:          return "?";
    }
}

void adc_button_deinit(adc_button_handle_t handle)
{
    adc_button_ctx_t *ctx = (adc_button_ctx_t *)handle;
    if (!ctx) return;
    if (ctx->calibrated) adc_cali_delete_scheme_curve_fitting(ctx->cali_handle);
    adc_oneshot_del_unit(ctx->adc_handle);
    free(ctx);
}