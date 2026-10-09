# Week 01 State — 传感数据采集与 Web 展示

**状态**: ✅ 完成  
**日期**: 2026-10

## 交付物

- QMA6100P 三轴加速度计驱动（I2C: SDA=GPIO4, SCL=GPIO5, addr=0x12）
- ADC 按键检测（GPIO1/ADC1_CH0, 4-key）
- Wi-Fi STA 连接
- HTTP 服务器 + Web 仪表盘（500ms 直读刷新）
- REST API: `GET /api/sensor`
- LED 测试（GPIO38 + GPIO3）
- PSRAM 验证

## 备注

- 全部代码在 main.c 中单体实现，Week 02 进行了模块化拆分