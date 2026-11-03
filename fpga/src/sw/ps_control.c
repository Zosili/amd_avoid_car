//============================================================
// 文件名   : ps_control.c
// 用途     : PS 端裸机程序（Vitis），测试 AXI GPIO -> PWM 控制链
// 动作     : 循环执行 前进3s -> 原地右转2s -> 停1s
// 硬件前提 : 块设计已生成 xsa，本文件建在 Vitis 平台应用工程里
//============================================================

#include "xparameters.h"
#include "xgpio.h"
#include "xil_printf.h"
#include "sleep.h"

// GPIO 基地址以 Vivado Address Editor 实际分配为准（默认 AXI GPIO 是 0x41200000）
// 注意：gpio_v4_12 的 XGpio_Initialize 有两套签名，由 SDT 宏切换 ——
//   经典流程(#ifndef SDT)：第二参数是 DeviceId (u16)
//   SDT 流程(#ifdef  SDT)：第二参数是基地址 (UINTPTR)
// Vitis 2025.2 统一 IDE 新建平台默认走 SDT，但两者都兼容才不会踩坑。
#ifdef SDT
  #define GPIO_INIT_ARG   XPAR_AXI_GPIO_0_BASEADDR
#else
  #define GPIO_INIT_ARG   XPAR_AXI_GPIO_0_DEVICE_ID
#endif

#define CHANNEL_OUT    1        // 用的通道号

// 把四个控制量拼成一个 32 位寄存器值，位域与 motor_axi_top.v 对应
#define MOTOR(duty_l, duty_r, dir_l, dir_r) \
    ( ((unsigned)((duty_l) & 0x7F))         |  \
      ((unsigned)((duty_r) & 0x7F)) << 8    |  \
      ((unsigned)(dir_l) & 1)       << 16   |  \
      ((unsigned)(dir_r) & 1)       << 17 )

static XGpio gpio;

static void motor_set(int duty_l, int duty_r, int dir_l, int dir_r)
{
    XGpio_DiscreteWrite(&gpio, CHANNEL_OUT, MOTOR(duty_l, duty_r, dir_l, dir_r));
}

int main(void)
{
    int status = XGpio_Initialize(&gpio, GPIO_INIT_ARG);
    if (status != XST_SUCCESS) {
        xil_printf("GPIO init failed!\r\n");
        return XST_FAILURE;
    }
    // 通道1 全部设为输出（0 = 输出）
    XGpio_SetDataDirection(&gpio, CHANNEL_OUT, 0x00000000);

    xil_printf("motor AXI control test start\r\n");

    while (1) {
        motor_set(60, 60, 0, 0);      // 前进 60%
        xil_printf("FWD 60/60\r\n");
        sleep(3);

        motor_set(60, 60, 0, 1);      // 原地右转 2s
        xil_printf("TURN R\r\n");
        sleep(2);

        motor_set(0, 0, 0, 0);        // 停 1s
        xil_printf("STOP\r\n");
        sleep(1);
    }

    return 0;
}

//============================================================
// 后续接决策代码：把 motor_set() 的参数来源换成传感器计算值即可
//   例：雷达 < 30cm -> motor_set(30, 50, 0, 0) 减速右绕
//============================================================
