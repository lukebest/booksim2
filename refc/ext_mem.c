#include "ext_mem.h"

#include <string.h>

static uint8_t ext_mem_storage[EXT_MEM_SIZE];
static ext_mem_stats_t ext_mem_stats;

void ext_mem_clear(void)
{
    (void)memset(ext_mem_storage, 0, sizeof(ext_mem_storage));
    ext_mem_reset_stats();
}

void ext_mem_read(uint32_t addr, void *buf, uint32_t size)
{
    uint32_t bounded_size = 0U;

    if ((addr < EXT_MEM_SIZE) && (buf != NULL)) {
        bounded_size = size;
        if (bounded_size > (EXT_MEM_SIZE - addr)) {
            bounded_size = EXT_MEM_SIZE - addr;
        }
        (void)memcpy(buf, &ext_mem_storage[addr], bounded_size);
    }
    ext_mem_stats.total_reads++;
    ext_mem_stats.total_read_bytes += bounded_size;
    ext_mem_stats.estimated_read_cycles += MEM_LATENCY_EXTERNAL;
}

void ext_mem_write(uint32_t addr, const void *buf, uint32_t size)
{
    uint32_t bounded_size = 0U;

    if ((addr < EXT_MEM_SIZE) && (buf != NULL)) {
        bounded_size = size;
        if (bounded_size > (EXT_MEM_SIZE - addr)) {
            bounded_size = EXT_MEM_SIZE - addr;
        }
        (void)memcpy(&ext_mem_storage[addr], buf, bounded_size);
    }
    ext_mem_stats.total_writes++;
    ext_mem_stats.total_write_bytes += bounded_size;
    ext_mem_stats.estimated_write_cycles += MEM_LATENCY_EXTERNAL;
}

ext_mem_stats_t ext_mem_get_stats(void)
{
    return ext_mem_stats;
}

void ext_mem_reset_stats(void)
{
    (void)memset(&ext_mem_stats, 0, sizeof(ext_mem_stats));
}
