#include <stdio.h>
#include "commands.h"
static volatile uint64_t src __attribute__((aligned(64)));
static volatile uint64_t dst __attribute__((aligned(64)));
int main(void) {
    src=0x123456789abcdef0UL; dst=0xa5;
    puts("DMA basic submitting"); fflush(stdout);
    dma_fence();
    if(dma_config((const void *)&src,(void *)&dst)) return 1;
    if(dma_copy_command(8)) return 2;
    dma_fence();
    if(dst!=src) return 3;
    puts("DMA basic PASS"); fflush(stdout);
    return 0;
}
