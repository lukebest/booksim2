#include <stdio.h>
#include <string.h>

#include "watchdog_demote.h"

int main(void)
{
    watchdog_demote_t watchdog;
    flit_t input;
    flit_t escaped;
    bool released_once;
    bool pass = true;

    (void)memset(&input, 0, sizeof(input));
    input.flit_class = FLIT_CLASS_CALENDAR;
    input.dst_x = 5U;
    input.dst_y = 7U;
    input.lane[0] = UINT64_C(0x1234);
    watchdog_demote_init(&watchdog);
    pass = pass && watchdog_demote_arm(&watchdog, 10U, &input, 1U, 42U,
                                       PORT_MASK(PORT_EAST) | PORT_MASK(PORT_SOUTH));
    pass = pass && !watchdog_demote_arm(&watchdog, 11U, &input, 1U, 43U, 0U);
    pass = pass && !watchdog_demote_take_escape(&watchdog, 41U, &escaped,
                                                 &released_once);
    pass = pass && watchdog_demote_take_escape(&watchdog, 42U, &escaped,
                                                &released_once);
    pass = pass && released_once && (escaped.flit_class == FLIT_CLASS_DEMOTED) &&
           (escaped.remaining_mask == PORT_MASK(PORT_EAST)) &&
           (escaped.lane[0] == input.lane[0]);
    pass = pass && watchdog_demote_take_escape(&watchdog, 42U, &escaped,
                                                &released_once);
    pass = pass && !released_once &&
           (escaped.remaining_mask == PORT_MASK(PORT_SOUTH));
    pass = pass && !watchdog_demote_take_escape(&watchdog, 42U, &escaped,
                                                 &released_once);
    (void)printf("test_demote_noloss %s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}
