`timescale 1ns / 1ps

//============================================================
// 测试台 : tb_motor_seq
// 技巧   : 把 SYS_FREQ 调小到 50_000，"1 秒"在仿真里就是
//          1ms，8 秒整套动作 8ms 就能看完，不用苦等
//============================================================

module tb_motor_seq;

    reg  clk;
    reg  rst_n;
    wire pwm_l, in1, in2;
    wire pwm_r, in3, in4;

    motor_seq #(
        .SYS_FREQ(50_000)      // 仿真加速：1 秒 = 1ms 实际时间
    ) u_motor_seq (
        .clk   (clk),
        .rst_n (rst_n),
        .pwm_l (pwm_l),
        .in1   (in1),
        .in2   (in2),
        .pwm_r (pwm_r),
        .in3   (in3),
        .in4   (in4)
    );

    initial clk = 1'b0;
    always #10 clk = ~clk;     // 波形里的"时钟"仍是 20ns 周期

    initial begin
        rst_n = 1'b0;
        #100;
        rst_n = 1'b1;
        #9_000_000;            // 跑 9ms：覆盖前进3+右转2+后退1+停车2 = 8ms 全序列
        $display("Seq done. state=%0d dutyL=%0d dutyR=%0d dirL=%b dirR=%b",
                 u_motor_seq.state, u_motor_seq.duty_l_cnt,
                 u_motor_seq.duty_r_cnt, u_motor_seq.dir_l, u_motor_seq.dir_r);
        $stop;
    end

endmodule
