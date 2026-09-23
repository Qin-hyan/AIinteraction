/**
 * @file adc_button.h
 * @brief ADC 按键检测驱动 (GPIO1 / ADC1_CH0)
 *
 * ESP32-S3-EYE v2.2 按键分压网络:
 *   MENU: 2.41V   PLAY: 1.98V   DOWN: 0.38V   UP+: 0.82V
 *   无按键按下: ~3.3V
 */

#ifndef ADC_BUTTON_H
#define ADC_BUTTON_H

#include "esp_err.h"
#include "hal/adc_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ================================================================
 * 按键枚举
 * ================================================================ */
typedef enum {
    BTN_NONE = 0,   /**< 无按键按下 */
    BTN_MENU,       /**< MENU 键 (2.41V) */
    BTN_PLAY,       /**< PLAY 键 (1.98V) */
    BTN_DOWN,       /**< DOWN 键 (0.38V) */
    BTN_UP,         /**< UP+ 键  (0.82V) */
    BTN_UNKNOWN     /**< 无法识别的电压 */
} adc_button_t;

/* ================================================================
 * 句柄
 * ================================================================ */
typedef void *adc_button_handle_t;

/* ================================================================
 * API
 * ================================================================ */

/**
 * @brief 初始化 ADC 按键检测
 * @param[out] handle 返回句柄
 * @return ESP_OK 成功
 */
esp_err_t adc_button_init(adc_button_handle_t *handle);

/**
 * @brief 读取当前按下的按键
 * @param[in]  handle 句柄
 * @return 当前按键枚举值
 */
adc_button_t adc_button_read(adc_button_handle_t handle);

/**
 * @brief 获取按键名称字符串
 * @param[in] btn 按键枚举值
 * @return 按键名称
 */
const char *adc_button_name(adc_button_t btn);

/**
 * @brief 反初始化
 * @param[in] handle 句柄
 */
void adc_button_deinit(adc_button_handle_t handle);

#ifdef __cplusplus
}
#endif

#endif /* ADC_BUTTON_H */