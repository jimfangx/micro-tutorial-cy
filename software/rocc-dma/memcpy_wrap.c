#include "rocc_dma.h"
#include <stdio.h>
#include <stdlib.h>
void *__real_memcpy(void *, const void *, size_t);
void *__wrap_memcpy(void *dst, const void *src, size_t n) {
    dma_stats.intercepted++;
#ifndef DMA_DISABLED
    if (n >= DMA_THRESHOLD && dma_supported(dst,src,n)) {
        int result=rocc_dma_copy(dst,src,n);
        if (result == DMA_OK) return dst;
        /* Only validation/unsupported errors promise no destination writes. */
        if (result != DMA_UNSUPPORTED && result != DMA_BAD_COMMAND) {
            printf("fatal DMA error status=%d\n",result);
            exit(90);
        }
    }
#endif
    dma_stats.fallback++;
    return __real_memcpy(dst,src,n);
}
