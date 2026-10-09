/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef DDRTRACE1_H
#define DDRTRACE1_H
#include "early.h"
#define T ((volatile u32 *)0xbe22fa00u)
#define TRACE_WORDS 64
int trace_alias(u32 ram);
#endif
