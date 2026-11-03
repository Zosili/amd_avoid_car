//============================================================
// 文件名   : motor_seq.v
// 功能     : 状态机版差速小车 —— 按固定节拍自动执行动作序列
//
//  动作循环（全流程 8 秒，无限循环）：
//    状态0  前进 3 秒   左右轮同向 60%
//    状态1  原地右转 2 秒  左轮正转 右轮反转
//    状态2  后退 1 秒   左右轮同向反转 50%
//    状态3  停车 2 秒   占空比 0
//
//  说明：这是"写死的路线"——不看传感器，撞不撞全靠现场摆放。
//        传感器决策版是阶段 2（PS 写寄存器）的事。
//  注意：复位键按下立即回到"前进 3 秒"重新开始。
//============================================================

module motor_seq #(
    parameter SYS_FREQ = 50_000_000,   // 板载晶振 50MHz（仿真时可调小加速）
    parameter PWM_FREQ = 20_000        // PWM 频率 20kHz
)(
    input  wire clk,      // 系统时钟
    input  wire rst_n,    // 复位按键，低有效（按下=重新从头跑序列）
    output reg  pwm_l,    // -> ENA 左路
    output wire in1,      // -> IN1 左路方向
    output wire in2,      // -> IN2
    output reg  pwm_r,    // -> ENB 右路
    output wire in3,      // -> IN3 右路方向
    output wire in4       // -> IN4
);

    // ---------------- PWM 部分 ----------------
    localparam CNT_MAX = SYS_FREQ / PWM_FREQ - 1;   // 2499
    localparam CNT_W   = $clog2(CNT_MAX + 1);       // 12bit

    reg [CNT_W-1:0] cnt;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cnt <= {CNT_W{1'b0}};
        else if (cnt == CNT_MAX)
            cnt <= {CNT_W{1'b0}};
        else
            cnt <= cnt + 1'b1;
    end

    // ---------------- 秒级节拍 ----------------
    // 50MHz 数满一秒产生一个 tick 脉冲
    localparam TICK_MAX = SYS_FREQ - 1;
    reg [25:0] tick_cnt;
    wire tick = (tick_cnt == TICK_MAX[25:0]);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            tick_cnt <= 26'd0;
        else if (tick)
            tick_cnt <= 26'd0;
        else
            tick_cnt <= tick_cnt + 1'b1;
    end

    // ---------------- 状态定义 ----------------
    localparam S_FWD    = 2'd0;   // 前进
    localparam S_TURN_R = 2'd1;   // 原地右转
    localparam S_BACK   = 2'd2;   // 后退
    localparam S_STOP   = 2'd3;   // 停车

    // 各状态时长（秒），想改节奏改这里
    localparam T_FWD    = 3;
    localparam T_TURN_R = 2;
    localparam T_BACK   = 1;
    localparam T_STOP   = 2;

    // 各状态占空比：直接用 PWM 计数值表示，2500 = 100%
    localparam [11:0] DC_60 = (CNT_MAX + 1) * 60 / 100;   // 1500
    localparam [11:0] DC_50 = (CNT_MAX + 1) * 50 / 100;   // 1250
    localparam [11:0] DC_00 = 12'd0;

    reg [1:0]  state;
    reg [7:0]  sec_timer;      // 当前状态已持续的秒数
    reg [11:0] duty_l_cnt, duty_r_cnt;
    reg        dir_l, dir_r;

    // 当前状态的时长（-1 是因为从 0 开始计数）
    reg [7:0] period;
    always @(*) begin
        case (state)
            S_FWD    : period = T_FWD    - 1;
            S_TURN_R : period = T_TURN_R - 1;
            S_BACK   : period = T_BACK   - 1;
            default  : period = T_STOP   - 1;
        endcase
    end

    // ---------------- 状态机：每秒走一步 ----------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state      <= S_FWD;
            sec_timer  <= 8'd0;
            duty_l_cnt <= DC_60;
            duty_r_cnt <= DC_60;
            dir_l      <= 1'b0;
            dir_r      <= 1'b0;
        end else if (tick) begin
            if (sec_timer != period)
                sec_timer <= sec_timer + 1'b1;
            else begin
                sec_timer <= 8'd0;
                case (state)
                    S_FWD: begin        // -> 原地右转
                        state      <= S_TURN_R;
                        duty_l_cnt <= DC_60;
                        duty_r_cnt <= DC_60;
                        dir_l      <= 1'b0;
                        dir_r      <= 1'b1;
                    end
                    S_TURN_R: begin     // -> 后退
                        state      <= S_BACK;
                        duty_l_cnt <= DC_50;
                        duty_r_cnt <= DC_50;
                        dir_l      <= 1'b1;
                        dir_r      <= 1'b1;
                    end
                    S_BACK: begin       // -> 停车
                        state      <= S_STOP;
                        duty_l_cnt <= DC_00;
                        duty_r_cnt <= DC_00;
                    end
                    default: begin      // S_STOP -> 前进，循环
                        state      <= S_FWD;
                        duty_l_cnt <= DC_60;
                        duty_r_cnt <= DC_60;
                        dir_l      <= 1'b0;
                        dir_r      <= 1'b0;
                    end
                endcase
            end
        end
    end

    // ---------------- PWM 输出 ----------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pwm_l <= 1'b0;
        else        pwm_l <= (cnt < duty_l_cnt);
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pwm_r <= 1'b0;
        else        pwm_r <= (cnt < duty_r_cnt);
    end

    assign in1 = dir_l ? 1'b0 : 1'b1;
    assign in2 = dir_l ? 1'b1 : 1'b0;
    assign in3 = dir_r ? 1'b0 : 1'b1;
    assign in4 = dir_r ? 1'b1 : 1'b0;

endmodule
