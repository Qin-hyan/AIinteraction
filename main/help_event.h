/**
 * @file help_event.h
 * @brief Week 03: 教学测试消息 — 实体按键触发与物理反馈闭环
 *
 * 本模块实现：
 *   实体按键 → 去抖 → 生成唯一 event_id → 本地反馈 →
 *   发送教学测试消息 → Web/HTTP 接收 → 远端证据返回 → 更新物理反馈 →
 *   支持取消/回应
 *
 * 【教学测试专用，非真实紧急求助】
 */

#ifndef HELP_EVENT_H
#define HELP_EVENT_H

#include "esp_err.h"
#include "adc_button.h"
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ================================================================
 * Week 03: 教学测试消息状态枚举
 * ================================================================ */

/** @brief 帮助事件状态 */
typedef enum {
    HELP_IDLE = 0,          /**< 无活跃事件，等待按键 */
    HELP_TRIGGERED,          /**< 按键已检测，本地确认触发 */
    HELP_SENDING,            /**< 正在发送到 Web/HTTP */
    HELP_SENT,               /**< 已发送，等待远端接收确认 */
    HELP_REMOTE_RECEIVED,    /**< 远端已确认接收 */
    HELP_CANCELLED,          /**< 被取消/回应 */
    HELP_FAILED,             /**< 发送失败（网络/超时） */
} help_status_t;

/* ================================================================
 * Week 03: 帮助事件数据结构
 * ================================================================ */

/** @brief 单次教学测试消息事件 */
typedef struct {
    char            help_id[32];        /**< 唯一事件 ID: help-{ms}-{counter:04d} */
    help_status_t   status;             /**< 当前状态 */
    adc_button_t    trigger_button;     /**< 触发按键 */
    int             event_counter;      /**< 事件计数器（每次按键 +1） */
    /* 时间戳（μs since boot，相对时间） */
    int64_t         triggered_at_us;    /**< 按键触发时间 */
    int64_t         sent_at_us;         /**< 发送完成时间 */
    int64_t         remote_received_at_us; /**< 远端确认接收时间 */
    int64_t         cancelled_at_us;    /**< 取消时间 */
    /* 远端证据 */
    bool            remote_confirmed;   /**< 远端是否已确认接收 */
    char            remote_note[64];    /**< 远端返回的备注 */
} help_event_t;

/* ================================================================
 * Week 03: 按键去抖状态机（独立于 help_event）
 * ================================================================ */

/**
 * @brief 按键去抖上下文
 *
 * 实现边沿检测（press/release），防止按住时重复触发。
 * 在主循环中定期调用 help_button_fsm_update() 推进。
 */
typedef struct {
    adc_button_t    last_stable;        /**< 上一次稳定状态 */
    adc_button_t    current_raw;        /**< 当前原始读数 */
    int             stable_count;       /**< 同一读数的连续采样计数 */
    int             debounce_threshold; /**< 去抖阈值（连续相同次数） */
    bool            event_pending;      /**< 是否有新按下事件待处理 */
    adc_button_t    pending_button;     /**< 待处理的按键类型 */
    int64_t         pending_at_us;      /**< 事件发生时间 */
} help_button_fsm_t;

/* ================================================================
 * Week 03: LED 反馈模式
 * ================================================================ */

/** @brief LED 反馈模式 */
typedef enum {
    LED_PATTERN_IDLE,           /**< 空闲：LED 关闭 */
    LED_PATTERN_TRIGGERED,      /**< 已触发：快速闪烁 (本地确认) */
    LED_PATTERN_SENDING,        /**< 发送中：慢速闪烁 */
    LED_PATTERN_SENT,           /**< 已发送：脉冲 */
    LED_PATTERN_REMOTE_RECEIVED,/**< 远端已收到：常亮 */
    LED_PATTERN_CANCELLED,      /**< 已取消：LED 关闭 */
    LED_PATTERN_FAILED,         /**< 失败：长亮警告 */
} led_pattern_t;

/* ================================================================
 * 事件生命周期管理
 * ================================================================ */

/**
 * @brief 初始化帮助事件管理器
 * @param[out] event 事件结构体（调用方分配）
 */
void help_event_init(help_event_t *event);

/**
 * @brief 初始化按键去抖状态机
 * @param[out] fsm 状态机上下文
 * @param[in]  debounce_threshold 去抖阈值（连续相同采样次数）
 */
void help_button_fsm_init(help_button_fsm_t *fsm, int debounce_threshold);

/**
 * @brief 按键去抖更新
 *
 * 每轮主循环调用一次，传入当前 ADC 读数。
 * 检测到有效按下时设置 event_pending = true。
 *
 * @param[in,out] fsm   状态机上下文
 * @param[in]     btn   当前 ADC 按键读数
 */
void help_button_fsm_update(help_button_fsm_t *fsm, adc_button_t btn);

/**
 * @brief 创建新的教学测试消息事件
 *
 * 仅在被按键触发（本地确认）后调用。
 * 生成唯一 help_id，状态设为 HELP_TRIGGERED。
 *
 * @param[in,out] event  事件结构
 * @param[in]     button 触发按键
 */
void help_event_trigger(help_event_t *event, adc_button_t button);

/**
 * @brief 更新事件状态为正在发送
 * @param[in,out] event 事件结构
 */
void help_event_start_sending(help_event_t *event);

/**
 * @brief 更新事件状态为已发送
 * @param[in,out] event 事件结构
 */
void help_event_mark_sent(help_event_t *event);

/**
 * @brief 更新事件状态为远端已收到
 * @param[in,out] event  事件结构
 * @param[in]     note   远端返回的备注信息
 */
void help_event_mark_remote_received(help_event_t *event, const char *note);

/**
 * @brief 取消事件
 * @param[in,out] event 事件结构
 */
void help_event_cancel(help_event_t *event);

/**
 * @brief 标记事件失败
 * @param[in,out] event 事件结构
 */
void help_event_fail(help_event_t *event);

/**
 * @brief 获取事件状态的可读名称
 * @param[in] status 状态枚举
 * @return 状态名称字符串
 */
const char *help_status_name(help_status_t status);

/**
 * @brief 根据 help_event 状态推导当前 LED 反馈模式
 * @param[in] event 事件结构
 * @return LED 模式
 */
led_pattern_t help_event_led_pattern(const help_event_t *event);

/**
 * @brief LED 反馈更新（主循环每轮调用）
 *
 * 根据当前模式驱动 GPIO，实现闪烁/脉冲等效果。
 *
 * @param[in] pattern  当前 LED 模式
 * @param[in] now_us   当前时间 (μs since boot)
 */
void help_led_update(led_pattern_t pattern, int64_t now_us);

/**
 * @brief 重置帮助事件为空闲状态（用于测试/恢复）
 * @param[in,out] event 事件结构
 */
void help_event_reset(help_event_t *event);

#ifdef __cplusplus
}
#endif

#endif /* HELP_EVENT_H */