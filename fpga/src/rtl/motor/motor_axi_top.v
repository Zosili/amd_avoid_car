//============================================================
// 文件名   : motor_axi_top.v
// 功能     : 块设计顶层包装 —— 把 AXI GPIO 的 32 位输出拆成
//            左右轮的占空比/方向，喂给 motor_diff_ctrl
//
//  gpio_out 位域约定（PS 写值就按这个拼）：
//    [6:0]   duty_l  左轮占空比 0~100
//    [14:8]  duty_r  右轮占空比 0~100
//    [16]    dir_l   左轮方向 0=正 1=反
//    [17]    dir_r   右轮方向 0=正 1=反
//    [18]    beep    蜂鸣器（top.v 直接取这一位，本模块不用）
//    [19]    stop_lock 硬钳位：1 = 强制两路 PWM 输出 0
//    其余位  保留，写 0
//
//  为什么要有 stop_lock：
//    PS 看门狗能停电机，但那依赖 PS 还在跑。若 PS 死锁 / 程序跑飞 /
//    AXI 写挂在总线上，duty 会停在最后一个非零值，车会一直冲。
//    stop_lock 是组合逻辑，不经过任何时序单元，PS 卡死也能立刻断 PWM。
//    PS 侧正常运行时随时写 0；一旦判断危险就写 1，同时把 duty 清 0。
//    （PC 掉线时 PS 自己会置 1，见 ps_fusion_ctrl.c 的看门狗。）
//
//  用法：Vivado 里 Synthesis 后 -> Open Block Design ->
//        右键画布 -> Add Module -> 选本模块
//============================================================

module motor_axi_top (
    input  wire        clk,        // <- FCLK_CLK0
    input  wire        rst_n,      // <- FCLK_RESET0_N
    input  wire [31:0] gpio_out,   // <- AXI GPIO 通道1输出（PS 可写）
    output wire        pwm_l,
    output wire        in1,
    output wire        in2,
    output wire        pwm_r,
    output wire        in3,
    output wire        in4
);

    wire pwm_l_raw, pwm_r_raw;

    // 硬钳位：组合逻辑，无时序单元、无状态机，PS 卡死也立刻生效
    wire stop_lock = gpio_out[19];

    assign pwm_l = stop_lock ? 1'b0 : pwm_l_raw;
    assign pwm_r = stop_lock ? 1'b0 : pwm_r_raw;

    motor_diff_ctrl #(
        .SYS_FREQ(100_000_000)   // 与 FCLK_CLK0 一致
    ) u_ctrl (
        .clk    (clk),
        .rst_n  (rst_n),
        .duty_l (gpio_out[6:0]),
        .duty_r (gpio_out[14:8]),
        .dir_l  (gpio_out[16]),
        .dir_r  (gpio_out[17]),
        .pwm_l  (pwm_l_raw),
        .in1    (in1),
        .in2    (in2),
        .pwm_r  (pwm_r_raw),
        .in3    (in3),
        .in4    (in4)
    );

endmodule
