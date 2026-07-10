#include <stdio.h>

#include "calendar_replay.h"

int main(int argc, char **argv)
{
    bfm_calendar_result_t result;

    if (argc != 2 || !bfm_replay_calendar(argv[1], &result)) {
        (void)fprintf(stderr, "FAIL calendar=%s\n", argc == 2 ? argv[1] : "<missing>");
        return 1;
    }
    (void)printf("PASS calendar=%s makespan=%u ejections=%u/%u pe_handoffs=%u\n",
                 argv[1], result.makespan, result.ejections,
                 result.expected_ejections, result.pe_handoffs);
    return 0;
}
