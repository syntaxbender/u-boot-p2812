/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef NANDSOURCE4_STATS_H
#define NANDSOURCE4_STATS_H
#include "early.h"
/* All records above the descending SRAM stack. Leave SPL FC00/FC04 intact. */
#define C ((volatile u32 *)0xbe22f900u)
#define B ((volatile u32 *)0xbe22fd00u)
enum { B_MAGIC,B_VERSION,B_STATUS,B_PHASE,B_RAM,B_MEMORY_WORDS,
       B_IMAGE_BYTES,B_CRC_BEFORE,B_CRC_AFTER,B_MAIN_CRC,B_MAIN_BYTES,
       B_NAND_CORRECTED };
#endif
