#ifndef ROCC_DMA_H
#define ROCC_DMA_H
#include <stddef.h>
#include <stdint.h>
#ifndef DMA_THRESHOLD
#define DMA_THRESHOLD 64
#endif
#ifndef DMA_RAM_BASE
#define DMA_RAM_BASE UINT64_C(0x80000000)
#endif
#ifndef DMA_RAM_END
#define DMA_RAM_END UINT64_C(0x90000000)
#endif
enum { DMA_OK=0, DMA_BAD_COMMAND=1, DMA_UNSUPPORTED=2, DMA_MEMORY_ERROR=3 };
struct dma_statistics {
    uint64_t intercepted, offloaded, fallback, bytes, cycles, setup_cycles, errors;
};
extern struct dma_statistics dma_stats;
int dma_supported(void *dst, const void *src, size_t length);
int rocc_dma_copy(void *dst, const void *src, size_t length);
void dma_stats_reset(void);
static inline uint64_t rocc_dma_cycles(void) {
    uint64_t n;
    __asm__ volatile ("rdcycle %0" : "=r"(n) :: "memory");
    return n;
}
#endif
