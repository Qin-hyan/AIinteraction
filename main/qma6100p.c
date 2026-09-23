/**
 * @file qma6100p.c
 * @brief QMA6100P 三轴加速度传感器驱动实现
 */

#include "qma6100p.h"
#include "esp_log.h"
#include <string.h>

static const char *TAG = "QMA6100P";

/* ---- 内部辅助: 写寄存器 ---- */
static esp_err_t qma6100p_write_reg(qma6100p_handle_t *handle,
                                     uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_master_transmit(handle->dev_handle, buf, sizeof(buf), -1);
}

/* ---- 内部辅助: 读寄存器 ---- */
static esp_err_t qma6100p_read_reg(qma6100p_handle_t *handle,
                                    uint8_t reg, uint8_t *val)
{
    return i2c_master_transmit_receive(handle->dev_handle,
                                        &reg, 1, val, 1, -1);
}

/* ---- 内部辅助: 读连续寄存器 ---- */
static esp_err_t qma6100p_read_regs(qma6100p_handle_t *handle,
                                     uint8_t reg, uint8_t *buf, size_t len)
{
    return i2c_master_transmit_receive(handle->dev_handle,
                                        &reg, 1, buf, len, -1);
}

/* ---- 12-bit 左对齐原始值 → 带符号 mg ---- */
static int16_t raw_to_mg(int16_t raw, uint16_t sensitivity)
{
    int16_t val = (raw >> 4) & 0x0FFF;
    if (val & 0x0800) {
        val |= 0xF000;
    }
    return (int16_t)((int32_t)val * 1000 / sensitivity);
}

/* ---- 量程 → 灵敏度 (LSB/g) ---- */
static uint16_t get_sensitivity(uint8_t range)
{
    switch (range) {
        case QMA6100P_RANGE_2G:  return 1024;
        case QMA6100P_RANGE_4G:  return 512;
        case QMA6100P_RANGE_8G:  return 256;
        case QMA6100P_RANGE_16G: return 128;
        case QMA6100P_RANGE_32G: return 64;
        default:                 return 1024;
/* ================================================================
 * 公开 API
 * ================================================================ */

esp_err_t qma6100p_init(i2c_master_bus_handle_t bus_handle,
                         uint8_t dev_addr,
                         qma6100p_handle_t **handle)
{
    ESP_LOGI(TAG, "Init QMA6100P at addr 0x%02X", dev_addr);

    qma6100p_handle_t *h = calloc(1, sizeof(qma6100p_handle_t));
    if (!h) return ESP_ERR_NO_MEM;

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address  = dev_addr,
        .scl_speed_hz    = 400000,
    };
    esp_err_t ret = i2c_master_bus_add_device(bus_handle, &dev_cfg,
                                               &h->dev_handle);
    if (ret != ESP_OK) { free(h); return ret; }

    /* 软复位 */
    ret = qma6100p_write_reg(h, QMA6100P_REG_SOFT_RESET, 0xB6);
    if (ret != ESP_OK) goto fail;
    vTaskDelay(pdMS_TO_TICKS(10));

    /* 验证 CHIP_ID = 0x90 */
    uint8_t chip_id = 0;
    ret = qma6100p_read_reg(h, QMA6100P_REG_CHIP_ID, &chip_id);
    if (ret != ESP_OK) goto fail;
    ESP_LOGI(TAG, "CHIP_ID = 0x%02X", chip_id);
    if (chip_id != 0x90) {
        ESP_LOGE(TAG, "Wrong CHIP_ID! Not QMA6100P?");
        ret = ESP_ERR_NOT_FOUND;
        goto fail;
    }

    /* 量程 ±8g, 带宽 256Hz */
    h->range = QMA6100P_RANGE_8G;
    qma6100p_write_reg(h, QMA6100P_REG_RANGE, h->range);
    qma6100p_write_reg(h, QMA6100P_REG_BANDWIDTH, QMA6100P_BW_256HZ);

    /* 正常工作模式 */
    ret = qma6100p_write_reg(h, QMA6100P_REG_POWER_CTL,
                              QMA6100P_POWER_NORMAL);
    if (ret != ESP_OK) goto fail;
    vTaskDelay(pdMS_TO_TICKS(5));

    h->sensitivity = get_sensitivity(h->range);
    ESP_LOGI(TAG, "QMA6100P ready (range=±8g, bw=256Hz)");
    *handle = h;
    return ESP_OK;

fail:
    i2c_master_bus_rm_device(h->dev_handle);
    free(h);
    return ret;
}

esp_err_t qma6100p_read_accel(qma6100p_handle_t *handle,
                               qma6100p_data_t *data)
{
    if (!handle || !data) return ESP_ERR_INVALID_ARG;

    uint8_t buf[6];
    esp_err_t ret = qma6100p_read_regs(handle, QMA6100P_REG_X_LSB,
                                        buf, sizeof(buf));
    if (ret != ESP_OK) return ret;

    int16_t rx = (int16_t)((buf[1] << 8) | buf[0]);
    int16_t ry = (int16_t)((buf[3] << 8) | buf[2]);
    int16_t rz = (int16_t)((buf[5] << 8) | buf[4]);

    data->x = raw_to_mg(rx, handle->sensitivity);
    data->y = raw_to_mg(ry, handle->sensitivity);
    data->z = raw_to_mg(rz, handle->sensitivity);
    return ESP_OK;
}

esp_err_t qma6100p_deinit(qma6100p_handle_t *handle)
{
    if (!handle) return ESP_OK;
    if (handle->dev_handle) i2c_master_bus_rm_device(handle->dev_handle);
    free(handle);
    return ESP_OK;
}
    }
}