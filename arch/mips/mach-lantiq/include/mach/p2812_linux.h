/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef P2812_LINUX_RUN_H
#define P2812_LINUX_RUN_H

#include <linux/types.h>

/* Called only after bootm has actually acquired its output reservation. */
void p2812_linux_lmb_record(phys_addr_t address, phys_size_t size);

#endif
