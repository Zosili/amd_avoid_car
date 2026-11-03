`timescale 1ns / 1ps

//============================================================
// 测试台 : tb_motor_actions
// 目的   : 一个测试台覆盖三个单动作工程（左转/右转/倒退）
// 验证点 :
//   1. 方向电平：三个模块的 in1~in4 是否符合各自设定
//   2. 占空比：pwm_l/pwm_r 高电平脉宽应约为 0.6 * 50us = 30us
// 说明   : 这三个工程的 PWM 发生器和已验证过的 motor_diff 完全
//          同源，唯一的新变量是方向常量，所以重点是验证 1。
//============================================================

module tb_motor_actions;

    reg  clk;
    reg  rst_n;

    // --- 左转 ---
    wire pwm_l_lft, in1_lft, in2_lft, pwm_r_lft, in3_lft, in4_lft;
    // --- 右转 ---
    wire pwm_l_rgt, in1_rgt, in2_rgt, pwm_r_rgt, in3_rgt, in4_rgt;
    // --- 倒退 ---
    wire pwm_l_bck, in1_bck, in2_bck, pwm_r_bck, in3_bck, in4_bck;

    motor_left  u_left (.clk(clk), .rst_n(rst_n), .pwm_l(pwm_l_lft), .in1(in1_lft), .in2(in2_lft), .pwm_r(pwm_r_lft), .in3(in3_lft), .in4(in4_lft));
    motor_right u_right(.clk(clk), .rst_n(rst_n), .pwm_l(pwm_l_rgt), .in1(in1_rgt), .in2(in2_rgt), .pwm_r(pwm_r_rgt), .in3(in3_rgt), .in4(in4_rgt));
    motor_back  u_back (.clk(clk), .rst_n(rst_n), .pwm_l(pwm_l_bck), .in1(in1_bck), .in2(in2_bck), .pwm_r(pwm_r_bck), .in3(in3_bck), .in4(in4_bck));

    initial clk = 1'b0;
    always #10 clk = ~clk;   // 50MHz

    integer errors = 0;

    task check_dir;
        input        [39:0] name_;     // 5 字符
        input in1_, in2_, in3_, in4_;
        input exp_in1, exp_in2, exp_in3, exp_in4;
        begin
            if (in1_ !== exp_in1 || in2_ !== exp_in2 ||
                in3_ !== exp_in3 || in4_ !== exp_in4) begin
                errors = errors + 1;
                $display("[FAIL] %0s: in1=%b in2=%b in3=%b in4=%b (期望 %b%b%b%b)",
                         name_, in1_, in2_, in3_, in4_,
                         exp_in1, exp_in2, exp_in3, exp_in4);
            end else begin
                $display("[PASS] %0s: in1=%b in2=%b in3=%b in4=%b", name_, in1_, in2_, in3_, in4_);
            end
        end
    endtask

    initial begin
        rst_n = 1'b0;
        #100;
        rst_n = 1'b1;

        // 等 2 个 PWM 周期，让方向电平稳定（方向是纯组合，实际立刻就绪）
        #100_000;

        // 左转：左轮反转(01) 右轮正转(10)
        check_dir("left ", in1_lft, in2_lft, in3_lft, in4_lft, 1'b0, 1'b1, 1'b1, 1'b0);
        // 右转：左轮正转(10) 右轮反转(01)
        check_dir("right", in1_rgt, in2_rgt, in3_rgt, in4_rgt, 1'b1, 1'b0, 1'b0, 1'b1);
        // 倒退：双轮反转(01/01)
        check_dir("back ", in1_bck, in2_bck, in3_bck, in4_bck, 1'b0, 1'b1, 1'b0, 1'b1);

        $display("---- 方向电平检查完（共 %0d 处错误）----", errors);
        $display("占空比请在波形里看：pwm 高电平脉宽应为 30us（60%% x 50us 周期）");
        #100_000;
        if (errors == 0) $display("ALL TESTS PASSED");
        $stop;
    end

endmodule
