/**
 * @file help_uplink.h
 * @brief Week 03: 求助事件上行推送 — ESP32 → VPS HTTP POST
 *
 * 本模块将 help_event_t 事件构造为 JSON，通过 esp_http_client
 * 向外部 VPS 服务端发送 HTTP POST 请求。
 *
 * 【教学测试专用，非真实紧急求助】
 */

#ifndef HELP_UPLINK_H
#define HELP_UPLINK_H

#include "esp_err.h"
#include "help_event.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief 检查 VPS URL 是否已在 menuconfig 中配置
 *
 * @return true 已配置（CONFIG_HELP_VPS_URL 非空）
 * @return false 未配置（上行功能不可用）
 */
bool help_uplink_is_configured(void);

/**
 * @brief 发送求助事件到 VPS 服务器（同步阻塞，最长 10 秒）
 *
 * 构造 JSON 载荷并通过 HTTP POST 推送到 CONFIG_HELP_VPS_URL。
 * VPS URL 未配置时返回 ESP_ERR_INVALID_STATE，不伪装发送成功。
 *
 * **本函数不修改 event 的任何字段（状态、时间戳等）。**
 * 调用方应在调用前自行更新事件状态（如 help_event_start_sending /
 * help_event_mark_sent），并根据返回值判断上传结果后再驱动后续状态转换。
 *
 * **本函数为同步阻塞调用，不能在主循环（1 Hz）中直接使用。**
 * 主循环应使用异步接口（help_uplink_request_send + help_uplink_poll）。
 *
 * JSON 字段直接从 help_event_t 映射：
 * | JSON 字段           | 来源                           | 类型            |
 * |---------------------|--------------------------------|-----------------|
 * | help_id             | event->help_id                 | string          |
 * | status              | help_status_name(event->status)| string          |
 * | trigger_button      | adc_button_name(...)           | string          |
 * | event_counter       | event->event_counter           | number          |
 * | triggered_at_us     | event->triggered_at_us         | number 或 null  |
 * | sent_at_us          | event->sent_at_us              | number 或 null  |
 * | remote_received_at_us | event->remote_received_at_us | number 或 null  |
 * | cancelled_at_us     | event->cancelled_at_us         | number 或 null  |
 * | remote_confirmed    | event->remote_confirmed        | boolean         |
 * | remote_note         | event->remote_note             | string          |
 *
 * 时间戳语义（单位 μs since boot）：
 * - triggered_at_us:     按键触发时刻
 * - sent_at_us:          **设备端** HTTP POST 完成时刻（非 VPS 上传时间）
 * - remote_received_at_us: VPS 确认接收时刻（由远端 API 设置）
 * - cancelled_at_us:     取消操作时刻
 * 未发生的时间戳值为 0，序列化为 JSON null。
 *
 * @param[in] event  当前求助事件（const，不会被修改）
 * @return
 *     - ESP_OK                发送成功（HTTP 2xx 响应）
 *     - ESP_ERR_INVALID_ARG   event 参数为空
 *     - ESP_ERR_INVALID_STATE VPS URL 未配置（CONFIG_HELP_VPS_URL 为空）
 *     - ESP_FAIL              HTTP 请求失败（连接拒绝、超时、非 2xx 等）
 */
esp_err_t help_uplink_send_event(const help_event_t *event);

/* ================================================================
 * 异步接口 — 供主循环使用（避免阻塞 1 Hz 主循环）
 * ================================================================ */

/**
 * @brief 启动上行推送后台任务
 *
 * 创建独立的 FreeRTOS 任务，等待主循环通过 help_uplink_request_send()
 * 触发 HTTP POST。
 *
 * @return ESP_OK 成功，ESP_FAIL 任务创建失败
 */
esp_err_t help_uplink_start(void);

/**
 * @brief 请求后台任务发送事件（非阻塞）
 *
 * 将 event 拷贝到任务内部缓冲区，通知后台任务执行 HTTP POST。
 * 同一时间只有一个请求在处理；若前一个请求尚未完成，返回 false。
 *
 * @param[in] event  待发送事件
 * @return true  请求已被接受
 * @return false 任务正忙或未启动
 */
bool help_uplink_request_send(const help_event_t *event);

/**
 * @brief 轮询后台任务是否完成（非阻塞）
 *
 * 主循环每轮调用此函数检查 HTTP POST 结果。
 *
 * @param[out] out_result  输出结果（ESP_OK 表示 HTTP 2xx）
 * @return true  任务已结束，out_result 有效
 * @return false 任务仍在进行中
 */
bool help_uplink_poll(esp_err_t *out_result);

#ifdef __cplusplus
}
#endif

#endif /* HELP_UPLINK_H */