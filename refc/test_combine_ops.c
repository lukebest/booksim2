#include <stdio.h>
#include <string.h>

#include "combine_unit.h"

static uint64_t expected(calendar_opcode_t opcode, uint64_t left, uint64_t right)
{
    if (opcode == CAL_OP_COMBINE_ADD) {
        return left + right;
    }
    if (opcode == CAL_OP_COMBINE_AND) {
        return left & right;
    }
    if (opcode == CAL_OP_COMBINE_OR) {
        return left | right;
    }
    if (opcode == CAL_OP_COMBINE_XOR) {
        return left ^ right;
    }
    if (opcode == CAL_OP_COMBINE_MIN) {
        return (left < right) ? left : right;
    }
    return (left > right) ? left : right;
}

int main(void)
{
    const calendar_opcode_t operations[] = {
        CAL_OP_COMBINE_ADD, CAL_OP_COMBINE_AND, CAL_OP_COMBINE_OR,
        CAL_OP_COMBINE_XOR, CAL_OP_COMBINE_MIN, CAL_OP_COMBINE_MAX
    };
    combine_unit_t unit;
    flit_t left;
    flit_t right;
    flit_t result;
    uint32_t operation_index;
    uint32_t lane;
    bool pass = true;

    for (operation_index = 0U;
         operation_index < (uint32_t)(sizeof(operations) / sizeof(operations[0]));
         ++operation_index) {
        (void)memset(&left, 0, sizeof(left));
        (void)memset(&right, 0, sizeof(right));
        for (lane = 0U; lane < FLIT_LANES; ++lane) {
            left.lane[lane] = UINT64_C(0xF000000000000000) + lane;
            right.lane[lane] = UINT64_C(0x0FFFFFFFFFFFFFFF) - lane;
        }
        left.reserved = (uint8_t)operation_index;
        right.reserved = (uint8_t)operation_index;
        if (operations[operation_index] == CAL_OP_COMBINE_ADD) {
            left.lane[0] = UINT64_MAX;
            right.lane[0] = 1U;
        }
        combine_unit_init(&unit);
        pass = pass && !combine_unit_accept(&unit, operations[operation_index], &left);
        pass = pass && combine_unit_accept(&unit, operations[operation_index], &right);
        pass = pass && !combine_unit_advance(&unit, &result);
        pass = pass && !combine_unit_advance(&unit, &result);
        pass = pass && combine_unit_advance(&unit, &result);
        pass = pass && (result.reserved == (uint8_t)operation_index);
        for (lane = 0U; lane < FLIT_LANES; ++lane) {
            pass = pass && (result.lane[lane] ==
                expected(operations[operation_index], left.lane[lane], right.lane[lane]));
        }
    }
    (void)printf("test_combine_ops %s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}
