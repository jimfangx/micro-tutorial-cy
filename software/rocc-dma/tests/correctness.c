#include <stdio.h>
#include <stdint.h>
#include "commands.h"
/* 2 MiB exceeds the configured 32 KiB L1D and 512 KiB L2. */
#define CAP (2 * 1024 * 1024 + 8192)
static uint64_t src_words[CAP/8] __attribute__((aligned(4096)));
static uint64_t dst_words[CAP/8] __attribute__((aligned(4096)));
#define src ((unsigned char *)src_words)
#define dst ((unsigned char *)dst_words)
static unsigned char pattern(size_t i, unsigned seed) { return (unsigned char)((i * 37) ^ (i >> 8) ^ seed); }
static uint64_t word_pattern(size_t i, unsigned seed) {
    return UINT64_C(0x0102030405060708) ^ (i * UINT64_C(0x9e3779b97f4a7c15)) ^ seed;
}
static int run(size_t offset, size_t n, unsigned seed) {
    /* Small cases stay dirty/cached; big cases exercise cache capacity. */
    size_t lo = offset - 8, hi = offset + n + 8;
    /* Compare all 64 bits of every aligned unit, including the 8-byte guards.
       This retains full byte coverage without millions of byte-loop steps. */
    for (size_t i = lo/8; i < hi/8; i++) {
        src_words[i] = word_pattern(i, seed);
        dst_words[i] = UINT64_C(0xa5a5a5a5a5a5a5a5);
    }
    dma_fence();
    if (dma_config(src + offset, dst + offset) || dma_copy_command(n)) return 1;
    dma_fence();
    for (size_t i = lo/8; i < hi/8; i++) {
        uint64_t want = (i >= offset/8 && i < (offset+n)/8) ?
            word_pattern(i, seed) : UINT64_C(0xa5a5a5a5a5a5a5a5);
        if (dst_words[i] != want || src_words[i] != word_pattern(i, seed)) {
            printf("mismatch n=%lu word=%lu got=%lx want=%lx\n", n, i, dst_words[i], want); return 2;
        }
    }
    return 0;
}
static int reject(void *d, const void *s, size_t n) {
    dma_fence();
    if (dma_config(s, d)) return 1;
    unsigned long r = dma_copy_command(n);
    dma_fence();
    return r != 2;
}
int main(void) {
    const size_t sizes[] = {0,8,24,64,192,8192,65536,2*1024*1024};
    if (dma_copy_command(8) != 2) return 1; /* reset: no configuration */
    if (dma_bad_command() != 1) return 2;
    for (unsigned i=0; i<sizeof(sizes)/sizeof(sizes[0]); i++) {
        if (run(4096-8, sizes[i], i+1)) return 3;
        printf("DMA correctness bytes=%lu PASS\n", sizes[i]);
        fflush(stdout);
    }
    for (unsigned i=0; i<32; i++) if (run(64, 192, i+31)) return 4;
    for (unsigned i=0; i<256; i++) { src[i]=pattern(i,9); dst[i]=0xa5; }
    if (reject(dst+1,src,64) || reject(dst,src+1,64) || reject(dst,src,9) ||
        reject(src+8,src,64) || reject(dst,(void *)0x10000UL,64) ||
        reject((void *)0x10000UL,src,64) || reject(dst,(void *)-8UL,64) ||
        reject(dst,(void *)0x8ffffff8UL,16) || reject(dst,src,(size_t)-8)) return 5;
    for (unsigned i=0; i<256; i++) if (src[i]!=pattern(i,9) || dst[i]!=0xa5) return 6;
    puts("DMA correctness/coherence PASS");
    return 0;
}
