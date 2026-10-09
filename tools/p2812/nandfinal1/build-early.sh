#!/bin/bash
set -euo pipefail
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1791504000}" TZ=UTC LC_ALL=C
kit=/source/tools/p2812
out=/results
mkdir "$out/early"
cp -R "$kit/early" /tmp/nandsource4
early=/tmp/nandsource4
python3 - "$out/package/u-boot.bin" <<'PY'
from pathlib import Path
import sys,zlib
data=Path(sys.argv[1]).read_bytes()
Path('/tmp/nandsource4/generated.h').write_text(
    '#define MAIN_BYTES '+str(len(data))+'u\n#define MAIN_EXPECTED_CRC 0x%08xu\n'%zlib.crc32(data))
PY
cd "$out/early"
flags='-EB -march=mips32r2 -mtune=34kc -msoft-float -mno-abicalls -fno-pic -G0 -Os -ffreestanding -fno-builtin -fno-stack-protector -ffunction-sections -fdata-sections -fstack-usage -Wall -Wextra -Werror -Wno-unused-parameter'
for file in runtime ebu clock soc trace memory nand image tpl vendor/nand_ecc vendor/lzo1x_decompress; do
    name=$(basename "$file")
    mips-linux-gnu-gcc $flags -I"$early" -I"$early/include" -c "$early/$file.c" -o "$name.o"
done
mips-linux-gnu-gcc $flags -c "$early/start.S" -o start.o
mips-linux-gnu-ld -EB --gc-sections -T "$early/tpl.lds" -Map tpl.map -o tpl.elf \
    start.o runtime.o ebu.o clock.o soc.o trace.o memory.o nand.o image.o tpl.o nand_ecc.o lzo1x_decompress.o
mips-linux-gnu-objcopy -O binary tpl.elf tpl.bin
python3 - <<'PY'
from pathlib import Path
import zlib
data=Path('tpl.bin').read_bytes()
if len(data)>0x7800:raise RuntimeError('TPL layout overflow')
data=data.ljust(0x7800,b'\xff')
Path('tpl-padded.bin').write_bytes(data)
with Path('/tmp/nandsource4/generated.h').open('a') as stream:
    stream.write('#define TPL_CRC 0x%08xu\n'%zlib.crc32(data))
PY
mips-linux-gnu-gcc $flags -DBUILD_SPL -c "$early/start.S" -o spl-start.o
mips-linux-gnu-gcc $flags -I"$early" -I"$early/include" -c "$early/spl.c" -o spl.o
mips-linux-gnu-ld -EB --gc-sections -T "$early/spl.lds" -Map spl.map -o spl.elf \
    spl-start.o spl.o runtime.o ebu.o nand_ecc.o
mips-linux-gnu-objcopy -O binary spl.elf spl.bin
cp "$early/generated.h" generated.h
for stage in spl tpl; do
    mips-linux-gnu-nm -n "$stage.elf" > "$stage-symbols.txt"
    mips-linux-gnu-objdump -d "$stage.elf" > "$stage-disassembly.txt"
    mips-linux-gnu-readelf -h -l -S "$stage.elf" > "$stage-readelf.txt"
done
