// template_module.sv — Template demonstrating project coding conventions
module template_module
#(
  parameter int DATA_WIDTH = 32
) (
  input  logic                  sys_clk,
  input  logic                  sys_rst_n,
  input  logic [DATA_WIDTH-1:0] i_data,
  input  logic                  i_valid,
  output logic [DATA_WIDTH-1:0] o_result,
  output logic                  o_ready
);
endmodule
