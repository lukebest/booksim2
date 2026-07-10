#ifndef CALENDAR_REPLAY_H
#define CALENDAR_REPLAY_H

typedef struct {
    unsigned expected_ejections;
    unsigned ejections;
    unsigned expected_makespan;
    unsigned makespan;
    unsigned combine_ops;
} bfm_calendar_result_t;

int bfm_replay_calendar(const char *path, bfm_calendar_result_t *result);

#endif
