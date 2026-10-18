ROOT := $(abspath ../..)
BUILD := $(CURDIR)/build
CC := riscv64-unknown-elf-gcc
AR := riscv64-unknown-elf-ar
CFLAGS := -O2 -g -march=rv64gc -mabi=lp64d -mcmodel=medany -fno-common -fno-builtin-memcpy -fno-builtin-printf -Wall -Wextra -I$(CURDIR) -specs=htif_nano.specs
LDFLAGS := -static -Wl,--wrap=atexit
RUNTIME := $(ROOT)/tests/atexit-workaround.c
.PHONY: baseline command-smoke
baseline: $(BUILD)/hello.riscv
	$(MAKE) -C $(ROOT)/sims/verilator CONFIG=RocketConfig run-binary-fast BINARY=$< LOADMEM=1 EXTRA_SIM_FLAGS='+max-cycles=2000000 +cycle-count'
$(BUILD):
	mkdir -p $@
$(BUILD)/hello.riscv: $(ROOT)/tests/hello.c $(RUNTIME) | $(BUILD)
	$(CC) $(CFLAGS) $^ $(LDFLAGS) -o $@
$(BUILD)/command-smoke.riscv: tests/command-smoke.c tests/commands.h $(RUNTIME) | $(BUILD)
	$(CC) $(CFLAGS) tests/command-smoke.c $(RUNTIME) $(LDFLAGS) -o $@
command-smoke: $(BUILD)/command-smoke.riscv
	$(MAKE) -C $(ROOT)/sims/verilator CONFIG=DMARocketConfig run-binary-fast BINARY=$< LOADMEM=1 EXTRA_SIM_FLAGS='+max-cycles=2000000 +cycle-count'
$(BUILD)/correctness.riscv: tests/correctness.c tests/commands.h $(RUNTIME) | $(BUILD)
	$(CC) $(CFLAGS) tests/correctness.c $(RUNTIME) $(LDFLAGS) -o $@
.PHONY: correctness
correctness: $(BUILD)/correctness.riscv
	$(MAKE) -C $(ROOT)/sims/verilator CONFIG=DMARocketConfig run-binary-fast BINARY=$< LOADMEM=1 EXTRA_SIM_FLAGS='+max-cycles=200000000 +cycle-count'
