/* Chipyard HTIF runtime; no Embench source or startup changes. */
#include <stdio.h>
#include "rocc_dma.h"

static uint64_t region_begin, region_end;
static struct dma_statistics region_before, region_after;
static int benchmark_result, verification_result;
static void fence(void) { __asm__ volatile ("fence rw,rw" ::: "memory"); }
void initialise_board(void) { dma_stats_reset(); }
void __attribute__((noinline)) start_trigger(void)
{
    dma_stats_snapshot(&region_before);
    /* Cumulative counters can be differenced; extrema need a fresh interval. */
    dma_stats.min_cycles = dma_stats.max_cycles = 0;
    fence();
    region_begin = rocc_dma_cycles();
}
void __attribute__((noinline)) stop_trigger(void)
{
    fence();
    region_end = rocc_dma_cycles();
    dma_stats_snapshot(&region_after);
    if (region_before.min_cycles && (!dma_stats.min_cycles ||
        region_before.min_cycles < dma_stats.min_cycles))
        dma_stats.min_cycles = region_before.min_cycles;
    if (region_before.max_cycles > dma_stats.max_cycles)
        dma_stats.max_cycles = region_before.max_cycles;
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
    struct dma_statistics total, region;
    fence();
    uint64_t begin = rocc_dma_cycles();
    int result = __real_main(argc, argv);
    fence();
    uint64_t end = rocc_dma_cycles();
    dma_stats_snapshot(&total);
#define DELTA(f) region.f = region_after.f-region_before.f
    DELTA(intercepted); DELTA(offloaded); DELTA(fallback); DELTA(bytes);
    DELTA(cycles); DELTA(setup_cycles); DELTA(errors); DELTA(attempts);
    DELTA(retry_fallback); DELTA(e2e_cycles);
#undef DELTA
    region.min_cycles = region_after.min_cycles;
    region.max_cycles = region_after.max_cycles;
    for (unsigned i=0; i<8; i++)
        region.histogram[i] = region_after.histogram[i]-region_before.histogram[i];
    /* Reporting and its libc calls are outside all recorded intervals/counts. */
    printf("EMBENCH metrics_version=1 result=%d benchmark_result=%d verification_result=%d "
           "main_cycles=%lu benchmark_cycles=%lu", result, benchmark_result,
           verification_result, end-begin, region_end-region_begin);
    dma_stats_print(&total, "");
    dma_stats_print(&region, "region_");
    putchar('\n');
    fflush(stdout);
    return result;
}
