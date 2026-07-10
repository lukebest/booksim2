#include <stdio.h>
#include <string.h>

#include "mesh_sim.h"

/* Longest 6x8 route: (0,0) -> (5,7) = 12 hops. Bound = 328 cycles. */
int main(void)
{
    mesh_sim_t mesh;
    flit_t input;
    flit_t output;
    uint32_t inject_cycle;
    uint32_t step;
    bool found = false;
    uint32_t latency = 0U;

    (void)memset(&input, 0, sizeof(input));
    input.flit_class = FLIT_CLASS_BACKGROUND;
    input.src_x = 0U;
    input.src_y = 0U;
    input.dst_x = 5U;
    input.dst_y = 7U;
    input.lane[0] = UINT64_C(0xB6B6B6B6);

    mesh_sim_init(&mesh);
    inject_cycle = mesh.cycle;
    if (!mesh_sim_inject(&mesh, 0U, 0U, &input)) {
        (void)printf("test_bg_bound FAIL inject\n");
        return 1;
    }

    for (step = 0U; step < 400U; ++step) {
        mesh_sim_advance(&mesh);
        if (mesh_sim_take_ejected(&mesh, 5U, 7U, &output) &&
            (output.lane[0] == input.lane[0])) {
            latency = mesh.cycle - inject_cycle;
            found = true;
            break;
        }
    }

    if (!found) {
        (void)printf("test_bg_bound FAIL: no ejection\n");
        return 1;
    }
    if (latency > 328U) {
        (void)printf("test_bg_bound FAIL: latency=%u > 328\n", latency);
        return 1;
    }
    (void)printf("test_bg_bound PASS latency=%u bound=328\n", latency);
    return 0;
}
