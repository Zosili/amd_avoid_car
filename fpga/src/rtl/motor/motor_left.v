//============================================================
// 文件名   : motor_left.v
// 功能     : 演示工程① —— 原地左转（左轮反转，右轮正转）
// 使用     : 单独建一个 Vivado 工程，只加这一个 .v + motor_pins.xdc
// 修改     : 想改成"弧线左转"（不原地转，走弧线）：
//            把 DIR_L 改 0、DUTY_L 改 30、DUTY_R 保持 60
//            （左轮慢右轮快，同向前进即向左画弧）
//============================================================

module motor_left #(
    parameter SYS_FREQ = 50_000_000,
    parameter PWM_FREQ = 20_000,
    parameter DUTY     = 60,     // 转的快慢（0~100）
    parameter DIR_L    = 1,      // 左轮反转
    parameter DIR_R    = 0       // 右轮正转
)(
    input  wire clk,
    input  wire rst_n,
    output reg  pwm_l,
    output wire in1,
    output wire in2,
    output reg  pwm_r,
    output wire in3,
    output wire in4
);
    localparam CNT_MAX    = SYS_FREQ / PWM_FREQ - 1;
    localparam DUTY_CNT   = CNT_MAX * DUTY / 100;
    localparam CNT_W      = $clog2(CNT_MAX + 1);

    reg [CNT_W-1:0] cnt;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)            cnt <= {CNT_W{1'b0}};
        else if (cnt == CNT_MAX) cnt <= {CNT_W{1'b0}};
        else                   cnt <= cnt + 1'b1;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pwm_l <= 1'b0;
        else        pwm_l <= (cnt < DUTY_CNT);
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pwm_r <= 1'b0;
        else        pwm_r <= (cnt < DUTY_CNT);
    end

    assign in1 = DIR_L ? 1'b0 : 1'b1;
    assign in2 = DIR_L ? 1'b1 : 1'b0;
    assign in3 = DIR_R ? 1'b0 : 1'b1;
    assign in4 = DIR_R ? 1'b1 : 1'b0;

endmodule
