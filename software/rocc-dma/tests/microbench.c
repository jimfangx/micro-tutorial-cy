#include <stdio.h>
#include <string.h>
#include "rocc_dma.h"
static unsigned char src[65536] __attribute__((aligned(4096)));
static unsigned char dst[65536] __attribute__((aligned(4096)));
int main(void) {
    const size_t sizes[]={8,24,64,256,1024,4096,65536};
    for(unsigned k=0;k<sizeof(sizes)/sizeof(sizes[0]);k++) {
        size_t n=sizes[k];
        for(size_t i=0;i<n;i++) { src[i]=(unsigned char)(i*37+(i>>8)); dst[i]=0; }
        __asm__ volatile ("fence rw,rw" ::: "memory");
        uint64_t start=rocc_dma_cycles();
        memcpy(dst,src,n);
        __asm__ volatile ("fence rw,rw" ::: "memory");
        uint64_t cpu=rocc_dma_cycles()-start;
        for(size_t i=0;i<n;i++) if(src[i]!=dst[i]) return 1;
        for(size_t i=0;i<n;i++) dst[i]=0xa5;
        dma_stats_reset();
        start=rocc_dma_cycles();
        if(rocc_dma_copy(dst,src,n)) return 2;
        uint64_t dma=rocc_dma_cycles()-start;
        for(size_t i=0;i<n;i++) if(src[i]!=dst[i]) return 3;
        printf("MICRO bytes=%lu cpu_cycles=%lu dma_cycles=%lu op_cycles=%lu setup_cycles=%lu\n",
            n,cpu,dma,dma_stats.cycles,dma_stats.setup_cycles);
    }
    puts("DMA microbenchmark PASS");
    return 0;
}
