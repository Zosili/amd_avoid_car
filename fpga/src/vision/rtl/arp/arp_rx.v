//****************************************Copyright (c)***********************************//
//原子哥在线教学平台：www.yuanzige.com
//技术支持：http://www.openedv.com/forum.php
//淘宝店铺：https://zhengdianyuanzi.tmall.com
//关注微信公众平台微信号："正点原子"，免费获取ZYNQ & FPGA & STM32 & LINUX资料。
//版权所有，盗版必究。
//Copyright(C) 正点原子 2023-2033
//All rights reserved
//----------------------------------------------------------------------------------------
// File name:           arp_rx
// Created by:          正点原子
// Created date:        2025年10月13日09:40:02
// Version:             V1.0
// Descriptions:        arp接收模块
//
//----------------------------------------------------------------------------------------
//****************************************************************************************//

module arp_rx(
    input                  clk          , //时钟信号
    input                  rst_n        , //复位信号，低电平有效
    input                  gmii_rx_dv   , //GMII输入数据有效信号
    input        [7:0]     gmii_rxd     , //GMII输入数据

    output  reg            arp_rx_done  , //ARP接收完成信号
    output  reg            arp_rx_type  , //ARP数据类型，0：ARP请求 1：ARP应答
    output  reg  [47:0]    src_mac      , //接收到的源MAC地址
    output  reg  [31:0]    src_ip         //接收到的源IP地址
    );

//parameter define
//开发板的MAC地址 00-11-22-33-44-55
parameter BOARD_MAC = 48'h00_11_22_33_44_55;
//开发板的IP地址 192.168.1.10
parameter BOARD_IP  = {8'd192,8'd168,8'd1,8'd10};

//localparam define
localparam st_idle     = 5'b0_0001;
localparam st_preamble = 5'b0_0010;
localparam st_eth_head = 5'b0_0100;
localparam st_arp_data = 5'b0_1000;
localparam st_rx_end   = 5'b1_0000;

//以太网ARP的协议类型
parameter ETH_TYPE_ARP = 16'h0806;

//reg define
reg  [4:0]   cur_state    ;
reg  [4:0]   next_state   ;

reg  [4:0]   rx_cnt       ; //解析数据计数器
reg  [47:0]  des_mac_temp ; //接收到的目的MAC地址
reg  [31:0]  des_ip_temp  ; //接收到的目的IP地址
reg  [47:0]  src_mac_temp ; //接收到的源MAC地址
reg  [31:0]  src_ip_temp  ; //接收到的源IP地址
reg  [15:0]  eth_type     ; //以太网类型
reg  [15:0]  op_code      ; //操作码

//wire define
wire         des_mac_match  ;
wire         eth_type_match ;

//*****************************************************
//**                    main code
//*****************************************************

//判断接收到的目的MAC地址是不是开发板的或者公共MAC地址
assign des_mac_match = (des_mac_temp == BOARD_MAC) || (des_mac_temp == 48'hff_ff_ff_ff_ff_ff);
//判断以太网的协议类型是不是ARP
assign eth_type_match = (eth_type[15:8] == ETH_TYPE_ARP[15:8]) && (gmii_rxd == ETH_TYPE_ARP[7:0]);

//第一段状态机:同步时序描述状态转移
always @(posedge clk or negedge rst_n) begin
    if(!rst_n) 
        cur_state <= st_idle;
    else
        cur_state <= next_state;
end

//第二段状态机:组合逻辑判断状态转移条件
always @(*) begin
    next_state = st_idle;
    case(cur_state)
        st_idle : begin             //检测到第一个8'h55
            if((gmii_rx_dv == 1'b1) && (gmii_rxd == 8'h55))
                next_state = st_preamble;
            else
                next_state = st_idle;
        end
        st_preamble : begin         //接收前导码
            if(gmii_rx_dv) begin
                if((rx_cnt <= 5'd6) && (gmii_rxd == 8'h55))
                    next_state = st_preamble;
                else if((rx_cnt == 5'd7) && (gmii_rxd == 8'hd5))
                    next_state = st_eth_head;
                else
                    next_state = st_rx_end;
            end
            else
                next_state = st_rx_end;
        end
        st_eth_head : begin         //接收以太网帧头
            if(gmii_rx_dv) begin
                if(rx_cnt == 5'd13) begin
                    //判断MAC地址是否为开发板MAC地址或者公共MAC地址
                    if((des_mac_match == 1'b1) && (eth_type_match == 1'b1))
                        next_state = st_arp_data;
                    else
                        next_state = st_rx_end;
                end
                else
                    next_state = st_eth_head;
            end
            else
                next_state = st_rx_end;
        end
        st_arp_data : begin         //接收ARP数据
            if(gmii_rx_dv) begin
                if(rx_cnt == 5'd28)
                    next_state = st_rx_end;
                else
                    next_state = st_arp_data;
            end
            else
                next_state = st_rx_end;
        end
        st_rx_end : begin           //接收结束
            if(gmii_rx_dv == 1'b0)
                next_state = st_idle;
            else
                next_state = st_rx_end;
        end
        default : next_state = st_idle;
    endcase
end

//第三段状态机:时序电路描述状态输出,解析以太网数据
always @(posedge clk or negedge rst_n) begin
    if(!rst_n) begin
        rx_cnt       <= 5'd0 ;
        des_mac_temp <= 48'd0;
        des_ip_temp  <= 32'd0;
        src_mac_temp <= 48'd0;
        src_ip_temp  <= 32'd0;
        eth_type     <= 16'd0;
        op_code      <= 16'd0;
        arp_rx_done  <= 1'd0 ;
        arp_rx_type  <= 1'd0 ;
        src_mac      <= 48'd0;
        src_ip       <= 32'd0;
    end
    else begin
        arp_rx_done <= 1'b0;
        case(cur_state)
            st_idle : begin
                if((gmii_rx_dv == 1'b1) && (gmii_rxd == 8'h55))
                    rx_cnt <= rx_cnt + 5'd1;
                else;
            end
            st_preamble : begin
                if(gmii_rx_dv) begin
                    if(rx_cnt == 5'd7)
                        rx_cnt <= 5'd0;
                    else    
                        rx_cnt <= rx_cnt + 5'd1;
                end
                else
                    rx_cnt <= 5'd0;
            end
            st_eth_head : begin
                if(gmii_rx_dv) begin
                    rx_cnt <= rx_cnt + 5'd1;
                    if((rx_cnt >= 5'd0) && (rx_cnt <= 5'd5))
                        des_mac_temp <= {des_mac_temp[39:0],gmii_rxd};  //目的MAC地址
                    else if(rx_cnt == 5'd12)
                        eth_type[15:8] <= gmii_rxd;                     //以太网协议类型
                    else if(rx_cnt == 5'd13) begin
                        eth_type[7:0] <= gmii_rxd;
                        rx_cnt        <= 5'd0;
                    end
                end
                else
                    rx_cnt <= 5'd0;
            end
            st_arp_data : begin
                if(gmii_rx_dv) begin
                    rx_cnt <= rx_cnt + 5'd1;
                    if(rx_cnt == 5'd6)
                        op_code[15:8] <= gmii_rxd;                      //操作码
                    else if(rx_cnt == 5'd7)
                        op_code[7:0] <= gmii_rxd;
                    else if((rx_cnt >= 5'd8) && (rx_cnt < 5'd14))
                        src_mac_temp <= {src_mac_temp[39:0],gmii_rxd};  //源MAC地址
                    else if((rx_cnt >= 5'd14) && (rx_cnt < 5'd18))
                        src_ip_temp <= {src_ip_temp[23:0],gmii_rxd};    //源IP地址
                    else if((rx_cnt >= 5'd24) && (rx_cnt < 5'd28))
                        des_ip_temp <= {des_ip_temp[23:0],gmii_rxd};    //目标IP地址
                    else if(rx_cnt == 5'd28) begin
                        rx_cnt <= 5'd0;
                        //判断目的IP地址是不是开发板的IP地址
                        if(des_ip_temp == BOARD_IP) begin
                            if((op_code == 16'd1) || (op_code == 16'd2)) begin
                                arp_rx_done <= 1'b1;
                                src_mac     <= src_mac_temp;
                                src_ip      <= src_ip_temp;
                                if(op_code == 16'd1)   //ARP请求
                                    arp_rx_type <= 1'b0;
                                else                   //ARP应答
                                    arp_rx_type <= 1'b1;
                            end
                            else;
                       end 
                       else;
                    end
                    else;
                end
                else
                    rx_cnt <= 5'd0;
            end
            st_rx_end : begin
                rx_cnt       <= 5'd0 ;
                des_mac_temp <= 48'd0;
                des_ip_temp  <= 32'd0;
                src_mac_temp <= 48'd0;
                src_ip_temp  <= 32'd0;
                eth_type     <= 16'd0;
                op_code      <= 16'd0;
            end
            default : ;
        endcase
    end
end

endmodule