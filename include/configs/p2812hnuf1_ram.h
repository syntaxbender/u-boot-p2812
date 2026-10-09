/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __P2812HNUF1_RAM_CONFIG_H
#define __P2812HNUF1_RAM_CONFIG_H

/* Use uncached aliases for entry, stack and relocation in this first port. */
#define CFG_SYS_SDRAM_BASE	0xa0000000
#define CFG_SYS_SDRAM_SIZE	0x08000000
#define CFG_SYS_INIT_SP_OFFSET	0x00400000

#endif
