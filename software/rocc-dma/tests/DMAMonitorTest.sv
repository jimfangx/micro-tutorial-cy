// Exercise the frozen observer with controlled stalls and fault/status events.
module DMAMonitorTest;
  bit clock=0;
  always #5 clock=~clock;
  bit reset=1, busy=0, cmd_valid=0, cmd_ready=1, xd=1, xs1=1, xs2=1;
  logic [6:0] funct=0;
  logic [63:0] rs1=64'h80000000, rs2=64'h80001000, status=0;
  bit resp_valid=0, resp_ready=1, a_valid=0, a_ready=0, d_valid=0, d_ready=1;
  logic [2:0] a_opcode=4, d_opcode=1;
  logic [3:0] a_size=3, d_size=3;
  bit a_source=0,d_source=0,d_denied=0,d_corrupt=0;
  DMAPassiveMonitor dut(.*);
  task automatic tick; @(negedge clock); endtask
  initial begin
    tick(); reset=0;
    cmd_valid=1; tick(); cmd_valid=0; busy=1;
    resp_valid=1; tick(); resp_valid=0; busy=0;
    funct=1; xs2=0; rs1=8; cmd_valid=1; cmd_ready=0; repeat(2) tick();
    cmd_ready=1; tick(); cmd_valid=0; busy=1;
    a_valid=1; repeat(3) tick(); a_ready=1; tick(); a_valid=0;
    repeat(5) tick(); d_valid=1;
    if ($test$plusargs("denied")) d_denied=1;
    if ($test$plusargs("corrupt")) d_corrupt=1;
    d_ready=0; repeat(2) tick(); d_ready=1;
    tick(); d_valid=0;
    if (d_denied || d_corrupt) status=3;
    else begin
      a_opcode=0; a_valid=1; a_ready=0; repeat(2) tick(); a_ready=1; tick(); a_valid=0;
      if ($test$plusargs("bad-completion")) begin resp_valid=1; tick(); end
      repeat(4) tick(); d_opcode=0; d_valid=1; tick(); d_valid=0;
    end
    resp_valid=1; resp_ready=0; repeat(2) tick(); resp_ready=1; tick(); resp_valid=0; busy=0;
    tick();
    assert(dut.copies==1 && dut.reads==1 && dut.read_responses==1 && dut.outstanding==0);
    assert(dut.a_stall==(status==0?5:3) && dut.resp_stall==2);
    assert(dut.d_stall==2 && dut.cmd_stall==2);
    if (status==0) begin assert(dut.writes==1 && dut.write_acks==1 && dut.completed==1); end
    else begin assert(dut.failed==1 && dut.writes==0); end
    $display("DMA_MONITOR_TEST PASS");
    $finish;
  end
endmodule
