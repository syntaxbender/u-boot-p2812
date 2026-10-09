/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef P2812_EARLY_H
#define P2812_EARLY_H
#include <stddef.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define TPL_BASE 0xbe220800u
#define TPL_BYTES 0x7800u
#define MAIN_OFFSET 0x8000u
#define PART_END 0x40000u
#define MAIN_DEST 0xa0100000u
#define INPUT_BUFFER 0xa1000000u
#define OUT_LIMIT 0x100000u
#define PAGE_SIZE 2048u
#define OOB_SIZE 64u
#define EBU 0xbe105300u
#define NAND 0xb4000000u
#define WAIT_POLLS 100000u
#define SPL_CORRECTED 0xbe22fc00u
#define SPL_ERROR 0xbe22fc04u
enum { E_TIMEOUT=1, E_TPL_CRC, E_DDR, E_GEOMETRY, E_BADBLOCK,
       E_ECC, E_HEADER, E_DATA_CRC, E_LZOP, E_DECODE, E_BOUNDS,
       E_MEMORY=12,E_CLOCK,E_RAM_SIZE,E_PROBE,E_ALIAS,
       E_STATE=17, E_ACCESS=18, E_REMAP=19,E_PLL=21,E_MAIN_CRC=28 };
static inline u32 rd32(u32 a) { return *(volatile u32 *)(uintptr_t)a; }
static inline void wr32(u32 a,u32 v) { *(volatile u32 *)(uintptr_t)a=v; }
static inline u8 rd8(u32 a) { return *(volatile u8 *)(uintptr_t)a; }
static inline void wr8(u32 a,u8 v) { *(volatile u8 *)(uintptr_t)a=v; }
static inline u32 be32(const u8 *p) {
 return ((u32)p[0]<<24)|((u32)p[1]<<16)|((u32)p[2]<<8)|p[3];
}
void *memcpy(void *,const void *,size_t);
void *memset(void *,int,size_t);
u32 crc32_bytes(const u8 *,u32);
u32 adler32_bytes(const u8 *,u32);
int wait_bits(u32,u32,u32);
int nand_raw_page(u32,u8 *,u32);
void nand_release(void);
int soc_init(void);
int clock_prepare(void);
u32 memory_capacity(void);
int memory_check(void);
int ebu_prepare(void) __attribute__((noinline,noclone));
int nand_setup(void);
int nand_page_ecc(u32,u8 *);
int nand_read_exact(u32,u32,u8 *);
int decode_main(u8 *,u32,u8 *,u32 *);
void fail(u32) __attribute__((noreturn,noinline,noclone));
void handoff(void) __attribute__((noreturn));
extern volatile u32 early_error, early_corrected;
#endif
