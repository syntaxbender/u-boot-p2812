// SPDX-License-Identifier: GPL-2.0+
/* Compiler-derived target offsets for the Linux-only instruction model. */
#include <stddef.h>
#include <asm/global_data.h>
#include <image.h>
#include <bootm.h>
#include <lmb.h>
#include <command.h>
#include <linux/mtd/mtd.h>

const unsigned int linuxrun_layout[] = {
	sizeof(gd_t), offsetof(gd_t, bd), offsetof(gd_t, ram_size),
	offsetof(gd_t, flags), offsetof(gd_t, dram[0].start),
	offsetof(gd_t, dram[0].size), offsetof(gd_t, start_addr_sp),
	offsetof(gd_t, initial_relocaddr), offsetof(gd_t, ram_top),
	offsetof(gd_t, fdt_blob), GD_FLG_RELOC,
	sizeof(struct bd_info), offsetof(struct bd_info, bi_boot_params),
	sizeof(struct bootm_headers), offsetof(struct bootm_headers, os),
	offsetof(struct bootm_headers, ep), offsetof(struct bootm_headers, state),
	offsetof(struct bootm_headers, ft_len),
	sizeof(struct bootm_info), offsetof(struct bootm_info, images),
	offsetof(struct image_info, start), offsetof(struct image_info, end),
	offsetof(struct image_info, image_start), offsetof(struct image_info, image_len),
	offsetof(struct image_info, load), offsetof(struct image_info, comp),
	offsetof(struct image_info, type), offsetof(struct image_info, os),
	offsetof(struct image_info, arch), BOOTM_STATE_LOADOS,
	BOOTM_STATE_OS_PREP, BOOTM_STATE_OS_GO,
	BOOTM_STATE_START | BOOTM_STATE_FINDOS | BOOTM_STATE_FINDOTHER,
	sizeof(struct mtd_info), offsetof(struct mtd_info, size),
	offsetof(struct mtd_info, flags), offsetof(struct mtd_info, writesize),
	offsetof(struct mtd_info, oobsize), offsetof(struct mtd_info, erasesize),
	offsetof(struct mtd_info, ecc_stats.corrected),
	offsetof(struct lmb, available_mem), offsetof(struct lmb, used_mem),
	offsetof(struct alist, data), offsetof(struct alist, count),
	sizeof(struct lmb_region), offsetof(struct lmb_region, base),
	offsetof(struct lmb_region, size), offsetof(struct lmb_region, flags),
	sizeof(phys_addr_t), sizeof(phys_size_t),
	sizeof(struct cmd_tbl), offsetof(struct cmd_tbl, name),
	offsetof(struct cmd_tbl, cmd),
};
