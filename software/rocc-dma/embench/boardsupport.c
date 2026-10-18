/* Chipyard HTIF runtime; no Embench source or startup changes. */
#include <stdio.h>
#include "rocc_dma.h"

static uint64_t region_begin, region_end;
static struct dma_statistics region_before, region_after;
static int benchmark_result, verification_result;

/* Volatile loads keep snapshots out of the memcpy interception path. */
static void snapshot(struct dma_statistics *out)
{
    volatile const struct dma_statistics *s = &dma_stats;
    out->intercepted = s->intercepted;
    out->offloaded = s->offloaded;
    out->fallback = s->fallback;
    out->bytes = s->bytes;
    out->cycles = s->cycles;
    out->setup_cycles = s->setup_cycles;
    out->errors = s->errors;
}
static void fence(void) { __asm__ volatile ("fence rw,rw" ::: "memory"); }
void initialise_board(void) { dma_stats_reset(); }
void __attribute__((noinline)) start_trigger(void)
{
    snapshot(&region_before);
    fence();
    region_begin = rocc_dma_cycles();
}
void __attribute__((noinline)) stop_trigger(void)
{
    fence();
    region_end = rocc_dma_cycles();
    snapshot(&region_after);
}
int __real_verify_benchmark(int result);
int __wrap_verify_benchmark(int result)
{
    benchmark_result = result;
    verification_result = __real_verify_benchmark(result);
    return verification_result;
}
int __real_main(int argc, char **argv);
int __wrap_main(int argc, char **argv)
{
    struct dma_statistics total;
    fence();
    uint64_t begin = rocc_dma_cycles();
    int result = __real_main(argc, argv);
    fence();
    uint64_t end = rocc_dma_cycles();
    snapshot(&total);
    /* Reporting and its libc calls are outside all recorded intervals/counts. */
    printf("EMBENCH result=%d benchmark_result=%d verification_result=%d "
           "main_cycles=%lu benchmark_cycles=%lu intercepted=%lu offloaded=%lu "
           "fallback=%lu bytes=%lu dma_cycles=%lu setup_cycles=%lu errors=%lu "
           "region_intercepted=%lu region_offloaded=%lu region_fallback=%lu "
           "region_bytes=%lu region_dma_cycles=%lu region_setup_cycles=%lu region_errors=%lu\n",
           result, benchmark_result, verification_result, end-begin, region_end-region_begin,
           total.intercepted, total.offloaded, total.fallback, total.bytes,
           total.cycles, total.setup_cycles, total.errors,
           region_after.intercepted-region_before.intercepted,
           region_after.offloaded-region_before.offloaded,
           region_after.fallback-region_before.fallback,
           region_after.bytes-region_before.bytes,
           region_after.cycles-region_before.cycles,
           region_after.setup_cycles-region_before.setup_cycles,
           region_after.errors-region_before.errors);
    fflush(stdout);
    return result;
}
