`timescale 1ns / 1ps

//============================================================
// 测试台 : tb_motor_diff
// 目的   : 验证双路 PWM 左右占空比独立可调（差速的基础）
// 设置   : 左轮 DUTY_L=60，右轮 DUTY_R=30
// 看点   : pwm_l 高电平时间应约为 pwm_r 的两倍
//============================================================

module tb_motor_diff;

    reg  clk;
    reg  rst_n;
    wire pwm_l, in1, in2;
    wire pwm_r, in3, in4;

    motor_diff #(
        .DUTY_L(60),
        .DUTY_R(30),
        .DIR_L (0),
        .DIR_R (0)
    ) u_motor_diff (
        .clk   (clk),
        .rst_n (rst_n),
        .pwm_l (pwm_l),
        .in1   (in1),
        .in2   (in2),
        .pwm_r (pwm_r),
        .in3   (in3),
        .in4   (in4)
    );

    // 50MHz 时钟：周期 20ns
    initial clk = 1'b0;
    always #10 clk = ~clk;

    initial begin
        rst_n = 1'b0;
        #100;
        rst_n = 1'b1;
        #500_000;   // 跑 500us = 10 个 PWM 周期，波形足够看
        $display("Sim done. in1=%b in2=%b in3=%b in4=%b", in1, in2, in3, in4);
        $stop;
    end

endmodule
