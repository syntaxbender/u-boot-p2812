/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __LANTIQ_NAND_RO_H
#define __LANTIQ_NAND_RO_H

#include <linux/types.h>

struct mtd_info;
int ltq_nand_ro_probe(void);
struct mtd_info *ltq_nand_ro_mtd(void);
int ltq_nand_ro_read_page(u32 offset, bool raw, u8 *data, u8 *oob);
int ltq_nand_ro_isbad(u32 offset);
void ltq_nand_ro_print_id(void);

#endif
