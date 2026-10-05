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
#include "camera_app.h"

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
 * Week 2: 观测与记录工具
 *   已存观测（板上缓存）＝第 1 周"已存数据"；请求记录＝请求→回执→新观测
 * ================================================================ */

/** @brief 传感源说明（随界面显示，避免用型号推测单位） */
#define SENSOR_SRC  "QMA6100P 三轴加速度 (mg, I2C 0x12)"

/** @brief 相对时间（ms since boot）——板端无可靠墙钟，统一相对时间轴 */
static int64_t ms_since_boot(void)
{
    return esp_timer_get_time() / 1000;
}

/**
 * @brief 保存一条"已存观测"（板上缓存）
 *
 * 只有真实传感器读取成功时才调用本函数，因此观测序号 seq +1 即代表
 * "板端又完成了一次真实采集"，用于在页面上证明不是重显旧值。
 *
 * @param[in] t      任务上下文
 * @param[in] src    来源：OBS_SOURCE_LIVE（手动指令）/ OBS_SOURCE_CADENCE（周期采样）
 * @param[in] req_id 关联请求号（证明观测属于哪一次请求）
 */
static void save_observation(collect_task_t *t, const char *src,
                             const char *req_id, int x, int y, int z,
                             const char *btn)
{
    t->obs_seq++;
    observation_t *o = &t->last;
    o->valid = true;
    snprintf(o->record_id, sizeof(o->record_id), "obs-%05d", t->obs_seq);
    snprintf(o->request_id, sizeof(o->request_id), "%s", req_id);
    snprintf(o->source, sizeof(o->source), "%s", src);
    o->seq = t->obs_seq;
    o->accel_x = x;
    o->accel_y = y;
    o->accel_z = z;
    snprintf(o->button, sizeof(o->button), "%s", btn);
    o->observed_ms = ms_since_boot();   /* 采集时间（相对） */
    o->received_ms = o->observed_ms;    /* 板端"服务端"与设备同一时钟 */
}

/**
 * @brief 写入一条请求记录：请求 → 设备回执 → 新观测
 *
 * 超时/失败也写记录，但 has_observation=false，绝不把旧观测改标为本次完成。
 */
static void push_record(collect_task_t *t)
{
    int i = t->record_head;
    collect_record_t *r = &t->records[i];
    memset(r, 0, sizeof(*r));
    snprintf(r->request_id, sizeof(r->request_id), "%s", t->request_id);
    snprintf(r->status, sizeof(r->status), "%s",
             t->status == COLLECT_COMPLETED ? "completed" :
             t->status == COLLECT_TIMEOUT   ? "timeout"   : "failed");
    r->submitted_ms = (int)(t->submitted_at_us / 1000);
    r->received_ms  = t->received_at_us ? (int)(t->received_at_us / 1000) : -1;
    r->completed_ms = (int)(t->completed_at_us / 1000);
    r->elapsed_ms   = r->completed_ms - r->submitted_ms;

    /* 只有"新观测确实关联本次请求号"才认为带回结果 */
    bool linked = (t->status == COLLECT_COMPLETED) && t->last.valid &&
                  strcmp(t->last.request_id, t->request_id) == 0;
    r->has_observation = linked;
    if (linked) {
        snprintf(r->source, sizeof(r->source), "%s", t->last.source);
        r->seq = t->last.seq;
        r->accel_x = t->last.accel_x;
        r->accel_y = t->last.accel_y;
        r->accel_z = t->last.accel_z;
        snprintf(r->button, sizeof(r->button), "%s", t->last.button);
    } else {
        snprintf(r->source, sizeof(r->source), "%s", OBS_SOURCE_NONE);
        r->seq = 0;
    }
    t->record_head = (i + 1) % COLLECT_RECORD_MAX;
    if (t->record_count < COLLECT_RECORD_MAX) t->record_count++;
}

/* ================================================================
 * Week 3: 保存摄像头抓拍到照片槽位（环形缓冲）
 * ================================================================ */
static void save_capture_slot(collect_task_t *t, camera_frame_t *f,
                               const char *source)
{
    if (!t || !f || !f->buf) return;
    int idx = t->cap_head % CAPTURE_SLOTS_MAX;
    capture_slot_t *s = &t->cap_slots[idx];

    /* 释放旧缓冲区 */
    if (s->jpeg_buf) { free(s->jpeg_buf); s->jpeg_buf = NULL; }

    /* 拷贝 JPEG 数据 */
    s->jpeg_buf = (uint8_t *)malloc(f->len);
    if (!s->jpeg_buf) { ESP_LOGE(TAG, "OOM saving capture slot"); return; }
    memcpy(s->jpeg_buf, f->buf, f->len);
    s->jpeg_len = f->len;
    s->width    = f->width;
    s->height   = f->height;
    s->size_bytes = f->len;
    s->captured_us = f->timestamp_us;
    s->valid    = true;
    s->uploaded = false;
    s->task_seq = t->task_seq;
    snprintf(s->request_id, sizeof(s->request_id), "%s", t->request_id);
    snprintf(s->source, sizeof(s->source), "%s", source);
    snprintf(s->file_name, sizeof(s->file_name), "cap-%05d.jpg",
             t->cap_count + 1);
    snprintf(t->last_cap_request_id, sizeof(t->last_cap_request_id),
             "%s", t->request_id);
    t->cap_head++;
    t->cap_count++;
    ESP_LOGI(TAG, "[CAP] Saved photo #%d (%s) %dx%d, %d bytes",
             t->cap_count, s->file_name, s->width, s->height,
             (int)s->size_bytes);
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

    /* Camera (OV2640) init — Week 3 */
    camera_handle_t cam_handle = NULL;
    {
        esp_err_t cam_ret = camera_app_init(&cam_handle);
        if (cam_ret != ESP_OK) {
            ESP_LOGW(TAG, "Camera init failed: 0x%X — camera features disabled", cam_ret);
            cam_handle = NULL;
        } else {
            ESP_LOGI(TAG, "Camera (OV2640) ready ✓");
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

    /* HTTP 服务器 — Week 2: 加入采集任务上下文 */
    static collect_task_t collect_task = {0};
    collect_task.auto_refresh = true;              /* 默认开启周期上报 */
    collect_task.status = COLLECT_IDLE;
    snprintf(collect_task.last.source, sizeof(collect_task.last.source),
             "%s", OBS_SOURCE_NONE);
    sensor_ctx_t ctx = {
        .imu = imu, .btn = btn_handle,
        .device_id = DEVICE_ID, .sensor_src = SENSOR_SRC,
        .task = &collect_task,
        .cam = cam_handle,
    };
    http_server_start(&ctx);

    /* Camera auto-capture defaults */
    collect_task.auto_cap.interval_s = 10;
    collect_task.auto_cap.batch_limit = 100;
    collect_task.capture_camera = false;

    ESP_LOGI(TAG, "READY! Open http://%s/ in browser (Week 2: +collect)", wifi_app_get_ip());

    /* 主循环 — Week 1 周期上报 + Week 2 采集任务处理 */
    qma6100p_acce_value_t accel;
    int diag_cnt = 0;
    bool cadence_was_on = collect_task.auto_refresh;
    while (1) {
        int64_t now_us = esp_timer_get_time();

        /* ---- 周期上报开关变化留证（串口可核对） ---- */
        if (collect_task.auto_refresh != cadence_was_on) {
            cadence_was_on = collect_task.auto_refresh;
            ESP_LOGI(TAG, "[CADENCE] 周期上报 %s",
                     cadence_was_on ? "已恢复：板端恢复每 1s 采样"
                                    : "已暂停：板端停止周期采样，命令通道保持可用");
        }

        /* ---- 完成条件判定：受理后 COLLECT_TIMEOUT_MS 内未拿到设备结果 ----
         * 超时只说明"暂未收到设备结果"，不代表硬件故障；
         * 超时后本次请求关闭（不再接受迟到结果），旧观测不会被改标为本次完成。*/
        if ((collect_task.status == COLLECT_SUBMITTED ||
             collect_task.status == COLLECT_RECEIVED) &&
            collect_task.deadline_us > 0 && now_us > collect_task.deadline_us) {
            collect_task.status = COLLECT_TIMEOUT;
            collect_task.completed_at_us = now_us;
            ESP_LOGW(TAG, "[TASK] Timeout: %s — 未收到设备结果（不等于硬件故障）",
                     collect_task.request_id);
            push_record(&collect_task);
        }

        /* ---- Week 2: 处理手动采集任务 ---- */
        if (collect_task.status == COLLECT_SUBMITTED) {
            /* 设备已接收指令：登记回执时间，状态进入 RECEIVED */
            collect_task.received_at_us = esp_timer_get_time();
            collect_task.status = COLLECT_RECEIVED;
            ESP_LOGI(TAG, "[TASK] Received: %s (设备已接收，开始执行)",
                     collect_task.request_id);
            vTaskDelay(pdMS_TO_TICKS(50));
        }

        if (collect_task.status == COLLECT_RECEIVED) {
            /* 执行一次真实传感器读取（这就是"新采集"） */
            char btn_name[16] = "NONE";
            if (btn_handle)
                snprintf(btn_name, sizeof(btn_name), "%s",
                         adc_button_name(adc_button_read(btn_handle)));

            int x = 0, y = 0, z = 0;
            bool read_ok = false;
            if (imu && qma6100p_get_acce(imu, &accel) == ESP_OK) {
                x = (int)(accel.acce_x * 1000);
                y = (int)(accel.acce_y * 1000);
                z = (int)(accel.acce_z * 1000);
                read_ok = true;
            }
            collect_task.accel_x = x;
            collect_task.accel_y = y;
            collect_task.accel_z = z;
            snprintf(collect_task.button, sizeof(collect_task.button), "%s", btn_name);
            collect_task.completed_at_us = esp_timer_get_time();

            if (read_ok) {
                collect_task.status = COLLECT_COMPLETED;
                /* 只有真实读取成功才写入"已存观测"，并与本次请求号关联 */
                save_observation(&collect_task, OBS_SOURCE_LIVE,
                                 collect_task.request_id, x, y, z, btn_name);
                ESP_LOGI(TAG, "[TASK] Completed: %s X=%d Y=%d Z=%d → %s (seq=%d)",
                         collect_task.request_id, x, y, z,
                         collect_task.last.record_id, collect_task.last.seq);
            } else {
                collect_task.status = COLLECT_FAILED;
                ESP_LOGW(TAG, "[TASK] Failed: %s（传感器读取失败，未产生新观测）",
                         collect_task.request_id);
            }
            push_record(&collect_task);

            /* ---- Week 3: Camera capture (if requested) ---- */
            if (collect_task.capture_camera && cam_handle) {
                /* give I2C bus time to recover from sensor read */
                vTaskDelay(pdMS_TO_TICKS(150));
                camera_frame_t frame = {0};
                ESP_LOGI(TAG, "[TASK] Starting camera capture for %s", collect_task.request_id);
                if (camera_app_capture(cam_handle, &frame) == ESP_OK) {
                    save_capture_slot(&collect_task, &frame, "manual");
                    camera_app_release_frame(cam_handle, &frame);
                    ESP_LOGI(TAG, "[TASK] Camera captured OK: %d bytes", (int)frame.len);
                } else {
                    ESP_LOGW(TAG, "[TASK] Camera capture FAILED for %s",
                             collect_task.request_id);
                }
                collect_task.capture_camera = false;
            }
            /* 任务完成，复位状态 */
            collect_task.status = COLLECT_IDLE;
        }

        /* ---- Week 1 保留：周期上报（板端每 1s 采样一次并保存观测）----
         * 暂停后不再更新已存观测，旧观测保留原采集时间（数据陈旧 ≠ 硬件故障）。*/
        if (collect_task.auto_refresh &&
            collect_task.status != COLLECT_SUBMITTED &&
            collect_task.status != COLLECT_RECEIVED) {
            if (imu && qma6100p_get_acce(imu, &accel) == ESP_OK) {
                char btn_name[16] = "NONE";
                if (btn_handle)
                    snprintf(btn_name, sizeof(btn_name), "%s",
                             adc_button_name(adc_button_read(btn_handle)));
                int x = (int)(accel.acce_x * 1000);
                int y = (int)(accel.acce_y * 1000);
                int z = (int)(accel.acce_z * 1000);
                save_observation(&collect_task, OBS_SOURCE_CADENCE,
                                 OBS_REQ_CADENCE, x, y, z, btn_name);
                collect_task.poll_count++;
                ESP_LOGI(TAG, "IMU(cadence #%d %s): X=%6d Y=%6d Z=%6d mg",
                         collect_task.poll_count, collect_task.last.record_id,
                         x, y, z);
            }
        }

        /* ---- Week 3: 定时自动抓拍 ---- */
        if (collect_task.auto_cap.enabled && cam_handle) {
            int64_t now = esp_timer_get_time();
            if (collect_task.auto_cap.last_capture_us == 0 ||
                (now - collect_task.auto_cap.last_capture_us) >=
                collect_task.auto_cap.interval_s * 1000000LL) {
                bool limit_ok = (collect_task.auto_cap.batch_limit == 0 ||
                    collect_task.auto_cap.batch_count <
                    collect_task.auto_cap.batch_limit);
                if (limit_ok) {
                    camera_frame_t frame = {0};
                    if (camera_app_capture(cam_handle, &frame) == ESP_OK) {
                        char saved_req[32];
                        strncpy(saved_req, collect_task.request_id,
                                sizeof(saved_req) - 1);
                        int saved_seq = collect_task.task_seq;
                        snprintf(collect_task.request_id,
                                 sizeof(collect_task.request_id),
                                 "acap-%d", collect_task.auto_cap.batch_count + 1);
                        collect_task.task_seq = collect_task.auto_cap.batch_count + 1;
                        save_capture_slot(&collect_task, &frame, "auto");
                        strncpy(collect_task.request_id, saved_req,
                                sizeof(collect_task.request_id) - 1);
                        collect_task.task_seq = saved_seq;
                        camera_app_release_frame(cam_handle, &frame);
                        collect_task.auto_cap.batch_count++;
                        ESP_LOGI(TAG, "[AUTO-CAP] Captured #%d (%s)",
                                 collect_task.auto_cap.batch_count,
                                 collect_task.last_cap_request_id);
                    }
                }
                collect_task.auto_cap.last_capture_us = now;
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

        /* 说明：Week 1 的每秒 IMU 日志已并入上面的"周期上报"分支，
         * 避免重复读取传感器；周期上报暂停时板端不再采样。 */
        if (btn_handle) {
            adc_button_t btn = adc_button_read(btn_handle);
            if (btn != BTN_NONE)
                ESP_LOGI(TAG, "BTN: %s", adc_button_name(btn));
        }

        diag_cnt++;
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}