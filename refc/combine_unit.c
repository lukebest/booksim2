#include "combine_unit.h"

#include <string.h>

void combine_unit_init(combine_unit_t *unit)
{
    if (unit != NULL) {
        (void)memset(unit, 0, sizeof(*unit));
    }
}

bool combine_unit_accept(combine_unit_t *unit, calendar_opcode_t opcode,
                         const flit_t *input)
{
    uint32_t lane_index;
    flit_t result;

    if ((unit == NULL) || (input == NULL)) {
        return false;
    }
    if (!unit->operand_valid) {
        unit->operand = *input;
        unit->operand_opcode = opcode;
        unit->operand_tag = input->reserved;
        unit->operand_valid = true;
        return false;
    }
    if ((unit->operand_opcode != opcode) || (unit->operand_tag != input->reserved) ||
        unit->stage[0].valid) {
        return false;
    }
    result = unit->operand;
    for (lane_index = 0U; lane_index < FLIT_LANES; ++lane_index) {
        if (opcode == CAL_OP_COMBINE_ADD) {
            result.lane[lane_index] = unit->operand.lane[lane_index] +
                                       input->lane[lane_index];
        } else if (opcode == CAL_OP_COMBINE_AND) {
            result.lane[lane_index] = unit->operand.lane[lane_index] &
                                       input->lane[lane_index];
        } else if (opcode == CAL_OP_COMBINE_OR) {
            result.lane[lane_index] = unit->operand.lane[lane_index] |
                                       input->lane[lane_index];
        } else if (opcode == CAL_OP_COMBINE_XOR) {
            result.lane[lane_index] = unit->operand.lane[lane_index] ^
                                       input->lane[lane_index];
        } else if (opcode == CAL_OP_COMBINE_MIN) {
            result.lane[lane_index] = (unit->operand.lane[lane_index] <
                                        input->lane[lane_index]) ?
                                       unit->operand.lane[lane_index] :
                                       input->lane[lane_index];
        } else if (opcode == CAL_OP_COMBINE_MAX) {
            result.lane[lane_index] = (unit->operand.lane[lane_index] >
                                        input->lane[lane_index]) ?
                                       unit->operand.lane[lane_index] :
                                       input->lane[lane_index];
        } else {
            return false;
        }
    }
    result.flit_class = FLIT_CLASS_CALENDAR;
    unit->stage[0].flit = result;
    unit->stage[0].tag = unit->operand_tag;
    unit->stage[0].valid = true;
    unit->operand_valid = false;
    return true;
}

bool combine_unit_advance(combine_unit_t *unit, flit_t *result)
{
    bool valid;

    if ((unit == NULL) || (result == NULL)) {
        return false;
    }
    valid = unit->stage[2].valid;
    if (valid) {
        *result = unit->stage[2].flit;
    }
    unit->stage[2] = unit->stage[1];
    unit->stage[1] = unit->stage[0];
    unit->stage[0].valid = false;
    return valid;
}
