#ifndef CALENDAR_STORE_H
#define CALENDAR_STORE_H

#include <stdbool.h>
#include <stdint.h>

#include "noc_types.h"

#define CALENDAR_SLOTS 1024U

typedef enum {
    CAL_OP_FORWARD = 0,
    /*
     * Opcode 1 is retained for existing reduce/allreduce calendars.  It marks
     * a PE-side handoff: the router forwards the flit unchanged, and any
     * reduction is performed by the PE outside the router datapath.
     */
    CAL_OP_PE_HANDOFF = 1,
    /* Legacy combine encodings are reserved and have no router arithmetic. */
    CAL_OP_RESERVED_COMBINE_AND = 2,
    CAL_OP_RESERVED_COMBINE_OR = 3,
    CAL_OP_RESERVED_COMBINE_XOR = 4,
    CAL_OP_RESERVED_COMBINE_MIN = 5,
    CAL_OP_RESERVED_COMBINE_MAX = 6,
    CAL_OP_RESERVED_MAX = CAL_OP_RESERVED_COMBINE_MAX
} calendar_opcode_t;

typedef struct {
    uint8_t valid;
    uint8_t in_port;
    uint8_t out_port_mask;
    uint8_t opcode;
} calendar_entry_t;

/*
 * Physical calendar SRAM stores the 13-bit value:
 * {valid[12], in_port[11:9], out_port_mask[8:4], opcode[3:0]}.
 * The unpacked struct is retained only as a convenient model interface.
 */
#define CALENDAR_ENTRY_PACKED_BITS 13U
#define CALENDAR_ENTRY_PACKED_MASK UINT16_C(0x1FFF)

uint16_t calendar_entry_encode(const calendar_entry_t *entry);
bool calendar_entry_decode(uint16_t packed, calendar_entry_t *entry);

typedef struct {
    uint32_t bank_base[2];
    uint16_t active_bank;
    uint16_t slot_count;
} calendar_store_t;

void calendar_store_init(calendar_store_t *store, uint32_t external_base);
void calendar_store_load(calendar_store_t *store, uint16_t bank, uint16_t slot,
                         const calendar_entry_t *entry);
bool calendar_store_replay(const calendar_store_t *store, uint32_t cycle,
                           calendar_entry_t *entry);
void calendar_store_select_bank(calendar_store_t *store, uint16_t bank);

#endif
