#!/bin/bash
set -euo pipefail
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1791504000}" TZ=UTC LC_ALL=C PYTHONDONTWRITEBYTECODE=1
export KBUILD_BUILD_USER=research KBUILD_BUILD_HOST=local
src=/tmp/p2812-nandfinal1-src
obj=/tmp/p2812-nandfinal1-obj
out=/results
mkdir "$src" "$obj" "$out/main"
tar -C "$src" -xf /inputs/source.tar
make -C "$src" O="$obj" CROSS_COMPILE=mips-linux-gnu- \
    p2812hnuf1_nandfinal1_defconfig > "$out/main/build.log" 2>&1
if ! make -C "$src" -j4 O="$obj" CROSS_COMPILE=mips-linux-gnu- all \
        >> "$out/main/build.log" 2>&1; then
    tail -60 "$out/main/build.log"
    exit 1
fi
for file in u-boot u-boot.bin u-boot.map System.map .config; do
    cp "$obj/$file" "$out/main/$file"
done
mips-linux-gnu-nm -n "$obj/u-boot" > "$out/main/symbols.txt"
mips-linux-gnu-objdump -d "$obj/u-boot" > "$out/main/disassembly.txt"
mips-linux-gnu-readelf -h -l -S "$obj/u-boot" > "$out/main/readelf.txt"
cp "$src/tools/p2812/nandfinal1/layout.c" "$src/board/zyxel/p2812hnuf1/linuxrun_layout.c"
make -C "$src" O="$obj" CROSS_COMPILE=mips-linux-gnu- \
    board/zyxel/p2812hnuf1/linuxrun_layout.o >> "$out/main/build.log" 2>&1
mips-linux-gnu-objcopy -O binary -j '.rodata*' \
    "$obj/board/zyxel/p2812hnuf1/linuxrun_layout.o" "$out/main/linuxrun-layout.bin"
mips-linux-gnu-gcc --version > "$out/main/compiler.txt"
python3 "$src/tools/p2812/tests/test_modern_driver.py" --source "$src" > "$out/uart-clock-tests.log" 2>&1
python3 "$src/tools/p2812/tests/test_modern_nandro_driver.py" --source "$src" > "$out/nand-driver-tests.log" 2>&1
printf 'nandfinal1_main_build_completed\n' > "$out/status.txt"

python3 - "$src/Makefile" "$out/main/upstream-version.txt" <<'PYVERSION'
from pathlib import Path
import re,sys
text=Path(sys.argv[1]).read_text()
parts={}
for key in ('VERSION','PATCHLEVEL','SUBLEVEL','EXTRAVERSION'):
    m=re.search(r'^'+key+r'[ \t]*=[ \t]*([^\n]*)',text,re.M)
    if not m:raise RuntimeError('Upstream version field missing: '+key)
    parts[key]=m[1].strip()
version=parts['VERSION']+'.'+parts['PATCHLEVEL']
if parts['SUBLEVEL']:version+='.'+parts['SUBLEVEL']
version+=parts['EXTRAVERSION']
if not re.fullmatch(r'20[0-9]{2}\.[0-9]{2}(?:\.[0-9]+)?(?:-rc[0-9]+)?',version):
    raise RuntimeError('Unexpected upstream version: '+version)
Path(sys.argv[2]).write_text(version+'\n')
PYVERSION
