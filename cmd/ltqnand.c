// SPDX-License-Identifier: GPL-2.0+
/* Physical NAND diagnostics and bounded reads; no persistent modification. */
#include <command.h>
#include <linux/errno.h>
#include <linux/mtd/mtd.h>
#include <linux/string.h>
#include <linux/types.h>
#include <stdio.h>
#include <vsprintf.h>
#include <u-boot/crc.h>
#include <mach/nand_ro.h>

#define NAND_PAGE_SIZE 2048
#define NAND_OOB_SIZE 64
#define MAX_READ 0x01000000UL
#define RAM_MIN 0xa1000000UL
#define RAM_END 0xa5000000UL

static u8 page_data[NAND_PAGE_SIZE];
static u8 page_oob[NAND_OOB_SIZE];

static int parse_hex32(const char *text, ulong *value)
{
	const char *digits = text;
	size_t count;

	if (digits[0] == '0' && (digits[1] == 'x' || digits[1] == 'X'))
		digits += 2;
	count = strlen(digits);
	/* Reject wraparound on the target's 32-bit unsigned long. */
	if (!count || count > 8)
		return -EINVAL;
	return strict_strtoul(text, 16, value);
}

static bool valid_nand_range(ulong offset, ulong length)
{
	return length && length <= MAX_READ && !(offset % NAND_PAGE_SIZE) &&
		!(length % NAND_PAGE_SIZE) && offset < 0x08000000UL &&
		length <= 0x08000000UL - offset;
}

static bool valid_ram_range(ulong address, ulong length)
{
	return address >= RAM_MIN && address < RAM_END &&
		length <= RAM_END - address;
}

static int do_ltqnand(struct cmd_tbl *cmdtp, int flag, int argc,
		     char *const argv[])
{
	struct mtd_info *mtd;
	ulong offset = 0, length = 0, address = 0, done;
	u32 checksum = 0, corrected;
	bool read, raw;
	int ret;

	/* Parse/validate the entire request before touching EBU/GPIO. */
	if (argc < 2)
		return CMD_RET_USAGE;
	read = !strcmp(argv[1], "read");
	raw = !strcmp(argv[1], "rawcrc");
	if (!strcmp(argv[1], "info") || !strcmp(argv[1], "bad")) {
		if (argc != 2)
			return CMD_RET_USAGE;
	} else if (read || raw || !strcmp(argv[1], "crc")) {
		if (argc != (read ? 5 : 4))
			return CMD_RET_USAGE;
		if ((read && parse_hex32(argv[2], &address)) ||
		    parse_hex32(argv[read ? 3 : 2], &offset) ||
		    parse_hex32(argv[read ? 4 : 3], &length) ||
		    !valid_nand_range(offset, length) ||
		    (read && !valid_ram_range(address, length)))
			return CMD_RET_USAGE;
	} else {
		return CMD_RET_USAGE;
	}
	ret = ltq_nand_ro_probe();
	if (ret) {
		printf("NAND read-only probe failed: %d\n", ret);
		return CMD_RET_FAILURE;
	}
	mtd = ltq_nand_ro_mtd();
	if (!strcmp(argv[1], "info")) {
		ltq_nand_ro_print_id();
		printf("size=%llu page=%u oob=%u erase=%u ECC=Hamming 1/256 RO=1\n",
		       mtd->size, mtd->writesize, mtd->oobsize, mtd->erasesize);
		return CMD_RET_SUCCESS;
	}
	if (!strcmp(argv[1], "bad")) {
		unsigned int bad = 0;

		for (offset = 0; offset < mtd->size; offset += mtd->erasesize) {
			ret = ltq_nand_ro_isbad(offset);
			if (ret < 0)
				goto error;
			if (ret) {
				printf("bad block at %08lx\n", offset);
				bad++;
			}
		}
		printf("Bad blocks: %u (first/second-page markers; no flash BBT)\n", bad);
		return CMD_RET_SUCCESS;
	}
	corrected = mtd->ecc_stats.corrected;
	for (done = 0; done < length; done += NAND_PAGE_SIZE) {
		/* RAM read refuses a bad block; CRCs keep physical dump offsets. */
		if (read && (!done || !((offset + done) % mtd->erasesize))) {
			ret = ltq_nand_ro_isbad((offset + done) & ~(mtd->erasesize - 1));
			if (ret) {
				if (ret > 0)
					ret = -EIO;
				goto error;
			}
		}
		ret = ltq_nand_ro_read_page(offset + done, raw, page_data, page_oob);
		if (ret)
			goto error;
		checksum = crc32(checksum, page_data, NAND_PAGE_SIZE);
		if (raw)
			checksum = crc32(checksum, page_oob, NAND_OOB_SIZE);
		if (read)
			memcpy((void *)(address + done), page_data, NAND_PAGE_SIZE);
	}
	printf("NAND %s offset=%08lx length=%08lx CRC32=%08x corrected=%u\n",
	       argv[1], offset, length, checksum, mtd->ecc_stats.corrected - corrected);
	return CMD_RET_SUCCESS;
error:
	printf("NAND read stopped: %d; no jump or boot performed\n", ret);
	return CMD_RET_FAILURE;
}

U_BOOT_CMD(ltqnand, 5, 0, do_ltqnand,
	   "read-only P2812 NAND diagnostics (hex arguments)",
	   "info\n"
	   "ltqnand bad\n"
	   "ltqnand crc offset main_length\n"
	   "ltqnand rawcrc offset main_length - CRC of main+OOB per page\n"
	   "ltqnand read uncached_ram_address offset main_length\n"
	   "Physical offsets, page aligned, at most 16 MiB; read RAM a1000000..a5000000");
