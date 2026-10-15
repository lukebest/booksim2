CA-Credit (ca_credit): keeps an in-flight counter per resource. The link credit is the bandwidth-delay product: link bytes per cycle × no-load RTT × --ca-credit-scale. The HA credit is its tracker count. Each epoch, requests are scanned round robin. A request is granted only if every resource on its footprint has credit, and the credit is returned when the DONE message arrives. Area is the smallest of the three.



CA-GiSLIP (ca_gislip): two-level iSLIP with one VOQ bitmap per core (60 × 96 bits). Each link direction, landing port and HA has a grant pointer, and each core has an accept pointer. A transaction is a candidate only if every resource on its path grants it. A fill pass then packs leftovers in a fixed order, and pointers advance only after accepted grants. Each resource's capacity per epoch is its bandwidth × the epoch length. Knobs: --ca-iters 0|1|2, and --ca-grain core|group. With group, grant pointers point at one of the 6 top dies and a round robin inside the die picks the core, which shrinks the pointers from 6 bits to 3.



CA-Rate (ca_rate): every --ca-period the arbiter collects each core's demand per 3DIO link (8 links per die), computes max-min fair rates over the link and HA capacities by water-filling, and sends the rates down. Cores enforce them with the existing token-bucket logic at TrafficGen::admit. It has the lowest control-plane overhead.
