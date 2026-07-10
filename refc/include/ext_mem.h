#ifndef EXT_MEM_H
#define EXT_MEM_H

#include <stdint.h>

#define MEM_LATENCY_INTERNAL 1U
#define MEM_LATENCY_EXTERNAL 500U
#define EXT_MEM_SIZE (1024U * 1024U)

typedef struct {
    uint64_t total_reads;
    uint64_t total_writes;
    uint64_t total_read_bytes;
    uint64_t total_write_bytes;
    uint64_t estimated_read_cycles;
    uint64_t estimated_write_cycles;
} ext_mem_stats_t;

void ext_mem_clear(void);
void ext_mem_read(uint32_t addr, void *buf, uint32_t size);
void ext_mem_write(uint32_t addr, const void *buf, uint32_t size);
ext_mem_stats_t ext_mem_get_stats(void);
void ext_mem_reset_stats(void);

#endif
