// Fixed, simulation-only observer. Inputs only: no ready/valid or datapath drivers.
// The existing TileLink monitors retain full protocol/mask/stability assertions.
module DMAPassiveMonitor #(parameter SOURCE_BITS=1) (
  input clock, reset, busy, cmd_valid, cmd_ready,
  input [6:0] funct,
  input xd, xs1, xs2,
  input [63:0] rs1, rs2,
  input resp_valid, resp_ready,
  input [63:0] status,
  input a_valid, a_ready, d_valid, d_ready,
  input [2:0] a_opcode, d_opcode,
  input [3:0] a_size, d_size,
  input [SOURCE_BITS-1:0] a_source, d_source,
  input d_denied, d_corrupt
);
  localparam SOURCES=1<<SOURCE_BITS;
  longint unsigned cycle=0, configs=0, copies=0, completed=0, failed=0;
  longint unsigned busy_cycles=0, idle_cycles=0, busy_transitions=0;
  longint unsigned reads=0, writes=0, read_bytes=0, write_bytes=0;
  longint unsigned read_responses=0, write_acks=0, outstanding=0, peak_outstanding=0;
  longint unsigned outstanding_cycles=0, a_stall=0, d_stall=0, cmd_stall=0, resp_stall=0;
  longint unsigned read_latency=0, read_min=0, read_max=0;
  longint unsigned write_latency=0, write_min=0, write_max=0;
  longint unsigned denied=0, corrupt=0, protocol_errors=0;
  longint unsigned config_cycle=0, copy_cycle=0, valid_cycle=0;
  longint unsigned source_address=0, destination_address=0, length=0;
  longint unsigned before_reads=0, before_writes=0, before_read_bytes=0, before_write_bytes=0;
  longint unsigned accepted_at[SOURCES];
  bit active[SOURCES], is_write[SOURCES];
  logic [3:0] sizes[SOURCES];
  bit pending=0, copy_pending=0, response_seen=0, was_busy=0;
  bit trace_events, command_was_pending;
  longint unsigned latency;
  initial trace_events=$test$plusargs("dma-trace");

  task automatic violation(input string message);
    protocol_errors++;
    $fdisplay(32'h80000002,"DMA_PROTOCOL_FAIL metrics_version=1 cycle=%0d reason=%s",cycle,message);
    $fatal(1,"DMA passive protocol check failed");
  endtask

  task automatic accept_command;
    if (pending) violation("overlapping_RoCC_commands");
    pending=xd;
    if (funct==0) begin
      configs++; config_cycle=cycle;
      if (xd && xs1 && xs2) begin source_address=rs1; destination_address=rs2; end
      if (trace_events) $fdisplay(32'h80000002,"DMA_CONFIG metrics_version=1 accept_cycle=%0d src=%0d dst=%0d",cycle,rs1,rs2);
    end
    if (funct==1 && xd) begin
      copies++; copy_pending=1; response_seen=0; copy_cycle=cycle; length=rs1;
      before_reads=reads; before_writes=writes;
      before_read_bytes=read_bytes; before_write_bytes=write_bytes;
    end
  endtask

  always @(posedge clock) begin
    if (reset) begin
      // Board reset defines a fresh observation epoch.
      cycle=0; configs=0; copies=0; completed=0; failed=0;
      busy_cycles=0; idle_cycles=0; busy_transitions=0; was_busy=0;
      reads=0; writes=0; read_bytes=0; write_bytes=0;
      read_responses=0; write_acks=0; outstanding=0; peak_outstanding=0;
      outstanding_cycles=0; a_stall=0; d_stall=0; cmd_stall=0; resp_stall=0;
      read_latency=0; read_min=0; read_max=0; write_latency=0; write_min=0; write_max=0;
      denied=0; corrupt=0; protocol_errors=0; config_cycle=0;
      pending=0; copy_pending=0; response_seen=0;
      foreach (active[i]) active[i]=0;
    end else begin
      command_was_pending=pending;
      cycle++;
      if (busy) busy_cycles++; else idle_cycles++;
      if (busy != was_busy) busy_transitions++;
      was_busy=busy;
      outstanding_cycles+=outstanding;
      if (a_valid && !a_ready) a_stall++;
      if (d_valid && !d_ready) d_stall++;
      if (cmd_valid && !cmd_ready) cmd_stall++;
      if (resp_valid && !resp_ready) resp_stall++;

      // With no older command, same-edge traffic and responses belong to this command.
      if (cmd_valid && cmd_ready && !command_was_pending) accept_command();

      // Count accepted A requests, keyed by TileLink source, before D handling
      // so a legal zero-latency response can be measured as well.
      if (a_valid && a_ready) begin
        if (!copy_pending) violation("memory_request_without_COPY");
        if (active[a_source]) violation("source_reused_before_response");
        if (a_opcode!=4 && a_opcode!=0 && a_opcode!=1) violation("unsupported_A_opcode");
        active[a_source]=1; accepted_at[a_source]=cycle;
        is_write[a_source]=(a_opcode!=4); sizes[a_source]=a_size;
        outstanding++;
        if (outstanding>peak_outstanding) peak_outstanding=outstanding;
        if (a_opcode==4) begin reads++; read_bytes+=(64'd1<<a_size); end
        else begin writes++; write_bytes+=(64'd1<<a_size); end
      end
      if (d_valid && d_ready) begin
        if (!active[d_source]) violation("response_without_request");
        if (sizes[d_source]!=d_size) violation("response_size_mismatch");
        if (d_opcode != (is_write[d_source] ? 0 : 1)) violation("response_opcode_mismatch");
        latency=cycle-accepted_at[d_source];
        if (is_write[d_source]) begin
          write_acks++; write_latency+=latency;
          if (write_acks==1 || latency<write_min) write_min=latency;
          if (latency>write_max) write_max=latency;
        end else begin
          read_responses++; read_latency+=latency;
          if (read_responses==1 || latency<read_min) read_min=latency;
          if (latency>read_max) read_max=latency;
        end
        if (d_denied) denied++;
        if (d_corrupt) corrupt++;
        active[d_source]=0; outstanding--;
      end
      if (resp_valid) begin
        if (!pending) violation("RoCC_response_without_command");
        if (copy_pending && !response_seen) begin
          if (outstanding) violation("COPY_completed_before_memory_ack");
          valid_cycle=cycle; response_seen=1;
        end
      end
      if (resp_valid && resp_ready) begin
        if (copy_pending) begin
          if (status==0) begin
            if (read_bytes-before_read_bytes!=length || write_bytes-before_write_bytes!=length)
              violation("successful_COPY_traffic_length_mismatch");
            completed++;
          end else failed++;
          $fdisplay(32'h80000002,"DMA_COPY metrics_version=1 seq=%0d bytes=%0d src=%0d dst=%0d config_cycle=%0d accept_cycle=%0d valid_cycle=%0d response_cycle=%0d hw_cycles=%0d status=%0d reads=%0d writes=%0d read_bytes=%0d write_bytes=%0d",
            copies,length,source_address,destination_address,config_cycle,copy_cycle,
            valid_cycle,cycle,cycle-copy_cycle,status,reads-before_reads,writes-before_writes,
            read_bytes-before_read_bytes,write_bytes-before_write_bytes);
          $fflush(32'h80000002);
        end
        pending=0; copy_pending=0;
      end
      // Retire the previous response before recording its successor's metadata.
      if (cmd_valid && cmd_ready && command_was_pending) accept_command();
    end
  end
  final begin
    $fdisplay(32'h80000002,"DMA_HW metrics_version=1 result=%0d cycles=%0d configs=%0d copies=%0d completed=%0d failed=%0d busy_cycles=%0d idle_cycles=%0d busy_transitions=%0d reads=%0d writes=%0d read_bytes=%0d write_bytes=%0d read_responses=%0d write_acks=%0d outstanding=%0d peak_outstanding=%0d outstanding_cycles=%0d a_stall=%0d d_stall=%0d cmd_stall=%0d resp_stall=%0d read_latency=%0d read_min=%0d read_max=%0d write_latency=%0d write_min=%0d write_max=%0d denied=%0d corrupt=%0d protocol_errors=%0d pending=%0d",
      (outstanding!=0 || pending || protocol_errors!=0),cycle,configs,copies,completed,failed,
      busy_cycles,idle_cycles,busy_transitions,reads,writes,read_bytes,write_bytes,
      read_responses,write_acks,outstanding,peak_outstanding,outstanding_cycles,a_stall,d_stall,
      cmd_stall,resp_stall,read_latency,read_min,read_max,write_latency,write_min,write_max,
      denied,corrupt,protocol_errors,pending);
  end
endmodule
