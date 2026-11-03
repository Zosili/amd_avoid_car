//============================================================
// 文件名   : speed_pid.v
// 功能     : 速度闭环 PID（前馈 + PI）
//
//  反馈量 = 编码器脉冲频率（脉冲/采样周期），目标是让轮子转速
//  稳定在 target，不被负载（下地后车重+摩擦）拉低。
//
//  算法（前馈 + PI，定点 Q8，2 级流水线）：
//      Stage1: p_mul = KP * err      （乘法打拍，走 DSP48）
//              i_mul = KI * err
//      Stage2: integral += i_mul>>8  （限幅 I_LIMIT）
//              duty = FF_DUTY + (p_mul>>8) + integral，限幅 [0, DUTY_MAX]
//
//  为什么有 FF_DUTY（前馈）：纯 PI 从 0 起步响应慢，前馈先给一个
//  接近目标速度的基础占空比，PI 只做小修正，启动快、不震荡。
//
//  2 级流水线：乘法单独打拍走 DSP48 硬乘法器，避免 LUT 乘法在
//  125MHz 下的长组合链违例。2 拍延迟对 10ms 速度环完全无感。
//
//  ★ 关键（2026-11-01 修 bug）：积分累加必须只在【每个采样周期
//    一次】（speed_valid 延迟一拍 spd_valid_d1 时）执行。之前 Stage2
//    的 integral 在 run=1 时每个时钟都累加 i_inc，而 i_mul_r 每 10ms
//    才更新一次 → 10ms 内累加 125 万次 → 积分瞬间饱和 ±2000 → duty
//    瞬间 100% 或 0% → 表现为"左快右停"。现已加 spd_valid_d1 门控。
//
//  参数（都要实测标定/调）：
//    TARGET（在 motor_decision 里给）= 目标脉冲频率，先空转 70%
//      占空比测出编码器脉冲/10ms，填进来
//    KP / KI = 比例/积分增益，Q8 定点（128 = 0.5，256 = 1.0）
//    FF_DUTY = 前馈基础占空比，约等于目标速度对应的开环占空比
//============================================================

module speed_pid #(
    // ★ 2026-11-02：FF_DUTY 由参数改成端口 ff_duty。
    //   原来的前馈是编译期常量，转向时目标速度从 70 降到 28，前馈却还按
    //   70 对应的占空比 45% 给，比例项(KP=0.125)只能修 5 个点，于是
    //   "转向时内侧轮根本不降速"（实测 steer=1/2 两轮速度都不变）。
    //   现在由上层按 target/TARGET_NOMINAL 的比例缩放后再送进来。
    parameter [15:0] KP       = 16'd128,     // 比例增益 Q8（128=0.5）
    parameter [15:0] KI       = 16'd64,      // 积分增益 Q8（64=0.25）
    parameter signed [31:0] I_LIMIT = 32'd60,     // 积分限幅（原2000太大，duty饱和后积分仍windup）
    parameter [7:0]  DUTY_MAX = 8'd100       // 输出占空比上限 %
)(
    input  wire              clk,
    input  wire              rst_n,
    input  wire              run,            // 1=闭环运行；0=复位（积分清零、duty=0）
    input  wire              hold,           // 1=冻结（堵转助推/失效期间）：积分和 duty 都保持不动
    input  wire [7:0]        ff_duty,        // 前馈基础占空比 %（上层按目标比例给）
    input  wire signed [31:0] target,        // 目标速度（脉冲/采样周期）
    input  wire signed [31:0] measured,      // 实测速度（脉冲/采样周期）
    input  wire              speed_valid,    // 实测有效脉冲（每采样周期 1 拍）
    output reg  [7:0]        duty            // 输出占空比 0~100
);

    // ---- Stage 1：误差 + 乘法（打拍，乘法走 DSP48）----
    reg signed [47:0] p_mul_r;   // KP * error（16bit × 32bit = 48bit）
    reg signed [47:0] i_mul_r;   // KI * error

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            p_mul_r <= 48'sd0;
            i_mul_r <= 48'sd0;
        end else if (speed_valid) begin
            p_mul_r <= $signed(KP) * (target - measured);
            i_mul_r <= $signed(KI) * (target - measured);
        end
    end

    // speed_valid 延迟一拍，对齐 Stage1 -> Stage2（1 拍流水线延迟）
    reg spd_valid_d1;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) spd_valid_d1 <= 1'b0;
        else spd_valid_d1 <= speed_valid;
    end

    // ---- Stage 2：只在 spd_valid_d1 时累加积分 + 更新 duty ----
    reg signed [31:0] integral;

    wire signed [31:0] i_inc      = i_mul_r >>> 8;
    wire signed [31:0] p_scaled   = p_mul_r >>> 8;
    wire signed [31:0] i_sum      = integral + i_inc;
    // anti-windup：输出已饱和时，若积分仍朝饱和方向累积，则冻结积分。
    // 否则启动/大扰动时积分 windup，duty 长时间顶满 100%（"全速"根因，
    // 2026-11-01 上板实测：target=100 却跑到 156 脉冲/10ms）。
    // 用上一拍的 duty（8 位寄存器）判断饱和，避免 32 位组合比较链导致时序违例。
    wire pos_sat = (duty >= DUTY_MAX[7:0]) && (i_inc > 0);
    wire neg_sat = (duty == 8'd0) && (i_inc < 0);
    wire signed [31:0] i_next     = pos_sat ? integral :
                                    neg_sat ? integral :
                                    (i_sum > I_LIMIT)  ? I_LIMIT :
                                    (i_sum < -I_LIMIT) ? -I_LIMIT : i_sum;
    wire signed [31:0] raw        = $signed({24'd0, ff_duty}) + p_scaled + i_next;
    wire signed [31:0] raw_lim    = (raw < 0) ? 32'sd0 :
                                    (raw > DUTY_MAX) ? $signed({24'd0, DUTY_MAX}) : raw;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            integral <= 32'sd0;
            duty     <= 8'd0;
        end else if (!run) begin
            integral <= 32'sd0;
            duty     <= 8'd0;
        end else if (hold) begin
            // 堵转助推 / 失效窗口：积分和 duty 全部冻结，避免"越顶越大，
            // 一恢复就 100% 暴冲"（实测手拨轮子后全速冲出去就是这个）
            integral <= integral;
            duty     <= duty;
        end else if (spd_valid_d1) begin   // ★ 只每个采样周期累加一次
            integral <= i_next;
            duty     <= raw_lim[7:0];
        end
    end

endmodule
