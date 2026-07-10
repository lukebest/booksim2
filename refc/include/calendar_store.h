#ifndef CALENDAR_STORE_H
#define CALENDAR_STORE_H

#include <stdbool.h>
#include <stdint.h>

#include "noc_types.h"

/*
 * SparseCal (Trial 3 / Arch-A3):
 *   Per-router ordered event list, not a dense 1024-slot SRAM.
 *   Each entry: (slot, in_port, out_port_mask, opcode).
 *   Depth 128 covers observed max busy-router occupancy 49 (allreduce m=1)
 *   with margin. Global cycle/slot counter still wraps at 1024 for matching
 *   (max_slot ≈ 951).
 */
#define CALENDAR_SLOT_WRAP 1024U
#define CALENDAR_SPARSE_DEPTH 128U
/* Legacy alias: wrap modulus for cycle→slot matching (not dense SRAM depth). */
#define CALENDAR_SLOTS CALENDAR_SLOT_WRAP

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
 * Hardware sparse event word (23 bits modeled):
 *   {slot[9:0], valid[0], in_port[2:0], out_port_mask[4:0], opcode[3:0]}
 * RefC stores events as (slot, packed13) for clarity; dual-bank hot-swap retained.
 */
#define CALENDAR_ENTRY_PACKED_BITS 13U
#define CALENDAR_ENTRY_PACKED_MASK UINT16_C(0x1FFF)
#define CALENDAR_SPARSE_EVENT_BITS 23U
#define CALENDAR_BANKS 2U

uint16_t calendar_entry_encode(const calendar_entry_t *entry);
bool calendar_entry_decode(uint16_t packed, calendar_entry_t *entry);

typedef struct {
    uint16_t slot;   /* absolute send cycle mod CALENDAR_SLOT_WRAP */
    uint16_t packed; /* 13-bit {valid,in_port,out_port_mask,opcode} */
} calendar_sparse_event_t;

typedef struct {
    uint32_t bank_base[CALENDAR_BANKS];
    uint16_t active_bank;
    uint16_t depth;                          /* max events per bank (128) */
    uint16_t count[CALENDAR_BANKS];          /* occupied events per bank */
    uint16_t slot_wrap;                      /* 1024 */
} calendar_store_t;

void calendar_store_init(calendar_store_t *store, uint32_t external_base);
void calendar_store_load(calendar_store_t *store, uint16_t bank, uint16_t slot,
                         const calendar_entry_t *entry);
bool calendar_store_replay(const calendar_store_t *store, uint32_t cycle,
                           calendar_entry_t *entry);
void calendar_store_select_bank(calendar_store_t *store, uint16_t bank);

#endif
