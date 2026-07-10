#ifndef COMBINE_UNIT_H
#define COMBINE_UNIT_H

#include <stdbool.h>

#include "calendar_store.h"

typedef struct {
    bool operand_valid;
    flit_t operand;
    calendar_opcode_t operand_opcode;
    uint8_t operand_tag;
    struct {
        bool valid;
        flit_t flit;
        uint8_t tag;
    } stage[3];
} combine_unit_t;

void combine_unit_init(combine_unit_t *unit);
bool combine_unit_accept(combine_unit_t *unit, calendar_opcode_t opcode,
                         const flit_t *input);
bool combine_unit_advance(combine_unit_t *unit, flit_t *result);

#endif
