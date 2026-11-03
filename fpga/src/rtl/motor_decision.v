//============================================================
// 文件名   : motor_decision.v
// 功能     : PL 端融合决策 —— 收 UDP 指令帧，按阈值决策电机+蜂鸣器
//
//  这是"决策放 PL"的实现：PC 只发"最近人的距离 + 转向方向"，这里自己判断
//  该走/该鸣/该停/该绕障，直接输出电机 PWM 占空比/方向和蜂鸣器电平。
//  不经过 PS、不需要 lwIP，全 PL 闭环。
//
//  时钟域：eth_rx_clk（RGMII 接收时钟，125MHz）。输入 udp_rec_*
//  都在这个域，输出 duty/dir/beep 是慢速信号，由 top 层用 2 级
//  同步器跨到电机 PWM 的时钟域。
//
//  UDP 指令帧协议（PC 端 detect_fusion_live.py 的 CmdSender 发出）：
//    字节 [0] = 0xAA      帧头 1
//    字节 [1] = 0x55      帧头 2
//    字节 [2] = 距离高 8 位（mm，u16 大端）
//    字节 [3] = 距离低 8 位（mm）
//    字节 [4] = 转向方向：0x00=直行 0x01=左转(障碍在右) 0x02=右转(障碍在左)
//    距离 = 0xFFFF 表示"无人 / 测不出距离"
//  与 start_transfer_ctrl 认的单字节 '1' 不冲突：那个要求
//  udp_rec_byte_num==1，这个要求 ==5 且带 0xAA55 帧头。
//
//  决策规则（沿用 DecisionConfig 默认阈值，1m/3m）：
//    无人 / 距离 > 3000mm        -> CLEAR：70% 前进，蜂鸣器静音
//    1000 < 距离 <= 3000mm       -> WARN ：70% 前进，蜂鸣器短促间歇（120ms/1s）
//    距离 <= 1000mm              -> STOP ：占空比清零，蜂鸣器连续响
//    看门狗：超过 400ms 没收到新帧 -> SAFE：停车但**静音**
//    非活体障碍绕行（转向字节）：障碍在左 -> 右转，在右 -> 左转，减速缓转
//        （一侧轮降到 TURN_DUTY，另一侧保持 CRUISE_DUTY，边走边转，不停车不响）
//    全局静音：BEEP_ENABLE=0 可关掉所有鸣叫，只保留电机动作
//
//  说明：PC 端 ground_distance 已经算好"最近人的距离"，这里不重复
//  几何计算，只做阈值决策 + 转向 + 看门狗，职责和 ps_fusion_ctrl.c 对齐。
//============================================================

module motor_decision #(
    parameter CLK_FREQ       = 125_000_000,  // eth_rx_clk 频率
    parameter CRUISE_DUTY    = 70,           // 巡航占空比 %
    parameter TURN_DUTY      = 30,           // 转向时内侧轮的占空比（减速缓转）
    parameter STOP_DIST_MM   = 1000,         // <=1m 停车
    parameter WARN_DIST_MM   = 3000,         // <=3m 鸣叫（>1m）
    parameter WDT_TIMEOUT_US = 400000,       // 400ms 看门狗
    parameter BEEP_ENABLE    = 1,            // 0 = 全局静音（只留电机动作）
    parameter WDT_BEEP       = 0,            // 1 = 断流时也连续响（默认静音）
    // ---- 速度闭环 PID（左右后轮编码器）----
    parameter [31:0] TARGET_SPEED = 32'd70,   // 目标脉冲频率(脉冲/10ms)。全速实测≈156，取~45%中速
    parameter [7:0]  TURN_RATIO   = 8'd40,    // 转向时内侧轮速度百分比(%)
    parameter [7:0]  PID_FF       = 8'd45,    // 前馈基础占空比(%)，≈target 对应的开环占空比
    parameter [15:0] PID_KP       = 16'd32,   // 比例增益 Q8 (32=0.125)，原128太激进致overshoot
    parameter [15:0] PID_KI       = 16'd16,   // 积分增益 Q8 (16=0.0625)，原64太大
    // ---- 雷达避障（第二路）----
    //   RADAR_AVOID_EN=1 时，PC 没给转向（steer=0）的情况下由雷达接管转向。
    //   若雷达装车底、测到的全是地面回波导致误转向，把这里改成 1'b0 重新综合即可停用。
    parameter        RADAR_AVOID_EN = 1'b1,
    //   雷达最小有效障碍距离(mm)：低于它的回波视为地面/贴脸误报，不触发转向。
    //   雷达装在车底朝下时，地面回波恒为"离地高度"(约 100~200mm)，靠这个下限滤掉。
    //   雷达最小有效障碍距离(mm)。原设 150 是想滤"车底雷达扫到地面"的固定回波，
    //   但实测雷达测距本身是准的，这个下限反而制造了 50~150mm 的动作空档
    //   （既不触发转向也不触发急停）。现归零，让 50~500mm 全部交给转向，
    //   50mm 以内由下面的 RADAR_ESTOP 兜底。
    parameter [15:0] RADAR_MIN_MM   = 16'd0,
    // ---- 雷达紧急停车保护（2026-11-02 新增）----
    //   雷达报"最近障碍 < 5cm"时强制停车 + 长鸣，直到回波回到 > 50cm 才解除。
    //   这是兜底：视觉可能漏检、单目测距可能算不出，但雷达测量面是硬的。
    //   ★ 前提：雷达前方不能有恒定的贴脸回波（轮胎/底盘/地面），否则会一直停。
    //     若上板发现车不走，先看 PS 打印的前方距离是多少，再调 RADAR_ESTOP_MM。
    parameter [15:0] RADAR_ESTOP_MM     = 16'd150,   // < 15cm 触发紧急停车（2026-11-03 晚：10cm 太近）
    parameter [15:0] RADAR_ESTOP_REL_MM = 16'd250,   // > 25cm 解除
    // ---- 停车缓冲（2026-11-02 新增，解决"人一闪身/漏检车就蹿出去"）----
    //   进入 STOP 后，必须同时满足下面两条才允许恢复行驶：
    //     ① 已经在 STOP 停留 >= STOP_HOLD_US（停车后 1s 内无论发生什么都不动）
    //     ② 人退到 3m 外；或者人消失并持续 >= GONE_HOLD_US
    //   放在 PL 而不是 PC：PL 才是最终决策者，PC 一旦漏检发 0xFFFF，
    //   PL 的 !have_person 会直接判 CLEAR，缓冲会被绕过。
    parameter [31:0] STOP_HOLD_US  = 32'd1_000_000,   // 停车后最短保持 1s
    parameter [31:0] GONE_HOLD_US  = 32'd1_000_000,   // 人消失需持续 1s 才放行
    // ---- 开环测试 ----
    //   已改成【运行时切换】，不再需要改参数重综合：
    //   发 AA 55 <左duty%> <右duty%> 03 即进入开环，见下方 test_mode 说明。
    //   这一行只留作占位，恒为 0（正式量产可删）。
    parameter        OPEN_LOOP_TEST = 1'd0,
    // ---- 全开环行驶（2026-11-02 23:07，右编码器失效后的兜底方案）----
    //   1 时彻底不用编码器：占空比固定，直行 DUTY_CRUISE，转向外侧 DUTY_TURN_OUT / 内侧 DUTY_TURN_IN。
    //   状态机（STOP / SAFE / WARN）仍然生效，所以"人靠近停车""雷达 <5cm 急停"
    //   "PC 断流停车"这些保护全部保留，只是不再做速度闭环。
    //   代价：车速不随电池电压和负载自动补偿；转向靠固定的 85/65 差速。
    //   （2026-11-03 实测：空转 80% 偏快、下地又偏慢，统一提到 85%；转向
    //     差速从 80/50 收到 85/65，免得转弯太急。）
    //   右编码器修好后把这里改回 0 即可（届时双路闭环+自动降级都会重新生效）。
    parameter        OPENLOOP_ALL  = 1'b1,
    parameter [7:0]  DUTY_CRUISE   = 8'd70,   // 直行占空比 %（2026-11-03 晚：80→70，下地偏快）
    parameter [7:0]  DUTY_TURN_OUT = 8'd80,   // 转向外侧轮占空比 %
    parameter [7:0]  DUTY_TURN_IN  = 8'd70,   // 转向内侧轮占空比（绝对值）%
    // ★ 内轮反转（2026-11-03 晚 "80/-70"）：1 = 转向时内侧轮**倒转**，
    //   配合 80% 的外轮可以做到接近原地转，开环下最灵敏的方式。
    //   代价：内侧轮反向时电机会有一瞬间反电动势冲击，电流较大；
    //   且没有编码器反馈，占空比给大了可能打滑。设 0 则退回"只降速不反转"。
    parameter        TURN_IN_REVERSE = 1'b1
)(
    input  wire        clk,            // eth_rx_clk
    input  wire        rst_n,          // 低有效复位

    // ---- UDP 接收接口（来自 eth_top，本模块所在时钟域）----
    input  wire        udp_rec_pkt_done,
    input  wire        udp_rec_en,
    input  wire [7:0]  udp_rec_data,
    input  wire [15:0] udp_rec_byte_num,

    // ---- 雷达距离输入（来自 PS 经 AXI GPIO，已在 top 层跨时钟域同步）----
    // 位域约定（与 docs/interface.md §6 一致）：
    //   [15:0]  雷达最近障碍距离 mm（0xFFFF = 无）
    //   [23:16] 心跳计数，PS 每次刷新 +1（本模块用它判断数据新鲜度）
    input  wire [15:0] radar_dist,
    input  wire [7:0]  radar_seq,
    //   雷达障碍转向方向（PS 算好）：0=直行 1=左转(障碍在右) 2=右转(障碍在左)
    input  wire [1:0]  radar_steer,

    // ---- 编码器输入（AB 相，左右后轮各一对）----
    input  wire        enc_l_a,
    input  wire        enc_l_b,
    input  wire        enc_r_a,
    input  wire        enc_r_b,

    // ---- 电机控制输出（慢速，跨时钟域同步）----
    output reg  [6:0]  duty_l,        // 0~100
    output reg  [6:0]  duty_r,
    output reg         dir_l,         // 0=正 1=反（实测 dir=1 前进）
    output reg         dir_r,
    output reg         beep,          // 1=响

    // ---- 调试输出（2026-11-02 引到板上 LED，排查"转向不生效"）----
    //   dbg_udp_steer : UDP 帧解析出来的转向字节是否为非 0（PC 的指令到了没有）
    //   dbg_eff_steer : 真正生效的 eff_steer 是否为非 0（雷达仲裁后还有没有效）
    //   两个都亮  -> 转向信号正常，问题在 PID/前馈
    //   只亮 udp  -> eff_steer 被清零（雷达仲裁逻辑）
    //   都不亮    -> UDP 帧根本没解析出转向字节
    output wire        dbg_udp_steer,
    output wire        dbg_eff_steer,
    //   dbg_duty_l_lt_ff : 左轮占空比是否已跌破前馈值（转向时应当为 1）
    output wire        dbg_duty_l_low
);

//-----------------------------------------------------------------
// 时间计数（微秒粒度，用于看门狗）
//-----------------------------------------------------------------
localparam CNT_PER_US = CLK_FREQ / 1_000_000;   // 125

reg [31:0] us_cnt;
wire       us_tick;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) us_cnt <= 32'd0;
    else if (us_cnt >= CNT_PER_US - 1) us_cnt <= 32'd0;
    else us_cnt <= us_cnt + 1'b1;
end
assign us_tick = (us_cnt == CNT_PER_US - 1);

reg [31:0] last_rx_us;    // 最后一帧时刻（us）
reg [31:0] now_us;
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) now_us <= 32'd0;
    else if (us_tick) now_us <= now_us + 1'b1;
end

//-----------------------------------------------------------------
// UDP 帧解析：找 0xAA 0x55 帧头 + 2 字节距离
//-----------------------------------------------------------------
reg [2:0]  rx_state;     // 状态机
reg [7:0]  dist_hi;
reg [7:0]  dist_lo;      // 距离低字节。常规模式下只是 dist_mm 的一半；
                         // 开环测试模式(steer=3)下它单独当"右通道占空比%"用。
reg [15:0] dist_mm;      // 解析出的距离
(* mark_debug = "true" *) reg [1:0]  steer;        // 转向方向：0=直行 1=左转 2=右转 3=开环测试

// ---- 下面三个信号前移到此处声明，供蜂鸣器的"编码器活体检测"提前使用 ----
wire signed [31:0] speed_l, speed_r;            // 编码器实测速度（脉冲/10ms，带符号）
wire               speed_valid_l, speed_valid_r; // 速度采样完成脉冲
wire               test_mode = (steer == 2'd3);  // 开环测试模式
reg        dist_valid;   // 本包解析到有效距离
reg        have_person;  // 距离 != 0xFFFF
reg        ever_rx;      // 收到过至少一帧有效距离

// ---- 雷达第二路：心跳看门狗 ----
reg [7:0]  radar_seq_reg;    // 上次看到的雷达心跳
reg        radar_seq_chg;    // 本拍雷达心跳有变化（PS 更新了新数据）
reg [31:0] last_radar_us;    // 最后一次雷达心跳变化时刻
reg        radar_ever;       // 雷达心跳曾经变过（PS 真正写过数据）
wire       radar_fresh;      // 雷达数据新鲜（心跳在超时窗口内）
localparam RADAR_WDT_US = 500000;   // 500ms 内无心跳更新 = 雷达失效

// 帧 = AA 55 + 2 字节距离 + 1 字节转向，共 5 字节 = 5 个 udp_rec_en 脉冲。
// 状态数必须与字节数一致：S_IDLE / S_HDR1 吃两个帧头，
// S_HDR2 存高字节，S_DHI 用当前字节 + 已存高字节**当场提交**距离，
// S_STEER 收转向字节并提交。
// （旧版多了一个 S_DLO，要等第 5 个字节才提交，而包早就结束了，
//   被 pkt_done 打回 IDLE，距离永远提交不了 —— 已修）
localparam S_IDLE = 3'd0, S_HDR1 = 3'd1, S_HDR2 = 3'd2,
           S_DHI  = 3'd3, S_STEER = 3'd4;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        rx_state    <= S_IDLE;
        dist_hi     <= 8'd0;
        dist_lo     <= 8'd0;
        dist_mm     <= 16'hFFFF;
        steer       <= 2'd0;
        dist_valid  <= 1'b0;
        have_person <= 1'b0;
        ever_rx     <= 1'b0;
        last_rx_us  <= 32'd0;
        radar_seq_reg <= 8'd0;
        radar_seq_chg <= 1'b0;
        last_radar_us <= 32'd0;
        radar_ever    <= 1'b0;
    end else begin
        dist_valid <= 1'b0;   // 默认本拍无新帧

        // ---- 雷达心跳检测：seq 变化 = PS 刷新了新的雷达距离 ----
        radar_seq_chg <= 1'b0;
        if (radar_seq != radar_seq_reg) begin
            radar_seq_reg <= radar_seq;
            radar_seq_chg <= 1'b1;
            last_radar_us <= now_us;
            radar_ever    <= 1'b1;
        end

        if (udp_rec_en) begin
            case (rx_state)
                S_IDLE: begin
                    if (udp_rec_data == 8'hAA) rx_state <= S_HDR1;
                end
                S_HDR1: begin
                    if (udp_rec_data == 8'h55) rx_state <= S_HDR2;
                    else if (udp_rec_data == 8'hAA) rx_state <= S_HDR1;
                    else rx_state <= S_IDLE;
                end
                S_HDR2: begin
                    dist_hi <= udp_rec_data;      // 距离高字节
                    rx_state <= S_DHI;
                end
                S_DHI: begin
                    // 距离低字节到齐，本拍提交距离
                    dist_lo    <= udp_rec_data;
                    dist_mm    <= {dist_hi, udp_rec_data};
                    rx_state   <= S_STEER;
                end
                S_STEER: begin
                    // 转向字节到齐，提交转向并回到 IDLE
                    //   0=直行 1=左转 2=右转 3=开环测试模式（见下方 test_mode）
                    steer      <= (udp_rec_data == 8'h02) ? 2'd2 :
                                  (udp_rec_data == 8'h01) ? 2'd1 :
                                  (udp_rec_data == 8'h03) ? 2'd3 : 2'd0;
                    dist_valid <= 1'b1;
                    rx_state   <= S_IDLE;
                end
                default: rx_state <= S_IDLE;
            endcase
        end

        if (udp_rec_pkt_done) begin
            // 包结束，状态机归位（防半包残留）
            rx_state <= S_IDLE;
        end

        if (dist_valid) begin
            have_person <= (dist_mm != 16'hFFFF);
            last_rx_us  <= now_us;
            ever_rx     <= 1'b1;
        end
    end
end

//-----------------------------------------------------------------
// 看门狗 + 决策
//-----------------------------------------------------------------
wire wdt_triggered = (!ever_rx) || (now_us - last_rx_us > WDT_TIMEOUT_US);

// 雷达数据新鲜度：心跳在 500ms 内更新过，且 PS 曾经真正写过（radar_ever）
assign radar_fresh = radar_ever && (now_us - last_radar_us <= RADAR_WDT_US);

// 障碍距离（PS 算好，经 GPIO）：[15:0] 距离 mm，0xFFFF=无
// 障碍方向：[25:24]（由 top 层从 gpio_out 抽取传入，这里只关心距离+新鲜度）

// 决策输出（组合，由下方时序块打拍）
reg [2:0] state;   // 0=CLEAR 1=WARN 2=STOP 3=SAFE
// ST_SAFE：断流/上电，停车但静音。没收到数据不等于前方危险。
localparam ST_CLEAR = 3'd0, ST_WARN = 3'd1,
           ST_STOP  = 3'd2, ST_SAFE = 3'd3;

// 障碍避障阈值：< 1m 触发转向（2026-11-03 下午：从 50cm 改成 1m）
localparam AVOID_DIST_MM = 1000;

// ---- 停车缓冲计时（微秒，靠 us_tick 驱动）----
//   stop_us：已在 STOP 停留了多久，离开 STOP 立刻清零
//   gone_us：连续"看不到人"的时长，看到人立刻清零
//   上限 4s 就够（只用于和 1s 门槛比较），防止长期累加到溢出
localparam [31:0] HOLD_CNT_MAX = 32'd4_000_000;
reg [31:0] stop_us, gone_us;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        stop_us <= 32'd0;
        gone_us <= 32'd0;
    end else if (us_tick) begin
        stop_us <= (state == ST_STOP)
                   ? ((stop_us < HOLD_CNT_MAX) ? stop_us + 1'b1 : stop_us)
                   : 32'd0;
        gone_us <= (!have_person)
                   ? ((gone_us < HOLD_CNT_MAX) ? gone_us + 1'b1 : gone_us)
                   : 32'd0;
    end
end

//-----------------------------------------------------------------
// 雷达紧急停车（<5cm 强制停，>50cm 才解除）
//-----------------------------------------------------------------
//   视觉可能漏检、单目可能算不出距离，但"雷达测到的最近回波 < 5cm"这件事
//   本身足以判定快要撞上了，直接停车 + 长鸣，压过其余所有状态转移。
//   注意：这段必须放在状态机之前（Verilog 不允许先用后定义）。
wire radar_estop_set = radar_fresh && (radar_dist != 16'hFFFF)
                    && (radar_dist < RADAR_ESTOP_MM);

// 解除条件（三条任一即可）：
//   ① 回波回到 > 50cm
//   ② 雷达报 0xFFFF（前方无目标）—— 注意 0xFFFF=65535 数值上也 >500，
//      必须单独判，否则"无目标"会被当成"很远"从而误解除；反过来说，
//      如果没有这一条，急停后一旦报无目标就永远解除不了。
//   ③ 雷达完全没数据（不能因为雷达掉线把车锁死，掉线有独立保护）
wire radar_estop_rel = (!radar_fresh)
                    || (radar_dist == 16'hFFFF)
                    || ((radar_dist > RADAR_ESTOP_REL_MM)
                        && (radar_dist != 16'hFFFF));

reg radar_estop;
always @(posedge clk or negedge rst_n) begin
    if (!rst_n)               radar_estop <= 1'b0;
    else if (radar_estop_set) radar_estop <= 1'b1;
    else if (radar_estop_rel) radar_estop <= 1'b0;
end

// 下降沿：刚解除的那一拍让状态机直接回 CLEAR。
// 否则会卡在 STOP 分支的"必须退到 3m 外或消失 1s"里 —— 那个条件是为
// "人"设计的，跟雷达紧急停车无关，解除后应该立刻能走。
reg radar_estop_d;
always @(posedge clk or negedge rst_n) begin
    if (!rst_n) radar_estop_d <= 1'b0;
    else        radar_estop_d <= radar_estop;
end
wire estop_release = radar_estop_d && !radar_estop;

//-----------------------------------------------------------------
// 雷达掉线保护（2026-11-02 新增）
//-----------------------------------------------------------------
//   PS 曾经给过数据(radar_ever 置过)，现在超过 500ms 没有心跳 = 掉线。
//   处理：立刻停车（进 ST_SAFE）+ 蜂鸣器"中速"叫 3 声后静音。
//   为什么不是一直响：掉线是"能力失效"，不是"前方危险"，叫完就该安静，
//   否则车停在原地一直嘀嘀会很吵，也分不清是人触发还是掉线触发。
wire radar_lost = radar_ever && !radar_fresh;

localparam [31:0] LOST_BEEP_PERIOD_US = 32'd400_000;  // 400ms 一个周期
localparam [31:0] LOST_BEEP_ON_US     = 32'd200_000;  // 前 200ms 响 -> 2.5 声/秒
localparam [1:0]  LOST_BEEP_NUM       = 2'd3;         // 叫 3 声

reg [31:0] lost_cnt;      // 当前周期内已过微秒
reg [1:0]  lost_remain;   // 还剩几声
reg        lost_d;        // 掉线上升沿检测

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        lost_cnt    <= 32'd0;
        lost_remain <= 2'd0;
        lost_d      <= 1'b0;
    end else begin
        lost_d <= radar_lost;
        if (!radar_lost) begin
            // 雷达恢复：清零，下次掉线重新叫 3 声
            lost_cnt    <= 32'd0;
            lost_remain <= 2'd0;
        end else if (!lost_d) begin
            // 刚掉线的那一拍：装载 3 声
            lost_cnt    <= 32'd0;
            lost_remain <= LOST_BEEP_NUM;
        end else if (us_tick) begin
            if (lost_cnt >= LOST_BEEP_PERIOD_US - 1) begin
                lost_cnt <= 32'd0;
                if (lost_remain != 2'd0) lost_remain <= lost_remain - 1'b1;
            end else begin
                lost_cnt <= lost_cnt + 1'b1;
            end
        end
    end
end

wire lost_beep = radar_lost && (lost_remain != 2'd0)
                 && (lost_cnt < LOST_BEEP_ON_US);

// 雷达急停蜂鸣（2026-11-03 下午）：中速 3 声后静音，复用掉线同款节奏。
//   为什么不是连续长鸣：长鸣专指"人贴脸"；雷达急停要和它区分开，
//   否则分不清是雷达触发还是人触发（之前"没人却长鸣"就是这个歧义）。
reg [31:0] estop_cnt;
reg [1:0]  estop_remain;
reg        estop_d;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        estop_cnt    <= 32'd0;
        estop_remain <= 2'd0;
        estop_d      <= 1'b0;
    end else begin
        estop_d <= radar_estop;
        if (!radar_estop) begin
            estop_cnt    <= 32'd0;
            estop_remain <= 2'd0;
        end else if (!estop_d) begin          // 急停上升沿：装 3 声
            estop_cnt    <= 32'd0;
            estop_remain <= LOST_BEEP_NUM;
        end else if (us_tick) begin
            if (estop_cnt >= LOST_BEEP_PERIOD_US - 1) begin
                estop_cnt <= 32'd0;
                if (estop_remain != 2'd0) estop_remain <= estop_remain - 1'b1;
            end else begin
                estop_cnt <= estop_cnt + 1'b1;
            end
        end
    end
end

wire estop_beep = radar_estop && (estop_remain != 2'd0)
               && (estop_cnt < LOST_BEEP_ON_US);

// ---- 停车判据用的距离：一律是 PC 发来的"人的视觉距离" ----
//   设计约定（2026-11-02 重新厘清，之前的"雷达优先测距"是理解错了）：
//     50cm 以外 —— **人的检测优先**：停车/鸣叫只看人的距离(dist_mm)，
//                  雷达这时候即使测到更近的障碍也不参与停车判断。
//     50cm 以内 —— **避障优先**：由 radar_obstacle 触发差速转向绕行，
//                  停不停车仍然看人（人不在 1m 内就不停车）。
//   为什么雷达距离不能拿来判"人有多远"：
//     PS 送给 PL 的只是"±35° 走廊内最近回波"这一个标量，不区分人和障碍。
//     人 2m、前方 1m 有把椅子时，radar_dist=1000 代表的是椅子；拿它当人的
//     距离就会误判成"人走到 1m 了"。而 50cm 以内才用雷达，恰好避开了这个歧义
//     ——那个距离上无论是人还是障碍，动作都是"减速绕开"。
// 0xFFFE = 有人但测不出距离（PC 端约定）→ 保守取 WARN 区（2000mm）：只鸣叫、不停车。
//   实测单目经常测不出（低视角相机 + 脚点不在框底），若一律当 0mm 停车，
//   会变成"一检测到人就停"、太激进。近距离安全由雷达兜底（<1m 转向 + <10cm 急停）。
//   0xFFFF = 无人。其余 = 距离 mm。
wire [15:0] dec_dist = (dist_mm == 16'hFFFE) ? 16'd2000 : dist_mm;

// 解除停车的条件（两条，必须都满足前面那组计时）：
//   人还在视野里但已退到 3m 之外   —— 立即算"安全"
//   人已经完全消失               —— 要消失满 GONE_HOLD_US 才信
wire person_far_gone = (have_person && (dec_dist > WARN_DIST_MM))
                    || (!have_person && (gone_us >= GONE_HOLD_US));

// 允许从 STOP 恢复行驶：停车满 1s 且 人已远离/消失满 1s
wire stop_release = (stop_us >= STOP_HOLD_US) && person_far_gone;

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        state   <= ST_SAFE;    // 上电默认停车+静音，等 PC 第一帧
    end else begin
        if (test_mode) begin
            // 开环测试模式：帧里的两个字节是占空比，不是距离。
            // 若照常解析会被当成 "人在 0mm" → ST_STOP → 蜂鸣器长鸣，
            // 正好把"编码器活体检测"的蜂鸣提示盖掉（实测踩过）。
            // 所以测试模式下强制 CLEAR：不报警、不留 STOP。
            state <= ST_CLEAR;
        end else if (radar_estop) begin
            // ★ 最高优先级：雷达报最近回波 < 5cm，快要撞上了。
            //   STOP 状态蜂鸣器本来就是长鸣，正好符合要求。
            state <= ST_STOP;
        end else if (radar_lost) begin
            // 雷达掉线：立刻停车。但如果此刻本来就是 STOP（人已经贴到 1m 内），
            // 保持 STOP 不要降级成 SAFE —— SAFE 是静音，人就在跟前却不长鸣是错的。
            if (state != ST_STOP) state <= ST_SAFE;
        end else if (estop_release) begin
            // 刚解除紧急停车：按人当前的距离决定，别一律放行。
            // 人在 1m 内仍然 STOP，否则才恢复行驶（下一帧再按正常逻辑微调）。
            state <= (have_person && (dec_dist <= STOP_DIST_MM)) ? ST_STOP : ST_CLEAR;
        end else if (wdt_triggered) begin
            state <= ST_SAFE;
        end else if (dist_valid) begin
            // 距离一律用 dec_dist：检测到人时雷达优先，雷达失效才用单目
            if (state == ST_STOP) begin
                // ★ 停车后只能通过 stop_release 解除，不允许因为
                //   "距离回到 1~3m（WARN 区）"就把状态降级成 WARN 直接走车。
                //   人一闪身、漏检一两帧、退到 1.5m，都继续停。
                if (stop_release) state <= ST_CLEAR;
            end else if (!have_person || dec_dist > WARN_DIST_MM)
                state <= ST_CLEAR;
            else if (dec_dist <= STOP_DIST_MM)
                state <= ST_STOP;
            else
                state <= ST_WARN;
        end
        // 雷达障碍（独立于视觉）：新鲜且 <50cm 时，虽不改变"人"的状态，
        // 但会通过转向字节让车绕行（见下方电机输出块）
    end
end

//-----------------------------------------------------------------
// 蜂鸣器：CLEAR 静音；STOP 连续响；WARN 间歇响（约 2Hz）
//-----------------------------------------------------------------
reg [23:0] beep_cnt;
wire       beep_toggle;
localparam BEEP_PERIOD_US = 1_000_000;   // 1s 一个周期
localparam BEEP_ON_US     = 120_000;     // 响 120ms（短促，不吵）

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) beep_cnt <= 24'd0;
    else if (us_tick) begin
        if (beep_cnt >= BEEP_PERIOD_US - 1) beep_cnt <= 24'd0;
        else beep_cnt <= beep_cnt + 1'b1;
    end
end
assign beep_toggle = (beep_cnt < BEEP_ON_US);

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) beep <= 1'b0;
    else if (!BEEP_ENABLE) beep <= 1'b0;      // 全局静音
    // 编码器活体检测：开环测试模式下把两路占空比都设 0（电机不动），
    // 用手转轮子，有脉冲蜂鸣器就响 —— 用来直接判断编码器到底有没有信号。
    // ★ 必须排在 STOP 长鸣之前：测试模式下 dist 字节是占空比（常为 0），
    //   不走这一步会被当成"人在 0mm"而长鸣，把检测提示完全盖掉。
    else if (test_mode && (dist_hi == 8'd0) && (dist_lo == 8'd0))
        beep <= (speed_l != 32'sd0) || (speed_r != 32'sd0);
    // 优先级：雷达急停（中速 3 声）> 人贴脸长鸣 > 雷达掉线 3 声 > 各状态默认
    // 雷达急停必须排在 state==ST_STOP 之前：急停也会把 state 拉成 STOP，
    // 不先判断就会被当成"人贴脸"连续长鸣（实测"没人却长鸣"就是这个）。
    else if (radar_estop)     beep <= estop_beep;   // 雷达急停：中速 3 声后静音
    else if (state == ST_STOP) beep <= 1'b1;         // 人贴脸：连续响
    else if (lost_beep)       beep <= 1'b1;          // 雷达掉线：中速 3 声
    else begin
        case (state)
            ST_CLEAR: beep <= 1'b0;
            ST_WARN:  beep <= beep_toggle;          // 短促间歇
            ST_STOP:  beep <= 1'b1;                 // 已在上面处理，保留兜底
            ST_SAFE:  beep <= (WDT_BEEP != 0) ? 1'b1 : 1'b0;  // 断流默认静音
            default:  beep <= 1'b0;
        endcase
    end
end

//-----------------------------------------------------------------
// 电机：速度闭环 PID（编码器反馈），CLEAR/WARN 运行，STOP/SAFE 清零
//-----------------------------------------------------------------
// 有效转向（2026-11-02 重做判据，保持"雷达优先"不变）
//   设计语义：避障是安全功能，一旦雷达确认前方有非活体障碍，必须压过
//   PC 下发的"继续走"，所以仍然是雷达优先、PC 兜底。
//
//   但"雷达优先"的前提是雷达数据可信。旧版踩的坑是：只要 radar_dist
//   < 500mm 就转向，而雷达装在车底朝下时，扫到的固定回波（地面/底盘/轮胎）
//   永远在这个范围内，于是 steer 被永久锁死在一侧 —— 表现为"一侧轮一直
//   比另一侧慢、PC 发转向完全没反应"。
//
//   现在雷达回波必须同时满足四条才算"真障碍"：
//     ① radar_fresh        —— 500ms 内有心跳，PS 的雷达桥还活着
//     ② dist != 0xFFFF     —— 不是"无目标"占位值
//     ③ dist >= RADAR_MIN_MM —— 下限，滤掉车底打到地面/底盘的固定近距离回波
//     ④ dist <  AVOID_DIST_MM —— 上限 50cm，超出不算障碍
//     ⑤ radar_steer != 0   —— PS 已经判过左右
//   任一条不满足 -> 视为无障碍 -> 服从 PC 的转向字节（通常是 0，直行）。
//
//   RADAR_MIN_MM 是现场唯一需要标定的参数：上板后看 PS 打印的"前方最近 XXm"，
//   把车上没有真实障碍时的那个读数往上留 20~30mm 余量填进来即可。
//   若雷达暂时不可用，把 RADAR_AVOID_EN 设 1'b0 整体停用。
wire radar_obstacle = radar_fresh
                   && (radar_dist != 16'hFFFF)
                   && (radar_dist >= RADAR_MIN_MM)
                   && (radar_dist <  AVOID_DIST_MM)
                   && (radar_steer != 2'd0);

(* mark_debug = "true" *) wire [1:0] eff_steer = (RADAR_AVOID_EN && radar_obstacle) ? radar_steer : steer;

//-----------------------------------------------------------------
// 转向换向最小保持时间 —— 已于 2026-11-03 晚删除
//-----------------------------------------------------------------
//   本来加过一层 300ms 的换向保持（把 eff_steer 锁成 steer_held，防止障碍在
//   扇区边缘抖动导致内轮高频正反转）。实测下来延迟感明显：挡住障碍后车要
//   等 300ms 才开始转，脱障后又要等 300ms 才回正，跑起来"发木"。
//   已按用户要求去掉，eff_steer 直接驱动占空比和 dir。
//
//   ⚠️ 副作用（知道就行）：雷达 10Hz / 视觉 20Hz 读数抖动时，内侧轮可能跟着
//   高频正反转，H 桥换向电流偏大。如果实际跑听到连续"咔咔"声，就说明抖动
//   太频繁了 —— 那时应该从源头解决（加大雷达扇区/加迟滞），而不是再锁时间。

// 电机运行条件：只有 CLEAR / WARN 才让 PID 闭环，STOP/SAFE 复位清零
wire motor_run = (state == ST_CLEAR) || (state == ST_WARN);

// 目标速度：直行 = TARGET_SPEED；转向时内侧轮降到 TURN_RATIO%
//   左转(eff_steer=1)：左轮减速；右转(eff_steer=2)：右轮减速
wire signed [31:0] target_l = (eff_steer == 2'd1)
    ? ($signed(TARGET_SPEED) * $signed({24'd0, TURN_RATIO}) / 100)
    : $signed(TARGET_SPEED);
wire signed [31:0] target_r = (eff_steer == 2'd2)
    ? ($signed(TARGET_SPEED) * $signed({24'd0, TURN_RATIO}) / 100)
    : $signed(TARGET_SPEED);

// 前馈占空比也要按目标速度同比例缩放（2026-11-02 修）
//   原来 FF_DUTY 是编译期常量 45（对应巡航 70 的占空比预算）。转向时
//   target 降到 28，前馈却还按 70 给 45%，而 KP=0.125 最多只能修 5 个点，
//   所以内侧轮几乎不降速 —— 实测 steer=1 / steer=2 两轮速度都不变。
//   （同时验证过：发 STOP 距离能正常停车长鸣，说明帧和 dist 解析都没问题，
//     纯粹是转向这条链路没作用到占空比上。）
//   45 * 40 / 100 = 18：转向时前馈直接掉到 18%，目标 28 才配得上这个预算。
//   ★ 位宽陷阱：Verilog 里 8位×8位 的结果宽度仍是 8 位，45*40=1800 会被
//     截成 8，再除 100 得 0 —— 那样内侧轮就不是降速而是直接停转。
//     必须先把两个操作数拼成 16 位再相乘。
localparam [15:0] FF_TURN = ({8'd0, PID_FF} * {8'd0, TURN_RATIO}) / 16'd100;
wire [7:0] ff_l = (eff_steer == 2'd1) ? FF_TURN[7:0] : PID_FF;
wire [7:0] ff_r = (eff_steer == 2'd2) ? FF_TURN[7:0] : PID_FF;

// ---- 编码器计数（AB 相 4 倍频正交解码，测脉冲频率）----
//   speed_l / speed_r / speed_valid_* 已前移到 steer 附近声明（见上方）
wire [7:0]         pid_duty_l;
wire [7:0]         pid_duty_r;
// 堵转助推/失效期间冻结 PID（定义在下方堵转检测里，这里先声明供例化使用）
wire               hold_l, hold_r;
(* mark_debug = "true" *) wire [7:0] dbg_duty_l = pid_duty_l;
(* mark_debug = "true" *) wire [7:0] dbg_duty_r = pid_duty_r;

// ---- 反馈取绝对值（2026-11-02 22:10）----
//   踩过的坑：dir 一翻，电机反转，编码器计数的符号也跟着翻。
//   PID 用的是 error = target - measured，measured 变负号以后 error 变成
//   target+|v|，占空比越推越大 → 越推越快 → 正反馈跑飞。实测"改 dir 就全速"
//   就是这个，不是线接错。
//   本车只做单向行驶（转向靠差速，不靠反转），所以直接对反馈取绝对值：
//   无论编码器 A/B 怎么接、dir 怎么设，环路都只看"转多快"，不看"往哪边转"。
//   副作用：方向从此和闭环稳定性解耦，dir 反了只需要对调电机输出线，
//   不用重综合；编码器 A/B 也不必纠结顺序。
wire signed [31:0] speed_l_fb = (speed_l < 32'sd0) ? -speed_l : speed_l;
wire signed [31:0] speed_r_fb = (speed_r < 32'sd0) ? -speed_r : speed_r;

encoder_counter u_enc_l (
    .clk(clk), .rst_n(rst_n),
    .enc_a(enc_l_a), .enc_b(enc_l_b),
    .speed(speed_l), .speed_valid(speed_valid_l)
);
encoder_counter u_enc_r (
    .clk(clk), .rst_n(rst_n),
    .enc_a(enc_r_a), .enc_b(enc_r_b),
    .speed(speed_r), .speed_valid(speed_valid_r)
);

// ---- 速度 PID（左右各一路，前馈 + PI）----
speed_pid #(
    .KP(PID_KP), .KI(PID_KI)
) u_pid_l (
    .clk(clk), .rst_n(rst_n), .run(motor_run), .hold(hold_l),
    .ff_duty(ff_l),
    .target(target_l), .measured(speed_l_fb), .speed_valid(speed_valid_l),
    .duty(pid_duty_l)
);
speed_pid #(
    .KP(PID_KP), .KI(PID_KI)
) u_pid_r (
    .clk(clk), .rst_n(rst_n), .run(motor_run), .hold(hold_r),
    .ff_duty(ff_r),
    .target(target_r), .measured(speed_r_fb), .speed_valid(speed_valid_r),
    .duty(pid_duty_r)
);

// =====================================================================
// 开环测试模式（2026-11-02 晚新增，用来排查"两轮不转 / 转几圈就停"）
// ---------------------------------------------------------------------
//   帧：AA 55 <左通道duty%> <右通道duty%> 03
//       例 AA 55 46 46 03 → 两路各 70%（0x46=70）
//          AA 55 46 00 03 → 只驱动"左通道"，用来确认通道与物理轮的对应
//          AA 55 00 46 03 → 只驱动"右通道"
//   完全绕开 PID、状态机、编码器保护，纯手动开环。
//   正式行驶发 steer=0/1/2 即可，完全不受影响。
//
//   ★ 编码器活体检测：把两路占空比都发 0（AA 55 00 00 03，电机不转），
//     然后用手转轮子 —— 蜂鸣器响 = 编码器有脉冲进来，不响 = 编码器/接线坏了。
//     这是不用示波器就能判定编码器死活的硬办法。
// =====================================================================
// test_mode 已前移到 steer 附近声明

wire [7:0] duty_ol_l = (dist_hi > 8'd100) ? 8'd100 : dist_hi;   // 左通道
wire [7:0] duty_ol_r = (dist_lo > 8'd100) ? 8'd100 : dist_lo;   // 右通道

// ---- 堵转检测 / 助推 / 自动重试（替换原来会永久锁死的 enc_fail）----
//   旧版：连续 200ms speed==0 → duty 永久清零。可是 duty 一清零，轮子更不可能
//   转起来，speed 就永远是 0 —— 一旦触发再也救不回来，只能靠手拨轮子才恢复。
//   （实测"手拨一下右轮就全速冲出去"正是这个锁死被外力打破的表现，而且因为
//     PID 在锁死期间一直按 error=target 积分 windup，一恢复就直接顶到 100%。）
//   新版分三段，永不锁死：
//     ① 0~30ms     正常 PID，给积分一点时间把占空比顶上去
//     ② 30~600ms   助推：duty 强制 KICK_DUTY(70%)，同时冻结积分防暴冲
//     ③ >600ms     判定堵转/编码器失效：duty=0，但每 2.56s 自动重试 300ms
localparam [7:0] KICK_DUTY = 8'd70;   // 助推占空比（实测起转需 60~70%）
localparam [9:0] KICK_AT   = 10'd3;   // 连续 30ms 无脉冲 → 开始助推
localparam [9:0] FAIL_AT   = 10'd60;  // 连续 600ms 无脉冲 → 判定失效

reg [9:0] stall_l, stall_r;
always @(posedge clk or negedge rst_n) begin
    if (!rst_n)                       stall_l <= 10'd0;
    else if (!motor_run || test_mode) stall_l <= 10'd0;
    else if (speed_valid_l) stall_l <= (speed_l == 32'sd0) ? (stall_l + 10'd1) : 10'd0;
end
always @(posedge clk or negedge rst_n) begin
    if (!rst_n)                       stall_r <= 10'd0;
    else if (!motor_run || test_mode) stall_r <= 10'd0;
    else if (speed_valid_r) stall_r <= (speed_r == 32'sd0) ? (stall_r + 10'd1) : 10'd0;
end

wire fail_l  = (stall_l >= FAIL_AT);
wire fail_r  = (stall_r >= FAIL_AT);
// 失效后 stall 继续累加（10 位自然回绕，1024×10ms≈10.2s 一轮），
// 每 2.56s 出现一次 stall[7:0] < 30 的窗口 → 助推 300ms 试一次
wire retry_l = fail_l && (stall_l[7:0] < 8'd30);
wire retry_r = fail_r && (stall_r[7:0] < 8'd30);
wire kick_l  = (!fail_l && (stall_l >= KICK_AT)) || retry_l;
wire kick_r  = (!fail_r && (stall_r >= KICK_AT)) || retry_r;
// 冻结 PID 的时机：堵转助推、判定失效、开环测试 —— 三种情况都不该让积分继续累加
assign hold_l = test_mode || kick_l || fail_l;
assign hold_r = test_mode || kick_r || fail_r;

// 失效且不在重试窗口 → 强制 0；助推窗口 → KICK_DUTY；其余 → PID
wire [7:0] duty_loop_l = (fail_l && !retry_l) ? 8'd0 :
                         kick_l ? KICK_DUTY : pid_duty_l;
wire [7:0] duty_loop_r = (fail_r && !retry_r) ? 8'd0 :
                         kick_r ? KICK_DUTY : pid_duty_r;

// ---- 单编码器自动降级（2026-11-02 23:05）----
//   实测：右编码器 J4-25 / J4-4 恒 3.3V 不跳变（左轮正常跳变），右路闭环
//   拿不到反馈，会一直卡在"助推 0.6s → 停 → 每 2.56s 重试"的循环里，
//   表现就是右轮一顿一顿、走走停停。
//   这里做自动降级：某一路判定编码器失效、而另一路正常时，直接**跟随另一路
//   的占空比开环输出**。左轮仍然闭环节速，右轮跟着左轮的占空比走，车能连贯跑。
//   ★ 自愈：编码器一旦恢复（speed != 0），fail 自动清零，立刻回到正常闭环，
//     不需要重烧位流。
//   ★ 限制：失效那一路没有独立调速能力，两轮负载不同会跑偏。右路可用
//     MIRROR_TRIM 微调补偿（<100 让右轮慢一点，>100 快一点）。
localparam [7:0] MIRROR_TRIM = 8'd100;   // 降级跟随比例 %
// 先定左路（本车左编码器已验证可用），右路再按它跟随，避免组合逻辑回环
wire [7:0]  duty_l_final = (fail_l && !fail_r) ? duty_loop_r : duty_loop_l;
wire [15:0] mirror_r_raw  = ({8'd0, duty_l_final} * {8'd0, MIRROR_TRIM}) / 16'd100;
wire [7:0]  duty_r_final  = (fail_r && !fail_l) ? mirror_r_raw[7:0] : duty_loop_r;

// 最终占空比
//   测试模式   ：帧里的两个字节直接当两路占空比（调试专用）
//   全开环行驶 ：固定 85 / 转向内侧 65，不经过 PID，但仍然受状态机（停车）约束
//   正常模式   ：PID 输出 + 堵转助推/失效停车/单编码器降级
// 全开环模式下也保持 motor_run 门控：STOP（人 <1m / 雷达 <5cm）和 SAFE（断流）
// 依然会把占空比清零，安全功能和以前完全一致。
//
// ★ 转向方向（2026-11-03 实测修正）：之前 steer=1（左转）时降左通道、steer=2
//   （右转）时降右通道，结果车上实测"该左转却右转、该右转却左转"——说明左通道
//   实际接的是右轮、右通道接的是左轮（和之前"拨左轮右轮冲"的交叉现象一致）。
//   所以把内侧轮反过来指：左转(1) 降右通道、右转(2) 降左通道。
//   2026-11-03 晚：直行 70，转向 80 / 内轮反向 70（= 用户说的 "80/-70"）。
//   内轮的"负号"由 dir 位实现（见下方 always 块），这里只给占空比绝对值。
localparam [6:0] DUTY_CRUISE_7   = DUTY_CRUISE[6:0];
localparam [6:0] DUTY_TURN_OUT_7 = DUTY_TURN_OUT[6:0];
localparam [6:0] DUTY_TURN_IN_7  = DUTY_TURN_IN[6:0];
wire [6:0] duty_fix_l = (eff_steer == 2'd2) ? DUTY_TURN_IN_7  :  // 右转：左通道=内(70,反转)
                        (eff_steer == 2'd1) ? DUTY_TURN_OUT_7 :  // 左转：左通道=外(80)
                        DUTY_CRUISE_7;                            // 直行：70
wire [6:0] duty_fix_r = (eff_steer == 2'd1) ? DUTY_TURN_IN_7  :  // 左转：右通道=内(70,反转)
                        (eff_steer == 2'd2) ? DUTY_TURN_OUT_7 :  // 右转：右通道=外(80)
                        DUTY_CRUISE_7;                            // 直行：70

wire [6:0] duty_out_l = test_mode     ? duty_ol_l[6:0] :
                        OPENLOOP_ALL  ? (motor_run ? duty_fix_l : 7'd0) :
                                        (motor_run ? duty_l_final[6:0] : 7'd0);
wire [6:0] duty_out_r = test_mode     ? duty_ol_r[6:0] :
                        OPENLOOP_ALL  ? (motor_run ? duty_fix_r : 7'd0) :
                                        (motor_run ? duty_r_final[6:0] : 7'd0);

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        duty_l <= 7'd0;
        duty_r <= 7'd0;
        dir_l  <= 1'b0;   // dir=0 前进（开环 70% 实测确认）
        dir_r  <= 1'b0;
    end else begin
        duty_l <= duty_out_l;
        duty_r <= duty_out_r;
        // 直线：两轮 dir=0 前进。
        // 转向（2026-11-03 晚 "80/-70"）：内侧轮反向，做到接近原地转。
        //   通道与物理轮是交叉的（见上方 duty_fix 注释）：
        //     左转(eff_steer=1) → 右通道是内侧 → dir_r=1
        //     右转(eff_steer=2) → 左通道是内侧 → dir_l=1
        // TURN_IN_REVERSE=0 则退回"只降速不反转"的老行为。
        dir_l <= (TURN_IN_REVERSE && (eff_steer == 2'd2)) ? 1'b1 : 1'b0;
        dir_r <= (TURN_IN_REVERSE && (eff_steer == 2'd1)) ? 1'b1 : 1'b0;
    end
end

//-----------------------------------------------------------------
// 调试输出（接板载 LED）：一刀切开"转向到底卡在哪一层"
//-----------------------------------------------------------------
assign dbg_udp_steer  = (steer     != 2'd0);   // UDP 帧里的转向字节
assign dbg_eff_steer  = (eff_steer != 2'd0);   // 雷达仲裁后真正生效的
assign dbg_duty_l_low = (duty_l < PID_FF[6:0]); // 左轮 duty 是否已低于巡航前馈

endmodule
