#include <stdio.h>
#include <string.h>
#include "rocc_dma.h"
static unsigned char src[16384] __attribute__((aligned(4096)));
static unsigned char dst[16384] __attribute__((aligned(4096)));
int main(void) {
    for(unsigned i=0;i<sizeof(src);i++) { src[i]=(unsigned char)(i*23+(i>>8)); dst[i]=0xa5; }
    dma_stats_reset();
    if (rocc_dma_copy(dst,src,0) || rocc_dma_copy(dst+1,src,64)!=DMA_UNSUPPORTED ||
        rocc_dma_copy(src+8,src,64)!=DMA_UNSUPPORTED) return 1;
    const size_t sizes[]={0,1,7,8,24,63,64,72,512,4096,8192};
    for(unsigned k=0;k<sizeof(sizes)/sizeof(sizes[0]);k++) {
        size_t n=sizes[k];
        for(unsigned i=0;i<sizeof(dst);i++) dst[i]=0xa5;
        if(memcpy(dst,src,n)!=dst) return 2;
        for(unsigned i=0;i<sizeof(dst);i++) if(dst[i]!=(i<n?src[i]:0xa5)) return 3;
    }
    if(memcpy(dst+1,src+3,129)!=dst+1 || memcmp(dst+1,src+3,129)) return 4;
    /* memmove must remain a CPU operation even with wrapping enabled. */
    uint64_t before=dma_stats.offloaded;
    memmove(dst+1,dst,128);
    if(dma_stats.offloaded!=before) return 5;
    for(unsigned i=0;i<sizeof(src);i++) if(src[i]!=(unsigned char)(i*23+(i>>8))) return 6;
#ifdef DMA_DISABLED
    if(dma_stats.offloaded || dma_stats.intercepted!=dma_stats.fallback) return 7;
#else
    if(dma_stats.offloaded!=5 || dma_stats.bytes!=12936 || dma_stats.fallback!=7) return 8;
#endif
    printf("DMA wrapper PASS intercepted=%lu offloaded=%lu fallback=%lu bytes=%lu cycles=%lu\n",
        dma_stats.intercepted,dma_stats.offloaded,dma_stats.fallback,dma_stats.bytes,dma_stats.cycles);
    return 0;
}
