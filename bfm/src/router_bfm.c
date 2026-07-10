#include "bfm_model.h"

bool bfm_router_model_is_refc_compatible(void)
{
    /*
     * The executable links the Phase-2 C router model directly.  This keeps
     * the BFM's flit semantics bit-exact while this file owns the explicit
     * Phase-3 BFM compatibility boundary.
     */
    return (MESH_X == 6U) && (MESH_Y == 8U) && (FLIT_LANES == 8U) &&
           (PORT_COUNT == 5U);
}
