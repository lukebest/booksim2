#include "calendar_store.h"

#include <string.h>

#include "ext_mem.h"

uint16_t calendar_entry_encode(const calendar_entry_t *entry)
{
    uint16_t packed;

    if ((entry == NULL) || (entry->valid > 1U) || (entry->in_port >= PORT_COUNT) ||
        ((entry->out_port_mask & (uint8_t)~UINT8_C(0x1F)) != 0U) ||
        (entry->opcode > UINT8_C(0x0F))) {
        return 0U;
    }
    packed = (uint16_t)((uint16_t)entry->opcode |
                        ((uint16_t)entry->out_port_mask << 4U) |
                        ((uint16_t)entry->in_port << 9U) |
                        ((uint16_t)entry->valid << 12U));
    return (uint16_t)(packed & CALENDAR_ENTRY_PACKED_MASK);
}

bool calendar_entry_decode(uint16_t packed, calendar_entry_t *entry)
{
    if ((entry == NULL) || ((packed & (uint16_t)~CALENDAR_ENTRY_PACKED_MASK) != 0U)) {
        return false;
    }
    entry->valid = (uint8_t)((packed >> 12U) & 1U);
    entry->in_port = (uint8_t)((packed >> 9U) & UINT16_C(0x07));
    entry->out_port_mask = (uint8_t)((packed >> 4U) & UINT16_C(0x1F));
    entry->opcode = (uint8_t)(packed & UINT16_C(0x0F));
    return (entry->in_port < PORT_COUNT);
}

void calendar_store_init(calendar_store_t *store, uint32_t external_base)
{
    if (store != NULL) {
        store->bank_base[0] = external_base;
        store->bank_base[1] = external_base +
                              (CALENDAR_SLOTS * (uint32_t)sizeof(uint16_t));
        store->active_bank = 0U;
        store->slot_count = CALENDAR_SLOTS;
    }
}

void calendar_store_load(calendar_store_t *store, uint16_t bank, uint16_t slot,
                         const calendar_entry_t *entry)
{
    uint32_t addr;
    uint16_t packed;

    if ((store == NULL) || (entry == NULL) || (bank > 1U) ||
        (slot >= store->slot_count)) {
        return;
    }
    packed = calendar_entry_encode(entry);
    if ((entry->valid != 0U) && (packed == 0U)) {
        return;
    }
    addr = store->bank_base[bank] + ((uint32_t)slot * (uint32_t)sizeof(packed));
    ext_mem_write(addr, &packed, (uint32_t)sizeof(packed));
}

bool calendar_store_replay(const calendar_store_t *store, uint32_t cycle,
                           calendar_entry_t *entry)
{
    uint16_t slot;
    uint32_t addr;
    uint16_t packed;

    if ((store == NULL) || (entry == NULL) || (store->slot_count == 0U)) {
        return false;
    }
    slot = (uint16_t)(cycle % (uint32_t)store->slot_count);
    addr = store->bank_base[store->active_bank] +
           ((uint32_t)slot * (uint32_t)sizeof(packed));
    ext_mem_read(addr, &packed, (uint32_t)sizeof(packed));
    return calendar_entry_decode(packed, entry) && (entry->valid != 0U);
}

void calendar_store_select_bank(calendar_store_t *store, uint16_t bank)
{
    if ((store != NULL) && (bank < 2U)) {
        store->active_bank = bank;
    }
}
