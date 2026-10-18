#include <stdio.h>
#include "commands.h"
int main(void) {
    dma_fence();
    if (dma_config((void *)0x80000000UL, (void *)0x80001000UL) != 0) return 1;
    if (dma_copy_command(0) != 0) return 2;
    if (dma_bad_command() != 1) return 3;
    dma_fence();
    puts("DMA command smoke PASS");
    return 0;
}
