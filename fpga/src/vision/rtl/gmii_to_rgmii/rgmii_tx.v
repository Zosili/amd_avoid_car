//****************************************Copyright (c)***********************************//
//原子哥在线教学平台：www.yuanzige.com
//技术支持：http://www.openedv.com/forum.php
//淘宝店铺：https://zhengdianyuanzi.tmall.com
//关注微信公众平台微信号："正点原子"，免费获取ZYNQ & FPGA & STM32 & LINUX资料。
//版权所有，盗版必究。
//Copyright(C) 正点原子 2023-2033
//All rights reserved
//----------------------------------------------------------------------------------------
// File name:           rgmii_tx
// Created by:          正点原子
// Created date:        2025年10月13日09:40:02
// Version:             V1.0
// Descriptions:        RGMII发送模块
//
//----------------------------------------------------------------------------------------
//****************************************************************************************//

module rgmii_tx(
    //GMII发送端口
    input              gmii_tx_clk , //GMII发送时钟    
    input              gmii_tx_en  , //GMII输出数据有效信号
    input       [7:0]  gmii_txd    , //GMII输出数据        
    
    //RGMII发送端口
    output             rgmii_txc   , //RGMII发送数据时钟    
    output             rgmii_tx_ctl, //RGMII输出数据有效信号
    output      [3:0]  rgmii_txd     //RGMII输出数据     
    );

//*****************************************************
//**                    main code
//*****************************************************

assign rgmii_txc = gmii_tx_clk;

//输出双沿采样寄存器 (rgmii_tx_ctl)
ODDR #(
    .DDR_CLK_EDGE  ("SAME_EDGE"),  // "OPPOSITE_EDGE" or "SAME_EDGE" 
    .INIT          (1'b0),         // Initial value of Q: 1'b0 or 1'b1
    .SRTYPE        ("SYNC")        // Set/Reset type: "SYNC" or "ASYNC" 
) ODDR_inst (
    .Q             (rgmii_tx_ctl), // 1-bit DDR output
    .C             (gmii_tx_clk),  // 1-bit clock input
    .CE            (1'b1),         // 1-bit clock enable input
    .D1            (gmii_tx_en),   // 1-bit data input (positive edge)
    .D2            (gmii_tx_en),   // 1-bit data input (negative edge)
    .R             (1'b0),         // 1-bit reset
    .S             (1'b0)          // 1-bit set
); 

genvar i;
generate for (i=0; i<4; i=i+1)
    begin : txdata_bus
        //输出双沿采样寄存器 (rgmii_txd)
        ODDR #(
            .DDR_CLK_EDGE  ("SAME_EDGE"),  // "OPPOSITE_EDGE" or "SAME_EDGE" 
            .INIT          (1'b0),         // Initial value of Q: 1'b0 or 1'b1
            .SRTYPE        ("SYNC")        // Set/Reset type: "SYNC" or "ASYNC" 
        ) ODDR_inst (
            .Q             (rgmii_txd[i]), // 1-bit DDR output
            .C             (gmii_tx_clk),  // 1-bit clock input
            .CE            (1'b1),         // 1-bit clock enable input
            .D1            (gmii_txd[i]),  // 1-bit data input (positive edge)
            .D2            (gmii_txd[4+i]),// 1-bit data input (negative edge)
            .R             (1'b0),         // 1-bit reset
            .S             (1'b0)          // 1-bit set
        );        
    end
endgenerate

endmodule