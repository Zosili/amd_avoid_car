//=============================================================================
// motor_axi.v — AXI4-Lite 电机控制器（徐的 motor_diff 的"可被 PS 控制"版本）
//=============================================================================
// 为什么要这个文件
//   现有 motor_diff 只有 8 个端口，速度是 RTL 里的常量，PS 改不了。
//   本模块把"速度"变成可写寄存器，PS 用 mmio.write 就能改。
//   PS 侧对应代码：integration/run_on_board.py::MotorBridge
//
// 寄存器映射（改这里必须同步改 run_on_board.py）
//   0x00  CTRL       W  bit0=使能(0=强制停车)  bit1=清急停（保留）
//   0x04  SPEED_L    W  有符号 16 位，-1000~+1000，负数=反转
//   0x08  SPEED_R    W  同上
//   0x0C  HEARTBEAT  W  每 100ms 写一次；300ms 不变 → 硬件急停（保命）
//   0x10  ENC_L      R  左编码器计数（4 倍频，未接编码器时恒 0）
//   0x14  ENC_R      R  右编码器计数
//   0x18  STATUS     R  bit0=看门狗超时  bit1=急停锁死  bit2=电机使能中
//
// 用法（Vivado）
//   1. 新建工程 → Create and Package New IP → Create AXI4 Peripheral
//      → 选 "Edit IP"，把本文件内容贴进去（或直接例化 + 手工连 AXI 互联）
//   2. 本模块替代 motor_diff 的 PWM 部分；引脚约束沿用 motor_pins.xdc
//   3. 时钟接 PS 的 FCLK_CLK0（50MHz），复位接 peripheral_aresetn（低有效）
//
// 参数
//   CLK_HZ  : AXI 时钟频率，必须和实际一致，否则 PWM 频率和看门狗时间都会偏
//   PWM_HZ  : PWM 载波频率。20kHz 超出人耳，电机不啸叫；太低会抖
//   WDT_MS  : 看门狗超时毫秒数
//=============================================================================

module motor_axi #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer PWM_HZ = 20_000,
    parameter integer WDT_MS = 300
)(
    // ---- AXI4-Lite slave ----
    input  wire        s_axi_aclk,
    input  wire        s_axi_aresetn,       // 低有效

    input  wire [5:0]  s_axi_awaddr,
    input  wire [2:0]  s_axi_awprot,
    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,

    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,

    output reg  [1:0]  s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,

    input  wire [5:0]  s_axi_araddr,
    input  wire [2:0]  s_axi_arprot,
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,

    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,

    // ---- 电机输出（沿用 motor_pins.xdc 的引脚）----
    output wire        pwm_l,
    output wire        in1,
    output wire        in2,
    output wire        pwm_r,
    output wire        in3,
    output wire        in4,

    // ---- 编码器输入（没接就接 0，计数恒 0）----
    input  wire        enc_l_a,
    input  wire        enc_l_b,
    input  wire        enc_r_a,
    input  wire        enc_r_b
);

    // ===================== 参数 =====================
    localparam integer PWM_PERIOD = CLK_HZ / PWM_HZ;      // 20kHz@50MHz = 2500
    localparam integer WDT_LIMIT  = (CLK_HZ / 1000) * WDT_MS;  // 300ms@50MHz = 15e6
    localparam integer SPD_FULL   = 1000;                 // 与 contract.py::PWM_MAX 对应

    // ===================== 寄存器（全部前置声明）=====================
    reg [31:0] ctrl_reg;
    reg [15:0] spd_l_reg;
    reg [15:0] spd_r_reg;
    reg [31:0] hb_reg;
    reg [31:0] status_reg;
    reg [31:0] enc_l_cnt;
    reg [31:0] enc_r_cnt;

    // ===================== AXI 写通道 =====================
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            s_axi_awready <= 1'b0;
            s_axi_wready  <= 1'b0;
            s_axi_bvalid  <= 1'b0;
            s_axi_bresp   <= 2'b00;
        end else begin
            if (~s_axi_awready && s_axi_awvalid && s_axi_wvalid) begin
                s_axi_awready <= 1'b1;
                s_axi_wready  <= 1'b1;
            end else begin
                s_axi_awready <= 1'b0;
                s_axi_wready  <= 1'b0;
            end

            if (s_axi_awready && s_axi_wready) begin
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= 2'b00;                    // OKAY
            end else if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end
        end
    end

    wire wr_en = s_axi_awready && s_axi_wready;

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            ctrl_reg  <= 32'h0;
            spd_l_reg <= 16'h0;
            spd_r_reg <= 16'h0;
            hb_reg    <= 32'h0;
        end else if (wr_en) begin
            case (s_axi_awaddr[5:2])
                4'd0: ctrl_reg  <= s_axi_wdata;
                4'd1: spd_l_reg <= s_axi_wdata[15:0];
                4'd2: spd_r_reg <= s_axi_wdata[15:0];
                4'd3: hb_reg    <= s_axi_wdata;
                default: ;                                // 其他地址忽略
            endcase
        end
    end

    // ===================== AXI 读通道 =====================
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            s_axi_arready <= 1'b0;
            s_axi_rvalid  <= 1'b0;
            s_axi_rdata   <= 32'h0;
            s_axi_rresp   <= 2'b00;
        end else begin
            if (~s_axi_arready && s_axi_arvalid) begin
                s_axi_arready <= 1'b1;
                case (s_axi_araddr[5:2])
                    4'd0:    s_axi_rdata <= ctrl_reg;
                    4'd1:    s_axi_rdata <= {16'h0, spd_l_reg};
                    4'd2:    s_axi_rdata <= {16'h0, spd_r_reg};
                    4'd3:    s_axi_rdata <= hb_reg;
                    4'd4:    s_axi_rdata <= enc_l_cnt;
                    4'd5:    s_axi_rdata <= enc_r_cnt;
                    4'd6:    s_axi_rdata <= status_reg;
                    default: s_axi_rdata <= 32'hDEADBEEF;
                endcase
            end else begin
                s_axi_arready <= 1'b0;
            end

            if (s_axi_arready && s_axi_arvalid) begin
                s_axi_rvalid <= 1'b1;
                s_axi_rresp  <= 2'b00;
            end else if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end
        end
    end

    // ===================== 看门狗（保命逻辑）=====================
    // PS 每 100ms 写一次 HEARTBEAT；300ms 没变 → 判定 PS 挂了 → 强制停车
    reg [31:0] hb_prev;
    reg [31:0] wdt_cnt;
    reg        wdt_fired;

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            hb_prev   <= 32'h0;
            wdt_cnt   <= 32'h0;
            wdt_fired <= 1'b0;
        end else begin
            hb_prev <= hb_reg;
            if (hb_reg != hb_prev) begin
                wdt_cnt   <= 32'h0;                       // 心跳变了 → 清零
                wdt_fired <= 1'b0;
            end else if (wdt_cnt < WDT_LIMIT) begin
                wdt_cnt <= wdt_cnt + 32'd1;
            end else begin
                wdt_fired <= 1'b1;                        // 锁死，直到心跳再来
            end
        end
    end

    wire motor_en = ctrl_reg[0] && !wdt_fired;
    wire estop    = ctrl_reg[0] && wdt_fired;             // 使能着但被看门狗掐了

    // STATUS: bit0=看门狗超时 bit1=急停锁死 bit2=电机使能中
    always @(*) begin
        status_reg = {29'h0, motor_en, estop, wdt_fired};
    end

    // ===================== PWM 发生器 =====================
    reg [15:0] pwm_cnt;

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            pwm_cnt <= 16'd0;
        end else if (pwm_cnt >= PWM_PERIOD - 1) begin
            pwm_cnt <= 16'd0;
        end else begin
            pwm_cnt <= pwm_cnt + 16'd1;
        end
    end

    // 有符号速度 → 幅值 + 方向
    wire signed [15:0] spd_l_s = spd_l_reg;
    wire signed [15:0] spd_r_s = spd_r_reg;
    wire [15:0] spd_l_abs = spd_l_s[15] ? (~spd_l_s + 16'd1) : spd_l_s;
    wire [15:0] spd_r_abs = spd_r_s[15] ? (~spd_r_s + 16'd1) : spd_r_s;

    // 幅值 → 占空比计数值：duty = |spd| * PWM_PERIOD / SPD_FULL
    // 常数除法由综合器优化成乘法+移位，不消耗除法器
    wire [31:0] duty_l_w = (spd_l_abs * PWM_PERIOD) / SPD_FULL;
    wire [15:0] duty_l   = duty_l_w[15:0];
    wire [31:0] duty_r_w = (spd_r_abs * PWM_PERIOD) / SPD_FULL;
    wire [15:0] duty_r   = duty_r_w[15:0];

    assign pwm_l = motor_en && (pwm_cnt < duty_l);
    assign pwm_r = motor_en && (pwm_cnt < duty_r);

    // 方向：正转 in1=1/in2=0，反转 in1=0/in2=1，速度 0 时都 0（惰行）
    assign in1 = motor_en && !spd_l_s[15] && (spd_l_abs != 16'd0);
    assign in2 = motor_en &&  spd_l_s[15] && (spd_l_abs != 16'd0);
    assign in3 = motor_en && !spd_r_s[15] && (spd_r_abs != 16'd0);
    assign in4 = motor_en &&  spd_r_s[15] && (spd_r_abs != 16'd0);

    // ===================== 编码器计数（4 倍频）=====================
    // ⚠️ 输入是异步信号，这里只做两级同步，没做消抖。
    //    若实测计数乱跳，在 enc_* 前面加 RC 滤波或 10ms 计数消抖。
    reg [1:0]  la_s, lb_s, ra_s, rb_s;
    reg [1:0]  l_prev, r_prev;

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            la_s <= 2'b00; lb_s <= 2'b00;
            ra_s <= 2'b00; rb_s <= 2'b00;
            l_prev <= 2'b00; r_prev <= 2'b00;
            enc_l_cnt <= 32'd0; enc_r_cnt <= 32'd0;
        end else begin
            la_s <= {la_s[0], enc_l_a};
            lb_s <= {lb_s[0], enc_l_b};
            ra_s <= {ra_s[0], enc_r_a};
            rb_s <= {rb_s[0], enc_r_b};

            l_prev <= {la_s[1], lb_s[1]};
            r_prev <= {ra_s[1], rb_s[1]};

            // 标准正交解码：{旧状态, 新状态} 的合法跳变只有 8 种
            case ({l_prev, la_s[1], lb_s[1]})
                4'b00_01, 4'b01_11, 4'b11_10, 4'b10_00:
                    enc_l_cnt <= enc_l_cnt + 32'd1;
                4'b00_10, 4'b10_11, 4'b11_01, 4'b01_00:
                    enc_l_cnt <= enc_l_cnt - 32'd1;
                default: ;
            endcase
            case ({r_prev, ra_s[1], rb_s[1]})
                4'b00_01, 4'b01_11, 4'b11_10, 4'b10_00:
                    enc_r_cnt <= enc_r_cnt + 32'd1;
                4'b00_10, 4'b10_11, 4'b11_01, 4'b01_00:
                    enc_r_cnt <= enc_r_cnt - 32'd1;
                default: ;
            endcase
        end
    end

endmodule
