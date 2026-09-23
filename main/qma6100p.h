/**
 * @file qma6100p.h
 * @brief QMA6100P 三轴加速度传感器驱动 (I2C)
 *
 * ESP32-S3-EYE v2.2 使用 QMA6100P（非 QMA7981）。
 * I2C 地址: 0x12 (SA0=GND) 或 0x13 (SA0=VDDIO)
 * 总线共享: SDA=GPIO4, SCL=GPIO5（与摄像头共用）
 */

#ifndef QMA6100P_H
#define QMA6100P_H

#include "driver/i2c_master.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ================================================================
 * QMA6100P 寄存器定义
 * ================================================================ */

/** 芯片 ID 寄存器 (RO)，期望值 0x90 */
#define QMA6100P_REG_CHIP_ID       0x00

/** 加速度数据寄存器 (RO, 12-bit 左对齐) */
#define QMA6100P_REG_X_LSB         0x01
#define QMA6100P_REG_X_MSB         0x02
#define QMA6100P_REG_Y_LSB         0x03
#define QMA6100P_REG_Y_MSB         0x04
#define QMA6100P_REG_Z_LSB         0x05
#define QMA6100P_REG_Z_MSB         0x06

/** 中断状态寄存器 */
#define QMA6100P_REG_INT_STATUS    0x09
#define QMA6100P_REG_FIFO_STATUS   0x0A

/** 量程选择寄存器 (R/W) */
#define QMA6100P_REG_RANGE         0x0F

/** 电源控制寄存器 (R/W) */
#define QMA6100P_REG_POWER_CTL     0x10

/** 带宽/ODR 寄存器 (R/W) */
#define QMA6100P_REG_BANDWIDTH     0x11

/** 软复位寄存器 (W) */
#define QMA6100P_REG_SOFT_RESET    0x36

/* ================================================================
 * 量程设置 (REG 0x0F bits[4:2])
 * ================================================================ */
#define QMA6100P_RANGE_2G          0x00  /**< ±2g  */
#define QMA6100P_RANGE_4G          0x04  /**< ±4g  */
#define QMA6100P_RANGE_8G          0x08  /**< ±8g  */
#define QMA6100P_RANGE_16G         0x0C  /**< ±16g */
#define QMA6100P_RANGE_32G         0x10  /**< ±32g */

/* ================================================================
 * 电源模式 (REG 0x10)
 * ================================================================ */
#define QMA6100P_POWER_STANDBY     0x00  /**< 待机模式 */
#define QMA6100P_POWER_LOW_POWER   0x80  /**< 低功耗模式 */
#define QMA6100P_POWER_NORMAL      0xC0  /**< 正常工作模式 */

/* ================================================================
 * 带宽 / 输出数据速率 (REG 0x11)
 * ================================================================ */
#define QMA6100P_BW_128HZ          0x08  /**< 128 Hz */
#define QMA6100P_BW_256HZ          0x09  /**< 256 Hz */
#define QMA6100P_BW_512HZ          0x0A  /**< 512 Hz */
#define QMA6100P_BW_1024HZ         0x0B  /**< 1024 Hz */

/* ================================================================
 * 默认 I2C 地址
 * ================================================================ */
#define QMA6100P_I2C_ADDR_0        0x12  /**< SA0 = GND */
#define QMA6100P_I2C_ADDR_1        0x13  /**< SA0 = VDDIO */

/* ================================================================
 * 数据结构
 * ================================================================ */

/** 三轴加速度数据 (原始值，mg 单位) */
typedef struct {
    int16_t x;  /**< X 轴加速度 (mg) */
    int16_t y;  /**< Y 轴加速度 (mg) */
    int16_t z;  /**< Z 轴加速度 (mg) */
} qma6100p_data_t;

/** 传感器句柄 */
typedef struct {
    i2c_master_dev_handle_t dev_handle; /**< I2C 设备句柄 */
    uint8_t                 range;      /**< 当前量程 */
    uint16_t                sensitivity;/**< 灵敏度 (LSB/g)，取决于量程 */
} qma6100p_handle_t;

/* ================================================================
 * API 函数
 * ================================================================ */

/**
 * @brief 初始化 QMA6100P 传感器
 *
 * @param[in]  bus_handle  已初始化的 I2C 主总线句柄
 * @param[in]  dev_addr    I2C 设备地址 (0x12 或 0x13)
 * @param[out] handle      返回的传感器句柄指针
 * @return
 *     - ESP_OK: 初始化成功
 *     - ESP_ERR_NOT_FOUND: 未检测到设备 (CHIP_ID 不匹配)
 *     - 其他: I2C 通信错误
 */
esp_err_t qma6100p_init(i2c_master_bus_handle_t bus_handle,
                         uint8_t dev_addr,
                         qma6100p_handle_t **handle);

/**
 * @brief 读取三轴加速度数据
 *
 * @param[in]  handle  传感器句柄
 * @param[out] data    读取到的加速度数据 (mg)
 * @return ESP_OK 成功，其他值失败
 */
esp_err_t qma6100p_read_accel(qma6100p_handle_t *handle,
                               qma6100p_data_t *data);

/**
 * @brief 反初始化传感器（释放资源）
 *
 * @param[in] handle 传感器句柄
 * @return ESP_OK 成功
 */
esp_err_t qma6100p_deinit(qma6100p_handle_t *handle);

#ifdef __cplusplus
}
#endif

#endif /* QMA6100P_H */