//============================================================
// 文件名   : motor_diff_ctrl.v
// 功能     : 双电机差速 PWM —— 寄存器可写版
// 改动     : 与 motor_diff.v 的唯一区别是占空比/方向不再是
//            编译期参数，而是来自 PS 经 AXI GPIO 写入的值，
//            运行中随时可改，这是"PS 决策、PL 执行"的接口
// 时钟     : 接 PS 的 FCLK_CLK0（Vivado 默认 100MHz，
//            若你在 PS 里改了频率，同步改 SYS_FREQ 参数）
//
// ------------------------------------------------------------
// 时序修正记录（2026-10-24）
// ------------------------------------------------------------
// 原写法：
//     wire [CNT_W+6:0] l_full = FULL * {1'b0, duty_l};
//     wire [CNT_W-1:0] duty_l_cnt = l_full / 100;
// 综合出来是一条 22 级逻辑（CARRY4 x12 + DSP48E1 x1）的
// 「乘 5000 再除 100」组合链，数据路径 14.945ns，而 clk_fpga_0
// 周期只有 10ns —— WNS -5.058ns / TNS -9.845ns（2 个端点违例）。
//
// 两处修改：
//   ① FULL=5000 能被 100 整除（5000/100=50），直接用乘法代替除法，
//      (FULL*duty)/100 等价于 duty*50，省掉整条除法网络。
//      参数化了，整除条件不成立时自动退回原公式，不改语义。
//   ② 阈值打一拍(thr_l/thr_r)再送比较器，彻底切断
//      AXI GPIO 寄存器 -> PWM 输出寄存器的长路径。
//      duty 由 PS 写入，变化频率远低于 100MHz，多 1 拍延迟无影响。
//============================================================

module motor_diff_ctrl #(
    parameter SYS_FREQ = 100_000_000,   // FCLK_CLK0 默认 100MHz
    parameter PWM_FREQ = 20_000         // PWM 20kHz
)(
    input  wire clk,       // <- PS FCLK_CLK0
    input  wire rst_n,     // <- PS FCLK_RESET0_N（低有效）
    input  wire [6:0] duty_l,   // 左轮占空比 0~100，>100 视为满速
    input  wire [6:0] duty_r,   // 右轮占空比 0~100
    input  wire dir_l,          // 左轮方向 0=正 1=反
    input  wire dir_r,          // 右轮方向 0=正 1=反
    output reg  pwm_l,          // -> ENA
    output wire in1,
    output wire in2,
    output reg  pwm_r,          // -> ENB
    output wire in3,
    output wire in4
);
    localparam CNT_MAX = SYS_FREQ / PWM_FREQ - 1;    // 100MHz/20k-1 = 4999
    localparam CNT_W   = $clog2(CNT_MAX + 1);        // 13bit
    localparam [CNT_W:0] FULL = CNT_MAX + 1;         // 一个周期的计数总数

    // 每个百分点对应的计数值：FULL/100。仅当能整除时用乘法近似，
    // 否则退回 (FULL*duty)/100 的原公式（两者结果在此条件下完全一致）。
    localparam FULL_EXACT    = ((FULL % 100) == 0);
    localparam [CNT_W:0] FULL_PER_PCT = FULL / 100;  // 5000/100 = 50

    // ---------------- 占空比 -> 阈值（组合，仅一级乘法） ----------------
    wire [CNT_W:0] thr_l_c;
    wire [CNT_W:0] thr_r_c;

    generate
        if (FULL_EXACT) begin : g_mul
            // 5000 能被 100 整除：duty*5000/100 == duty*50，精确等价
            assign thr_l_c = FULL_PER_PCT * duty_l;
            assign thr_r_c = FULL_PER_PCT * duty_r;
        end else begin : g_div
            assign thr_l_c = (FULL * duty_l) / 100;
            assign thr_r_c = (FULL * duty_r) / 100;
        end
    endgenerate

    // ---------------- 阈值打一拍（切断长路径） ----------------
    reg [CNT_W-1:0] thr_l;
    reg [CNT_W-1:0] thr_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            thr_l <= {CNT_W{1'b0}};
            thr_r <= {CNT_W{1'b0}};
        end else begin
            thr_l <= (duty_l >= 7'd100) ? CNT_MAX[CNT_W-1:0] : thr_l_c[CNT_W-1:0];
            thr_r <= (duty_r >= 7'd100) ? CNT_MAX[CNT_W-1:0] : thr_r_c[CNT_W-1:0];
        end
    end

    // ---------------- PWM 计数器 ----------------
    reg [CNT_W-1:0] cnt;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)              cnt <= {CNT_W{1'b0}};
        else if (cnt == CNT_MAX) cnt <= {CNT_W{1'b0}};
        else                     cnt <= cnt + 1'b1;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pwm_l <= 1'b0;
        else        pwm_l <= (cnt < thr_l);
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pwm_r <= 1'b0;
        else        pwm_r <= (cnt < thr_r);
    end

    assign in1 = dir_l ? 1'b0 : 1'b1;
    assign in2 = dir_l ? 1'b1 : 1'b0;
    assign in3 = dir_r ? 1'b0 : 1'b1;
    assign in4 = dir_r ? 1'b1 : 1'b0;

endmodule
