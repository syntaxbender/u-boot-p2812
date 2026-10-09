#!/usr/bin/env python3
"""Run the actual NAND driver/CLI C with fake MMIO and NAND-core callbacks.

Checks command blocking, timeout propagation, GPIO/EBU write boundaries,
read-only MTD methods, request parsing and RAM boundaries. The NAND core and
physical ECC/bus effects are not emulated by this harness; backup ECC is
separately tested against upstream C. Linux native GCC with UBSan only.
"""
import argparse
from pathlib import Path
import platform
import subprocess
import tempfile

STUBS = {
    "linux/types.h": r"""
#ifndef TEST_TYPES_H
#define TEST_TYPES_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <sys/types.h>
typedef uint32_t u32; typedef uint8_t u8; typedef unsigned long ulong;
#define __iomem
#endif
""",
    "linux/bitops.h": "#define BIT(n) (1U << (n))\n",
    "linux/errno.h": "#include_next <linux/errno.h>\n#define ENOTSUPP 524\n",
    "linux/string.h": "#include <string.h>\n",
    "time.h": "#include <linux/types.h>\nulong get_timer(ulong base);\n",
    "asm/io.h": r"""
#include <linux/types.h>
u32 test_readl(const void *address);
void test_writel(u32 value, void *address);
u8 test_readb(const void *address);
void test_writeb(u8 value, void *address);
#define __raw_readl(p) test_readl(p)
#define __raw_writel(v,p) test_writel(v,p)
#define __raw_readb(p) test_readb(p)
#define __raw_writeb(v,p) test_writeb(v,p)
""",
    "linux/mtd/mtd.h": r"""
#ifndef TEST_MTD_H
#define TEST_MTD_H
#include <linux/types.h>
#define MTD_WRITEABLE 0x400
#define MTD_OPS_RAW 2
#define MTD_OPS_PLACE_OOB 0
struct erase_info { int dummy; };
struct mtd_oob_ops {
    unsigned int mode; size_t len, retlen, ooblen, oobretlen;
    u8 *datbuf, *oobbuf;
};
struct mtd_info {
    unsigned long long size;
    u32 flags, writesize, oobsize, erasesize;
    struct { u32 corrected; } ecc_stats;
    int (*_erase)(struct mtd_info *, struct erase_info *);
    int (*_write)(struct mtd_info *, loff_t, size_t, size_t *, const u8 *);
    int (*_panic_write)(struct mtd_info *, loff_t, size_t, size_t *, const u8 *);
    int (*_write_oob)(struct mtd_info *, loff_t, struct mtd_oob_ops *);
    int (*_block_markbad)(struct mtd_info *, loff_t);
    int (*_lock)(struct mtd_info *, loff_t, uint64_t);
    int (*_unlock)(struct mtd_info *, loff_t, uint64_t);
};
int mtd_read_oob(struct mtd_info *, loff_t, struct mtd_oob_ops *);
int mtd_block_isbad(struct mtd_info *, loff_t);
#endif
""",
    "linux/mtd/rawnand.h": r"""
#ifndef TEST_RAWNAND_H
#define TEST_RAWNAND_H
#include <linux/mtd/mtd.h>
#define NAND_CMD_NONE -1
#define NAND_CMD_READ0 0
#define NAND_CMD_READSTART 0x30
#define NAND_CMD_RNDOUT 5
#define NAND_CMD_RNDOUTSTART 0xe0
#define NAND_CMD_READID 0x90
#define NAND_CMD_STATUS 0x70
#define NAND_CMD_RESET 0xff
#define NAND_CMD_PARAM 0xec
#define NAND_CMD_GET_FEATURES 0xee
#define NAND_CTRL_CHANGE 0x80
#define NAND_ALE 4
#define NAND_CLE 2
#define NAND_ROM 0x800
#define NAND_KEEP_TIMINGS 0x800000
#define NAND_SKIP_BBTSCAN 0x10000
#define NAND_NO_SUBPAGE_WRITE 0x200
#define NAND_BUSWIDTH_16 2
#define NAND_BBT_SCAN2NDPAGE 0x4000
#define NAND_ECC_SOFT 1
struct nand_ecclayout { unsigned int eccbytes; unsigned int eccpos[24]; };
struct nand_chip {
    struct mtd_info mtd;
    void *IO_ADDR_R, *IO_ADDR_W;
    void (*cmd_ctrl)(struct mtd_info *, int, unsigned int);
    void (*select_chip)(struct mtd_info *, int);
    int (*dev_ready)(struct mtd_info *);
    u8 (*read_byte)(struct mtd_info *);
    void (*read_buf)(struct mtd_info *, u8 *, int);
    void (*write_byte)(struct mtd_info *, u8);
    void (*write_buf)(struct mtd_info *, const u8 *, int);
    int chip_delay; unsigned int options, bbt_options;
    struct { int mode, size, strength, bytes; struct nand_ecclayout *layout; } ecc;
    struct { int len; u8 data[8]; } id;
};
static inline struct nand_chip *mtd_to_nand(struct mtd_info *m) { return (void *)m; }
static inline struct mtd_info *nand_to_mtd(struct nand_chip *c) { return &c->mtd; }
static inline bool nand_is_slc(struct nand_chip *c) { return true; }
int nand_scan_ident(struct mtd_info *, int, void *);
int nand_scan_tail(struct mtd_info *);
#endif
""",
    "nand.h": "#include <linux/mtd/mtd.h>\nint nand_register(int, struct mtd_info *);\n",
    "command.h": r"""
#define CMD_RET_SUCCESS 0
#define CMD_RET_FAILURE 1
#define CMD_RET_USAGE -1
struct cmd_tbl { int dummy; };
#define U_BOOT_CMD(...)
""",
    "vsprintf.h": "#include <linux/types.h>\nint strict_strtoul(const char *, unsigned int, ulong *);\n",
    "u-boot/crc.h": "#include <linux/types.h>\nu32 test_crc32(u32, const u8 *, unsigned int);\n#define crc32 test_crc32\n",
}

HARNESS = r"""
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <zlib.h>
#include <linux/types.h>
#include <linux/bitops.h>

static u32 regs[256];
static unsigned int byte_writes, word_writes, read_calls, tests;
static unsigned int fake_geometry_error, fake_read_error, short_read, fake_bad;
static ulong fake_clock;
static u8 last_value, input_value = 0xa5;
static ulong last_address;
static u32 pmu_power, pmu_status;

u32 test_readl(const void *address)
{
    ulong a = (ulong)address;
    if (a == 0xbf10201c) return pmu_power;
    if (a == 0xbf102020) return pmu_status;
    assert((a >= 0xbe105300 && a <= 0xbe1053b4) ||
           (a >= 0xbe100b00 && a <= 0xbe100bac));
    assert(!(a & 3));
    return regs[(a & 0xff) / 4 + (a >= 0xbe105300 ? 64 : 0)];
}
void test_writel(u32 value, void *address)
{
    ulong a = (ulong)address;
    if (a == 0xbf10201c) {
        assert((value ^ pmu_power) == BIT(10));
        pmu_power = value; word_writes++; return;
    }
    if (a >= 0xbe105300) {
        assert(a == 0xbe105324 || a == 0xbe105364 || a == 0xbe1053b0);
    } else {
        const ulong allowed[] = {
            0xbe100b1c, 0xbe100b20, 0xbe100b24, 0xbe100b18,
            0xbe100b4c, 0xbe100b50, 0xbe100b54, 0xbe100b48,
            0xbe100bac, 0xbe100b64, 0xbe100b34, 0xbe100ba8,
        };
        bool found = false;
        for (unsigned int i = 0; i < sizeof(allowed)/sizeof(allowed[0]); i++)
            found |= a == allowed[i];
        assert(found);
    }
    regs[(a & 0xff) / 4 + (a >= 0xbe105300 ? 64 : 0)] = value;
    word_writes++;
}
u8 test_readb(const void *address)
{
    assert((ulong)address == 0xb4000010);
    return input_value;
}
void test_writeb(u8 value, void *address)
{
    ulong a = (ulong)address;
    assert(a == 0xb4000018 || a == 0xb4000014);
    byte_writes++;
    last_address = a; last_value = value;
}
ulong get_timer(ulong base) { return fake_clock++ - base; }
int strict_strtoul(const char *text, unsigned int base, ulong *value)
{
    if (!text[0] || text[0] == '-' || text[0] == '+' || text[0] == ' ')
        return -EINVAL;
    char *end; errno = 0;
    ulong v = strtoul(text, &end, base);
    if (errno || *end) return -EINVAL;
    *value = v; return 0;
}
u32 test_crc32(u32 start, const u8 *data, unsigned int length)
{
    return (u32)crc32(start, data, length);
}

#include "drivers/mtd/nand/raw/lantiq_nand_ro.c"
#include "cmd/ltqnand.c"

int nand_scan_ident(struct mtd_info *mtd, int chips, void *table)
{
    assert(chips == 1 && !table);
    assert(ltq_chip.options & NAND_ROM);
    assert(ltq_chip.options & NAND_SKIP_BBTSCAN);
    assert(ltq_chip.options & NAND_KEEP_TIMINGS);
    assert(ltq_chip.write_byte == ro_write_byte && ltq_chip.write_buf == ro_write_buf);
    mtd->size = 0x08000000; mtd->writesize = fake_geometry_error ? 4096 : 2048;
    mtd->oobsize = 64; mtd->erasesize = 0x20000;
    ltq_chip.id.len = 5;
    mtd_to_nand(mtd)->select_chip(mtd, 0);
    mtd_to_nand(mtd)->cmd_ctrl(mtd, 0x90, NAND_CTRL_CHANGE | NAND_CLE);
    return io_error;
}
int nand_scan_tail(struct mtd_info *mtd)
{
    static struct nand_ecclayout layout;
    layout.eccbytes = 24;
    for (unsigned int i = 0; i < 24; i++) layout.eccpos[i] = i + 40;
    ltq_chip.ecc.bytes = 3; ltq_chip.ecc.layout = &layout;
    mtd->flags = MTD_WRITEABLE;
    return 0;
}
int nand_register(int number, struct mtd_info *mtd)
{
    assert(number == 0 && !(mtd->flags & MTD_WRITEABLE));
    return 0;
}
int mtd_read_oob(struct mtd_info *mtd, loff_t offset, struct mtd_oob_ops *ops)
{
    assert(ops->len == 2048 && ops->ooblen == 64);
    assert(ops->mode == MTD_OPS_RAW || ops->mode == MTD_OPS_PLACE_OOB);
    read_calls++;
    memset(ops->datbuf, 0x5a, 2048); memset(ops->oobbuf, 0xff, 64);
    ops->retlen = short_read ? 2047 : 2048; ops->oobretlen = 64;
    if (fake_read_error == 1) io_error = -ETIMEDOUT;
    if (fake_read_error == 2) return -EBADMSG;
    if (fake_read_error == 3) { mtd->ecc_stats.corrected++; return -EUCLEAN; }
    return 0;
}
int mtd_block_isbad(struct mtd_info *mtd, loff_t offset)
{
    assert(!(offset % mtd->erasesize));
    return fake_bad;
}
static int command(int argc, char **args) { return do_ltqnand(NULL, 0, argc, args); }
static void fresh(void)
{
    memset(&ltq_chip, 0, sizeof(ltq_chip));
    io_error = probe_status = 0; probe_attempted = false;
    regs[(0xb4 / 4) + 64] = WAIT_COMPLETE | WAIT_READY;
    fake_geometry_error = 0; fake_read_error = 0; fake_bad = 0; short_read = 0;
    pmu_power = 0xa5a5ffff; pmu_status = 0;
}

int main(void)
{
    fresh();
    unsigned int before = word_writes;
    board_nand_init();
    assert(!probe_attempted && word_writes == before && byte_writes == 0);
    tests++;
    char *invalid[][5] = {
        {"ltqnand", "write", "0", "800"},
        {"ltqnand", "erase", "0", "800"},
        {"ltqnand", "crc", "0", "0"},
        {"ltqnand", "crc", "1", "800"},
        {"ltqnand", "crc", "0", "801"},
        {"ltqnand", "crc", "8000000", "800"},
        {"ltqnand", "crc", "7fff800", "1000"},
        {"ltqnand", "crc", "0", "1000800"},
        {"ltqnand", "crc", "junk", "800"},
        {"ltqnand", "read", "80100000", "0", "800"},
        {"ltqnand", "read", "a03f0000", "0", "800"},
        {"ltqnand", "read", "a5000000", "0", "800"},
        {"ltqnand", "read", "a4fff800", "0", "1000"},
        {"ltqnand", "read", "ffffffff", "0", "800"},
    };
    for (unsigned int i = 0; i < sizeof(invalid)/sizeof(invalid[0]); i++) {
        assert(command(i >= 9 ? 5 : 4, invalid[i]) == CMD_RET_USAGE);
        assert(!probe_attempted && word_writes == before && byte_writes == 0);
        tests++;
    }
    assert(valid_ram_range(0xa1000000, 0x1000000));
    assert(valid_ram_range(0xa4fff800, 0x800));
    assert(valid_nand_range(0x7fff800, 0x800));
    assert(!valid_nand_range(ULONG_MAX - 0x7ff, 0x800));
    tests += 4;
    ulong parsed;
    assert(parse_hex32("100000000", &parsed) == -EINVAL);
    assert(parse_hex32("0x100000000", &parsed) == -EINVAL);
    assert(parse_hex32("0xffffffff", &parsed) == 0 && parsed == 0xffffffff);
    assert(parse_hex32("0x", &parsed) == -EINVAL);
    tests += 4;
    u32 gpio_before[64];
    for (unsigned int i=0; i<64; i++) regs[i] = 0xdeadbeef;
    memcpy(gpio_before, regs, sizeof(gpio_before));
    assert(ltq_nand_ro_probe() == 0);
    assert(pmu_power == (0xa5a5ffff & ~BIT(10)));
    assert(regs[0x24 / 4 + 64] == 0x14000031);
    assert(regs[0x64 / 4 + 64] == 0x0040d295);
    assert(!(regs[0xb0 / 4 + 64] & (LATCH_CS | NANDM)));
    /* Only the five board NAND pins change; unrelated GPIO bits preserved. */
    assert((regs[0x4c / 4] & (BIT(7) | BIT(8))) == (BIT(7) | BIT(8)));
    assert((regs[0xac / 4] & (BIT(0) | BIT(1))) == (BIT(0) | BIT(1)));
    assert((regs[0xa8 / 4] & (BIT(0) | BIT(1))) == BIT(1));
    for (unsigned int i=0; i<64; i++) {
        u32 mask = 0;
        if (i==0x18/4 || i==0x1c/4 || i==0x20/4 || i==0x24/4) mask=BIT(13);
        if (i==0x48/4 || i==0x4c/4 || i==0x50/4 || i==0x54/4) mask=BIT(7)|BIT(8);
        if (i==0xa8/4 || i==0xac/4 || i==0x64/4 || i==0x34/4) mask=BIT(0)|BIT(1);
        assert(!((regs[i] ^ gpio_before[i]) & ~mask));
    }
    before = word_writes;
    assert(ltq_nand_ro_probe() == 0 && word_writes == before);
    tests++;
    struct mtd_info *mtd = ltq_nand_ro_mtd();
    assert(mtd && !(mtd->flags & MTD_WRITEABLE));
    size_t retlen = 9; struct mtd_oob_ops ops = {.retlen=9, .oobretlen=9};
    before = byte_writes;
    assert(mtd->_erase(mtd, NULL) == -EROFS);
    assert(mtd->_write(mtd, 0, 1, &retlen, NULL) == -EROFS && retlen == 0);
    assert(mtd->_panic_write(mtd, 0, 1, &retlen, NULL) == -EROFS);
    assert(mtd->_write_oob(mtd, 0, &ops) == -EROFS && !ops.retlen && !ops.oobretlen);
    assert(mtd->_block_markbad(mtd, 0) == -EROFS);
    assert(mtd->_lock(mtd, 0, 1) == -EROFS && mtd->_unlock(mtd, 0, 1) == -EROFS);
    ro_write_byte(mtd, 0x80); ro_write_buf(mtd, NULL, 1);
    assert(byte_writes == before && io_error == -EROFS);
    tests += 8;
    const int allowed[] = {0, 0x30, 5, 0xe0, 0x90, 0x70, 0xff, 0xec, 0xee};
    for (int value = 0; value < 256; value++) {
        bool ok = false;
        for (unsigned int i=0; i<sizeof(allowed)/sizeof(allowed[0]); i++)
            ok |= value == allowed[i];
        before = byte_writes; io_error = 0;
        ro_cmd_ctrl(mtd, value, NAND_CTRL_CHANGE | NAND_CLE);
        assert(byte_writes == before + ok);
        assert(io_error == (ok ? 0 : -EROFS));
        if (ok) assert(last_address == 0xb4000018 && last_value == value);
        tests++;
    }
    io_error = 0; before = byte_writes;
    ro_cmd_ctrl(mtd, 0x80, NAND_CTRL_CHANGE); /* data cycle rejected */
    assert(io_error == -EROFS && byte_writes == before);
    io_error = 0;
    ro_cmd_ctrl(mtd, 0x80, NAND_CTRL_CHANGE | NAND_ALE); /* address byte accepted */
    assert(!io_error && last_address == 0xb4000014);
    tests += 2;
    io_error = 0; regs[0xb4 / 4 + 64] = 0; before = byte_writes;
    ro_cmd_ctrl(mtd, 0x90, NAND_CTRL_CHANGE | NAND_CLE);
    assert(io_error == -ETIMEDOUT && byte_writes == before + 1);
    assert(ro_dev_ready(mtd) && ro_read_byte(mtd) == 0xff);
    regs[0xb4 / 4 + 64] = WAIT_COMPLETE | WAIT_READY;
    tests++;
    u8 data[2048], oob[64];
    assert(ltq_nand_ro_read_page(0, false, data, oob) == 0);
    assert(data[0] == 0x5a && oob[0] == 0xff);
    assert(ltq_nand_ro_read_page(0, true, data, oob) == 0);
    before = read_calls;
    assert(ltq_nand_ro_read_page(1, false, data, oob) == -EINVAL);
    assert(ltq_nand_ro_read_page(0x8000000, false, data, oob) == -EINVAL);
    assert(read_calls == before);
    fake_read_error = 1;
    assert(ltq_nand_ro_read_page(0, false, data, oob) == -ETIMEDOUT);
    fake_read_error = 2;
    assert(ltq_nand_ro_read_page(0, false, data, oob) == -EBADMSG);
    fake_read_error = 3;
    assert(ltq_nand_ro_read_page(0, false, data, oob) == 0 && mtd->ecc_stats.corrected == 1);
    fake_read_error = 0; short_read = 1;
    assert(ltq_nand_ro_read_page(0, false, data, oob) == -EIO);
    short_read = 0;
    tests += 8;
    char *crcargs[] = {"ltqnand", "crc", "0", "800"};
    char *rawargs[] = {"ltqnand", "rawcrc", "0", "800"};
    assert(command(4, crcargs) == CMD_RET_SUCCESS);
    assert(command(4, rawargs) == CMD_RET_SUCCESS);
    tests += 2;
    void *ram = mmap((void *)0xa1000000, 8192, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    assert(ram == (void *)0xa1000000);
    memset(ram, 0x3c, 8192);
    char *readargs[] = {"ltqnand", "read", "a1000000", "0", "1000"};
    assert(command(5, readargs) == CMD_RET_SUCCESS);
    for (unsigned int i=0; i<8192; i++)
        assert(((u8 *)ram)[i] == (i < 4096 ? 0x5a : 0x3c));
    memset(ram, 0x3c, 8192); fake_bad = 1;
    assert(command(5, readargs) == CMD_RET_FAILURE);
    for (unsigned int i=0; i<8192; i++) assert(((u8 *)ram)[i] == 0x3c);
    fake_bad = 0; munmap(ram, 8192); tests += 2;
    fresh(); fake_geometry_error = 1;
    assert(ltq_nand_ro_probe() == -ENOTSUPP && !ltq_nand_ro_mtd());
    before = word_writes;
    assert(ltq_nand_ro_probe() == -ENOTSUPP && word_writes == before);
    tests++;
    fresh(); pmu_status = BIT(10); before = byte_writes;
    assert(ltq_nand_ro_probe() == -ETIMEDOUT && !ltq_nand_ro_mtd());
    assert(byte_writes == before); tests++;
    printf("PASS: %u actual-C NAND/CLI checks; fake core/MMIO; UBSan\n", tests);
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux":
        raise RuntimeError("Linux native GCC only; no native Mac emulation")
    with tempfile.TemporaryDirectory(prefix="p2812-nandro-c-") as directory:
        work = Path(directory)
        for name, content in STUBS.items():
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (work / "test.c").write_text(HARNESS)
        binary = work / "test"
        subprocess.run(["gcc", "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra",
                        "-Werror", "-Wno-unused-parameter", "-fsanitize=undefined",
                        "-fno-sanitize-recover=undefined", "-I", str(work),
                        "-I", str(args.source), "-I", str(args.source / "arch/mips/mach-lantiq/include"),
                        str(work / "test.c"), "-lz", "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
