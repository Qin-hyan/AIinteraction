/**
 * @file help_event.c
 * @brief Week 03: 教学测试消息事件管理实现
 */

#include "help_event.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <string.h>
#include <stdio.h>

static const char *TAG = "W3-EVENT";

/* LED 引脚定义 */
#define LED_GPIO            GPIO_NUM_38
#define MODULE_PWR_LED      GPIO_NUM_3

/* 全局事件计数器（设备级，不随事件重置） */
static int g_event_counter = 0;

/* LED 闪烁时间参数 */
#define LED_BLINK_FAST_MS   150
#define LED_BLINK_SLOW_MS   500
#define LED_PULSE_ON_MS     200
#define LED_PULSE_OFF_MS    800

/* ================================================================
 * 内部按键去抖状态机（不再对外暴露）
 * ================================================================ */

/** @brief 按键去抖上下文（内部） */
typedef struct {
    adc_button_t    last_stable;        /**< 上一次稳定状态 */
    adc_button_t    current_raw;        /**< 当前原始读数 */
    int             stable_count;       /**< 同一读数的连续采样计数 */
    int             debounce_threshold; /**< 去抖阈值（连续相同次数） */
    bool            event_pending;      /**< 是否有新按下事件待处理 */
    adc_button_t    pending_button;     /**< 待处理的按键类型 */
    int64_t         pending_at_us;      /**< 事件发生时间 */
} help_button_fsm_t;

/**
 * @brief 内部初始化按键去抖状态机
 */
static void help_button_fsm_init(help_button_fsm_t *fsm, int debounce_threshold)
{
    if (!fsm) return;
    memset(fsm, 0, sizeof(*fsm));
    fsm->last_stable = BTN_NONE;
    fsm->current_raw = BTN_NONE;
    fsm->stable_count = 0;
    fsm->debounce_threshold = debounce_threshold > 0 ? debounce_threshold : 4;
    fsm->event_pending = false;
    fsm->pending_button = BTN_NONE;
}

/**
 * @brief 内部按键去抖更新（边沿检测）
 */
static void help_button_fsm_update(help_button_fsm_t *fsm, adc_button_t btn)
{
    if (!fsm) return;

    if (btn != fsm->current_raw) {
        fsm->current_raw = btn;
        fsm->stable_count = 1;
        return;
    }

    fsm->stable_count++;

    if (fsm->stable_count >= fsm->debounce_threshold) {
        adc_button_t old_stable = fsm->last_stable;
        fsm->last_stable = btn;

        /* 边沿检测：从 NONE → 有效按键（按下） */
        if (old_stable == BTN_NONE && btn != BTN_NONE && btn != BTN_UNKNOWN) {
            ESP_LOGI(TAG, "btn pressed: %s", adc_button_name(btn));
            fsm->event_pending = true;
            fsm->pending_button = btn;
            fsm->pending_at_us = esp_timer_get_time();
        }
        /* 从有效按键 → NONE（释放） */
        else if (old_stable != BTN_NONE && btn == BTN_NONE) {
            ESP_LOGI(TAG, "btn released: was %s", adc_button_name(old_stable));
        }
    }
}

/* ================================================================
 * 按键扫描 FreeRTOS 任务（~30 Hz）
 * ================================================================ */

#define BTN_TASK_STACK_SIZE   2048
#define BTN_TASK_PRIORITY     5
#define BTN_SCAN_MS           33       /* ~30 Hz */

static QueueHandle_t        s_btn_queue    = NULL;
static adc_button_handle_t  s_btn_handle   = NULL;

/**
 * @brief FreeRTOS 任务：独立按键扫描
 *
 * 以约 30 Hz 周期采样 ADC，运行去抖 FSM，
 * 检测到有效按下后通过队列通知主循环。
 */
static void button_scan_task(void *arg)
{
    (void)arg;
    help_button_fsm_t fsm;

    help_button_fsm_init(&fsm, 3);  /* 阈值 3：保留原值 */

    ESP_LOGI(TAG, "button scan task started (~30 Hz, debounce threshold=%d)",
             fsm.debounce_threshold);

    while (1) {
        adc_button_t btn = adc_button_read(s_btn_handle);
        help_button_fsm_update(&fsm, btn);

        if (fsm.event_pending) {
            fsm.event_pending = false;
            /* 非阻塞发队列；若主循环未消费则丢弃旧事件（一次按下只产生一次事件） */
            if (xQueueSend(s_btn_queue, &fsm.pending_button, 0) != pdTRUE) {
                ESP_LOGW(TAG, "btn queue full, dropping event (main loop busy?)");
            }
        }

        vTaskDelay(pdMS_TO_TICKS(BTN_SCAN_MS));
    }
}

/* ================================================================
 * 公开 API
 * ================================================================ */

esp_err_t help_button_service_start(adc_button_handle_t btn_handle)
{
    if (!btn_handle) {
        ESP_LOGE(TAG, "help_button_service_start: btn_handle is NULL");
        return ESP_ERR_INVALID_ARG;
    }

    if (s_btn_queue != NULL) {
        ESP_LOGW(TAG, "button service already started");
        return ESP_OK;
    }

    s_btn_handle = btn_handle;

    /* 队列长度 = 1：一次只容纳一个待处理事件 */
    s_btn_queue = xQueueCreate(1, sizeof(adc_button_t));
    if (!s_btn_queue) {
        ESP_LOGE(TAG, "failed to create button event queue");
        return ESP_ERR_NO_MEM;
    }

    BaseType_t ret = xTaskCreate(button_scan_task,
                                  "btn_scan",
                                  BTN_TASK_STACK_SIZE,
                                  NULL,
                                  BTN_TASK_PRIORITY,
                                  NULL);
    if (ret != pdPASS) {
        vQueueDelete(s_btn_queue);
        s_btn_queue = NULL;
        ESP_LOGE(TAG, "failed to create button scan task");
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "button scanning service started (~30 Hz)");
    return ESP_OK;
}

bool help_button_get_pending(adc_button_t *btn)
{
    if (!s_btn_queue || !btn) return false;
    return xQueueReceive(s_btn_queue, btn, 0) == pdTRUE;
}

/* ================================================================
 * 事件生命周期
 * ================================================================ */

void help_event_init(help_event_t *event)
{
    if (!event) return;
    memset(event, 0, sizeof(*event));
    event->status = HELP_IDLE;
    event->help_id[0] = '\0';
}

void help_event_trigger(help_event_t *event, adc_button_t button)
{
    if (!event) return;
    g_event_counter++;
    event->event_counter = g_event_counter;
    event->trigger_button = button;
    event->triggered_at_us = esp_timer_get_time();
    event->remote_confirmed = false;
    event->remote_note[0] = '\0';

    int64_t now_ms = event->triggered_at_us / 1000;
    snprintf(event->help_id, sizeof(event->help_id),
             "help-%lld-%04d", (long long)now_ms, g_event_counter % 10000);

    event->status = HELP_TRIGGERED;

    ESP_LOGI(TAG, "created id=%s btn=%s counter=%d (LOCAL CONFIRMED)",
             event->help_id, adc_button_name(button), g_event_counter);
}

void help_event_start_sending(help_event_t *event)
{
    if (!event) return;
    event->status = HELP_SENDING;
    ESP_LOGI(TAG, "sending id=%s", event->help_id);
}

void help_event_mark_sent(help_event_t *event)
{
    if (!event) return;
    event->sent_at_us = esp_timer_get_time();
    event->status = HELP_SENT;
    ESP_LOGI(TAG, "sent id=%s", event->help_id);
}

void help_event_mark_remote_received(help_event_t *event, const char *note)
{
    if (!event) return;
    event->remote_received_at_us = esp_timer_get_time();
    event->remote_confirmed = true;
    event->status = HELP_REMOTE_RECEIVED;
    if (note) {
        snprintf(event->remote_note, sizeof(event->remote_note), "%s", note);
    }
    ESP_LOGI(TAG, "remote-received id=%s note=%s", event->help_id,
             note ? note : "");
}

void help_event_cancel(help_event_t *event)
{
    if (!event) return;
    event->cancelled_at_us = esp_timer_get_time();
    event->status = HELP_CANCELLED;
    event->remote_confirmed = false;
    ESP_LOGI(TAG, "cancelled id=%s", event->help_id);
}

void help_event_fail(help_event_t *event)
{
    if (!event) return;
    event->failed_at_us = esp_timer_get_time();
    event->status = HELP_FAILED;
    event->remote_confirmed = false;
    ESP_LOGW(TAG, "failed id=%s", event->help_id);
}

void help_event_reset(help_event_t *event)
{
    if (!event) return;
    event->status = HELP_IDLE;
    event->remote_confirmed = false;
    event->failed_at_us = 0;
    ESP_LOGI(TAG, "reset to IDLE (was %s)",
             event->help_id[0] ? event->help_id : "empty");
}

const char *help_status_name(help_status_t status)
{
    switch (status) {
        case HELP_IDLE:            return "idle";
        case HELP_TRIGGERED:       return "triggered";
        case HELP_SENDING:         return "sending";
        case HELP_SENT:            return "sent";
        case HELP_REMOTE_RECEIVED: return "remote_received";
        case HELP_CANCELLED:       return "cancelled";
        case HELP_FAILED:          return "failed";
        default:                   return "unknown";
    }
}

/* ================================================================
 * LED 反馈
 * ================================================================ */

static bool led_blink(int period_ms, int64_t now_us)
{
    int64_t now_ms = now_us / 1000;
    int half = period_ms / 2;
    return ((now_ms % period_ms) < half);
}

led_pattern_t help_event_led_pattern(const help_event_t *event)
{
    if (!event) return LED_PATTERN_IDLE;

    switch (event->status) {
        case HELP_IDLE:            return LED_PATTERN_IDLE;
        case HELP_TRIGGERED:       return LED_PATTERN_TRIGGERED;
        case HELP_SENDING:         return LED_PATTERN_SENDING;
        case HELP_SENT:            return LED_PATTERN_SENT;
        case HELP_REMOTE_RECEIVED: return LED_PATTERN_REMOTE_RECEIVED;
        case HELP_CANCELLED:       return LED_PATTERN_CANCELLED;
        case HELP_FAILED:          return LED_PATTERN_FAILED;
        default:                   return LED_PATTERN_IDLE;
    }
}

void help_led_update(led_pattern_t pattern, int64_t now_us)
{
    bool led_on = false;

    switch (pattern) {
        case LED_PATTERN_IDLE:
        case LED_PATTERN_CANCELLED:
            led_on = false;
            break;

        case LED_PATTERN_TRIGGERED:
            led_on = led_blink(LED_BLINK_FAST_MS, now_us);
            break;

        case LED_PATTERN_SENDING:
            led_on = led_blink(LED_BLINK_SLOW_MS, now_us);
            break;

        case LED_PATTERN_SENT:
            {
                int cycle = LED_PULSE_ON_MS + LED_PULSE_OFF_MS;
                led_on = ((now_us / 1000) % cycle) < LED_PULSE_ON_MS;
            }
            break;

        case LED_PATTERN_REMOTE_RECEIVED:
            led_on = true;
            break;

        case LED_PATTERN_FAILED:
            led_on = true;
            break;
    }

    gpio_set_level(LED_GPIO, led_on ? 1 : 0);
    gpio_set_level(MODULE_PWR_LED, led_on ? 1 : 0);
}