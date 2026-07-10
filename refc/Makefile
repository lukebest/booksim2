CC := gcc
CFLAGS := -std=c11 -Wall -Wextra -Werror -O2 -Iinclude
BUILD := build
CORE_SOURCES := ext_mem.c calendar_store.c xy_route.c multicast_fork.c \
	watchdog_demote.c router.c mesh_sim.c
SOURCES := $(CORE_SOURCES) main_smoke.c
SMOKE := $(BUILD)/mesh_router_smoke
DEMOTE_TEST := $(BUILD)/test_demote_noloss
BG_WINDOW_TEST := $(BUILD)/test_bg_window
BLOCKED_FORK_TEST := $(BUILD)/test_blocked_fork
BG_BOUND_TEST := $(BUILD)/test_bg_bound

.PHONY: all build test test_demote_noloss test_bg_window test_blocked_fork test_bg_bound clean

all: build

build: $(SMOKE) $(DEMOTE_TEST) $(BG_WINDOW_TEST) $(BLOCKED_FORK_TEST) $(BG_BOUND_TEST)

$(SMOKE): $(SOURCES) include/noc_types.h include/ext_mem.h \
	include/calendar_store.h include/xy_route.h include/multicast_fork.h \
	include/watchdog_demote.h include/router.h \
	include/mesh_sim.h
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) $(SOURCES) -o $@

$(DEMOTE_TEST): $(CORE_SOURCES) test_demote_noloss.c
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@

$(BG_WINDOW_TEST): $(CORE_SOURCES) test_bg_window.c
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@

$(BLOCKED_FORK_TEST): $(CORE_SOURCES) test_blocked_fork.c
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@

$(BG_BOUND_TEST): $(CORE_SOURCES) test_bg_bound.c
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) $^ -o $@

test: build
	./$(SMOKE)
	./$(DEMOTE_TEST)
	./$(BG_WINDOW_TEST)
	./$(BLOCKED_FORK_TEST)
	./$(BG_BOUND_TEST)

test_demote_noloss: $(DEMOTE_TEST)
	./$(DEMOTE_TEST)

test_bg_window: $(BG_WINDOW_TEST)
	./$(BG_WINDOW_TEST)

test_blocked_fork: $(BLOCKED_FORK_TEST)
	./$(BLOCKED_FORK_TEST)

clean:
	rm -rf $(BUILD)
