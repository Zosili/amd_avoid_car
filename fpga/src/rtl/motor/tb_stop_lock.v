//============================================================
// tb_stop_lock.v  --  验证 7020 版 motor_axi_top 的硬钳位
//
//   检查点：
//     1) stop_lock=0 时，duty>0 的轮子 PWM 有高电平
//     2) stop_lock 拉高后，两路 PWM 必须是常量 0（组合逻辑，立即生效）
//     3) stop_lock=1 期间即使 PS 仍写 duty=100，输出也必须是 0
//     4) stop_lock 拉低后 PWM 恢复，证明不是锁死
//
//   跑法（Vivado 自带 xsim，不需要 license）：
//     xvlog motor_diff_ctrl.v motor_axi_top.v tb_stop_lock.v
//     xelab tb_stop_lock -s tb_sim
//     xsim tb_sim -runall
//============================================================
`timescale 1ns / 1ps

module tb_stop_lock;

    reg         clk = 0;
    reg         rst_n = 0;
    reg  [31:0] gpio = 0;

    wire pwm_l, in1, in2, pwm_r, in3, in4;

    // 位域： [6:0] duty_l  [14:8] duty_r  [16] dir_l  [17] dir_r  [18] beep  [19] stop_lock
    localparam [31:0] DUTY50_STOP0 = (32'd50 << 0) | (32'd50 << 8);
    localparam [31:0] DUTY100     = (32'd100 << 0) | (32'd100 << 8);
    localparam [31:0] STOP_BIT    = (32'd1 << 19);

    motor_axi_top u_dut (
        .clk      (clk),
        .rst_n    (rst_n),
        .gpio_out (gpio),
        .pwm_l    (pwm_l),
        .in1      (in1),
        .in2      (in2),
        .pwm_r    (pwm_r),
        .in3      (in3),
        .in4      (in4)
    );

    always #5 clk = ~clk;          // 100 MHz

    integer errors = 0;

    // 在 window_ns 内统计 pwm 的高电平周期数
    task measure(input integer window_ns, output integer hi_l, output integer hi_r);
        integer i;
        begin
            hi_l = 0;
            hi_r = 0;
            for (i = 0; i < window_ns / 10; i = i + 1) begin
                @(posedge clk);
                if (pwm_l === 1'b1) hi_l = hi_l + 1;
                if (pwm_r === 1'b1) hi_r = hi_r + 1;
            end
        end
    endtask

    task check_hi(input integer hi_l, input integer hi_r, input integer expect_hi);
        begin
            if (expect_hi) begin
                if (hi_l == 0 || hi_r == 0) begin
                    $display("  [FAIL] expected PWM activity, got hi_l=%0d hi_r=%0d",
                             hi_l, hi_r);
                    errors = errors + 1;
                end else begin
                    $display("  [ ok ] PWM active  hi_l=%0d hi_r=%0d", hi_l, hi_r);
                end
            end else begin
                if (hi_l != 0 || hi_r != 0) begin
                    $display("  [FAIL] expected PWM silent, got hi_l=%0d hi_r=%0d",
                             hi_l, hi_r);
                    errors = errors + 1;
                end else begin
                    $display("  [ ok ] PWM silent  hi_l=%0d hi_r=%0d", hi_l, hi_r);
                end
            end
        end
    endtask

    integer hl, hr;

    initial begin
        $display("=== tb_stop_lock ===");

        // 复位
        rst_n = 0;
        gpio  = 0;
        repeat (20) @(posedge clk);
        rst_n = 1;
        repeat (10) @(posedge clk);

        // --- 1. duty=50, stop_lock=0 -> 应该有 PWM ---
        gpio = DUTY50_STOP0;
        repeat (5) @(posedge clk);
        measure(200_000, hl, hr);          // 200us = 4 个 20kHz 周期
        check_hi(hl, hr, 1);

        // --- 2. 拉高 stop_lock，立即检查 ---
        gpio = DUTY50_STOP0 | STOP_BIT;
        @(posedge clk);                    // 只等一个时钟沿
        if (pwm_l !== 1'b0 || pwm_r !== 1'b0) begin
            $display("  [FAIL] stop_lock not immediate: pwm_l=%b pwm_r=%b", pwm_l, pwm_r);
            errors = errors + 1;
        end else begin
            $display("  [ ok ] stop_lock takes effect within 1 clock");
        end
        measure(200_000, hl, hr);
        check_hi(hl, hr, 0);

        // --- 3. stop_lock=1 期间 PS 继续写 duty=100，仍须为 0 ---
        gpio = DUTY100 | STOP_BIT;
        repeat (5) @(posedge clk);
        measure(200_000, hl, hr);
        check_hi(hl, hr, 0);

        // --- 4. 释放 stop_lock，PWM 必须恢复 ---
        gpio = DUTY100;
        repeat (5) @(posedge clk);
        measure(200_000, hl, hr);
        check_hi(hl, hr, 1);

        // --- 5. duty=0 时本来就该静音（区分"钳位"与"没占空比"）---
        gpio = 32'd0;
        repeat (5) @(posedge clk);
        measure(200_000, hl, hr);
        check_hi(hl, hr, 0);

        if (errors == 0)
            $display("\n=== 结果: ALL PASS ===");
        else
            $display("\n=== 结果: %0d 项失败 ===", errors);
        $finish;
    end

endmodule
