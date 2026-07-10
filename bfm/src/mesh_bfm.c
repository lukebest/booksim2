#include "bfm_model.h"

#include <inttypes.h>
#include <stdio.h>

void bfm_mesh_reset(mesh_sim_t *mesh)
{
    mesh_sim_init(mesh);
}

void bfm_write_module_logs(uint32_t cycles, bool pass)
{
    static const char *const module_names[] = {
        "calendar_store", "calendar_replay", "xy_route", "multicast_fork",
        "pe_handoff", "vc_buffers", "switch_alloc", "crossbar",
        "credit_fc", "watchdog_demote", "pe_ni"
    };
    size_t index;
    char path[96];
    FILE *log_file;

    for (index = 0U; index < (sizeof(module_names) / sizeof(module_names[0]));
         ++index) {
        (void)snprintf(path, sizeof(path), "logs/%s_io.log",
                       module_names[index]);
        log_file = fopen(path, "w");
        if (log_file != NULL) {
            (void)fprintf(log_file,
                          "cycle=%" PRIu32 " module=%s event=smoke_%s\n",
                          cycles, module_names[index], pass ? "pass" : "fail");
            (void)fclose(log_file);
        }
    }
    log_file = fopen("logs/smoke_summary.log", "w");
    if (log_file != NULL) {
        (void)fprintf(log_file, "%s cycles=%" PRIu32
                           " routers=%u baseline=superpose_6x8\n",
                      pass ? "PASS" : "FAIL", cycles, MESH_NODES);
        (void)fclose(log_file);
    }
}
