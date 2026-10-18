#!/usr/bin/env python3
"""Bind only to the public RoCC/TL boundary; never inspect the DMA controller."""
import re
import sys
from pathlib import Path

rtl, output = map(Path, sys.argv[1:])
header = rtl.read_text().split(');', 1)[0]
ports = set(re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', header))
connections = {
    'clock':'clock', 'reset':'reset', 'busy':'io_busy',
    'cmd_valid':'io_cmd_valid', 'cmd_ready':'io_cmd_ready',
    'funct':'io_cmd_bits_inst_funct', 'xd':'io_cmd_bits_inst_xd',
    'xs1':'io_cmd_bits_inst_xs1', 'xs2':'io_cmd_bits_inst_xs2',
    'rs1':'io_cmd_bits_rs1', 'rs2':'io_cmd_bits_rs2',
    'resp_valid':'io_resp_valid', 'resp_ready':'io_resp_ready', 'status':'io_resp_bits_data',
}
for channel in ('a','d'):
    for field in ('valid','ready'):
        connections[f'{channel}_{field}']=f'auto_tl_out_{channel}_{field}'
    for field in ('opcode','size','source'):
        connections[f'{channel}_{field}']=f'auto_tl_out_{channel}_bits_{field}'
for field in ('denied','corrupt'):
    connections[f'd_{field}']=f'auto_tl_out_d_bits_{field}'
# Firtool prunes constant A ports in the initial 8-byte/source-0 implementation.
# A future variable-sized or multi-source implementation exposes those ports
# automatically. Response size/source checks prevent silent bad measurements.
constants={'a_size':"4'd3", 'a_source':"0"}
for key, port in connections.items():
    if port not in ports:
        if key not in constants:
            raise SystemExit(f'Missing observation port: {port}')
        connections[key]=constants[key]
source_port='auto_tl_out_d_bits_source'
source_bits=re.search(r'(?:input|output)\s+\[(\d+):0\]\s+'+source_port,header)
width=int(source_bits[1])+1 if source_bits else 1
if width > 10:
    raise SystemExit('Passive monitor supports at most 1024 source IDs')
output.write_text('bind RoCCDMA DMAPassiveMonitor #(.SOURCE_BITS('+str(width)+')) dma_observer (\n'+
                  ',\n'.join(f'  .{key}({port})' for key,port in connections.items())+'\n);\n')
