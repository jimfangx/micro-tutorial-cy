#include "rocc_dma.h"
#include <stdio.h>
struct dma_statistics dma_stats;
void dma_stats_reset(void) {
    dma_stats = (struct dma_statistics){0};
}
void dma_stats_snapshot(struct dma_statistics *out) {
    volatile const struct dma_statistics *s = &dma_stats;
#define COPY(f) out->f = s->f
    COPY(intercepted); COPY(offloaded); COPY(fallback); COPY(bytes);
    COPY(cycles); COPY(setup_cycles); COPY(errors); COPY(attempts);
    COPY(retry_fallback); COPY(e2e_cycles); COPY(min_cycles); COPY(max_cycles);
#undef COPY
    for (unsigned i=0; i<8; i++) out->histogram[i] = s->histogram[i];
}
void dma_stats_print(const struct dma_statistics *s, const char *p) {
#define PRINT(key, field) printf(" %s" key "=%lu", p, s->field)
    PRINT("intercepted",intercepted); PRINT("offloaded",offloaded);
    PRINT("fallback",fallback); PRINT("bytes",bytes); PRINT("dma_cycles",cycles);
    PRINT("setup_cycles",setup_cycles); PRINT("errors",errors);
    PRINT("attempts",attempts); PRINT("retry_fallback",retry_fallback);
    PRINT("e2e_cycles",e2e_cycles); PRINT("min_cycles",min_cycles); PRINT("max_cycles",max_cycles);
#undef PRINT
    for (unsigned i=0; i<8; i++) printf(" %shist%u=%lu",p,i,s->histogram[i]);
}
int dma_supported(void *dst, const void *src, size_t n) {
    uintptr_t d=(uintptr_t)dst, s=(uintptr_t)src;
    if (!n) return 1;
    if ((d | s | n) & 7) return 0;
    /* Subtraction bounds avoid pointer arithmetic and integer overflow. */
    if (s < DMA_RAM_BASE || s >= DMA_RAM_END || n > DMA_RAM_END-s ||
        d < DMA_RAM_BASE || d >= DMA_RAM_END || n > DMA_RAM_END-d) return 0;
    return d >= s ? n <= d-s : n <= s-d;
}
int rocc_dma_copy(void *dst, const void *src, size_t n) {
#ifndef DMA_DISABLED
    uint64_t entered=rocc_dma_cycles();
#endif
    if (!dma_supported(dst, src, n)) return DMA_UNSUPPORTED;
    if (!n) return DMA_OK;
#ifdef DMA_DISABLED
    return DMA_UNSUPPORTED;
#else
    unsigned long status;
    dma_stats.attempts++;
    uint64_t begin=rocc_dma_cycles();
    __asm__ volatile ("fence rw,rw" ::: "memory");
    __asm__ volatile (".insn r 0x0b, 7, 0, %0, %1, %2"
                      : "=r"(status) : "r"(src), "r"(dst) : "memory");
    uint64_t configured=rocc_dma_cycles();
    if (!status) __asm__ volatile (".insn r 0x0b, 6, 1, %0, %1, x0"
                      : "=r"(status) : "r"(n) : "memory");
    /* Fence also waits for the selected Rocket's RoCC busy/pipeline state. */
    __asm__ volatile ("fence rw,rw" ::: "memory");
    uint64_t end=rocc_dma_cycles();
    dma_stats.cycles += end-begin;
    dma_stats.setup_cycles += configured-begin;
    uint64_t elapsed=end-entered;
    dma_stats.e2e_cycles += elapsed;
    if (!dma_stats.min_cycles || elapsed < dma_stats.min_cycles) dma_stats.min_cycles=elapsed;
    if (elapsed > dma_stats.max_cycles) dma_stats.max_cycles=elapsed;
    if (status) dma_stats.errors++;
    else {
        dma_stats.offloaded++; dma_stats.bytes += n;
        unsigned bucket = n<=8 ? 0 : n<=64 ? 1 : n<=256 ? 2 : n<=1024 ? 3 :
                          n<=4096 ? 4 : n<=16384 ? 5 : n<=65536 ? 6 : 7;
        dma_stats.histogram[bucket]++;
    }
    return (int)status;
#endif
}
