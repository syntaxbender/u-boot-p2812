#include "early.h"
static inline u16 get_unaligned_be16(const u8 *p) {return ((u16)p[0]<<8)|p[1];}
static inline u32 get_unaligned_be32(const u8 *p) {return be32(p);}
static inline u16 get_unaligned_le16(const u8 *p) {return p[0]|((u16)p[1]<<8);}
static inline u32 get_unaligned(const u32 *v) {return be32((const u8 *)v);}
static inline void put_unaligned(u32 v,u32 *d) {
 u8 *p=(u8 *)d; p[0]=v>>24;p[1]=v>>16;p[2]=v>>8;p[3]=v;
}
