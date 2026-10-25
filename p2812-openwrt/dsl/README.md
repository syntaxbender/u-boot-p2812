# P2812 OpenWrt DSL companion

This recipe selects the AVM VRX200 Annex A DSP **5.9.1.4.0.7**, extracted from
the official FRITZ!Box 7490 / FRITZ!OS 7.62 image. It is a separate OpenWrt
component. The U-Boot firmware and stable-release workflow continue independently.

`inputs.lock.json` pins the vendor image, the OpenWrt 25.12.0 init script,
SquashFS sources and all 22 Freetz-NG extraction patches by SHA-256. The
extractor builds the AVM big-endian SquashFS reader, reads only selected files,
applies the vendor BSDIFF data and verifies both the embedded MD5 and the final
SHA-256. It does not mount images or execute vendor programs. The vendor's outer
image signature is not separately verified cryptographically.

The DSP remains proprietary. This repository and its CI artifacts contain the
source recipe and verification metadata. Vendor images, DSP blobs and generated
overlays remain in the local or runner temporary directory.

## Generate the files overlay on Linux

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends build-essential patch liblzma-dev bsdiff
python3 -B p2812-openwrt/dsl/verify_avm.py \
  --cache /tmp/p2812-dsl-inputs --work /tmp/p2812-dsl-verify
```

The resulting `files/` directory can be supplied as `FILES=` to a compatible
OpenWrt build/ImageBuilder. It contains:

- `/lib/firmware/avm-vrx200-a-5.9.1.4.0.7.bin`
- the hash-checked `dsl_control` script with the optional DSM mode hook
- a first-boot UCI script setting only `network.dsl.firmware` and
  `network.dsl.vectoring=1`

This overlay is not a full OpenWrt image or a U-Boot image. Image generation,
installation and board-specific kernel/Wi-Fi changes are separate steps.
Updating an existing installation requires retaining a local configuration/init
backup, copying the verified files, selecting the two DSL options and restarting
DSL deliberately. This CI never connects to or updates a modem.

## Why the DSM hook is needed

In MEI 1.5.17.6 the default vectoring preference is chosen for the first loaded
firmware. Switching from a non-vectoring DSP can preserve mode0/OFF. The small
init change reads the optional `network.dsl.vectoring` value and runs `dsmcs`
in `WaitForConfiguration`, before line training. Values are 0=off, 1=full,
2=friendly; an unset option preserves upstream behavior. Invalid values are
rejected. This companion option is an extension to the pinned OpenWrt script.

Check both the requested mode and negotiated result after installation:

```sh
dsl_cpe_pipe.sh dsmcg
dsl_cpe_pipe.sh dsmsg
ubus call dsl metrics
```

The installed P2812 baseline loaded this exact DSP, reported DSM request1 and
passed DSL/PPPoE/ping/HTTPS checks. Active vectoring was still not observed on
that line. A capable DSP and local enable do not establish the operator port's
vectoring configuration or guarantee a speed/stability improvement.

## Continuous verification

**P2812 OpenWrt AVM DSL verification** runs when this recipe changes and can be
started manually. It performs the extraction and overlay checks and retains
`verification.json` and a source-only recipe ZIP. A missing source, hash mismatch or changed init base fails
the workflow. Updating the pinned AVM release requires a reviewed lock update;
the U-Boot stable-tag monitor does not select DSL firmware versions.
