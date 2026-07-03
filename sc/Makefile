SYSTEMC_CFLAGS := $(shell pkg-config --cflags systemc 2>/dev/null)
SYSTEMC_LIBS   := $(shell pkg-config --libs systemc 2>/dev/null)
ifeq ($(strip $(SYSTEMC_LIBS)),)
SYSTEMC_LIBS := -lsystemc -lpthread
endif

CXX := g++
CXXFLAGS := -O2 -std=c++17 -Wall -Wextra $(SYSTEMC_CFLAGS)

all: mesh_tb

mesh_tb: mesh_tb.cpp afifo.h
	$(CXX) $(CXXFLAGS) -o mesh_tb mesh_tb.cpp $(SYSTEMC_LIBS)

clean:
	rm -f mesh_tb *.o

.PHONY: all clean
