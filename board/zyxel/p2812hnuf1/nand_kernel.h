/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef P2812_NAND_KERNEL_H
#define P2812_NAND_KERNEL_H

int p2812_nand_kernel_load(void);
int p2812_ram_kernel_verify(void);
void p2812_nand_kernel_invalidate(void);

#endif
