CC ?= cc
CFLAGS ?= -O2
STRICT = -std=c17 -Wall -Wextra -Wpedantic -Werror
LIBS = $(shell pkg-config --libs libcurl jansson)
INCLUDES = $(shell pkg-config --cflags libcurl jansson)

.PHONY: all clean test

all: scout

scout: src/scout.c
	$(CC) $(CFLAGS) $(STRICT) $(INCLUDES) $< -o $@ $(LIBS)

test: scout
	! ./scout
	! ./scout ""
	! ./scout one two

clean:
	rm -f scout
