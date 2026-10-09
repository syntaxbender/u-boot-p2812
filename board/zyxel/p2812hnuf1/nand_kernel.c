// SPDX-License-Identifier: GPL-2.0+
/* Fixed-layout NAND kernel reader. No decompression, execution or flash writes. */
#include <command.h>
#include <image.h>
#include <linux/errno.h>
#include <linux/mtd/mtd.h>
#include <linux/string.h>
#include <stdio.h>
#include <u-boot/crc.h>
#include <mach/nand_ro.h>
#include "nand_kernel.h"

#define KERNEL_START 0x00060000U
#define KERNEL_END 0x00560000U
#define KERNEL_CAPACITY (KERNEL_END - KERNEL_START)
#define KERNEL_RAM 0xa2000000UL
#define KERNEL_ENTRY 0x80002000U
#define PAGE_BYTES 2048U
#define ERASE_BYTES 0x20000U
#define LZMA_HEADER_BYTES 13U

struct kernel_reader {
	u32 offset;
	u32 checked_block;
	u32 pages;
	u32 skipped;
};

static u8 kernel_page[PAGE_BYTES] __aligned(4);
static u8 kernel_oob[64];

/* Bad blocks consume physical partition space, never the next partition. */
static int kernel_next_page(struct kernel_reader *reader)
{
	int ret;
	u32 block;

	while (reader->offset < KERNEL_END) {
		block = reader->offset & ~(ERASE_BYTES - 1);
		if (block != reader->checked_block) {
			ret = ltq_nand_ro_isbad(block);
			if (ret < 0)
				return ret;
			if (ret) {
				reader->skipped++;
				reader->offset = block + ERASE_BYTES;
				continue;
			}
			reader->checked_block = block;
		}
		ret = ltq_nand_ro_read_page(reader->offset, false,
					  kernel_page, kernel_oob);
		if (ret)
			return ret;
		reader->offset += PAGE_BYTES;
		reader->pages++;
		return 0;
	}
	return -ENOSPC;
}

static u32 kernel_le32(const u8 *p)
{
	return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}

static int kernel_check_header(const struct legacy_img_hdr *header,
			       u32 capacity, u32 *total, u32 *output)
{
	const u8 *lzma = (const u8 *)header + sizeof(*header);
	u32 payload, dictionary, lc, lp;

	if (!image_check_magic(header) || !image_check_hcrc(header))
		return -EBADMSG;
	if (!image_check_os(header, IH_OS_LINUX) ||
	    !image_check_arch(header, IH_ARCH_MIPS) ||
	    !image_check_type(header, IH_TYPE_KERNEL) ||
	    image_get_comp(header) != IH_COMP_LZMA ||
	    image_get_load(header) != KERNEL_ENTRY ||
	    image_get_ep(header) != KERNEL_ENTRY)
		return -ENOTSUPP;
	payload = image_get_data_size(header);
	/* Subtract before adding so a hostile 32-bit size cannot wrap. */
	if (payload <= LZMA_HEADER_BYTES ||
	    payload > capacity - sizeof(*header))
		return -EFBIG;
	if (lzma[0] >= 9 * 5 * 5)
		return -ENOTSUPP;
	lc = lzma[0] % 9;
	lp = (lzma[0] / 9) % 5;
	dictionary = kernel_le32(lzma + 1);
	/* Keep decoder allocation bounded, and require a declared output size. */
	if (lc + lp > 4 || dictionary < 4096 ||
	    dictionary > CONFIG_SYS_BOOTM_LEN || kernel_le32(lzma + 9))
		return -ENOTSUPP;
	*output = kernel_le32(lzma + 5);
	if (!*output || *output > CONFIG_SYS_BOOTM_LEN)
		return -EFBIG;
	*total = sizeof(*header) + payload;
	return 0;
}

void p2812_nand_kernel_invalidate(void)
{
	/* Invalidate stale input and stale bootm state, including after a failure.
	 * A new successful bootm start is required before any later bootm stage.
	 */
	memset((void *)KERNEL_RAM, 0, sizeof(struct legacy_img_hdr));
	memset(&images, 0, sizeof(images));
	images.state = BOOTM_STATE_OS_GO;
}

int p2812_nand_kernel_load(void)
{
	struct kernel_reader reader = { KERNEL_START, ~0U, 0, 0 };
	struct legacy_img_hdr *header = (void *)KERNEL_RAM;
	struct mtd_info *mtd;
	u32 total = 0, output = 0, done, take, corrected;
	int ret;

	p2812_nand_kernel_invalidate();
	ret = ltq_nand_ro_probe();
	if (ret)
		goto error;
	mtd = ltq_nand_ro_mtd();
	if (!mtd || mtd->size != 0x08000000ULL ||
	    mtd->writesize != PAGE_BYTES || mtd->oobsize != 64 ||
	    mtd->erasesize != ERASE_BYTES || (mtd->flags & MTD_WRITEABLE)) {
		ret = -ENOTSUPP;
		goto error;
	}
	corrected = mtd->ecc_stats.corrected;
	ret = kernel_next_page(&reader);
	if (ret)
		goto error;
	ret = kernel_check_header((const void *)kernel_page, KERNEL_CAPACITY,
				  &total, &output);
	if (ret)
		goto error;
	for (done = 0; done < total; done += take) {
		if (done) {
			ret = kernel_next_page(&reader);
			if (ret)
				goto error;
		}
		take = total - done;
		if (take > PAGE_BYTES)
			take = PAGE_BYTES;
		memcpy((u8 *)header + done, kernel_page, take);
	}
	/* Always verify data CRC, regardless of the environment's verify flag. */
	if (!image_check_dcrc(header)) {
		ret = -EBADMSG;
		goto error;
	}
	printf("NAND kernel verified: ram=%08lx bytes=%u CRC32=%08x dataCRC=%08x\n",
	       KERNEL_RAM, total, crc32(0, (const u8 *)header, total),
	       image_get_dcrc(header));
	printf("NAND kernel bounds: partition=%08x..%08x physical_end=%08x pages=%u skipped=%u corrected=%u\n",
	       KERNEL_START, KERNEL_END, reader.offset, reader.pages,
	       reader.skipped, mtd->ecc_stats.corrected - corrected);
	printf("NAND kernel LZMA: output=%u load=%08x entry=%08x limit=%u\n",
	       output, KERNEL_ENTRY, KERNEL_ENTRY, CONFIG_SYS_BOOTM_LEN);
	puts("NAND kernel ready in RAM; Linux not executed\n");
	return CMD_RET_SUCCESS;
error:
	p2812_nand_kernel_invalidate();
	printf("NAND kernel load failed: %d; image invalidated; staying at console\n",
	       ret);
	return CMD_RET_FAILURE;
}

static int do_nandkload(struct cmd_tbl *cmdtp, int flag, int argc,
			char *const argv[])
{
	if (argc != 1)
		return CMD_RET_USAGE;
	return p2812_nand_kernel_load();
}

#ifdef CONFIG_CMD_P2812_LINUXRUN
int p2812_ram_kernel_verify(void)
{
	const struct legacy_img_hdr *header = (const void *)KERNEL_RAM;
	u32 total, output;
	int ret;

	/* Preserve the freshly loaded input; clear only prior bootm state. */
	memset(&images, 0, sizeof(images));
	images.state = BOOTM_STATE_OS_GO;
	ret = kernel_check_header(header, 0x1000000U, &total, &output);
	if (!ret && !image_check_dcrc(header))
		ret = -EBADMSG;
	if (ret) {
		p2812_nand_kernel_invalidate();
		printf("RAM kernel verify failed: %d; image invalidated\n", ret);
		return CMD_RET_FAILURE;
	}
	printf("RAM kernel verified: ram=%08lx bytes=%u output=%u\n",
	       KERNEL_RAM, total, output);
	return CMD_RET_SUCCESS;
}
#endif

U_BOOT_CMD(nandkload, 1, 0, do_nandkload,
	   "read and verify P2812 NAND kernel to fixed RAM (read only)",
	   "No arguments. Kernel partition 60000..560000, RAM a2000000.\n"
	   "Skips bad blocks within partition; checks ECC, header/data CRC and bounds.\n"
	   "Does not decompress or execute Linux. Use fresh bootm start afterwards.");
