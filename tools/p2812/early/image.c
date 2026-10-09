/* SPDX-License-Identifier: GPL-2.0+ */
#include "early.h"
#include "generated.h"
#include <linux/lzo.h>
/* This deliberate narrow LZOP schema is exactly lzop -9 -c stdin output:
 * v1.040, method3, level9, OS3, flags0x0d, empty name, one block, Adler32
 * data and header, zero footer, no trailing bytes. Do not call upstream's
 * unbounded parse_header/lzop_decompress container functions.
 */
int decode_main(u8 *image,u32 total,u8 *output,u32 *written) {
 static const u8 magic[9]={0x89,0x4c,0x5a,0x4f,0,13,10,26,10};
 u32 n,hcrc,crc,packed,raw,i;u8 *s;size_t cap;int rc;
 *written=0;
 if(total<64 || total>PART_END-MAIN_OFFSET)return E_BOUNDS;
 if(be32(image)!=0x27051956 || be32(image+16)!=MAIN_DEST ||
    be32(image+20)!=MAIN_DEST || image[28]!=17 || image[29]!=5 ||
    image[30]!=5 || image[31]!=4)return E_HEADER;
 hcrc=be32(image+4);memset(image+4,0,4);crc=crc32_bytes(image,64);
 image[4]=hcrc>>24;image[5]=hcrc>>16;image[6]=hcrc>>8;image[7]=hcrc;
 if(crc!=hcrc)return E_HEADER;
 n=be32(image+12);if(n!=total-64)return E_BOUNDS;
 s=image+64;if(crc32_bytes(s,n)!=be32(image+24))return E_DATA_CRC;
 if(n<54)return E_LZOP;
 for(i=0;i<9;i++)if(s[i]!=magic[i])return E_LZOP;
 if(s[9]!=0x10||s[10]!=0x40||s[15]!=3||s[16]!=9 ||
    be32(s+17)!=0x0300000d || s[33]!=0 ||
    be32(s+34)!=adler32_bytes(s+9,25))return E_LZOP;
 raw=be32(s+38);packed=be32(s+42);
 if(!raw || raw>OUT_LIMIT || raw!=MAIN_BYTES || packed<3 ||
    packed>raw || packed!=n-54 || be32(s+50+packed)!=0)return E_LZOP;
 if(raw==packed)memcpy(output,s+50,raw);
 else {
  cap=raw;rc=lzo1x_decompress_safe(s+50,packed,output,&cap);
  if(rc || cap!=raw)return E_DECODE;
 }
 if(adler32_bytes(output,raw)!=be32(s+46))return E_DECODE;
 *written=raw;return 0;
}
