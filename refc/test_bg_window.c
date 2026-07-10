#include <stdio.h>
#include <string.h>

#include "mesh_sim.h"

int main(void)
{
    mesh_sim_t mesh;
    flit_t input;
    flit_t output;
    uint32_t step;
    bool pass = true;

    (void)memset(&input, 0, sizeof(input));
    input.flit_class = FLIT_CLASS_BACKGROUND;
    input.dst_x = 1U;
    input.dst_y = 0U;
    input.lane[0] = UINT64_C(0xB6);
    mesh_sim_init(&mesh);
    pass = pass && mesh_sim_inject(&mesh, 0U, 0U, &input);
    for (step = 0U; step < 15U; ++step) {
        mesh_sim_advance(&mesh);
    }
    for (step = 0U; step < 24U; ++step) {
        mesh_sim_advance(&mesh);
    }
    pass = pass && mesh_sim_take_ejected(&mesh, 1U, 0U, &output) &&
           (output.lane[0] == input.lane[0]);
    (void)printf("test_bg_window %s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}
