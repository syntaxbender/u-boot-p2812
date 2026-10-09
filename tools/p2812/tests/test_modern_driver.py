#!/usr/bin/env python3
"""Execute the actual port C files against fake MMIO; no serial or hardware.

This checks clock decoding, UART baud accuracy, FIFO behaviour, byte lanes,
and the address range of driver writes. It does not emulate VRX288 hardware.
Run in the pinned build container, whose native GCC compiles the harness.
"""
from pathlib import Path
import argparse
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[3]

STUBS = {
    "dm.h": r"""
#ifndef TEST_DM_H
#define TEST_DM_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef uint32_t u32;
typedef uintptr_t fdt_addr_t;
#define __iomem
#define FDT_ADDR_T_NONE ((fdt_addr_t)-1)
#define UCLASS_SERIAL 1
#define DM_FLAG_PRE_RELOC 1
struct udevice { void *priv; fdt_addr_t addr; };
struct udevice_id { const char *compatible; };
struct test_driver {
    const char *name;
    int id;
    const struct udevice_id *of_match;
    int (*probe)(struct udevice *);
    size_t priv_auto;
    const void *ops;
    unsigned int flags;
};
#define U_BOOT_DRIVER(name) struct test_driver name
static inline void *dev_get_priv(struct udevice *d) { return d->priv; }
static inline fdt_addr_t dev_read_addr(struct udevice *d) { return d->addr; }
#endif
""",
    "serial.h": r"""
#include <dm.h>
struct dm_serial_ops {
    int (*putc)(struct udevice *, const char);
    int (*getc)(struct udevice *);
    int (*pending)(struct udevice *, bool);
    int (*setbrg)(struct udevice *, int);
};
""",
    "linux/bitops.h": "#define BIT(n) (1U << (n))\n",
    "asm/addrspace.h": r"""
#include <stdint.h>
void *test_kseg1(uintptr_t address);
#define CKSEG1ADDR(address) test_kseg1(address)
""",
    "asm/io.h": r"""
#include <stdint.h>
uint32_t test_readl(const void *address);
void test_writel(uint32_t value, void *address);
uint8_t test_readb(const void *address);
void test_writeb(uint8_t value, void *address);
#define __raw_readl(p) test_readl(p)
#define __raw_writel(v, p) test_writel(v, p)
#define __raw_readb(p) test_readb(p)
#define __raw_writeb(v, p) test_writeb(v, p)
""",
}

HARNESS = r"""
#include <assert.h>
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <mach/vrx200.h>

static uint32_t cgu;
static uint32_t asc[64];
static unsigned int writes;
static unsigned int tests;

void *test_kseg1(uintptr_t address)
{
    if (address == LTQ_VRX200_CGU_SYS) return &cgu;
    assert(address == LTQ_VRX200_ASC1);
    return asc;
}

uint32_t test_readl(const void *address)
{
    if (address == &cgu) return cgu;
    uintptr_t offset = (uintptr_t)address - (uintptr_t)asc;
    assert(offset < sizeof(asc) && offset % 4 == 0);
    return asc[offset / 4];
}

void test_writel(uint32_t value, void *address)
{
    uintptr_t offset = (uintptr_t)address - (uintptr_t)asc;
    assert(offset < sizeof(asc) && offset % 4 == 0);
    asc[offset / 4] = value;
    writes++;
}

uint8_t test_readb(const void *address)
{
    assert(address == (uint8_t *)asc + 0x27);
    return *(const uint8_t *)address;
}

void test_writeb(uint8_t value, void *address)
{
    assert(address == (uint8_t *)asc + 0x23);
    *(uint8_t *)address = value;
    writes++;
}

#include "arch/mips/mach-lantiq/clock.c"
#include "drivers/serial/serial_lantiq_asc.c"

int main(void)
{
    const unsigned long rates[] = {
        600000000, 500000000, 393219000, 333333333, 125000000,
        196609500, 196609500, 166666667, 125000000, 125000000
    };
    const unsigned int ocp[] = {0, 2, 3};
    const int baud[] = {9600, 115200, 921600};
    struct ltq_asc_priv priv = {0};
    struct udevice dev = {&priv, LTQ_VRX200_ASC1};
    double max_error = 0;

    for (unsigned int cpu = 0; cpu < 16; cpu++) {
        cgu = cpu << 4;
        unsigned long expected = cpu < 10 ? rates[cpu] : 0;
        assert(ltq_vrx200_cpu_rate() == expected);
        assert(get_tbclk() == expected / 2);
        tests++;
    }
    assert(serial_lantiq_asc.id == UCLASS_SERIAL);
    assert(serial_lantiq_asc.flags & DM_FLAG_PRE_RELOC);
    assert(ltq_asc_probe(&dev) == 0);
    assert(priv.base == asc);
    assert(asc[0x00 / 4] == 0x100);
    assert((asc[0x40 / 4] & 3) == 3);
    assert((asc[0x44 / 4] & 3) == 3);
    assert(asc[0x18 / 4] == 2);
    tests++;

    for (unsigned int cpu = 0; cpu < 10; cpu++) {
        for (unsigned int ratio = 0; ratio < 3; ratio++) {
            cgu = cpu << 4 | ocp[ratio];
            unsigned long fpi = rates[cpu];
            if (ratio == 1) fpi /= 2;
            if (ratio == 2) fpi = fpi * 2 / 5;
            assert(ltq_vrx200_bus_rate() == fpi);
            for (unsigned int b = 0; b < 3; b++) {
                assert(ltq_asc_setbrg(&dev, baud[b]) == 0);
                uint32_t fdv = asc[0x58 / 4];
                uint32_t bg = asc[0x50 / 4];
                assert(fdv > 0 && fdv <= 512 && bg < 65536);
                assert((asc[0x10 / 4] & ((1U << 15) | (1U << 9))) ==
                       ((1U << 15) | (1U << 9)));
                /* Independent ASC equation using full, untruncated clock. */
                double actual = (double)fpi * fdv / (8192.0 * (bg + 1));
                double error = fabs(actual - baud[b]) / baud[b];
                assert(error < 0.005);
                if (error > max_error) max_error = error;
                tests++;
            }
        }
    }

    /* Unknown clock and invalid baud must fail without any MMIO writes. */
    unsigned int before = writes;
    cgu = 0xf0;
    assert(ltq_asc_setbrg(&dev, 115200) == -EINVAL);
    cgu = 0x11;
    assert(ltq_vrx200_bus_rate() == 0);
    assert(ltq_asc_setbrg(&dev, 115200) == -EINVAL);
    cgu = 0x12;
    assert(ltq_asc_setbrg(&dev, 0) == -EINVAL);
    assert(ltq_asc_setbrg(&dev, -1) == -EINVAL);
    assert(writes == before);
    tests += 4;

    asc[0x48 / 4] = 0;
    assert(ltq_asc_getc(&dev) == -EAGAIN);
    assert(ltq_asc_putc(&dev, 'X') == -EAGAIN);
    assert(ltq_asc_pending(&dev, true) == 0);
    assert(ltq_asc_pending(&dev, false) == 0);
    asc[0x48 / 4] = (16U << 24) | (3U << 8) | 5;
    assert(ltq_asc_pending(&dev, true) == 5);
    assert(ltq_asc_pending(&dev, false) == 3);
    *((uint8_t *)asc + 0x27) = 0xa5;
    assert(ltq_asc_getc(&dev) == 0xa5);
    assert(ltq_asc_putc(&dev, 'X') == 0);
    assert(*((uint8_t *)asc + 0x23) == 'X');
    tests += 8;

    dev.addr = FDT_ADDR_T_NONE;
    before = writes;
    assert(ltq_asc_probe(&dev) == -EINVAL);
    assert(writes == before);
    tests++;
    printf("PASS: %u cases; max UART baud error %.6f%%; all writes confined "
           "to fake ASC; no hardware accessed\n", tests, max_error * 100);
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()
    source = args.source.resolve()
    with tempfile.TemporaryDirectory(prefix="p2812-driver-test-") as directory:
        tmp = Path(directory)
        for name, contents in STUBS.items():
            path = tmp / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        harness = tmp / "harness.c"
        executable = tmp / "harness"
        harness.write_text(HARNESS)
        subprocess.run([
            "gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=undefined", "-fno-sanitize-recover=all",
            "-I", str(tmp), "-I", str(source),
            "-I", str(source / "arch/mips/mach-lantiq/include"),
            str(harness), "-lm", "-o", str(executable),
        ], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
