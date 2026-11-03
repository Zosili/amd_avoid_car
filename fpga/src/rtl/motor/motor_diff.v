module motor_diff #(
    parameter SYS_FREQ = 50_000_000, 
    parameter PWM_FREQ = 20_000,    
    parameter DUTY_L   = 70,       
    parameter DUTY_R   = 70,   
    parameter DIR_L    = 0,     
    parameter DIR_R    = 0   
)(
    input  wire clk,   
    input  wire rst_n,  
    output reg  pwm_l,   
    output wire in1,      
    output wire in2,      
    output reg  pwm_r,    
    output wire in3,     
    output wire in4      
);

 
    localparam CNT_MAX    = SYS_FREQ / PWM_FREQ - 1;      
    localparam DUTY_L_CNT = CNT_MAX * DUTY_L / 100;        
    localparam DUTY_R_CNT = CNT_MAX * DUTY_R / 100;       
    localparam CNT_W      = $clog2(CNT_MAX + 1);         


    reg [CNT_W-1:0] cnt;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cnt <= {CNT_W{1'b0}};
        else if (cnt == CNT_MAX)
            cnt <= {CNT_W{1'b0}};
        else
            cnt <= cnt + 1'b1;
    end

  
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            pwm_l <= 1'b0;
        else
            pwm_l <= (cnt < DUTY_L_CNT);
    end

  
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            pwm_r <= 1'b0;
        else
            pwm_r <= (cnt < DUTY_R_CNT);
    end

  
    assign in1 = DIR_L ? 1'b0 : 1'b1;
    assign in2 = DIR_L ? 1'b1 : 1'b0;
    assign in3 = DIR_R ? 1'b0 : 1'b1;
    assign in4 = DIR_R ? 1'b1 : 1'b0;

endmodule