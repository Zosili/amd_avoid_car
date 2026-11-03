//============================================================
// 文件名   : motor_debug_top.v
// 功能     : 最小电机闭环调试工程（独立于整车）
//
//  只包含：编码器计数 + 速度 PID + 电机 PWM + 调试输出。
//  目的：脱离相机/网口/雷达，单独验证"编码器→计数→PID→PWM"
//  这条闭环，综合实现 1~2 分钟（整车要 10 分钟）。
//
//  调试输出（关键）：
//    dbg_pwm_l  —— 左轮编码器计数的"活动指示"：有脉冲就翻转，
//                  用万用表频率档/示波器直接看编码器通不通。
//    dbg_pwm_r  —— 右轮同理。
//    pwm_l/pwm_r —— 电机实际 PWM（PID 闭环输出）。
//
//  上电默认：run=1 直接跑闭环（不依赖 UDP 指令），方便独立测试。
//  目标速度 TARGET=70（脉冲/10ms，之前标定值）。
//============================================================

module motor_debug_top #(
    parameter CLK_FREQ      = 50_000_000,   // sys_clk 50MHz
    parameter [31:0] TARGET = 32'd70,       // 目标速度 脉冲/10ms
    parameter [7:0]  FF     = 8'd45         // 前馈占空比 %
)(
    input  wire        sys_clk,      // PL 50MHz 晶振
    input  wire        sys_rst_n,    // 复位（低有效）
    input  wire        enc_l_a,      // 左轮编码器 A
    input  wire        enc_l_b,      // 左轮编码器 B
    input  wire        enc_r_a,      // 右轮编码器 A
    input  wire        enc_r_b,      // 右轮编码器 B
    output wire        pwm_l,        // 左轮 PWM (ENA)
    output wire        pwm_r,        // 右轮 PWM (ENB)
    output wire        in1, in2,     // 左轮方向
    output wire        in3, in4,     // 右轮方向
    output wire        dbg_pwm_l     // 调试：左轮编码器活动指示
);

    // ---- 编码器计数（采样时钟用 sys_clk 50MHz）----
    wire signed [31:0] speed_l, speed_r;
    wire               speed_valid_l, speed_valid_r;

    encoder_counter #(
        .CLK_FREQ(CLK_FREQ), .SAMPLE_US(10000)
    ) u_enc_l (
        .clk(sys_clk), .rst_n(sys_rst_n),
        .enc_a(enc_l_a), .enc_b(enc_l_b),
        .speed(speed_l), .speed_valid(speed_valid_l)
    );
    encoder_counter #(
        .CLK_FREQ(CLK_FREQ), .SAMPLE_US(10000)
    ) u_enc_r (
        .clk(sys_clk), .rst_n(sys_rst_n),
        .enc_a(enc_r_a), .enc_b(enc_r_b),
        .speed(speed_r), .speed_valid(speed_valid_r)
    );

    // ---- 速度 PID（上电即闭环，run 恒 1）----
    wire [7:0] pid_duty_l, pid_duty_r;
    speed_pid #(.KP(16'd32), .KI(16'd16), .I_LIMIT(32'sd60))
    u_pid_l (
        .clk(sys_clk), .rst_n(sys_rst_n), .run(1'b1), .hold(1'b0),
        .ff_duty(FF),
        .target($signed(TARGET)), .measured(speed_l), .speed_valid(speed_valid_l),
        .duty(pid_duty_l)
    );
    speed_pid #(.KP(16'd32), .KI(16'd16), .I_LIMIT(32'sd60))
    u_pid_r (
        .clk(sys_clk), .rst_n(sys_rst_n), .run(1'b1), .hold(1'b0),
        .ff_duty(FF),
        .target($signed(TARGET)), .measured(speed_r), .speed_valid(speed_valid_r),
        .duty(pid_duty_r)
    );

    // ---- 电机 PWM（方向固定前进 dir=0）----
    motor_diff_ctrl #(.SYS_FREQ(CLK_FREQ))
    u_motor (
        .clk(sys_clk), .rst_n(sys_rst_n),
        .duty_l(pid_duty_l[6:0]), .duty_r(pid_duty_r[6:0]),
        .dir_l(1'b0), .dir_r(1'b0),
        .pwm_l(pwm_l), .in1(in1), .in2(in2),
        .pwm_r(pwm_r), .in3(in3), .in4(in4)
    );

    // ---- 调试：左轮编码器活动指示（接 V5，可选接万用表频率档）----
    // speed 每 10ms 更新一次。若 speed != 0 说明编码器在出脉冲，
    // 每次非零就翻转一次 -> 方波频率 = 采样率的 1/2 = 50Hz。
    reg dbg_l;
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) dbg_l <= 1'b0;
        else if (speed_valid_l && speed_l != 32'sd0) dbg_l <= ~dbg_l;
    end
    assign dbg_pwm_l = dbg_l;

endmodule
