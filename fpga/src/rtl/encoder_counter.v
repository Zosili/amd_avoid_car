//============================================================
// 文件名   : encoder_counter.v
// 功能     : 霍尔编码器 AB 相 4 倍频正交解码 + 速度测量
//
//  输入 A/B 两相（相位差 90°），做 4 倍频正交解码，统计每个
//  采样周期内的脉冲增量（带符号，正 = 正转 / 负 = 反转）。
//
//  4 倍频正交解码：
//    状态 {A,B} 格雷码序列 00 -> 10 -> 11 -> 01 -> 00 为正转
//    （A 超前 B），每个跳变计 1 个脉冲，一个完整周期计 4 个，
//    分辨率是"每圈线数 x 4"。
//
//  速度测量：
//    固定采样周期 SAMPLE_US（默认 10ms），输出本周期脉冲增量
//    speed + speed_valid 脉冲。speed 的单位是"脉冲/采样周期"，
//    闭环 PID 直接用这个量做反馈，不需要换算成 rpm（线数、减速比
//    都不用知道）。
//
//  时钟域：本模块在 motor_decision 的 eth_rx_clk（125MHz）域，
//  A/B 相先经过 2 级同步器再解码。TT 编码器脉冲频率远低于 125MHz，
//  采样完全足够。
//============================================================

module encoder_counter #(
    parameter CLK_FREQ  = 125_000_000,  // 采样时钟频率
    parameter SAMPLE_US = 10000          // 速度采样周期（微秒），默认 10ms
)(
    input  wire              clk,
    input  wire              rst_n,
    input  wire              enc_a,        // A 相
    input  wire              enc_b,        // B 相
    output reg  signed [31:0] speed,       // 每采样周期的脉冲增量（带符号）
    output reg               speed_valid   // 采样完成脉冲（持续 1 拍）
);

    // ---- A/B 相 2 级同步（消除亚稳态）----
    reg [1:0] a_sync, b_sync;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            a_sync <= 2'b00;
            b_sync <= 2'b00;
        end else begin
            a_sync <= {a_sync[0], enc_a};
            b_sync <= {b_sync[0], enc_b};
        end
    end
    wire a = a_sync[1];
    wire b = b_sync[1];

    // ---- 4 倍频正交解码 ----
    // 状态编码 {A,B}：00=0, 01=1, 10=2, 11=3
    // 正转（A 超前 B）：00->10->11->01->00
    // 反转（B 超前 A）：00->01->11->10->00
    reg [1:0]  ab_prev;
    reg signed [31:0] cnt;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ab_prev <= 2'b00;
            cnt     <= 0;
        end else begin
            ab_prev <= {a, b};
            case ({ab_prev, a, b})
                // 正转（A 超前）
                4'b0010, 4'b1011, 4'b1101, 4'b0100: cnt <= cnt + 1;
                // 反转（B 超前）
                4'b0001, 4'b0111, 4'b1110, 4'b1000: cnt <= cnt - 1;
                default: cnt <= cnt;
            endcase
        end
    end

    // ---- 采样周期计数 + 速度输出 ----
    // 每 SAMPLE_US 输出一次脉冲增量
    localparam integer SAMPLE_CNT = CLK_FREQ / 1_000_000 * SAMPLE_US;
    reg [31:0]        sample_cnt;
    reg signed [31:0] cnt_prev;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sample_cnt  <= 0;
            cnt_prev    <= 0;
            speed       <= 0;
            speed_valid <= 1'b0;
        end else begin
            speed_valid <= 1'b0;
            if (sample_cnt >= SAMPLE_CNT - 1) begin
                sample_cnt  <= 0;
                speed       <= cnt - cnt_prev;   // 本周期脉冲增量
                cnt_prev    <= cnt;
                speed_valid <= 1'b1;
            end else begin
                sample_cnt <= sample_cnt + 1;
            end
        end
    end

endmodule
