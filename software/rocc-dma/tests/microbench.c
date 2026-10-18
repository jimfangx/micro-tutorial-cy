#include <stdio.h>
#include <string.h>
#include "rocc_dma.h"
#define CAP (65536+8192)
static uint64_t source[CAP/8] __attribute__((aligned(4096)));
static uint64_t destination[CAP/8] __attribute__((aligned(4096)));
#define src ((unsigned char *)source)
#define dst ((unsigned char *)destination)
static uint64_t pattern(size_t i) { return UINT64_C(0x0102030405060708) ^ (i*UINT64_C(0x9e3779b97f4a7c15)); }
static int fail(const char *reason, size_t n) {
    printf("DMA_TEST_FAIL metrics_version=1 result=1 bytes=%lu reason=%s\n",n,reason);
    return 1;
}
static int sample(size_t n, size_t so, size_t d, unsigned seq, const char *tag) {
    size_t limit=(n+(so>d?so:d)+71)/8;
    for(size_t i=0;i<limit;i++) { source[i]=pattern(i); destination[i]=UINT64_C(0xa5a5a5a5a5a5a5a5); }
    __asm__ volatile ("fence rw,rw" ::: "memory");
    uint64_t start=rocc_dma_cycles();
    memcpy(dst+d,src+so,n);
    __asm__ volatile ("fence rw,rw" ::: "memory");
    uint64_t cpu=rocc_dma_cycles()-start;
    if(memcmp(dst+d,src+so,n)) return fail("cpu_baseline_data",n);
    for(size_t i=0;i<limit;i++) destination[i]=UINT64_C(0xa5a5a5a5a5a5a5a5);
    dma_stats_reset();
    start=rocc_dma_cycles();
    int status=rocc_dma_copy(dst+d,src+so,n);
    uint64_t dma=rocc_dma_cycles()-start;
    int supported=dma_supported(dst+d,src+so,n);
    if(status != (supported?DMA_OK:DMA_UNSUPPORTED)) return fail("completion_status",n);
    for(size_t i=0;i<limit;i++) {
        if(source[i]!=pattern(i)) return fail("source_modified",n);
        if(supported) {
            uint64_t want=(i>=d/8 && i<(d+n)/8)?pattern(i-d/8+so/8):UINT64_C(0xa5a5a5a5a5a5a5a5);
            if(destination[i]!=want) return fail(i>=d/8 && i<(d+n)/8?"data_coherence_ordering":"destination_guard",n);
        } else if(destination[i]!=UINT64_C(0xa5a5a5a5a5a5a5a5)) return fail("rejected_copy_modified_destination",n);
    }
    printf("%s metrics_version=1 result=0 bytes=%lu src=%lu dst=%lu src_align=%lu dst_align=%lu "
           "status=%d copy_seq=%u cpu_cycles=%lu dma_cycles=%lu op_cycles=%lu setup_cycles=%lu\n",
           tag,n,(uintptr_t)(src+so),(uintptr_t)(dst+d),(uintptr_t)(src+so)%64,
           (uintptr_t)(dst+d)%64,status,supported?seq:0,cpu,dma,dma_stats.cycles,dma_stats.setup_cycles);
    fflush(stdout);
    return 0;
}
int main(void) {
    const size_t sizes[]={8,24,64,256,1024,4096,65536};
    unsigned seq=0;
    for(unsigned k=0;k<sizeof(sizes)/sizeof(sizes[0]);k++)
        if(sample(sizes[k],0,0,++seq,"MICRO")) return 1;
    if(sample(64,8,24,++seq,"MICRO_ALIGN") || sample(256,16,56,++seq,"MICRO_ALIGN") ||
       sample(24,4088,4088,++seq,"MICRO_ALIGN") ||
       sample(64,1,0,0,"MICRO_ALIGN") || sample(64,0,3,0,"MICRO_ALIGN") ||
       sample(9,0,0,0,"MICRO_ALIGN")) return 2;
    puts("DMA microbenchmark PASS");
    return 0;
}
