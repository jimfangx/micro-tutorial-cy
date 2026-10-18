ROOT := $(abspath ../..)
BUILD := $(CURDIR)/build
CC := riscv64-unknown-elf-gcc
AR := riscv64-unknown-elf-ar
CFLAGS := -O2 -g -march=rv64gc -mabi=lp64d -mcmodel=medany -fno-common -fno-builtin-memcpy -fno-builtin-printf -Wall -Wextra -I$(CURDIR) -specs=htif_nano.specs
LDFLAGS := -static -Wl,--wrap=atexit
RUNTIME := $(ROOT)/tests/atexit-workaround.c
.PHONY: baseline command-smoke
baseline: $(BUILD)/hello.riscv
	$(MAKE) -C $(ROOT)/sims/verilator CONFIG=RocketConfig run-binary-fast BINARY=$< LOADMEM=1 TIMEOUT_CYCLES=2000000 EXTRA_SIM_FLAGS='+cycle-count'
$(BUILD):
	mkdir -p $@
$(BUILD)/hello.riscv: $(ROOT)/tests/hello.c $(RUNTIME) | $(BUILD)
	$(CC) $(CFLAGS) $^ $(LDFLAGS) -o $@
$(BUILD)/command-smoke.riscv: tests/command-smoke.c tests/commands.h $(RUNTIME) | $(BUILD)
	$(CC) $(CFLAGS) tests/command-smoke.c $(RUNTIME) $(LDFLAGS) -o $@
command-smoke: $(BUILD)/command-smoke.riscv
	$(MAKE) -C $(ROOT)/sims/verilator CONFIG=DMARocketConfig run-binary-fast BINARY=$< LOADMEM=1 TIMEOUT_CYCLES=2000000 EXTRA_SIM_FLAGS='+cycle-count'
$(BUILD)/correctness.riscv: tests/correctness.c tests/commands.h $(RUNTIME) | $(BUILD)
	$(CC) $(CFLAGS) tests/correctness.c $(RUNTIME) $(LDFLAGS) -o $@
.PHONY: correctness
correctness: $(BUILD)/correctness.riscv
	$(MAKE) -C $(ROOT)/sims/verilator CONFIG=DMARocketConfig run-binary-fast BINARY=$< LOADMEM=1 TIMEOUT_CYCLES=200000000 EXTRA_SIM_FLAGS='+cycle-count'

$(BUILD)/dma $(BUILD)/cpu $(BUILD)/wrapper:
	mkdir -p $@
$(BUILD)/dma/%.o: %.c rocc_dma.h | $(BUILD)/dma
	$(CC) $(CFLAGS) -c $< -o $@
$(BUILD)/cpu/%.o: %.c rocc_dma.h | $(BUILD)/cpu
	$(CC) $(CFLAGS) -DDMA_DISABLED -c $< -o $@
$(BUILD)/wrapper/%.o: %.c rocc_dma.h | $(BUILD)/wrapper
	$(CC) $(CFLAGS) -DDMA_DISABLED -c $< -o $@
$(BUILD)/%/librocc_dma.a: $(BUILD)/%/rocc_dma.o $(BUILD)/%/memcpy_wrap.o
	$(AR) rcs $@ $^
$(BUILD)/wrapper-dma.riscv: tests/wrapper.c $(BUILD)/dma/librocc_dma.a $(RUNTIME)
	$(CC) $(CFLAGS) $^ $(LDFLAGS) -Wl,--wrap=memcpy -o $@
$(BUILD)/wrapper-cpu.riscv: tests/wrapper.c $(BUILD)/wrapper/librocc_dma.a $(RUNTIME)
	$(CC) $(CFLAGS) -DDMA_DISABLED $^ $(LDFLAGS) -Wl,--wrap=memcpy -o $@
$(BUILD)/microbench.riscv: tests/microbench.c $(BUILD)/dma/librocc_dma.a $(RUNTIME)
	$(CC) $(CFLAGS) $^ $(LDFLAGS) -o $@
.PHONY: libraries software-tests
libraries: $(BUILD)/dma/librocc_dma.a $(BUILD)/cpu/librocc_dma.a $(BUILD)/wrapper/librocc_dma.a
software-tests: $(BUILD)/wrapper-dma.riscv $(BUILD)/wrapper-cpu.riscv $(BUILD)/microbench.riscv
	$(foreach binary,$^,$(MAKE) -C $(ROOT)/sims/verilator CONFIG=DMARocketConfig run-binary-fast BINARY=$(binary) LOADMEM=1 TIMEOUT_CYCLES=20000000 EXTRA_SIM_FLAGS='+cycle-count' && ) true
$(BUILD)/basic.riscv: tests/basic.c tests/commands.h $(RUNTIME) | $(BUILD)
	$(CC) $(CFLAGS) tests/basic.c $(RUNTIME) $(LDFLAGS) -o $@
.PHONY: basic
basic: $(BUILD)/basic.riscv
	$(MAKE) -C $(ROOT)/sims/verilator CONFIG=DMARocketConfig run-binary-fast BINARY=$< LOADMEM=1 TIMEOUT_CYCLES=500000
