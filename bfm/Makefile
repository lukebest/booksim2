CC := gcc
CFLAGS := -std=c11 -Wall -Wextra -Werror -O2 -Iinclude -I../refc/include
BUILD := build
REFC_SOURCES := ../refc/ext_mem.c ../refc/calendar_store.c ../refc/xy_route.c \
	../refc/multicast_fork.c ../refc/watchdog_demote.c \
	../refc/router.c ../refc/mesh_sim.c
SOURCES := src/router_bfm.c src/mesh_bfm.c src/dpi_bridge.c src/main_smoke.c
SMOKE := $(BUILD)/mesh_bfm_smoke
REPLAY_SOURCES := src/calendar_replay.c src/main_calendar_replay.c
REPLAY := $(BUILD)/calendar_replay

.PHONY: all build test test_calendars clean

all: build

build: $(SMOKE)

$(SMOKE): $(SOURCES) include/bfm_model.h include/dpi_bridge.h
	mkdir -p $(BUILD) logs
	$(CC) $(CFLAGS) $(REFC_SOURCES) $(SOURCES) -o $@

$(REPLAY): $(REPLAY_SOURCES) include/calendar_replay.h
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) $(REPLAY_SOURCES) -o $@

test: build
	./$(SMOKE)

test_calendars: $(REPLAY)
	python3 ../utils/export_calendar_slots.py
	./$(REPLAY) ../results/calendars/bcast_m1.json
	./$(REPLAY) ../results/calendars/gather_m1.json
	./$(REPLAY) ../results/calendars/reduce_m1.json
	./$(REPLAY) ../results/calendars/allreduce_m1.json
	./$(REPLAY) ../results/calendars/allgather_m1.json

clean:
	rm -rf $(BUILD) logs
