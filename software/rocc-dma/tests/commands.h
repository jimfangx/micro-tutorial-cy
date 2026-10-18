#ifndef DMA_TEST_COMMANDS_H
#define DMA_TEST_COMMANDS_H
#include <stdint.h>
#include <stddef.h>
static inline void dma_fence(void) { __asm__ volatile ("fence rw,rw" ::: "memory"); }
static inline unsigned long dma_config(const void *s, void *d) {
    unsigned long r;
    __asm__ volatile (".insn r 0x0b, 7, 0, %0, %1, %2" : "=r"(r) : "r"(s), "r"(d) : "memory");
    return r;
}
static inline unsigned long dma_copy_command(size_t n) {
    unsigned long r;
    __asm__ volatile (".insn r 0x0b, 6, 1, %0, %1, x0" : "=r"(r) : "r"(n) : "memory");
    return r;
}
static inline unsigned long dma_bad_command(void) {
    unsigned long r;
    __asm__ volatile (".insn r 0x0b, 4, 127, %0, x0, x0" : "=r"(r) :: "memory");
    return r;
}
static inline uint64_t dma_cycles(void) {
    uint64_t r; __asm__ volatile ("rdcycle %0" : "=r"(r) :: "memory"); return r;
}
#endif
