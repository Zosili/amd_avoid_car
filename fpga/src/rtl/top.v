//=====================================================================
// 文件名   : top.v
// 功能     : 7020 合并工程顶层 —— OV5640 图传 + 双路电机 PWM
//
//   子系统 A：ov5640_udp_pc（纯 PL，独立时钟域）
//       sys_clk 50MHz(U18) -> clk_wiz_0 -> 50M/200M
//       OV5640 RGB565 640x480 -> 异步 FIFO -> UDP -> RGMII -> PC:1234
//       板卡 IP 192.168.1.10，PC 需先发一个字节 '1' 触发
//
//   子系统 B：PS7 + AXI GPIO + motor_axi_top（PS 控制）
//       FCLK_CLK0 = 100MHz，peripheral_aresetn 低有效复位
//       AXI GPIO 基地址 0x4120_0000（通道 1，32 位全输出）
//       位域： [6:0] duty_l  [14:8] duty_r  [16] dir_l  [17] dir_r
//
//   两个子系统时钟独立、无数据通路耦合，只是合到同一个 bitstream 里。
//   若要"PC 识别结果回灌控车"，后续在 PL 侧解析 UDP 命令再接到电机即可。
//=====================================================================

module top (
    // ---------------- 时钟与复位（PL 50MHz 晶振）----------------
    input  wire         sys_clk        ,   // U18  50MHz
    input  wire         sys_rst_n      ,   // N16  按键复位，低有效

    // ---------------- 以太网 RGMII ----------------
    input  wire         eth_rxc        ,
    input  wire         eth_rx_ctl     ,
    input  wire [3:0]   eth_rxd        ,
    output wire         eth_txc        ,
    output wire         eth_tx_ctl     ,
    output wire [3:0]   eth_txd        ,
    output wire         eth_rst_n      ,

    // ---------------- OV5640 DVP ----------------
    input  wire         cam_pclk       ,
    input  wire         cam_vsync      ,
    input  wire         cam_href       ,
    input  wire [7:0]   cam_data       ,
    output wire         cam_rst_n      ,
    output wire         cam_pwdn       ,
    output wire         cam_scl        ,
    inout  wire         cam_sda        ,

    // ---------------- 双路电机（L298N）----------------
    output wire         pwm_l          ,   // U7  ENA
    output wire         in1            ,   // T9  IN1
    output wire         in2            ,   // Y7  IN2
    output wire         pwm_r          ,   // V8  ENB
    output wire         in3            ,   // V6  IN3
    output wire         in4            ,   // Y9  IN4

    // ---------------- 蜂鸣器 ----------------
    //   M14（正点原子领航者 V2 底板 PL_BEEP，LVCMOS33）
    //   由 AXI GPIO 的 bit18 驱动，见下方 beep 赋值。
    //   板上有源蜂鸣器，给高电平即响；若实测是无声型（无源），
    //   把 assign 换成 2kHz 方波即可（见文件末尾注释）。
    output wire         beep           ,
    // 调试 LED（2026-11-02）：排查"转向不生效"卡在哪一层
    //   led[0] = effect_steer 非 0（真正生效的转向）
    //   led[1] = udp steer 非 0（PC 指令解析出来了）
    //   led[2] = 左轮 duty 已低于巡航前馈（PID 确实降了占空比）
    output wire [2:0]   dbg_led        ,

    // ---------------- 编码器（AB 相，左右后轮各一对）----------------
    //   Bank13 / J4 扩展口。5V 霍尔编码器输出需先电平转换到 3.3V。
    //   下面注释 2026-11-02 按底板原理图 V3.9 第5页 "BANK13 Header 20X2" 重核：
    //   B13_Lxx_P/N 的 xx 与 FPGA 的 IO_Lxx 编号一致，J4 针脚号见括号。
    //   ★ 旧注释把 P/N 抄反了（写成 W8=L15_P、W10=L15_N、Y11=L18_P…），
    //     实际 W8 在 IO_L15*N*、W10 在 IO_L16*P*，别照旧注释去对线。
    input  wire         enc_l_a        ,   // Y12  B13_L20_P  (J4-24)
    input  wire         enc_l_b        ,   // W10  B13_L16_P  (J4-19)
    input  wire         enc_r_a        ,   // Y11  B13_L18_N  (J4-25)
    input  wire         enc_r_b        ,   // T5   B13_L19_P  (J4-4)

    // ---------------- PS 专用引脚（DDR3 / MIO，不写 xdc）----------------
    inout  wire [14:0]  DDR_0_addr     ,
    inout  wire [2:0]   DDR_0_ba       ,
    inout  wire         DDR_0_cas_n    ,
    inout  wire         DDR_0_ck_n     ,
    inout  wire         DDR_0_ck_p     ,
    inout  wire         DDR_0_cke      ,
    inout  wire         DDR_0_cs_n     ,
    inout  wire [3:0]   DDR_0_dm       ,
    inout  wire [31:0]  DDR_0_dq       ,
    inout  wire [3:0]   DDR_0_dqs_n    ,
    inout  wire [3:0]   DDR_0_dqs_p    ,
    inout  wire         DDR_0_odt      ,
    inout  wire         DDR_0_ras_n    ,
    inout  wire         DDR_0_reset_n  ,
    inout  wire         DDR_0_we_n     ,
    inout  wire         FIXED_IO_0_ddr_vrn ,
    inout  wire         FIXED_IO_0_ddr_vrp ,
    inout  wire [53:0]  FIXED_IO_0_mio     ,
    inout  wire         FIXED_IO_0_ps_clk  ,
    inout  wire         FIXED_IO_0_ps_porb ,
    inout  wire         FIXED_IO_0_ps_srstb,

    // ---------------- AXI UART Lite（雷达串口，PYNQ 读）----------------
    output wire         tx_0           ,   // U9  板子 TX -> 雷达 RX
    input  wire         rx_0               // U8  板子 RX <- 雷达 TX
);

    // BD 导出的内部信号
    wire        fclk_clk0      ;   // 100MHz，给电机逻辑
    wire [0:0]  peripheral_aresetn;   // 同步后的低有效复位
    wire [31:0] gpio_out       ;   // PS 经 AXI GPIO 写入的控制字（本版电机不再用它）

    // ---- PL 决策链路信号（eth_rx_clk 域 -> fclk_clk0 域）----
    wire        eth_rx_clk     ;   // RGMII 接收时钟 125MHz
    wire        udp_rec_pkt_done;
    wire        udp_rec_en     ;
    wire [7:0]  udp_rec_data   ;
    wire [15:0] udp_rec_byte_num;

    wire [6:0]  dec_duty_l     ;   // motor_decision 输出（eth_rx_clk 域）
    wire [6:0]  dec_duty_r     ;
    wire        dec_dir_l      ;
    wire        dec_dir_r      ;
    wire        dec_beep       ;
    // 调试信号（eth_rx_clk 域，慢变量，直接接 LED 不需要同步）
    wire        dbg_udp_steer  ;
    wire        dbg_eff_steer  ;
    wire        dbg_duty_l_low ;

    // 跨时钟域同步后（fclk_clk0 域）
    reg  [6:0]  m_duty_l, m_duty_l_s;
    reg  [6:0]  m_duty_r, m_duty_r_s;
    reg         m_dir_l,  m_dir_l_s;
    reg         m_dir_r,  m_dir_r_s;
    reg         m_beep,   m_beep_s;

    //=================================================================
    // 1. PS + AXI GPIO（block design）
    //=================================================================
    design_1_wrapper u_ps (
        .DDR_0_addr        (DDR_0_addr        ),
        .DDR_0_ba          (DDR_0_ba          ),
        .DDR_0_cas_n       (DDR_0_cas_n       ),
        .DDR_0_ck_n        (DDR_0_ck_n        ),
        .DDR_0_ck_p        (DDR_0_ck_p        ),
        .DDR_0_cke         (DDR_0_cke         ),
        .DDR_0_cs_n        (DDR_0_cs_n        ),
        .DDR_0_dm          (DDR_0_dm          ),
        .DDR_0_dq          (DDR_0_dq          ),
        .DDR_0_dqs_n       (DDR_0_dqs_n       ),
        .DDR_0_dqs_p       (DDR_0_dqs_p       ),
        .DDR_0_odt         (DDR_0_odt         ),
        .DDR_0_ras_n       (DDR_0_ras_n       ),
        .DDR_0_reset_n     (DDR_0_reset_n     ),
        .DDR_0_we_n        (DDR_0_we_n        ),
        .FIXED_IO_0_ddr_vrn(FIXED_IO_0_ddr_vrn),
        .FIXED_IO_0_ddr_vrp(FIXED_IO_0_ddr_vrp),
        .FIXED_IO_0_mio    (FIXED_IO_0_mio    ),
        .FIXED_IO_0_ps_clk (FIXED_IO_0_ps_clk ),
        .FIXED_IO_0_ps_porb(FIXED_IO_0_ps_porb),
        .FIXED_IO_0_ps_srstb(FIXED_IO_0_ps_srstb),
        .FCLK_CLK0_0       (fclk_clk0        ),
        .peripheral_aresetn_0(peripheral_aresetn),
        .gpio_io_o_0       (gpio_out         ),
        .tx_0              (tx_0             ),
        .rx_0              (rx_0             )
    );

    //=================================================================
    // 2. 电机 + 蜂鸣器 —— PL 决策驱动（motor_decision）
    //    数据流：PC 检测到人算距离 -> UDP -> PL 收帧 -> motor_decision
    //    按 1m/3m 阈值决策 -> 跨时钟域同步 -> motor_diff_ctrl 出 PWM
    //    原 motor_axi_top（PS 经 AXI GPIO 控制）已被替代，PS 侧不再
    //    参与电机控制，只保留提供 FCLK_CLK0 与复位。
    //=================================================================

    // 2a. 决策模块（eth_rx_clk 域，UDP 收帧就在这个域）
    //     雷达第二路：gpio_out 来自 PS（fclk_clk0 100M 域），跨时钟域同步到
    //     eth_rx_clk(125M) 域再喂给 motor_decision。
    //     位域： [15:0]=雷达障碍距离mm  [23:16]=心跳  [25:24]=转向方向
    reg [15:0] radar_dist_s1, radar_dist_s2;
    reg [7:0]  radar_seq_s1,  radar_seq_s2;
    reg [1:0]  radar_steer_s1, radar_steer_s2;
    always @(posedge eth_rx_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            radar_dist_s1  <= 16'hFFFF; radar_dist_s2  <= 16'hFFFF;
            radar_seq_s1   <= 8'd0;     radar_seq_s2   <= 8'd0;
            radar_steer_s1 <= 2'd0;     radar_steer_s2 <= 2'd0;
        end else begin
            radar_dist_s1  <= gpio_out[15:0];
            radar_dist_s2  <= radar_dist_s1;
            radar_seq_s1   <= gpio_out[23:16];
            radar_seq_s2   <= radar_seq_s1;
            radar_steer_s1 <= gpio_out[25:24];
            radar_steer_s2 <= radar_steer_s1;
        end
    end

    motor_decision u_decision (
        .clk              (eth_rx_clk      ),
        .rst_n            (sys_rst_n       ),
        .udp_rec_pkt_done (udp_rec_pkt_done),
        .udp_rec_en       (udp_rec_en      ),
        .udp_rec_data     (udp_rec_data    ),
        .udp_rec_byte_num (udp_rec_byte_num),
        .radar_dist       (radar_dist_s2   ),
        .radar_seq        (radar_seq_s2    ),
        .radar_steer      (radar_steer_s2  ),
        .enc_l_a          (enc_l_a         ),
        .enc_l_b          (enc_l_b         ),
        .enc_r_a          (enc_r_a         ),
        .enc_r_b          (enc_r_b         ),
        .duty_l           (dec_duty_l      ),
        .duty_r           (dec_duty_r      ),
        .dir_l            (dec_dir_l       ),
        .dir_r            (dec_dir_r       ),
        .beep             (dec_beep        ),
        .dbg_udp_steer    (dbg_udp_steer   ),
        .dbg_eff_steer    (dbg_eff_steer   ),
        .dbg_duty_l_low   (dbg_duty_l_low  )
    );

    // 2b. 跨时钟域同步：eth_rx_clk(125M) -> sys_clk(50M PL 晶振)，两级寄存器
    //     必须用 PL 自己的时钟：PS 只在被 FSBL/JTAG 初始化后才有 FCLK_CLK0，
    //     只烧 PL 位流时 fclk_clk0 不跑，会导致电机/蜂鸣器整条链路全死
    //     （PL 的以太网照常工作，很容易误判成"决策没生效"）。
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            m_duty_l  <= 7'd0; m_duty_l_s  <= 7'd0;
            m_duty_r  <= 7'd0; m_duty_r_s  <= 7'd0;
            m_dir_l   <= 1'b0; m_dir_l_s   <= 1'b0;
            m_dir_r   <= 1'b0; m_dir_r_s   <= 1'b0;
            m_beep    <= 1'b0; m_beep_s    <= 1'b0;
        end else begin
            m_duty_l_s <= dec_duty_l;  m_duty_l <= m_duty_l_s;
            m_duty_r_s <= dec_duty_r;  m_duty_r <= m_duty_r_s;
            m_dir_l_s  <= dec_dir_l;   m_dir_l  <= m_dir_l_s;
            m_dir_r_s  <= dec_dir_r;   m_dir_r  <= m_dir_r_s;
            m_beep_s   <= dec_beep;    m_beep   <= m_beep_s;
        end
    end

    // 2c. 电机 PWM（sys_clk 50MHz 域，不依赖 PS）
    motor_diff_ctrl #(
        .SYS_FREQ(50_000_000)
    ) u_motor (
        .clk      (sys_clk          ),
        .rst_n    (sys_rst_n        ),
        .duty_l   (m_duty_l         ),
        .duty_r   (m_duty_r         ),
        .dir_l    (m_dir_l          ),
        .dir_r    (m_dir_r          ),
        .pwm_l    (pwm_l            ),
        .in1      (in1              ),
        .in2      (in2              ),
        .pwm_r    (pwm_r            ),
        .in3      (in3              ),
        .in4      (in4              )
    );

    // 2d. 蜂鸣器（有源，电平直出，M14）
    assign beep = m_beep;

    // ---- 调试 LED（三个都是 eth_rx_clk 域的慢变量，直接接，不做 CDC）----
    //   dbg_led[0] = eff_steer != 0   ：转向真正生效
    //   dbg_led[1] = UDP steer != 0   ：PC 指令解析出来了
    //   dbg_led[2] = duty_l < 前馈    ：占空比确实被降下来了
    assign dbg_led = {dbg_duty_l_low, dbg_udp_steer, dbg_eff_steer};

    //=================================================================
    // 3. 图像采集 + UDP 发送（原 5640 工程，原样保留）
    //=================================================================
    ov5640_udp_pc u_img (
        .sys_clk    (sys_clk    ),
        .sys_rst_n  (sys_rst_n  ),
        .eth_rxc    (eth_rxc    ),
        .eth_rx_ctl (eth_rx_ctl ),
        .eth_rxd    (eth_rxd    ),
        .eth_txc    (eth_txc    ),
        .eth_tx_ctl (eth_tx_ctl ),
        .eth_txd    (eth_txd    ),
        .eth_rst_n  (eth_rst_n  ),
        .cam_pclk   (cam_pclk   ),
        .cam_vsync  (cam_vsync  ),
        .cam_href   (cam_href   ),
        .cam_data   (cam_data   ),
        .cam_rst_n  (cam_rst_n  ),
        .cam_pwdn   (cam_pwdn   ),
        .cam_scl    (cam_scl    ),
        .cam_sda    (cam_sda    ),
        .eth_rx_clk       (eth_rx_clk       ),
        .udp_rec_pkt_done (udp_rec_pkt_done ),
        .udp_rec_en       (udp_rec_en       ),
        .udp_rec_data     (udp_rec_data     ),
        .udp_rec_byte_num (udp_rec_byte_num )
    );

endmodule
