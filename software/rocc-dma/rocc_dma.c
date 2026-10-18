#include "rocc_dma.h"
struct dma_statistics dma_stats;
void dma_stats_reset(void) {
    dma_stats = (struct dma_statistics){0};
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
    if (!dma_supported(dst, src, n)) return DMA_UNSUPPORTED;
    if (!n) return DMA_OK;
#ifdef DMA_DISABLED
    return DMA_UNSUPPORTED;
#else
    unsigned long status;
    uint64_t begin=rocc_dma_cycles();
    __asm__ volatile ("fence rw,rw" ::: "memory");
    __asm__ volatile (".insn r 0x0b, 7, 0, %0, %1, %2"
                      : "=r"(status) : "r"(src), "r"(dst) : "memory");
    if (status) { dma_stats.errors++; return (int)status; }
    uint64_t configured=rocc_dma_cycles();
    __asm__ volatile (".insn r 0x0b, 6, 1, %0, %1, x0"
                      : "=r"(status) : "r"(n) : "memory");
    /* Fence also waits for the selected Rocket's RoCC busy/pipeline state. */
    __asm__ volatile ("fence rw,rw" ::: "memory");
    uint64_t end=rocc_dma_cycles();
    dma_stats.cycles += end-begin;
    dma_stats.setup_cycles += configured-begin;
    if (status) dma_stats.errors++;
    else { dma_stats.offloaded++; dma_stats.bytes += n; }
    return (int)status;
#endif
}
